from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import json
import re
import time
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

class JobSearchInput(BaseModel):
    url: str = Field(..., description="The direct Career Page URL to scrape.")

class JobSearchTool(BaseTool):
    name: str = "Hybrid Job Crawler"
    description: str = "Scrapes career pages using Visual Scrolling + API Sniffing (Network Listener)."
    args_schema: type[BaseModel] = JobSearchInput

    def _run(self, url: str) -> str:
        print(f"\n🔎 Scanning: {url}")
        print("   (Running Hybrid Engine: Visual Scraping + API Sniffing...)")

        found_jobs_map = {}

        with sync_playwright() as p:
            # Launch Browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            # ---------------------------------------------------------
            # 📡 BACKGROUND LISTENER (Your "Secret Weapon")
            # ---------------------------------------------------------
            def handle_response(response):
                try:
                    if "json" in response.headers.get("content-type", ""):
                        if "jobs" in response.url or "search" in response.url or "greenhouse" in response.url:
                            try:
                                data = response.json()
                                job_list = data if isinstance(data, list) else data.get("jobs", [])
                                
                                if isinstance(job_list, list) and len(job_list) > 0:
                                    print(f"      ⚡ Network Sniffer detected {len(job_list)} jobs via API!")
                                    
                                    for job in job_list:
                                        # Location Filter
                                        locations = job.get("locations", [])
                                        loc_str = str(locations) + str(job.get("location", ""))
                                        is_india = any(x in loc_str for x in ["India", "Bangalore", "Hyderabad", "Pune"])
                                        
                                        if is_india:
                                            j_id = str(job.get("id", "N_A"))
                                            title = job.get("title", "Unknown Role")
                                            j_url = job.get("absolute_url", "")
                                            raw_content = job.get("content", "")
                                            clean_content = re.sub(r'<[^<]+?>', '', raw_content) 
                                            
                                            if j_url and j_url not in found_jobs_map:
                                                found_jobs_map[j_url] = {
                                                    "id": j_id,
                                                    "title": title,
                                                    "url": j_url,
                                                    "content": clean_content 
                                                }
                            except: pass
                except: pass

            page.on("response", handle_response)

            # ---------------------------------------------------------
            # 👀 VISUAL SCRAPER
            # ---------------------------------------------------------
            try:
                page.goto(url, timeout=60000)
                page.wait_for_load_state("networkidle")

                # Scroll loop
                for i in range(5): 
                    page.mouse.wheel(0, 5000)
                    time.sleep(2)

                # Extract Links visually
                links = page.query_selector_all("a")
                for link in links:
                    try:
                        text = link.inner_text().strip()
                        href = link.get_attribute("href")
                        
                        if not href or len(text) < 5: continue
                        full_url = urljoin(url, href)
                        
                        if full_url in found_jobs_map: continue

                        # Simple keyword heuristic
                        if any(x in full_url.lower() for x in ["/job/", "/career/", "req-"]):
                            id_match = re.search(r"(\d+)", full_url)
                            job_id = id_match.group(1) if id_match else "N_A"
                            
                            found_jobs_map[full_url] = {
                                "id": job_id,
                                "title": text,
                                "url": full_url,
                                "content": None # Will fetch later
                            }
                    except: continue

            except Exception as e:
                print(f"   ⚠️ Crawl Warning: {e}")
            finally:
                browser.close()

        results = list(found_jobs_map.values())
        print(f"   ✅ Total Jobs Found: {len(results)}")
        return json.dumps(results)