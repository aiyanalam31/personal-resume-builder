"""
resume_generator.py
───────────────────
Uses the Groq API (Llama 3.3) to:
  1. Extract ATS keywords from the job description
  2. Select the most relevant experiences, projects, and skills from the DB
  3. Calculate a bullet budget so content fits one page
  4. Rewrite bullet points to be ATS-optimized and tailored to the JD
  5. Verify keyword coverage — append missing keywords to skills section
  6. Trim bullets if content still overflows after rewriting

Usage:
  from storage import ProfileDB
  from resume_generator import ResumeGenerator

  db  = ProfileDB("resume_builder.db")
  gen = ResumeGenerator(db, api_key="gsk_...")

  resume = gen.generate(job_description="...")

Or via CLI:
  python resume_generator.py --jd job_description.txt --db resume_builder.db
"""

import argparse
import json
import math
import os
import sys

from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))
from storage import ProfileDB


MODEL = "llama-3.3-70b-versatile"

# ── Page budget ───────────────────────────────────────────────────────────────
# Mirrors pdf_renderer.py layout constants.
# Adjust if you change font size or margins in the renderer.

CHARS_PER_LINE        = 95   # ~1 line at 9.5pt on 516pt content width
PAGE_LINE_BUDGET      = 47   # calibrated from actual renderer measurements
OVERHEAD_PER_EXP      = 3
OVERHEAD_PER_PROJ     = 2
FIXED_OVERHEAD        = 4

MONTH_ORDER = {
    "jan": 1,  "feb": 2,  "mar": 3,  "apr": 4,
    "may": 5,  "jun": 6,  "jul": 7,  "aug": 8,
    "sep": 9,  "oct": 10, "nov": 11, "dec": 12,
}

def _parse_date(date_str: str) -> tuple[int, int]:
    """Parse 'Mon YYYY' or 'YYYY' into (year, month) for sorting."""
    if not date_str:
        return (9999, 99)   # empty = present = sort to top
    parts = date_str.strip().split()
    try:
        if len(parts) == 2:
            month = MONTH_ORDER.get(parts[0].lower()[:3], 0)
            year  = int(parts[1])
        else:
            month = 0
            year  = int(parts[0])
        return (year, month)
    except ValueError:
        return (0, 0)

# ── Prompts ───────────────────────────────────────────────────────────────────

KEYWORD_EXTRACTION_PROMPT = """\
Extract the most important ATS keywords from this job description.
Focus on: required skills, tools, technologies, methodologies, and role-specific verbs.

JOB DESCRIPTION:
{jd}

Return ONLY valid JSON with no explanation or markdown:
{{
  "must_have":    [<up to 8 critical keywords that MUST appear in the resume>],
  "nice_to_have": [<up to 6 secondary keywords to include if possible>]
}}"""

SELECTION_PROMPT = """\
You are an expert resume strategist. Given a candidate's full profile and a job description,
select the most relevant items for a 1-page ATS-friendly resume.

CANDIDATE PROFILE:
{profile}

JOB DESCRIPTION:
{jd}

Page budget: you may select up to {max_exp} experiences and up to {max_proj} projects.
Choose fewer if the quality drop-off is significant — it's better to have 3 strong
experiences than 5 mediocre ones.

Return ONLY valid JSON with no explanation or markdown:
{{
  "selected_experience_ids":     [<list of experience id integers, max {max_exp}>],
  "selected_project_ids":        [<list of project id integers, max {max_proj}>],
  "selected_skills":             [<list of skill name strings, max 12>],
  "selected_certification_ids":  [<list of certification id integers directly relevant to the JD — empty list if none>]
}}

Rules:
- Prioritize relevance to the job description above all else
- Pick experiences and projects whose descriptions best match the JD keywords
- Pick skills explicitly mentioned or strongly implied in the JD first
- Only include certifications directly relevant to the role — omit generic or unrelated ones"""

