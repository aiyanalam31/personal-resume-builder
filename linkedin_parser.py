"""
linkedin_parser.py
──────────────────
Parses a LinkedIn data export (ZIP or extracted folder) into a clean,
structured JSON profile ready for resume generation.

LinkedIn archive files used:
  - Profile.csv       → name, headline, summary, location
  - Positions.csv     → work experience
  - Education.csv     → degrees / schools
  - Skills.csv        → skills list
  - Projects.csv      → projects
  - Certifications.csv (if present)

Usage:
  python linkedin_parser.py --input path/to/linkedin_export.zip
  python linkedin_parser.py --input path/to/extracted_folder/
  python linkedin_parser.py --input path/to/extracted_folder/ --output my_profile.json
"""

import argparse
import csv
import json
import os
import sys
import zipfile
from io import StringIO


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_csv_from_zip(zf: zipfile.ZipFile, name: str) -> list[dict]:
    """Read a CSV file from inside a ZipFile, return list of row dicts."""
    # LinkedIn sometimes capitalises differently; search case-insensitively
    matches = [n for n in zf.namelist() if os.path.basename(n).lower() == name.lower()]
    if not matches:
        return []
    with zf.open(matches[0]) as f:
        content = f.read().decode("utf-8-sig")  # strip BOM if present
    return list(csv.DictReader(StringIO(content)))


def _read_csv_from_folder(folder: str, name: str) -> list[dict]:
    """Read a CSV file from a folder, return list of row dicts."""
    path = os.path.join(folder, name)
    # Case-insensitive fallback
    if not os.path.exists(path):
        for fname in os.listdir(folder):
            if fname.lower() == name.lower():
                path = os.path.join(folder, fname)
                break
        else:
            return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _strip(val: str | None) -> str:
    """Strip whitespace and return empty string for None/whitespace-only."""
    return (val or "").strip()


def _first(row: dict, *keys: str) -> str:
    """Return the first non-empty value among the given keys in a row dict."""
    for k in keys:
        v = _strip(row.get(k, ""))
        if v:
            return v
    return ""


# ── Per-file parsers ──────────────────────────────────────────────────────────

def parse_profile(rows: list[dict]) -> dict:
    """
    Profile.csv columns (typical):
      First Name, Last Name, Maiden Name, Address, Birth Date,
      Headline, Summary, Industry, Zip Code, Geo Location,
      Twitter Handles, Websites, Instant Messengers
    """
    if not rows:
        return {}
    r = rows[0]
    return {
        "first_name":  _first(r, "First Name", "FirstName"),
        "last_name":   _first(r, "Last Name",  "LastName"),
        "headline":    _first(r, "Headline"),
        "summary":     _first(r, "Summary"),
        "location":    _first(r, "Geo Location", "Address"),
        "industry":    _first(r, "Industry"),
        "websites":    [w.strip() for w in _first(r, "Websites").split(",") if w.strip()],
    }


def parse_positions(rows: list[dict]) -> list[dict]:
    """
    Positions.csv columns (typical):
      Company Name, Title, Description, Location,
      Started On, Finished On
    """
    positions = []
    for r in rows:
        company  = _first(r, "Company Name", "Company")
        title    = _first(r, "Title")
        if not company and not title:
            continue
        positions.append({
            "title":       title,
            "company":     company,
            "location":    _first(r, "Location"),
            "start":       _first(r, "Started On"),
            "end":         _first(r, "Finished On"),       # empty = present
            "description": _first(r, "Description"),
        })
    return positions


def parse_education(rows: list[dict]) -> list[dict]:
    """
    Education.csv columns (typical):
      School Name, Start Date, End Date, Notes, Degree Name, Activities
    """
    education = []
    for r in rows:
        school = _first(r, "School Name", "School")
        if not school:
            continue
        education.append({
            "school":     school,
            "degree":     _first(r, "Degree Name", "Degree"),
            "field":      _first(r, "Notes"),          # LinkedIn puts field-of-study here
            "activities": _first(r, "Activities"),
            "start":      _first(r, "Start Date"),
            "end":        _first(r, "End Date"),
        })
    return education


