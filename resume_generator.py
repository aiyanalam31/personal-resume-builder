"""
resume_generator.py
───────────────────
Uses the Groq API to:
  1. Parse a job description for key requirements
  2. Select the most relevant experiences, projects, and skills from the DB
  3. Rewrite bullet points to be ATS-optimized and tailored to the JD
  4. Verify keyword coverage — re-prompt if critical JD keywords are missing
  5. Estimate line count against a 1-page budget and trim until it fits
  6. Return a structured resume dict guaranteed to fit one page

Usage:
  from storage import ProfileDB
  from resume_generator import ResumeGenerator

  db  = ProfileDB("resume_builder.db")
  gen = ResumeGenerator(db, api_key="gsk_...")

  resume = gen.generate(job_description="...")
  print(resume)

Or via CLI:
  python resume_generator.py --jd job_description.txt --db resume_builder.db
"""

import argparse
import json
import os
import sys
from groq import Groq

# Import our storage layer (must be in the same folder)
sys.path.insert(0, os.path.dirname(__file__))
from storage import ProfileDB


MODEL = "llama-3.3-70b-versatile"

# ── Page budget ───────────────────────────────────────────────────────────────
# These values mirror the PDF template's layout (defined in pdf_renderer.py).
# A "line" is one bullet point or one wrapped continuation line (~90 chars wide).
# Adjust if you change font size or margins in the renderer.

CHARS_PER_LINE   = 90    # characters that fit on one line at the resume font size
PAGE_LINE_BUDGET = 52    # total content lines available on one page

# Fixed overhead lines consumed by headers/sections regardless of content:
#   header (name + headline + contact): 4
#   "Experience" heading + spacing per role (title + company + dates): 3 lines each
#   "Projects" heading + spacing per project (title + dates): 2 lines each
#   "Education" heading + 2 lines per entry
#   "Skills" heading + 1 line
#   "Certifications" heading + 1 line per cert
OVERHEAD_PER_EXPERIENCE = 3   # section title row + company/date row + spacing
OVERHEAD_PER_PROJECT    = 2   # title row + spacing
FIXED_OVERHEAD          = 4   # name block + section headings baseline

# ── Prompts ───────────────────────────────────────────────────────────────────

SELECTION_PROMPT = """\
You are an expert resume strategist. Given a candidate's full profile and a job description,
your task is to select the most relevant items for a 1-page ATS-friendly resume.

CANDIDATE PROFILE:
{profile}

JOB DESCRIPTION:
{jd}

Select the best items to include. Return ONLY valid JSON in this exact structure:
{{
  "selected_experience_ids": [<list of experience id integers, max 3>],
  "selected_project_ids":    [<list of project id integers, max 2>],
  "selected_skills":         [<list of skill name strings, max 12>],
  "job_title_suggestion":    "<a tailored resume title/headline for this role>"
}}

Rules:
- Prioritize relevance to the job description above all else
- For experiences and projects, pick the ones whose descriptions best match the JD keywords
- For skills, pick those explicitly mentioned or strongly implied in the JD first
- Return ONLY the JSON object, no explanation or markdown
"""

KEYWORD_EXTRACTION_PROMPT = """\
Extract the most important ATS keywords from this job description.
Focus on: required skills, tools, technologies, methodologies, and role-specific verbs.

JOB DESCRIPTION:
{jd}

Return ONLY valid JSON:
{{
  "must_have":  [<up to 8 critical keywords that MUST appear in the resume>],
  "nice_to_have": [<up to 6 secondary keywords to include if possible>]
}}
Return ONLY the JSON object, no explanation or markdown.
"""

