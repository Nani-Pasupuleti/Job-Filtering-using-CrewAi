#!/usr/bin/env python
import sys
import json
import time
import re
import os
import logging
import ast
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import requests

# Import the Tool directly
from src.job_searcher.tools.resume_tool import ResumeBuilderTool

# --- CONFIGURATION ---
load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
model_name = os.getenv("MODEL") or "llama-3.1-8b-instant"

RESUME_CALL_SLEEP = 30  
ANALYZE_RATE_LIMIT_SLEEP = 30

if not groq_api_key:
    print("❌ Error: GROQ_API_KEY not found.")
    sys.exit(1)

logging.getLogger("httpx").setLevel(logging.WARNING)
print(f"🤖 Agent initialized using model: {model_name} (Direct Mode)")


# --- 1. UNIVERSAL HYBRID CRAWLER ---
def crawl_jobs(url: str):
    print(f"\n🔹 STEP 1: Scanning Career Page...")
    found_jobs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use a real user agent to avoid being blocked
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # --- A. NETWORK SNIFFER (Broadened) ---
        def handle_response(response):
            try:
                # Check if response is JSON
                if "json" in response.headers.get("content-type", ""):
                    try:
                        data = response.json()
                        
                        # Handle different API structures (data.jobs, data.results, or direct list)
                        job_list = []
                        if isinstance(data, list):
                            job_list = data
                        elif isinstance(data, dict):
                            # Common keys for job lists
                            for key in ["jobs", "results", "data", "hits", "positions"]:
                                if key in data and isinstance(data[key], list):
                                    job_list = data[key]
                                    break
                        
                        if job_list and len(job_list) > 0:
                            # Basic validation: does the first item look like a job?
                            first = job_list[0]
                            if isinstance(first, dict) and ("title" in first or "jobTitle" in first):
                                print(f"      ⚡ API detected {len(job_list)} potential jobs!")
                                
                                for job in job_list:
                                    # Normalize Keys
                                    title = job.get("title") or job.get("jobTitle") or job.get("name")
                                    location = str(job.get("location") or job.get("locations") or job.get("address") or "")
                                    
                                    # Construct URL (Handle relative or absolute)
                                    slug = job.get("url") or job.get("slug") or job.get("externalPath")
                                    j_url = slug
                                    if slug and not slug.startswith("http"):
                                        # Attempt to build full URL from base
                                        from urllib.parse import urljoin
                                        j_url = urljoin(url, slug)

                                    # Location Filter (India)
                                    is_india = any(x in location for x in ["India", "Bangalore", "Hyderabad", "Pune", "Gurgaon", "Noida", "Mumbai", "Chennai", "IN"])
                                    
                                    if is_india and title and j_url:
                                        found_jobs[j_url] = {
                                            "title": title,
                                            "url": j_url,
                                            "content": "" # Will fetch later
                                        }
                    except: pass
            except: pass

        page.on("response", handle_response)

        # --- B. VISUAL LOADING ---
        try:
            print(f"      🌍 Navigating to: {url}")
            page.goto(url, timeout=60000)
            
            # Force wait for dynamic content (React/Angular sites)
            print("      ⏳ Waiting for page to load (5s)...")
            page.wait_for_load_state("networkidle")
            time.sleep(5) 

            # Aggressive Scroll to trigger lazy loading
            print("      📜 Scrolling to load more jobs...")
            for _ in range(5):
                page.mouse.wheel(0, 5000)
                time.sleep(1.5)

            # --- C. VISUAL SCRAPER FALLBACK ---
            # If API sniffer failed, look for links on the page visually
            if len(found_jobs) < 5:
                print("      👀 Parsing visible links on page...")
                links = page.query_selector_all("a")
                for link in links:
                    try:
                        text = link.inner_text().strip()
                        href = link.get_attribute("href")
                        
                        if not href or len(text) < 4: continue
                        
                        # Filter for Job-like titles visually
                        text_lower = text.lower()
                        # Must contain tech keywords AND not be a generic link
                        is_job_link = any(x in text_lower for x in ["engineer", "developer", "analyst", "architect", "lead", "manager"])
                        is_generic = any(x in text_lower for x in ["read more", "learn more", "apply", "jobs", "careers"])
                        
                        if is_job_link and not is_generic:
                            from urllib.parse import urljoin
                            full_url = urljoin(url, href)
                            if full_url not in found_jobs:
                                found_jobs[full_url] = {
                                    "title": text,
                                    "url": full_url,
                                    "content": ""
                                }
                    except: continue

        except Exception as e:
            print(f"      ⚠️ Crawl Warning: {e}")
        finally:
            browser.close()

    jobs_list = list(found_jobs.values())
    print(f"   ✅ Total Jobs Found: {len(jobs_list)}")
    return jobs_list


