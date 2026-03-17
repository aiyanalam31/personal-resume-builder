"""
pdf_renderer.py
───────────────
Renders a resume dict (from resume_generator.py) into a clean 1-page PDF
using ReportLab, then measures the actual rendered height.

If the content overflows one page it returns an OverflowReport instead of
saving the file — the generator uses this to trim and retry.

Public API:
  from pdf_renderer import PDFRenderer, OverflowReport

  renderer = PDFRenderer()
  result   = renderer.render(resume_dict, "resume.pdf")

  if isinstance(result, OverflowReport):
      print(f"Overflowed by {result.overflow_px}px in sections: {result.overflow_sections}")
  else:
      print(f"Saved to {result}")

Layout (letter page, all measurements in points — 1pt = 1/72 inch):
  Page:        612 × 792 pt
  Margins:     top 44, bottom 36, left/right 48
  Content:     516 pt wide, 712 pt tall
  Sections:    Header · Experience · Projects · Education · Skills · Certs
"""

import io
import os
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Frame, KeepTogether
from reportlab.platypus.flowables import Flowable
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib import colors


# ── Page geometry ─────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = letter          # 612 × 792 pt
MARGIN_TOP     = 44
MARGIN_BOTTOM  = 36
MARGIN_LR      = 48
CONTENT_W      = PAGE_W - 2 * MARGIN_LR                      # 516 pt
CONTENT_H      = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM         # 712 pt
CONTENT_X      = MARGIN_LR
CONTENT_Y      = MARGIN_BOTTOM                                # ReportLab y from bottom

# ── Colors ────────────────────────────────────────────────────────────────────

BLACK    = colors.HexColor("#1A1A1A")
DARK     = colors.HexColor("#2D2D2D")
MID      = colors.HexColor("#555555")
LIGHT    = colors.HexColor("#888888")
RULE     = colors.HexColor("#CCCCCC")
ACCENT   = colors.HexColor("#2563EB")   # blue — section headings & name

# ── Styles ────────────────────────────────────────────────────────────────────

def _styles():
    base = dict(fontName="Helvetica", leading=13, textColor=BLACK)
    return {
        "name": ParagraphStyle("name",
            fontName="Helvetica-Bold", fontSize=20, leading=24,
            textColor=BLACK, alignment=TA_LEFT),
        "headline": ParagraphStyle("headline",
            fontName="Helvetica", fontSize=10, leading=13,
            textColor=MID, alignment=TA_LEFT),
        "contact": ParagraphStyle("contact",
            fontName="Helvetica", fontSize=9, leading=11,
            textColor=MID, alignment=TA_LEFT),
        "section": ParagraphStyle("section",
            fontName="Helvetica-Bold", fontSize=9.5, leading=12,
            textColor=ACCENT, spaceAfter=2),
        "role_title": ParagraphStyle("role_title",
            fontName="Helvetica-Bold", fontSize=10, leading=13,
            textColor=BLACK),
        "role_meta": ParagraphStyle("role_meta",
            fontName="Helvetica", fontSize=9, leading=11,
            textColor=MID),
        "bullet": ParagraphStyle("bullet",
            fontName="Helvetica", fontSize=9.5, leading=12.5,
            textColor=DARK, leftIndent=10, firstLineIndent=0,
            bulletIndent=0, spaceAfter=1),
        "skill_block": ParagraphStyle("skill_block",
            fontName="Helvetica", fontSize=9.5, leading=13,
            textColor=DARK),
        "skill_label": ParagraphStyle("skill_label",
            fontName="Helvetica-Bold", fontSize=9.5, leading=13,
            textColor=BLACK),
    }

STYLES = _styles()

# ── Overflow report ───────────────────────────────────────────────────────────

