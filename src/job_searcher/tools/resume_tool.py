from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import os
from typing import List, Union, Any

class ResumeBuilderInput(BaseModel):
    jobid: str = Field(..., description="Unique Job ID.")
    summary_points: List[str] = Field(..., description="List of 3 tailored summary bullet points.")
    skills_summary: Union[str, dict, list] = Field(..., description="Summary of skills.")
    projects: List[Union[str, dict]] = Field(..., description="List of project descriptions.")
    certifications: List[Union[str, dict]] = Field(..., description="List of certifications.")

class ResumeBuilderTool(BaseTool):
    name: str = "Resume Builder"
    description: str = "Compiles the tailored data into a final LaTeX .tex file."
    args_schema: type[BaseModel] = ResumeBuilderInput

    def _run(
        self,
        jobid: str,
        summary_points: list[str],
        skills_summary: Union[str, dict, list],
        projects: list[Union[str, dict]],
        certifications: list[Union[str, dict]],
    ) -> str:
        
        # --- HELPER: Escape Special LaTeX Characters ---
        # This prevents the "Missing $" errors in Overleaf
        def escape_latex(text: str) -> str:
            if not isinstance(text, str):
                return str(text)
            replacements = {
                "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_", 
                "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}"
            }
            for char, escaped in replacements.items():
                text = text.replace(char, escaped)
            return text

        # --- HELPER: Clean & Format Data ---
        # This prevents the "dict object has no attribute replace" errors
        def safe_str(item: Any) -> str:
            if isinstance(item, str): return escape_latex(item)
            if isinstance(item, list): return " ".join([escape_latex(str(i)) for i in item])
            if isinstance(item, dict):
                parts = []
                for v in item.values():
                    if isinstance(v, list): parts.append(" ".join([escape_latex(str(i)) for i in v]))
                    else: parts.append(escape_latex(str(v)))
                return " - ".join(parts)
            return escape_latex(str(item))

        # 1. Load Template
        template_path = os.path.join(os.getcwd(), "data", "resume_template.tex")
        with open(template_path, "r", encoding="utf-8") as file:
            template_content = file.read()

        # 2. Format Summary
        clean_summary = [safe_str(s) for s in summary_points]
        summary_latex = "\n".join([f"\\resumeItem{{{s}}}" for s in clean_summary if s.strip()])

        # 3. Format Skills
        skills_content_lines = []
        skills_text = ""
        
        # Handle if AI returns Dict or List instead of String
        if isinstance(skills_summary, dict):
            temp_parts = []
            for k, v in skills_summary.items():
                val_str = ", ".join([str(x) for x in v]) if isinstance(v, list) else str(v)
                temp_parts.append(f"{k}: {val_str}")
            skills_text = " | ".join(temp_parts)
        elif isinstance(skills_summary, list):
            skills_text = " | ".join([str(s) for s in skills_summary])
        else:
            skills_text = str(skills_summary)

        # Parse and Escape Skills
        if skills_text:
            for part in skills_text.split("|"):
                part = part.strip()
                if not part: continue
                if ":" in part:
                    cat, vals = part.split(":", 1)
                    cat = escape_latex(cat.strip())
                    vals = escape_latex(vals.strip())
                    skills_content_lines.append(f"\\textbf{{{cat}}}{{: {vals}}} \\\\")
                else:
                    part = escape_latex(part)
                    skills_content_lines.append(f"\\textbf{{Key Skills}}{{: {part}}} \\\\")
        skills_latex = "\n    ".join(skills_content_lines)

        # 4. Format Projects (With Capitalization Fix)
        project_blocks = []
        for p in projects:
            # Check if it's a Dictionary (Title: Description)
            if isinstance(p, dict):
                try:
                    # Get Key (Title) and Value (Description)
                    raw_key = list(p.keys())[0]
                    raw_val = list(p.values())[0]
                    
                    # --- CAPITALIZATION FIX ---
                    # "title" -> "Title"
                    title_formatted = escape_latex(str(raw_key).capitalize())
                    
                    # Clean Description
                    desc_str = " ".join(raw_val) if isinstance(raw_val, list) else str(raw_val)
                    desc_formatted = escape_latex(desc_str)
                    
                    text = f"\\textbf{{{title_formatted}}}: {desc_formatted}"
                except:
                    # Fallback if dict structure is weird
                    text = safe_str(p)
            else:
                # It's just a string
                text = safe_str(p)

            # Cleanup string artifacts
            text = text.replace("\n", " ").strip()
            if text.startswith("['") and text.endswith("']"):
                text = text[2:-2].replace("', '", " ")

            if not text: continue
            
            block = (
                "\\resumeSubheading\n"
                "      {Relevant Project / Experience}{}{}{}\n" 
                "      \\resumeItemListStart\n"
                f"        \\resumeItem{{{text}}}\n"
                "      \\resumeItemListEnd"
            )
            project_blocks.append(block)
        projects_latex = "\n\n".join(project_blocks)

        # 5. Format Certs
        cert_lines = [f"\\item {safe_str(c)}" for c in certifications if c]
        certs_latex = "\n    ".join(cert_lines)

        # 6. Replace Placeholders
        final_tex = template_content.replace("{{SUMMARY_CONTENT}}", summary_latex)
        final_tex = final_tex.replace("{{SKILLS_CONTENT}}", skills_latex)
        final_tex = final_tex.replace("{{PROJECTS_CONTENT}}", projects_latex)
        final_tex = final_tex.replace("{{CERTIFICATIONS_CONTENT}}", certs_latex)

        # 7. Save File
        output_dir = os.path.join(os.getcwd(), "output")
        os.makedirs(output_dir, exist_ok=True)
        
        safe_jobid = "".join(c for c in jobid if c.isalnum() or c in (' ', '_', '-')).strip()
        safe_jobid = safe_jobid.replace(" ", "_")
        
        output_filename = f"Resume_Nani_{safe_jobid}.tex"
        output_path = os.path.join(output_dir, output_filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_tex)

        return f"Resume created successfully at: {output_path}"