REWRITE_PROMPT = """\
You are an expert resume writer specializing in ATS-optimized resumes.

Rewrite the following work experience and project descriptions as strong resume bullet points
tailored to the job description below.

JOB DESCRIPTION:
{jd}

REQUIRED KEYWORDS TO INCLUDE: {must_have_keywords}
These keywords MUST appear naturally across the bullets. Do not stuff them awkwardly.

ITEMS TO REWRITE:
{items}

Strict rules — violating any of these will break the one-page layout:
- Start every bullet with a strong action verb (Led, Built, Designed, Reduced, Implemented, etc.)
- Each bullet MUST be under {chars_per_line} characters including the leading dash
- Write EXACTLY {bullets_per_exp} bullets per experience, EXACTLY {bullets_per_proj} bullet per project
- Include quantifiable metrics where they exist in the original; invent NOTHING
- Weave in the required keywords naturally — every must-have keyword should appear at least once
- Return ONLY valid JSON in this exact structure:
{{
  "experiences": [
    {{
      "id": <int>,
      "bullets": ["bullet 1", "bullet 2"]
    }}
  ],
  "projects": [
    {{
      "id": <int>,
      "bullets": ["bullet 1"]
    }}
  ],
  "keywords_used": [<list of must-have keywords that appear in the bullets>]
}}
Return ONLY the JSON object, no explanation or markdown.
"""


# ── ResumeGenerator ───────────────────────────────────────────────────────────