@dataclass
class OverflowReport:
    overflow_px: float                      # points overflowed (positive = too tall)
    overflow_sections: list[str]            # which sections contributed most
    total_content_height: float             # actual measured height in points
    budget: float = CONTENT_H              # allowed height in points
    suggestions: list[str] = field(default_factory=list)

    def __str__(self):
        return (
            f"OverflowReport: {self.overflow_px:.1f}pt over budget "
            f"(sections: {self.overflow_sections}). "
            f"Suggestions: {self.suggestions}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _measure_paragraph(para: Paragraph, width: float) -> float:
    """Return the height a Paragraph will consume at the given width."""
    w, h = para.wrap(width, 9999)
    return h

def _measure_flowables(flowables: list, width: float) -> float:
    total = 0.0
    for f in flowables:
        w, h = f.wrap(width, 9999)
        total += h
    return total

def _hr(canvas_obj, x, y, width, thickness=0.4):
    canvas_obj.setStrokeColor(RULE)
    canvas_obj.setLineWidth(thickness)
    canvas_obj.line(x, y, x + width, y)

def _bullet_text(text: str) -> str:
    return f"• {text}"

def _format_date_range(start: str, end: str) -> str:
    if not end:
        return f"{start} – Present"
    return f"{start} – {end}"


# ── Section builders (return lists of Flowables + height) ────────────────────

class HRule(Flowable):
    """A thin horizontal rule that participates in flowable layout."""
    def __init__(self, width, thickness=0.4, color=RULE, space_before=4, space_after=4):
        super().__init__()
        self.width_val = width
        self.thickness = thickness
        self.rule_color = color
        self.space_before = space_before
        self.space_after = space_after

    def wrap(self, availWidth, availHeight):
        return self.width_val, self.thickness + self.space_before + self.space_after

    def draw(self):
        self.canv.setStrokeColor(self.rule_color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, self.space_after, self.width_val, self.space_after)


class VSpace(Flowable):
    def __init__(self, height):
        super().__init__()
        self._height = height

    def wrap(self, aw, ah):
        return 0, self._height

    def draw(self):
        pass


def _section_heading(title: str) -> list:
    return [
        VSpace(6),
        Paragraph(title.upper(), STYLES["section"]),
        HRule(CONTENT_W, thickness=0.5, space_before=1, space_after=3),
    ]


def build_header(personal: dict) -> tuple[list, float]:
    name     = personal.get("name", "")
    headline = personal.get("headline", "")
    location = personal.get("location", "")
    websites = personal.get("websites", [])

    contact_parts = [p for p in [location] + websites if p]
    contact_str   = "  ·  ".join(contact_parts)

    flowables = [
        Paragraph(name, STYLES["name"]),
        Paragraph(headline, STYLES["headline"]),
    ]
    if contact_str:
        flowables.append(VSpace(2))
        flowables.append(Paragraph(contact_str, STYLES["contact"]))
    flowables.append(VSpace(4))
    flowables.append(HRule(CONTENT_W, thickness=1.0, color=ACCENT, space_before=0, space_after=6))

    return flowables, _measure_flowables(flowables, CONTENT_W)


def build_experience_section(experiences: list) -> tuple[list, float]:
    if not experiences:
        return [], 0.0

    flowables = _section_heading("Experience")

    for i, exp in enumerate(experiences):
        title    = exp.get("title", "")
        company  = exp.get("company", "")
        location = exp.get("location", "")
        dates    = _format_date_range(exp.get("start", ""), exp.get("end", ""))
        bullets  = exp.get("bullets", [])

        meta_right = f"{dates}  ·  {location}" if location else dates

        entry = [
            Paragraph(f"<b>{title}</b>  <font color='#{MID.hexval()[2:]}' size='9'>{company}</font>",
                      STYLES["role_title"]),
            Paragraph(meta_right, STYLES["role_meta"]),
        ]
        for b in bullets:
            entry.append(Paragraph(_bullet_text(b), STYLES["bullet"]))

        if i < len(experiences) - 1:
            entry.append(VSpace(5))

        flowables.extend(entry)

    return flowables, _measure_flowables(flowables, CONTENT_W)


def build_projects_section(projects: list) -> tuple[list, float]:
    if not projects:
        return [], 0.0

    flowables = _section_heading("Projects")

    for i, proj in enumerate(projects):
        title   = proj.get("title", "")
        dates   = _format_date_range(proj.get("start", ""), proj.get("end", ""))
        url     = proj.get("url", "")
        bullets = proj.get("bullets", [])

        title_str = f"<b>{title}</b>"
        if url:
            title_str += f'  <font color="#{ACCENT.hexval()[2:]}" size="9"><a href="{url}">{url}</a></font>'

        entry = [
            Paragraph(title_str, STYLES["role_title"]),
            Paragraph(dates, STYLES["role_meta"]),
        ]
        for b in bullets:
            entry.append(Paragraph(_bullet_text(b), STYLES["bullet"]))

        if i < len(projects) - 1:
            entry.append(VSpace(5))

        flowables.extend(entry)

    return flowables, _measure_flowables(flowables, CONTENT_W)


def build_education_section(education: list) -> tuple[list, float]:
    if not education:
        return [], 0.0

    flowables = _section_heading("Education")

    for edu in education:
        school  = edu.get("school", "")
        degree  = edu.get("degree", "")
        field   = edu.get("field", "")
        dates   = _format_date_range(edu.get("start", ""), edu.get("end", ""))
        deg_str = f"{degree}, {field}" if field else degree

        flowables.append(Paragraph(f"<b>{school}</b>", STYLES["role_title"]))
        flowables.append(Paragraph(f"{deg_str}  ·  {dates}", STYLES["role_meta"]))
        flowables.append(VSpace(3))

    return flowables, _measure_flowables(flowables, CONTENT_W)


def build_skills_section(skills: list) -> tuple[list, float]:
    if not skills:
        return [], 0.0

    flowables = _section_heading("Skills")
    skill_text = "  ·  ".join(skills)
    flowables.append(Paragraph(skill_text, STYLES["skill_block"]))
    flowables.append(VSpace(2))

    return flowables, _measure_flowables(flowables, CONTENT_W)


def build_certs_section(certs: list) -> tuple[list, float]:
    if not certs:
        return [], 0.0

    flowables = _section_heading("Certifications")

    for cert in certs:
        name      = cert.get("name", "")
        authority = cert.get("authority", "")
        issued    = cert.get("issued", "")
        meta      = "  ·  ".join(p for p in [authority, issued] if p)
        flowables.append(Paragraph(f"<b>{name}</b>", STYLES["role_title"]))
        if meta:
            flowables.append(Paragraph(meta, STYLES["role_meta"]))
        flowables.append(VSpace(3))

    return flowables, _measure_flowables(flowables, CONTENT_W)


# ── PDFRenderer ───────────────────────────────────────────────────────────────

class PDFRenderer:

    def render(self, resume: dict, output_path: str) -> "str | OverflowReport":
        """
        Attempt to render the resume to output_path.

        Returns:
          - output_path (str)     if the content fits on one page
          - OverflowReport        if the content overflows
        """
        flowables, section_heights = self._build_flowables(resume)
        total_height = sum(section_heights.values())

        if total_height > CONTENT_H:
            return self._make_overflow_report(total_height, section_heights)

        self._write_pdf(flowables, output_path)
        return output_path

    # ── Flowable construction ─────────────────────────────────────────────────

    def _build_flowables(self, resume: dict) -> tuple[list, dict]:
        """Build all flowables and return them alongside a per-section height map."""
        section_heights = {}
        all_flowables   = []

        header_f, h = build_header(resume.get("personal", {}))
        section_heights["header"] = h
        all_flowables.extend(header_f)

        exp_f, h = build_experience_section(resume.get("experiences", []))
        section_heights["experience"] = h
        all_flowables.extend(exp_f)

        proj_f, h = build_projects_section(resume.get("projects", []))
        section_heights["projects"] = h
        all_flowables.extend(proj_f)

        edu_f, h = build_education_section(resume.get("education", []))
        section_heights["education"] = h
        all_flowables.extend(edu_f)

        skills_f, h = build_skills_section(resume.get("skills", []))
        section_heights["skills"] = h
        all_flowables.extend(skills_f)

        certs_f, h = build_certs_section(resume.get("certifications", []))
        section_heights["certifications"] = h
        all_flowables.extend(certs_f)

        return all_flowables, section_heights

    # ── PDF writing ───────────────────────────────────────────────────────────

    def _write_pdf(self, flowables: list, output_path: str):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)

        frame = Frame(
            CONTENT_X, CONTENT_Y,
            CONTENT_W, CONTENT_H,
            leftPadding=0, rightPadding=0,
            topPadding=0,  bottomPadding=0,
            showBoundary=0
        )
        frame.addFromList(flowables, c)
        c.save()

        with open(output_path, "wb") as f:
            f.write(buf.getvalue())

    # ── Overflow analysis ─────────────────────────────────────────────────────

    def _make_overflow_report(self, total_height: float,
                               section_heights: dict) -> OverflowReport:
        overflow_px = total_height - CONTENT_H

        # Rank sections by height to suggest where to trim
        ranked = sorted(
            [(k, v) for k, v in section_heights.items() if k not in ("header",)],
            key=lambda x: x[1], reverse=True
        )
        overflow_sections = [k for k, _ in ranked[:3]]

        suggestions = []
        for section, height in ranked:
            if section == "experience":
                suggestions.append("Remove 1 bullet from the longest experience entry")
            elif section == "projects":
                suggestions.append("Remove 1 bullet from the longest project entry")
            if len(suggestions) >= 2:
                break

        return OverflowReport(
            overflow_px=overflow_px,
            overflow_sections=overflow_sections,
            total_content_height=total_height,
            suggestions=suggestions,
        )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json, sys

    parser = argparse.ArgumentParser(description="Render a resume JSON to PDF.")
    parser.add_argument("--resume", "-r", default="resume_content.json")
    parser.add_argument("--out",    "-o", default="resume.pdf")
    args = parser.parse_args()

    with open(args.resume) as f:
        resume = json.load(f)

    renderer = PDFRenderer()
    result   = renderer.render(resume, args.out)

    if isinstance(result, OverflowReport):
        print(f"❌ Overflow: {result}")
        sys.exit(1)
    else:
        print(f"✅ Saved: {result}")