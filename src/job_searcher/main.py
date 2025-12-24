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


# --- 1. HYBRID CRAWLER ---
def crawl_jobs(url: str):
    print(f"\n🔹 STEP 1: Scanning Career Page...")
    found_jobs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def handle_response(response):
            try:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype and ("jobs" in response.url or "search" in response.url):
                    data = response.json()
                    job_list = data if isinstance(data, list) else data.get("jobs", [])
                    if isinstance(job_list, list) and len(job_list) > 0:
                        print(f"      ⚡ API detected {len(job_list)} jobs!")
                        for job in job_list:
                            loc = str(job.get("locations", "")) + str(job.get("location", ""))
                            if any(x in loc for x in ["India", "Bangalore", "Hyderabad", "Pune"]):
                                j_url = job.get("absolute_url", "")
                                if j_url:
                                    found_jobs[j_url] = {
                                        "title": job.get("title", ""),
                                        "url": j_url,
                                        "content": re.sub(r"<[^<]+?>", "", job.get("content", "")),
                                    }
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            page.goto(url, timeout=60000)
            for _ in range(4):
                page.mouse.wheel(0, 4000)
                time.sleep(2)
        except Exception:
            pass
        finally:
            browser.close()

    jobs_list = list(found_jobs.values())
    print(f"   ✅ Total Jobs Found: {len(jobs_list)}")
    return jobs_list


# --- 2. FETCH TEXT ---
def get_job_text(job: dict) -> str:
    if job.get("content") and len(job["content"]) > 100:
        return job["content"][:6000] 

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(job["url"], timeout=30000)
            text = page.inner_text("body")
            return text[:6000] 
        except Exception:
            return ""
        finally:
            browser.close()


# --- 3. ANALYZE (Smart Holistic Matching) ---
def analyze_job(job_text: str, title: str) -> dict:
    try:
        with open("data/profile.json", "r", encoding="utf-8") as f:
            profile = f.read()
    except Exception:
        profile = "{}"

    # REVISED PROMPT: CONTEXT-AWARE MATCHER
    prompt = f"""
    You are an Expert Technical Talent Matcher. 
    
    JOB TITLE: {title}
    JOB DESC: {job_text[:6000]}
    
    CANDIDATE PROFILE (SOURCE OF TRUTH):
    {profile}
    
    YOUR GOAL: Calculate a relevance score (0-100) based on deep analysis of the candidate's Skills, Projects, and Certifications against the Job Requirements.
    
    --- ANALYSIS RULES ---
    
    1. **THE EXPERIENCE RULE (CRITICAL):**
       - The candidate is Early Career (approx. 1-2 years).
       - IF the job requires **> 5 years** of experience:
            - **REJECT (Score 0)** if the candidate's skills are only a "partial" match.
            - **ACCEPT (Score 80+)** ONLY if the candidate's Tech Stack is a **PERFECT** match (e.g., Job wants exactly Java+Spring+React+AWS, and Candidate has exactly that in Projects).
            - *Logic:* We ignore the years ONLY if the technical fit is 100%.
       - IF the job requires **Senior Management** (Director, VP, Principal): **Score 0** immediately.
    
    2. **HOLISTIC SKILL VERIFICATION:**
       - Do not just keyword match the 'skills' list. 
       - Look for **EVIDENCE** in 'projects' and 'certifications'.
       - *Example:* If Job wants "AI Agents", check if the Candidate has an "AI Agent" project. (He does: 'AI Code Quality Reviewer').
       - *Example:* If Job wants "Cloud/AWS", check 'certifications'. (He has: 'Amazon Web Services').
       - *Example:* If Job wants "Testing", check 'skills'. (He has: 'PyTest', 'API Testing').
    
    3. **SCORING ALGORITHM:**
       - **90-100:** Perfect Tech Stack Match + Relevant Project Evidence + Experience matches (or is ignored due to perfect skill fit).
       - **75-89:** Strong Tech Stack Match + Relevant Projects. (Good fit).
       - **50-74:** Partial Skill Match (e.g., matches Backend but not Frontend, or matches AI but not Java).
       - **< 50:** Irrelevant Role (Sales, HR, Non-Tech) or mismatching Tech Stack (e.g., C++ Embedded, .NET Legacy).
    
    --- OUTPUT FORMAT ---
    
    Return VALID JSON ONLY. No markdown.
    {{
        "matching_skills": ["List of matched skills found in Profile"],
        "best_projects": ["Title of 1-2 most relevant projects from Profile"],
        "score": int, 
        "reason": "Short summary (e.g., 'Perfect Java/AI match, ignoring 6yr req')",
        "justification": "2 sentences explaining the score based on Projects/Certs evidence."
    }}
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


# --- 4. GENERATE RESUME DATA (Strict Extraction) ---
def generate_resume_data(job_data):
    try:
        with open("data/profile.json", "r", encoding="utf-8") as f:
            profile = f.read()
    except: profile = "{}"

    # REVISED PROMPT: STRICT FILTERING MODE
    prompt = f"""
    You are a Strict Resume Data Extractor.
    
    TARGET JOB: {job_data['title']} at {job_data['company']}
    DESCRIPTION: {job_data['description'][:6000]}
    
    CANDIDATE SOURCE OF TRUTH (JSON):
    {profile}
    
    YOUR MISSION:
    Select and filter data from the SOURCE OF TRUTH to create a tailored resume.
    
    CRITICAL RULES (ZERO HALLUCINATION):
    1. **SOURCE ONLY**: You are FORBIDDEN from adding any skill, project, or certification that is not explicitly written in the "CANDIDATE SOURCE OF TRUTH" JSON.
    2. **FILTERING**:
       - **Skills**: Pick only skills from the JSON that match the Job Description.
       - **Projects**: Pick exactly 2 projects from the JSON. Do not invent new projects.
       - **Certifications**: Pick only relevant certifications from the JSON. 
         * IF Job is Backend/AI -> REMOVE Frontend/CSS certs.
         * IF Job is Frontend -> REMOVE Cloud/Backend certs.
         * If no relevant certs exist in JSON, return an empty list [].
    
    OUTPUT FORMAT (VALID JSON ONLY):
    {{
        "summary_points": ["Point 1 (Based on Profile)", "Point 2 (Based on Profile)", "Point 3"],
        "skills_summary": "Category: Skill, Skill | Category: Skill, Skill",
        "projects": ["Project Title... Description...", "Project Title... Description..."],
        "certifications": ["Cert Name 1", "Cert Name 2"]
    }}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
    data = {
        "model": model_name, 
        "messages": [{"role": "user", "content": prompt}], 
        "temperature": 0.0, # Temp 0.0 forces strict adherence to facts
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
    print(f"\n--- 🤖 JOB AGENT (Senior Mode - With Links) ---")
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
            # --- Added Link Display Here ---
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