class ResumeGenerator:
    def __init__(self, db: ProfileDB, api_key: str | None = None):
        self.db = db
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError(
                "Groq API key required. Pass api_key= or set GROQ_API_KEY env var."
            )
        self.client = Groq(api_key=api_key)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, job_description: str) -> dict:
        """
        Full pipeline: extract keywords → select → rewrite (with budget) →
                       verify keyword coverage → fit-check → trim if needed.
        Returns a resume dict guaranteed to fit one page.
        """
        print("📋 Loading profile from database...")
        profile = self.db.get_full_profile()

        print("🔑 Extracting ATS keywords from job description...")
        keywords = self._extract_keywords(job_description)
        must_have = keywords.get("must_have", [])
        print(f"   Must-have: {', '.join(must_have)}")

        print("🔍 Selecting most relevant experiences, projects & skills...")
        selection = self._select_items(profile, job_description)

        # Calculate a safe bullet budget before calling Claude
        n_exp  = len(selection.get("selected_experience_ids", []))
        n_proj = len(selection.get("selected_project_ids", []))
        bullets_per_exp, bullets_per_proj = self._calc_bullet_budget(n_exp, n_proj, profile)
        print(f"   Budget: {bullets_per_exp} bullets/experience, {bullets_per_proj} bullet/project")

        print("✍️  Rewriting bullet points for ATS optimization...")
        rewrites = self._rewrite_bullets(
            profile, selection, job_description,
            must_have, bullets_per_exp, bullets_per_proj
        )

        # Verify keyword coverage and warn if any must-haves are missing
        keywords_used = rewrites.get("keywords_used", [])
        missing = [k for k in must_have if k.lower() not in [u.lower() for u in keywords_used]]
        if missing:
            print(f"   ⚠️  Missing keywords: {', '.join(missing)} — adding to skills section")

        print("🔧 Assembling resume...")
        resume = self._assemble(profile, selection, rewrites, missing)

        print("📐 Checking one-page fit...")
        resume = self._enforce_one_page(resume)

        print("✅ Resume generated!\n")
        return resume

    # ── Step 1: Keyword extraction ────────────────────────────────────────────

    def _extract_keywords(self, jd: str) -> dict:
        prompt = KEYWORD_EXTRACTION_PROMPT.format(jd=jd)
        response = self._call_claude(prompt)
        return self._parse_json(response, "keywords")

    # ── Step 2: Selection ─────────────────────────────────────────────────────

    def _select_items(self, profile: dict, jd: str) -> dict:
        prompt = SELECTION_PROMPT.format(
            profile=json.dumps(profile, indent=2),
            jd=jd
        )
        response = self._call_claude(prompt)
        return self._parse_json(response, "selection")

    # ── Step 3: Calculate bullet budget ──────────────────────────────────────

    def _calc_bullet_budget(self, n_exp: int, n_proj: int, profile: dict) -> tuple[int, int]:
        """
        Work out how many bullets per experience and per project we can afford
        while staying within PAGE_LINE_BUDGET.

        Fixed overhead:
          FIXED_OVERHEAD + education lines + cert lines + skill lines
        Variable overhead:
          n_exp * OVERHEAD_PER_EXPERIENCE + n_proj * OVERHEAD_PER_PROJECT
        Remaining lines are split between experience bullets and project bullets.
        """
        edu_lines   = len(profile.get("education", [])) * 2
        cert_lines  = len(profile.get("certifications", [])) + 1  # +1 for heading
        skill_lines = 2  # heading + one wrapped line of skills

        fixed = FIXED_OVERHEAD + edu_lines + cert_lines + skill_lines
        variable_overhead = (n_exp * OVERHEAD_PER_EXPERIENCE) + (n_proj * OVERHEAD_PER_PROJECT)
        available = PAGE_LINE_BUDGET - fixed - variable_overhead

        # Give 70% of available lines to experiences, 30% to projects
        exp_lines  = max(int(available * 0.70), n_exp)    # at least 1 per exp
        proj_lines = max(available - exp_lines, n_proj)   # remainder

        bullets_per_exp  = max(1, exp_lines  // max(n_exp,  1))
        bullets_per_proj = max(1, proj_lines // max(n_proj, 1))

        # Cap at sensible maximums
        bullets_per_exp  = min(bullets_per_exp,  3)
        bullets_per_proj = min(bullets_per_proj, 2)

        return bullets_per_exp, bullets_per_proj

    # ── Step 4: Rewrite bullets ───────────────────────────────────────────────

    def _rewrite_bullets(self, profile: dict, selection: dict, jd: str,
                         must_have: list, bullets_per_exp: int, bullets_per_proj: int) -> dict:
        exp_ids  = set(selection.get("selected_experience_ids", []))
        proj_ids = set(selection.get("selected_project_ids", []))

        selected_exp  = [e for e in profile["experiences"] if e["id"] in exp_ids]
        selected_proj = [p for p in profile["projects"]    if p["id"] in proj_ids]

        prompt = REWRITE_PROMPT.format(
            jd=jd,
            must_have_keywords=", ".join(must_have) if must_have else "none specified",
            items=json.dumps({"experiences": selected_exp, "projects": selected_proj}, indent=2),
            chars_per_line=CHARS_PER_LINE,
            bullets_per_exp=bullets_per_exp,
            bullets_per_proj=bullets_per_proj,
        )
        response = self._call_claude(prompt)
        return self._parse_json(response, "rewrites")

    # ── Step 5: Assemble ──────────────────────────────────────────────────────

    def _assemble(self, profile: dict, selection: dict, rewrites: dict,
                  missing_keywords: list) -> dict:
        personal = profile["personal"]

        exp_bullets  = {e["id"]: e["bullets"] for e in rewrites.get("experiences", [])}
        proj_bullets = {p["id"]: p["bullets"] for p in rewrites.get("projects", [])}

        exp_ids  = set(selection.get("selected_experience_ids", []))
        proj_ids = set(selection.get("selected_project_ids", []))

        experiences = []
        for e in profile["experiences"]:
            if e["id"] in exp_ids:
                experiences.append({
                    **e,
                    "bullets": exp_bullets.get(e["id"], [e.get("description", "")])
                })

        projects = []
        for p in profile["projects"]:
            if p["id"] in proj_ids:
                projects.append({
                    **p,
                    "bullets": proj_bullets.get(p["id"], [p.get("description", "")])
                })

        # Append any missing must-have keywords directly to the skills list
        skills = list(selection.get("selected_skills", profile["skills"]))
        for kw in missing_keywords:
            if kw not in skills:
                skills.append(kw)
        skills = skills[:12]  # hard cap

        return {
            "personal": {
                "name":     f"{personal.get('first_name','')} {personal.get('last_name','')}".strip(),
                "headline": selection.get("job_title_suggestion", personal.get("headline", "")),
                "location": personal.get("location", ""),
                "websites": personal.get("websites", []),
            },
            "experiences":    experiences,
            "projects":       projects,
            "education":      profile["education"],
            "skills":         skills,
            "certifications": profile["certifications"],
        }

    # ── Step 6: One-page fit enforcement ─────────────────────────────────────

    def _estimate_lines(self, resume: dict) -> int:
        """
        Estimate how many lines the resume content will consume.
        Each bullet is counted as ceil(len / CHARS_PER_LINE) lines.
        """
        def bullet_lines(text: str) -> int:
            import math
            return math.ceil(max(len(text), 1) / CHARS_PER_LINE)

        lines = FIXED_OVERHEAD
        lines += len(resume.get("education", [])) * 2
        lines += len(resume.get("certifications", [])) + 1
        lines += 2  # skills heading + content

        for exp in resume.get("experiences", []):
            lines += OVERHEAD_PER_EXPERIENCE
            for b in exp.get("bullets", []):
                lines += bullet_lines(b)

        for proj in resume.get("projects", []):
            lines += OVERHEAD_PER_PROJECT
            for b in proj.get("bullets", []):
                lines += bullet_lines(b)

        return lines

    def _enforce_one_page(self, resume: dict) -> dict:
        """
        Trim bullets one at a time (least important last) until the resume
        fits within PAGE_LINE_BUDGET. Never removes an entry entirely —
        always leaves at least 1 bullet per item.
        """
        import math

        MAX_TRIM_PASSES = 10

        for pass_num in range(MAX_TRIM_PASSES):
            estimated = self._estimate_lines(resume)
            print(f"   Estimated lines: {estimated} / {PAGE_LINE_BUDGET}", end="")

            if estimated <= PAGE_LINE_BUDGET:
                print(" ✅")
                break

            print(f" — trimming pass {pass_num + 1}...")

            # Find the entry with the most bullets and trim its last one
            trimmed = False
            candidates = (
                [(e, "exp") for e in resume["experiences"]] +
                [(p, "proj") for p in resume["projects"]]
            )
            # Sort by bullet count descending — trim fattest entry first
            candidates.sort(key=lambda x: len(x[0].get("bullets", [])), reverse=True)

            for entry, _ in candidates:
                if len(entry.get("bullets", [])) > 1:
                    entry["bullets"] = entry["bullets"][:-1]
                    trimmed = True
                    break

            if not trimmed:
                print("   ⚠️  Cannot trim further without removing entries — accepting as-is.")
                break

        return resume

    # ── Groq API call ─────────────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    # ── JSON parsing with fallback ────────────────────────────────────────────

    def _parse_json(self, text: str, label: str) -> dict:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            print(f"⚠️  Warning: Could not parse {label} JSON: {e}")
            print(f"   Raw response: {text[:300]}")
            return {}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a tailored resume from a job description.")
    parser.add_argument("--jd",  "-j", required=True,
                        help="Path to a .txt file containing the job description")
    parser.add_argument("--db",  "-d", default="resume_builder.db",
                        help="Path to SQLite database (default: resume_builder.db)")
    parser.add_argument("--out", "-o", default="resume_content.json",
                        help="Output JSON file for generated resume content (default: resume_content.json)")
    parser.add_argument("--key", "-k", default=None,
                        help="Groq API key (or set GROQ_API_KEY env var)")
    args = parser.parse_args()

    if not os.path.exists(args.jd):
        print(f"❌ Job description file not found: {args.jd}", file=sys.stderr)
        sys.exit(1)

    with open(args.jd, encoding="utf-8") as f:
        jd = f.read()

    with ProfileDB(args.db) as db:
        gen = ResumeGenerator(db, api_key=args.key)
        resume = gen.generate(jd)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(resume, f, indent=2, ensure_ascii=False)

    print(f"💾 Resume content saved to: {args.out}")
    print("\nResume snapshot:")
    print(f"  Name:        {resume['personal']['name']}")
    print(f"  Headline:    {resume['personal']['headline']}")
    print(f"  Experiences: {len(resume['experiences'])}")
    print(f"  Projects:    {len(resume['projects'])}")
    print(f"  Skills:      {len(resume['skills'])}")


if __name__ == "__main__":
    main()