def parse_skills(rows: list[dict]) -> list[str]:
    """
    Skills.csv columns (typical):
      Name
    """
    return [_strip(r.get("Name", "")) for r in rows if _strip(r.get("Name", ""))]


def parse_projects(rows: list[dict]) -> list[dict]:
    """
    Projects.csv columns (typical):
      Title, Description, Url, Started On, Finished On
    """
    projects = []
    for r in rows:
        title = _first(r, "Title")
        if not title:
            continue
        projects.append({
            "title":       title,
            "description": _first(r, "Description"),
            "url":         _first(r, "Url", "URL"),
            "start":       _first(r, "Started On"),
            "end":         _first(r, "Finished On"),
        })
    return projects


def parse_certifications(rows: list[dict]) -> list[dict]:
    """
    Certifications.csv columns (typical):
      Name, Url, Authority, Started On, Finished On, License Number
    """
    certs = []
    for r in rows:
        name = _first(r, "Name")
        if not name:
            continue
        certs.append({
            "name":       name,
            "authority":  _first(r, "Authority"),
            "url":        _first(r, "Url", "URL"),
            "issued":     _first(r, "Started On"),
            "expires":    _first(r, "Finished On"),
            "license_no": _first(r, "License Number"),
        })
    return certs


# ── Main parser ───────────────────────────────────────────────────────────────

FILE_MAP = {
    "profile":        "Profile.csv",
    "positions":      "Positions.csv",
    "education":      "Education.csv",
    "skills":         "Skills.csv",
    "projects":       "Projects.csv",
    "certifications": "Certifications.csv",
}


def parse_linkedin_export(input_path: str) -> dict:
    """
    Accept either a .zip file or an extracted folder path.
    Returns a clean profile dict.
    """
    is_zip = zipfile.is_zipfile(input_path) if os.path.isfile(input_path) else False

    def read(name: str) -> list[dict]:
        if is_zip:
            with zipfile.ZipFile(input_path) as zf:
                return _read_csv_from_zip(zf, name)
        else:
            return _read_csv_from_folder(input_path, name)

    raw = {key: read(filename) for key, filename in FILE_MAP.items()}

    profile_data    = parse_profile(raw["profile"])
    positions_data  = parse_positions(raw["positions"])
    education_data  = parse_education(raw["education"])
    skills_data     = parse_skills(raw["skills"])
    projects_data   = parse_projects(raw["projects"])
    certs_data      = parse_certifications(raw["certifications"])

    profile = {
        "meta": {
            "source": os.path.basename(input_path),
            "files_found": {k: bool(v) for k, v in raw.items()},
        },
        "personal":       profile_data,
        "experiences":    positions_data,
        "education":      education_data,
        "skills":         skills_data,
        "projects":       projects_data,
        "certifications": certs_data,
    }

    _print_summary(profile)
    return profile


def _print_summary(profile: dict) -> None:
    p = profile["personal"]
    name = f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "Unknown"
    print(f"\n✅ Parsed LinkedIn profile for: {name}")
    print(f"   Headline:        {p.get('headline', '—')}")
    print(f"   Experiences:     {len(profile['experiences'])}")
    print(f"   Education:       {len(profile['education'])}")
    print(f"   Skills:          {len(profile['skills'])}")
    print(f"   Projects:        {len(profile['projects'])}")
    print(f"   Certifications:  {len(profile['certifications'])}")
    missing = [k for k, found in profile["meta"]["files_found"].items() if not found]
    if missing:
        print(f"   ⚠️  Missing files: {', '.join(missing)}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parse a LinkedIn data export into structured JSON."
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to LinkedIn export .zip file OR extracted folder"
    )
    parser.add_argument(
        "--output", "-o", default="linkedin_profile.json",
        help="Output JSON file path (default: linkedin_profile.json)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ Error: input path not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    profile = parse_linkedin_export(args.input)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    print(f"💾 Saved to: {args.output}\n")


if __name__ == "__main__":
    main()