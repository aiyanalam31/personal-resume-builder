"""
storage.py
──────────
SQLite storage layer for the LinkedIn resume builder.

Responsibilities:
  - Initialize the database and schema
  - Load a parsed LinkedIn profile JSON into the DB
  - Query experiences, projects, skills, education, certifications
  - Update or re-sync the profile when a new export is parsed

Usage:
  from storage import ProfileDB

  db = ProfileDB("resume_builder.db")
  db.load_profile("linkedin_profile.json")

  experiences = db.get_experiences()
  skills      = db.get_skills()
"""

import json
import sqlite3
from pathlib import Path


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS personal (
    id          INTEGER PRIMARY KEY,
    first_name  TEXT,
    last_name   TEXT,
    headline    TEXT,
    summary     TEXT,
    location    TEXT,
    industry    TEXT,
    websites    TEXT   -- JSON array stored as string
);

CREATE TABLE IF NOT EXISTS experiences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT,
    start       TEXT,
    end         TEXT,   -- empty string means "Present"
    description TEXT
);

CREATE TABLE IF NOT EXISTS education (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    school      TEXT NOT NULL,
    degree      TEXT,
    field       TEXT,
    activities  TEXT,
    start       TEXT,
    end         TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    url         TEXT,
    start       TEXT,
    end         TEXT
);

CREATE TABLE IF NOT EXISTS certifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    authority  TEXT,
    url        TEXT,
    issued     TEXT,
    expires    TEXT,
    license_no TEXT
);
"""


# ── ProfileDB ─────────────────────────────────────────────────────────────────

class ProfileDB:
    def __init__(self, db_path: str = "resume_builder.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row   # rows behave like dicts
        self._init_schema()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── Load ──────────────────────────────────────────────────────────────────

    def load_profile(self, json_path: str) -> None:
        """
        Parse a linkedin_profile.json file (output of linkedin_parser.py)
        and upsert everything into the database.
        Clears existing data first so re-syncing is always safe.
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"Profile JSON not found: {json_path}")

        with open(path, encoding="utf-8") as f:
            profile = json.load(f)

        self._clear_all()
        self._insert_personal(profile.get("personal", {}))
        self._insert_experiences(profile.get("experiences", []))
        self._insert_education(profile.get("education", []))
        self._insert_skills(profile.get("skills", []))
        self._insert_projects(profile.get("projects", []))
        self._insert_certifications(profile.get("certifications", []))
        self.conn.commit()

        self._print_summary()

    def _clear_all(self):
        tables = ["personal", "experiences", "education", "skills", "projects", "certifications"]
        for table in tables:
            self.conn.execute(f"DELETE FROM {table}")

    # ── Inserters ─────────────────────────────────────────────────────────────

    def _insert_personal(self, p: dict):
        self.conn.execute("""
            INSERT INTO personal (id, first_name, last_name, headline, summary, location, industry, websites)
            VALUES (1, :first_name, :last_name, :headline, :summary, :location, :industry, :websites)
        """, {
            "first_name": p.get("first_name", ""),
            "last_name":  p.get("last_name", ""),
            "headline":   p.get("headline", ""),
            "summary":    p.get("summary", ""),
            "location":   p.get("location", ""),
            "industry":   p.get("industry", ""),
            "websites":   json.dumps(p.get("websites", [])),
        })

    def _insert_experiences(self, experiences: list):
        self.conn.executemany("""
            INSERT INTO experiences (title, company, location, start, end, description)
            VALUES (:title, :company, :location, :start, :end, :description)
        """, experiences)

    def _insert_education(self, education: list):
        self.conn.executemany("""
            INSERT INTO education (school, degree, field, activities, start, end)
            VALUES (:school, :degree, :field, :activities, :start, :end)
        """, education)

    def _insert_skills(self, skills: list):
        self.conn.executemany(
            "INSERT OR IGNORE INTO skills (name) VALUES (?)",
            [(s,) for s in skills if s]
        )

    def _insert_projects(self, projects: list):
        self.conn.executemany("""
            INSERT INTO projects (title, description, url, start, end)
            VALUES (:title, :description, :url, :start, :end)
        """, projects)

    def _insert_certifications(self, certs: list):
        self.conn.executemany("""
            INSERT INTO certifications (name, authority, url, issued, expires, license_no)
            VALUES (:name, :authority, :url, :issued, :expires, :license_no)
        """, certs)

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_personal(self) -> dict:
        row = self.conn.execute("SELECT * FROM personal WHERE id = 1").fetchone()
        if not row:
            return {}
        result = dict(row)
        result["websites"] = json.loads(result.get("websites") or "[]")
        return result

    def get_experiences(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM experiences ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_education(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM education ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_skills(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM skills ORDER BY id").fetchall()
        return [r["name"] for r in rows]

    def get_projects(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM projects ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_certifications(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM certifications ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_full_profile(self) -> dict:
        """Return the entire stored profile as a single dict."""
        return {
            "personal":       self.get_personal(),
            "experiences":    self.get_experiences(),
            "education":      self.get_education(),
            "skills":         self.get_skills(),
            "projects":       self.get_projects(),
            "certifications": self.get_certifications(),
        }

    # ── Manual updates ────────────────────────────────────────────────────────

    def add_skill(self, skill: str) -> None:
        """Manually add a skill not in the LinkedIn export."""
        self.conn.execute("INSERT OR IGNORE INTO skills (name) VALUES (?)", (skill,))
        self.conn.commit()

    def add_experience(self, title: str, company: str, start: str, end: str = "",
                       location: str = "", description: str = "") -> None:
        """Manually add a work experience entry."""
        self.conn.execute("""
            INSERT INTO experiences (title, company, location, start, end, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (title, company, location, start, end, description))
        self.conn.commit()

    def add_project(self, title: str, description: str, url: str = "",
                    start: str = "", end: str = "") -> None:
        """Manually add a project."""
        self.conn.execute("""
            INSERT INTO projects (title, description, url, start, end)
            VALUES (?, ?, ?, ?, ?)
        """, (title, description, url, start, end))
        self.conn.commit()

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _print_summary(self):
        p = self.get_personal()
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "Unknown"
        print(f"\n✅ Stored profile for: {name}")
        print(f"   Experiences:     {len(self.get_experiences())}")
        print(f"   Education:       {len(self.get_education())}")
        print(f"   Skills:          {len(self.get_skills())}")
        print(f"   Projects:        {len(self.get_projects())}")
        print(f"   Certifications:  {len(self.get_certifications())}")
        print(f"   Database:        {self.db_path}\n")

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── CLI (quick test / re-sync) ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load a parsed LinkedIn JSON into the database.")
    parser.add_argument("--profile", "-p", default="linkedin_profile.json",
                        help="Path to linkedin_profile.json (default: linkedin_profile.json)")
    parser.add_argument("--db", "-d", default="resume_builder.db",
                        help="Path to SQLite database (default: resume_builder.db)")
    args = parser.parse_args()

    with ProfileDB(args.db) as db:
        db.load_profile(args.profile)
        print("Full profile snapshot:")
        print(json.dumps(db.get_full_profile(), indent=2))