# --- 2. FETCH TEXT (FULL CONTENT) ---
def get_job_text(job: dict) -> str:
    if job.get("content") and len(job["content"]) > 100:
        return job["content"][:6000] 

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(job["url"], timeout=30000)
            
            # Locate main content - Try common job board selectors
            content = ""
            for selector in ["main", "article", ".job-description", "#job-description", ".content"]:
                try:
                    if page.locator(selector).count() > 0:
                        content = page.inner_text(selector)
                        break
                except: pass
            
            # Fallback to body
            if not content:
                content = page.inner_text("body")
                
            return content[:6000] 
        except Exception:
            return ""
        finally:
            browser.close()


# --- 3. ANALYZE (Groq) ---
def analyze_job(job_text: str, title: str) -> dict:
    try:
        with open("data/profile.json", "r", encoding="utf-8") as f:
            profile = f.read()
    except Exception:
        profile = "{}"

    # REVISED PROMPT: AGGRESSIVE & OPTIMISTIC
    prompt = f"""
    You are an Ambitious Career Coach helping a skilled Engineer get hired.
    
    JOB TITLE: {title}
    JOB DESC: {job_text[:6000]}
    CANDIDATE PROFILE: {profile}
    
    STRATEGY:
    The candidate has ~1-2 years of experience but STRONG SKILLS in Java, Python, AI, and Automation.
    We apply to anything that matches the SKILLS, even if the "Years of Experience" asked is higher (up to 5-6 years).
    
    SCORING RULES:
    1. **IGNORE EXPERIENCE GAPS**: If the job asks for 3, 4, or 5 years, treat it as a MATCH. Only reject if it explicitly demands 8+ years or Senior Management (Director/VP).
    2. **SKILLS FIRST**: If the job needs "Python", "AI", "Java", or "Automation", and the candidate has it -> SCORE HIGH (80%+).
    3. **ROLE FLEXIBILITY**: "Escalation Engineer", "Support Engineer", "SDET" are GREAT MATCHES if they require coding/scripting/Linux.
    
    OUTPUT JSON:
    {{
        "matching_skills": [],
        "best_projects": [],
        "score": int, 
        "reason": "Short summary",
        "justification": "Why this is a good opportunity. Be optimistic."
    }}
    
    Return ONLY JSON.
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
    data = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=30)
            if resp.status_code == 429:
                print(f"      ⚠️ Rate limit. Sleeping {ANALYZE_RATE_LIMIT_SLEEP}s...")
                time.sleep(ANALYZE_RATE_LIMIT_SLEEP)
                continue
            
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.replace("```json", "").replace("```", "").strip()
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                json_str = match.group(0)
                try: return json.loads(json_str)
                except: return ast.literal_eval(json_str)
        except: pass
        time.sleep(2)

    return {"score": 0, "reason": "Failed", "justification": "Analysis failed.", "matching_skills": [], "best_projects": []}


# --- 4. GENERATE RESUME DATA (Strict Certs) ---
def generate_resume_data(job_data):
    try:
        with open("data/profile.json", "r", encoding="utf-8") as f:
            profile = f.read()
    except: profile = "{}"

    prompt = f"""
    You are an Expert Resume Writer & ATS Optimizer.
    
    TARGET JOB: {job_data['title']} at {job_data['company']}
    DESCRIPTION: {job_data['description'][:6000]}
    
    CANDIDATE SOURCE OF TRUTH (JSON):
    {profile}
    
    YOUR MISSION:
    Create a highly tailored resume JSON object.
    
    RULES FOR CONTENT:
    1. **Summary**: Write 3 punchy bullet points connecting the candidate's existing experience to the job requirements.
    
    2. **Skills Summary**: Create a string grouping skills by category.
       - Format: "Category: Skill, Skill | Category: Skill, Skill".
       - ONLY include skills relevant to this specific job.
    
    3. **Projects**: Select exactly 2 projects from the Profile that prove the candidate can do this specific job.
    
    4. **Certifications (STRICT FILTER)**: 
       - Look at the Job Description. Select ONLY certifications that are **directly relevant**.
       - **EXCLUDE** unrelated certs. 
         * Example: If the job is AI/Security, DO NOT include "Frontend/CSS" certifications.
         * Example: If the job is Backend, DO NOT include "UI/UX" certifications.
       - Keep only the top 2-3 most impactful certifications for this specific role.
    
    DO NOT HALLUCINATE. If the skill/project is not in the JSON, do not invent it.
    
    OUTPUT FORMAT (VALID JSON ONLY):
    {{
        "summary_points": ["Point 1", "Point 2", "Point 3"],
        "skills_summary": "Backend: Java, SQL | Tools: Git",
        "projects": ["Project Title 1... Description...", "Project Title 2... Description..."],
        "certifications": ["Cert 1", "Cert 2"]
    }}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
    data = {
        "model": model_name, 
        "messages": [{"role": "user", "content": prompt}], 
        "temperature": 0.1,
        "response_format": {"type": "json_object"} 
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            
            if resp.status_code == 429:
                print(f"      ⚠️ Writing Rate Limit. Sleeping {RESUME_CALL_SLEEP}s...")
                time.sleep(RESUME_CALL_SLEEP)
                continue
            
            if resp.status_code != 200:
                print(f"      ⚠️ API Error {resp.status_code}: {resp.text}")
                continue

            content = resp.json()["choices"][0]["message"]["content"]
            content = content.replace("```json", "").replace("```", "").strip()
            
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                json_str = match.group(0)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    try: return ast.literal_eval(json_str)
                    except: pass
                        
        except Exception as e:
            print(f"      ⚠️ Error generating data: {e}")
            time.sleep(5)
    
    return None

# --- 5. DETAILED FILTER LOGIC ---
def is_tech_job(title):
    title = title.lower()
    
    bad_map = {
        "director": "Too Senior (Director Level)",
        "vp ": "Too Senior (VP Level)",
        "principal": "Too Senior (Principal Level)",
        "manager": "Management Role",
        "head of": "Management Role",
        "sales": "Non-Tech (Sales)",
        "marketing": "Non-Tech (Marketing)",
        "account": "Non-Tech (Accounts/Sales)",
        "finance": "Non-Tech (Finance)",
        "legal": "Non-Tech (Legal)",
        "hr ": "Non-Tech (HR)",
        "recruiter": "Non-Tech (HR)",
        "representative": "Non-Tech (Support/Sales)",
        "tax": "Non-Tech (Finance)"
    }
    
    for keyword, reason in bad_map.items():
        if keyword in title:
            return False, reason

    tech_keywords = [
        "engineer", "developer", "architect", "lead", "data", "qa", "sdet", 
        "devops", "sre", "full stack", "backend", "frontend", "software", 
        "technologist", "analyst", "consultant", "scientist", "administrator"
    ]
    
    if not any(k in title for k in tech_keywords):
        return False, "Title does not contain Tech keywords"

    return True, "Valid Tech Role"

# --- MAIN ---
def run():
    print(f"\n--- 🤖 JOB AGENT (Universal Mode) ---")
    url = input("Enter Career Page URL: ")

    jobs = crawl_jobs(url)
    if not jobs: return

    print(f"\n🔹 STEP 2: Auditing & Scoring {len(jobs)} Jobs...")
    valid_jobs = []

    for i, job in enumerate(jobs):
        current = i + 1
        title = job.get("title", "")
        print(f"\n[{current}/{len(jobs)}] Checking: {title}")
        print(f"      🔗 {job['url']}")

        # 1. PYTHON FILTER
        is_valid, reason = is_tech_job(title)
        if not is_valid:
            print(f"      🔴 Skipped: {reason}")
            continue

        # 2. Text & Analyze
        text = get_job_text(job)
        if len(text) < 100:
            print("      ⚠️ Content Empty")
            continue

        result = analyze_job(text, title)
        score = result.get("score", 0)
        justification = result.get("justification", "No details provided.")
        
        icon = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"
        print(f"      {icon} SCORE: {score}%")
        print(f"      📝 REASON: {justification}")

        if score >= 50:
            job["description"] = text
            job["match_score"] = score
            job["matching_skills"] = result.get("matching_skills", [])
            job["best_projects"] = result.get("best_projects", [])
            job["company"] = "Zscaler" 
            valid_jobs.append(job)

        time.sleep(2) 

        if current % 5 == 0:
            print(f"\n--- ⏳ Processed {current} jobs. ---")
            choice = input("Press ENTER to continue, or type 'stop': ").lower()
            if choice == "stop": break

    # 3. Generate Resumes
    print(f"\n🔹 STEP 3: Generating Resumes...")
    valid_jobs.sort(key=lambda x: x["match_score"], reverse=True)

    for idx, j in enumerate(valid_jobs[:10]):
        print(f"{idx + 1}. [{j['match_score']}%] {j['title']}")

    try:
        limit = int(input("\nHow many resumes to generate? (0 to skip): "))
    except: limit = 0

    if limit > 0:
        builder = ResumeBuilderTool()
        
        for idx, j in enumerate(valid_jobs[:limit]):
            print(f"   Writing resume for: {j.get('title')}")
            print(f"      🔗 Apply Link: {j.get('url')}")
            
            ai_data = generate_resume_data(j)
            
            if ai_data:
                try:
                    result = builder._run(
                        jobid=f"{j['title'][:30]}_{idx+1}", 
                        summary_points=ai_data.get("summary_points", []),
                        skills_summary=ai_data.get("skills_summary", ""),
                        projects=ai_data.get("projects", []),
                        certifications=ai_data.get("certifications", [])
                    )
                    print(f"      ✅ {result}")
                except Exception as e:
                    print(f"      ❌ Builder Error: {e}")
            else:
                print("      ❌ Failed to generate resume data.")

            print(f"      ⏳ Cooling down {RESUME_CALL_SLEEP}s...")
            time.sleep(RESUME_CALL_SLEEP)

if __name__ == "__main__":
    run()