REWRITE_PROMPT = """\
You are an expert resume writer specializing in ATS-optimized resumes.

Rewrite the following work experience and project descriptions as strong, specific resume bullet points
tailored to the job description below.

JOB DESCRIPTION:
{jd}

REQUIRED KEYWORDS TO INCLUDE: {must_have_keywords}
These keywords MUST appear naturally across the bullets. Do not stuff them awkwardly.

ITEMS TO REWRITE:
{items}

Strict rules:
- Start every bullet with a strong past-tense action verb (Led, Built, Designed, Reduced, Implemented, etc.)
- Each bullet MUST be between 70 and {chars_per_line} characters — never shorter than 70 chars
- NEVER write vague bullets like "Built AI system" or "Developed code" — every bullet must be SPECIFIC and DETAILED
- Include the actual technology, method, or outcome — e.g. "Implemented RAG pipeline using LangChain to reduce query latency by 40%"
- Write EXACTLY {bullets_per_exp} bullets per experience entry
- Write EXACTLY {bullets_per_proj} bullet per project entry
- Include quantifiable metrics where they exist in the original — invent NOTHING
- Weave in the required keywords naturally across all bullets
- Return ONLY valid JSON with no explanation or markdown:
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
}}"""


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
        Full pipeline:
          extract keywords -> select -> calc budget -> rewrite ->
          verify coverage -> assemble -> trim to fit
        Returns a resume dict ready for the PDF renderer.
        """
        print("📋 Loading profile from database...")
        profile = self.db.get_full_profile()

        print("🔑 Extracting ATS keywords from job description...")
        keywords  = self._extract_keywords(job_description)
        must_have = keywords.get("must_have", [])
        print(f"   Must-have: {', '.join(must_have)}")

        print("🔍 Selecting most relevant experiences, projects & skills...")
        selection = self._select_items(profile, job_description, must_have)

        n_exp  = len(selection.get("selected_experience_ids", []))
        n_proj = len(selection.get("selected_project_ids", []))
        bullets_per_exp, bullets_per_proj = self._calc_bullet_budget(
            n_exp, n_proj, profile)
        print(f"   Budget: {bullets_per_exp} bullets/experience, "
              f"{bullets_per_proj} bullet/project")

        print("✍️  Rewriting bullet points for ATS optimization...")
        rewrites = self._rewrite_bullets(
            profile, selection, job_description,
            must_have, bullets_per_exp, bullets_per_proj
        )

        keywords_used = rewrites.get("keywords_used", [])
        missing = [k for k in must_have
                   if k.lower() not in [u.lower() for u in keywords_used]]
        if missing:
            print(f"   ⚠️  Missing keywords: {', '.join(missing)}"
                  f" — adding to skills section")

        print("🔧 Assembling resume...")
        resume = self._assemble(profile, selection, rewrites, missing)

        print("📐 Checking one-page fit...")
        resume = self._enforce_one_page(resume)

        print("✅ Resume generated!\n")
        return resume

    # ── Step 1: Keyword extraction ────────────────────────────────────────────

    def _extract_keywords(self, jd: str) -> dict:
        prompt = KEYWORD_EXTRACTION_PROMPT.format(jd=jd)
        return self._parse_json(self._call_api(prompt), "keywords")

    # ── Step 2: Selection ─────────────────────────────────────────────────────

    def _calc_max_entries(self, profile: dict) -> tuple[int, int]:
        """
        Calculate the maximum number of experiences and projects that fit
        on one page with a MINIMUM of 2 bullets each.
        This ensures depth over breadth — fewer, richer entries beat
        many entries with a single bullet each.
        """
        edu_lines   = len(profile.get("education", [])) * 2
        cert_lines  = 0  # unknown at selection time — assume none
        skill_lines = 2
        fixed = FIXED_OVERHEAD + edu_lines + cert_lines + skill_lines

        available = PAGE_LINE_BUDGET - fixed

        # Each entry needs overhead + minimum 2 bullets
        min_lines_per_exp  = OVERHEAD_PER_EXP  + 2
        min_lines_per_proj = OVERHEAD_PER_PROJ + 2

        # Find the max combination that fits, biasing 65% to exp
        best_exp, best_proj = 3, 2
        for n_exp in range(5, 0, -1):
            for n_proj in range(4, 0, -1):
                needed = (n_exp * min_lines_per_exp) + (n_proj * min_lines_per_proj)
                if needed <= available:
                    best_exp  = n_exp
                    best_proj = n_proj
                    return best_exp, best_proj

        return best_exp, best_proj

    def _trim_profile_for_selection(self, profile: dict,
                                    keywords: list) -> dict:
        """
        Reduce profile size before sending to the LLM for selection.
        Keeps the 8 most keyword-relevant experiences, top 8 projects,
        and 30 most relevant skills — cuts token usage by ~50%.
        """
        kw_lower = [k.lower() for k in keywords]

        def relevance(text: str) -> int:
            t = text.lower()
            return sum(1 for k in kw_lower if k in t)

        # Score and trim experiences
        exps = profile.get("experiences", [])
        exps_scored = sorted(
            exps,
            key=lambda e: relevance(
                f"{e.get('title','')} {e.get('company','')} "
                f"{e.get('description','')}"),
            reverse=True
        )
        trimmed_exps = exps_scored[:8]

        # Score and trim projects
        projs = profile.get("projects", [])
        projs_scored = sorted(
            projs,
            key=lambda p: relevance(
                f"{p.get('title','')} {p.get('description','')}"),
            reverse=True
        )
        trimmed_projs = projs_scored[:8]

        # Score and trim skills
        skills = profile.get("skills", [])
        skills_scored = sorted(
            skills,
            key=lambda s: relevance(s),
            reverse=True
        )
        # Always keep top keyword-matching skills, fill rest up to 30
        trimmed_skills = skills_scored[:30]

        return {
            **profile,
            "experiences": trimmed_exps,
            "projects":    trimmed_projs,
            "skills":      trimmed_skills,
        }

    def _select_items(self, profile: dict, jd: str,
                      keywords: list | None = None) -> dict:
        max_exp, max_proj = self._calc_max_entries(profile)
        print(f"   Page budget allows up to {max_exp} experiences, {max_proj} projects")
        trimmed = self._trim_profile_for_selection(profile, keywords or [])
        prompt = SELECTION_PROMPT.format(
            profile=json.dumps(trimmed, indent=2),
            jd=jd,
            max_exp=max_exp,
            max_proj=max_proj,
        )
        return self._parse_json(self._call_api(prompt), "selection")

    # ── Step 3: Bullet budget ─────────────────────────────────────────────────

    def _calc_bullet_budget(self, n_exp: int, n_proj: int,
                            profile: dict) -> tuple[int, int]:
        """
        Calculate how many bullets per entry fit within PAGE_LINE_BUDGET.
        """
        edu_lines   = len(profile.get("education", [])) * 2
        cert_lines  = len(profile.get("certifications", [])) + 1
        skill_lines = 2

        fixed     = FIXED_OVERHEAD + edu_lines + cert_lines + skill_lines
        overhead  = (n_exp * OVERHEAD_PER_EXP) + (n_proj * OVERHEAD_PER_PROJ)
        available = PAGE_LINE_BUDGET - fixed - overhead

        exp_lines  = max(int(available * 0.70), n_exp)
        proj_lines = max(available - exp_lines, n_proj)

        bullets_per_exp  = max(1, exp_lines  // max(n_exp,  1))
        bullets_per_proj = max(1, proj_lines // max(n_proj, 1))

        return bullets_per_exp, bullets_per_proj

    # ── Step 4: Rewrite bullets ───────────────────────────────────────────────

    def _rewrite_bullets(self, profile: dict, selection: dict, jd: str,
                         must_have: list, bullets_per_exp: int,
                         bullets_per_proj: int) -> dict:
        exp_ids  = set(selection.get("selected_experience_ids", []))
        proj_ids = set(selection.get("selected_project_ids", []))

        selected_exp  = [e for e in profile["experiences"] if e["id"] in exp_ids]
        selected_proj = [p for p in profile["projects"]    if p["id"] in proj_ids]

        prompt = REWRITE_PROMPT.format(
            jd=jd,
            must_have_keywords=(", ".join(must_have) if must_have
                                else "none specified"),
            items=json.dumps(
                {"experiences": selected_exp, "projects": selected_proj},
                indent=2),
            chars_per_line=CHARS_PER_LINE,
            bullets_per_exp=bullets_per_exp,
            bullets_per_proj=bullets_per_proj,
        )
        return self._parse_json(self._call_api(prompt), "rewrites")

    # ── Step 5: Assemble ──────────────────────────────────────────────────────

    def _assemble(self, profile: dict, selection: dict, rewrites: dict,
                  missing_keywords: list) -> dict:
        personal = profile["personal"]

        exp_bullets  = {e["id"]: e["bullets"]
                        for e in rewrites.get("experiences", [])}
        proj_bullets = {p["id"]: p["bullets"]
                        for p in rewrites.get("projects", [])}

        exp_ids  = set(selection.get("selected_experience_ids", []))
        proj_ids = set(selection.get("selected_project_ids", []))

        experiences = []
        for e in profile["experiences"]:
            if e["id"] in exp_ids:
                experiences.append({
                    **e,
                    "bullets": exp_bullets.get(e["id"],
                                               [e.get("description", "")])
                })

        projects = []
        for p in profile["projects"]:
            if p["id"] in proj_ids:
                projects.append({
                    **p,
                    "bullets": proj_bullets.get(p["id"],
                                                [p.get("description", "")])
                })

        # Append missing must-have keywords to skills list
        skills = list(selection.get("selected_skills", profile["skills"]))
        for kw in missing_keywords:
            if kw not in skills:
                skills.append(kw)
        skills = skills[:12]

        # Filter certifications to only relevant ones selected by LLM
        cert_ids = set(selection.get("selected_certification_ids", []))
        if cert_ids:
            certifications = [c for c in profile["certifications"]
                              if c.get("id") in cert_ids]
        else:
            certifications = []

        # Sort experiences and projects most-recent first
        # Ongoing roles (no end date) sort to top, then by start date descending
        def sort_key(item):
            end   = item.get("end", "").strip()
            start = item.get("start", "").strip()
            if not end:
                # Still ongoing — sort by start date descending (negate)
                sy, sm = _parse_date(start)
                return (0, -sy, -sm)
            else:
                ey, em = _parse_date(end)
                return (1, -ey, -em)

        experiences_sorted = sorted(experiences, key=sort_key)
        projects_sorted    = sorted(projects,    key=sort_key)

        return {
            "personal": {
                "name":     (f"{personal.get('first_name', '')} "
                             f"{personal.get('last_name', '')}").strip(),
                "location": personal.get("location", ""),
                "websites": personal.get("websites", []),
            },
            "experiences":    experiences_sorted,
            "projects":       projects_sorted,
            "education":      self._filter_education(profile["education"]),
            "skills":         skills,
            "certifications": certifications,
        }

    def _filter_education(self, education: list) -> list:
        """
        Remove high school entries — these are common on LinkedIn
        but should not appear on a professional resume.
        """
        hs_keywords = {
            "high school", "secondary school", "middle school",
            "junior high", "preparatory", "prep school", "grammar school"
        }
        filtered = []
        for edu in education:
            school = edu.get("school", "").lower()
            degree = edu.get("degree", "").lower()
            if any(kw in school or kw in degree for kw in hs_keywords):
                continue
            filtered.append(edu)
        return filtered

    # ── Step 6: Trim to fit ───────────────────────────────────────────────────

    def _estimate_lines(self, resume: dict) -> int:
        """Estimate total content lines using character-based wrapping."""
        def bullet_lines(text: str) -> int:
            return math.ceil(max(len(text), 1) / CHARS_PER_LINE)

        lines = FIXED_OVERHEAD
        lines += len(resume.get("education", [])) * 2
        lines += len(resume.get("certifications", [])) + 1
        lines += 2  # skills heading + content

        for exp in resume.get("experiences", []):
            lines += OVERHEAD_PER_EXP
            for b in exp.get("bullets", []):
                lines += bullet_lines(b)

        for proj in resume.get("projects", []):
            lines += OVERHEAD_PER_PROJ
            for b in proj.get("bullets", []):
                lines += bullet_lines(b)

        return lines

    def _enforce_one_page(self, resume: dict) -> dict:
        """
        Trim the last bullet from the fattest entry until content fits.
        Always preserves at least 1 bullet per entry.
        """
        for pass_num in range(10):
            estimated = self._estimate_lines(resume)
            print(f"   Lines: {estimated} / {PAGE_LINE_BUDGET}", end="")

            if estimated <= PAGE_LINE_BUDGET:
                print(" ✅")
                break

            print(f" — trimming pass {pass_num + 1}...")

            candidates = (
                [(e, "exp")  for e in resume["experiences"]] +
                [(p, "proj") for p in resume["projects"]]
            )
            candidates.sort(
                key=lambda x: len(x[0].get("bullets", [])), reverse=True)

            trimmed = False
            for entry, _ in candidates:
                if len(entry.get("bullets", [])) > 1:
                    entry["bullets"] = entry["bullets"][:-1]
                    trimmed = True
                    break

            if not trimmed:
                print("   ⚠️  Cannot trim further — accepting as-is.")
                break

        return resume

    # ── Groq API call ─────────────────────────────────────────────────────────

    def _call_api(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    # ── JSON parsing ──────────────────────────────────────────────────────────

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
    parser = argparse.ArgumentParser(
        description="Generate a tailored resume from a job description.")
    parser.add_argument("--jd",  "-j", required=True,
                        help="Path to a .txt file with the job description")
    parser.add_argument("--db",  "-d", default="resume_builder.db",
                        help="Path to SQLite database (default: resume_builder.db)")
    parser.add_argument("--out", "-o", default="resume_content.json",
                        help="Output JSON file (default: resume_content.json)")
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

    print(f"💾 Saved to: {args.out}")
    print(f"  Name:        {resume['personal']['name']}")
    print(f"  Headline:    {resume['personal']['headline']}")
    print(f"  Experiences: {len(resume['experiences'])}")
    print(f"  Projects:    {len(resume['projects'])}")
    print(f"  Skills:      {len(resume['skills'])}")


if __name__ == "__main__":
    main()