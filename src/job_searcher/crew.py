from crewai import Agent, Task, LLM
from crewai.project import CrewBase, agent
from src.job_searcher.tools.resume_tool import ResumeBuilderTool
import os
from dotenv import load_dotenv

load_dotenv()


@CrewBase
class JobSearcherCrew:
    """JobSearcher crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Single Groq LLM used by all agents
    groq_llm = LLM(
        model=f"groq/{os.getenv('MODEL')}",  # <-- note the "groq/" prefix
        api_key=os.getenv("GROQ_API_KEY"),
        provider="groq",
    )

    # --------- helpers ----------

    def load_profile(self) -> str:
        """Read profile.json as text."""
        path = os.path.join(os.getcwd(), "data", "profile.json")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # --------- AGENTS ----------

    @agent
    def profile_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["profile_analyst"],
            verbose=False,
            llm=self.groq_llm,
        )

    @agent
    def latex_developer(self) -> Agent:
        return Agent(
            config=self.agents_config["latex_developer"],
            tools=[ResumeBuilderTool()],
            verbose=True,
            llm=self.groq_llm,
        )

    # --------- TASK FACTORIES ----------

    def get_scoring_task(self, job_description: str):
        profile_data = self.load_profile()
        return Task(
            description=f"""
You are Nani Pasupuleti's Job Match Scorer.

JOB_DESCRIPTION:
{job_description}

CANDIDATE_PROFILE_JSON:
{profile_data}

Your goals:
1. Read the candidate profile JSON carefully. It contains:
   - skills: categories with real skills
   - certifications
   - projects (with titles and tech_stack)

2. Compare the job requirements with the candidate's profile:
   - If required experience clearly > 5 years -> score = 0.
   - If job location is not India -> score = 0.
   - Otherwise, compute a match score from 0 to 100:
     * 90–100 = Very strong match (most required skills present; at least one project obviously relevant).
     * 70–89  = Good match (many skills align, some gaps).
     * 50–69  = Possible match (some overlap; important gaps).
     * <50    = Weak / irrelevant.

3. Identify:
   - matching_skills: skills from the candidate that match the job.
   - missing_skills: important skills mentioned in the job but not in the profile.
   - best_projects: 1–2 project titles from the profile that are most relevant.
   - recommended_role_type: short label like "Backend Java", "Python/AI", "Fullstack",
     "Networking/Security", or "Non-tech".

Return ONLY valid JSON, nothing else, in this exact schema (values are examples):

{{
  "score": 78,
  "reason": "Many backend skills align, some gaps",
  "company_name": "",
  "matching_skills": ["Java", "Spring Boot", "React"],
  "missing_skills": ["Kubernetes"],
  "best_projects": ["Integrated Dashboard: YouTube & Expense Tracker"],
  "recommended_role_type": "Backend Java / Fullstack"
}}
            """,
            expected_output="Valid JSON string using the schema above.",
            agent=self.profile_analyst(),
        )

    def get_resume_task(self, job_data: dict):
        """
        job_data should contain:
        - title
        - company
        - description
        - matching_skills (list)
        - best_projects (list)
        """
        profile_data = self.load_profile()
        job_title_short = job_data["title"][:50]

        return Task(
            description=f"""
You are a LaTeX Automation Engineer.

Your goal is to prepare **plain text** inputs for the Resume Builder tool
for this job and then call the tool once.

Do NOT output any LaTeX. The tool will create LaTeX itself.

JOB_TITLE: {job_data['title']}
COMPANY: {job_data['company']}

JOB_DESCRIPTION:
{job_data['description']}

CANDIDATE_PROFILE_JSON:
{profile_data}

MATCHING_SKILLS_FROM_SCORING:
{job_data.get("matching_skills", [])}

BEST_PROJECT_TITLES_FROM_SCORING:
{job_data.get("best_projects", [])}

==========================
1) Build skills_summary (PLAIN TEXT ONLY)
==========================
- Use ONLY skills that are both in MATCHING_SKILLS_FROM_SCORING and in the profile.
- Group into logical categories like "Backend Technologies", "Security", "Tools".
- Output format (plain text, no LaTeX, no backslashes):
  Backend Technologies: Python, SQL, MySQL | Security: SSL Inspection, Firewall Policies, DNS Policies, IPv6, TCP/IP | Tools: Git, GitHub, Postman, VS Code, STS

Name this final string: skills_summary.

==========================
2) Build projects (PLAIN TEXT LIST)
==========================
- Select 2–3 projects from the candidate profile.
- Prefer those whose titles are in BEST_PROJECT_TITLES_FROM_SCORING.
- For each project, write a short paragraph describing what was built and why it fits this job.
- NO LaTeX, NO markdown, just plain text sentences.

Return them as a JSON array of strings, e.g.:
[
  "Built an automated AI agent integrated with GitHub Actions to review code on every PR.",
  "Developed a full-stack dashboard using Spring Boot and React to manage expenses and YouTube content."
]

Name this final array: projects.

==============================
3) Build certifications (PLAIN TEXT LIST)
==============================
- Select only certifications that make sense for this job.
- Just use their names as plain strings, e.g.:

[
  "Oracle Java Programming (Infosys Springboard)",
  "Amazon Web Services (AICTE)",
  "Networking (Cisco)",
  "Databricks (Databricks Academy)"
]

Name this final array: certifications.

========================================
4) FINAL ACTION: Call ResumeBuilderTool
========================================
Now you MUST call the tool **Resume Builder** exactly once.

Use this JSON shape (no extra keys, all values plain text or arrays):

{{
  "jobid": "{job_title_short}",
  "skills_summary": "skills_summary string from step 1",
  "projects": projects array from step 2,
  "certifications": certifications array from step 3
}}

Important:
- Do NOT include any LaTeX, backslashes, or '#' characters in any field.
- Ensure "projects" and "certifications" are JSON arrays of strings, not single strings.
- "jobid" and "skills_summary" are plain strings.

Do not write the full resume.
Do not change the template.
Only prepare these fields and invoke the tool once.
            """,
            expected_output="LaTeX .tex file generated successfully via ResumeBuilderTool.",
            agent=self.latex_developer(),
        )

