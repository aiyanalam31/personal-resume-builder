"""
app.py
──────
Streamlit web UI for the LinkedIn Resume Builder.

Run with:
  streamlit run app.py

All four modules must be in the same directory:
  linkedin_parser.py  storage.py  resume_generator.py  pdf_renderer.py
"""

import os
import sys
import json
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Resume Builder",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Styles ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

/* Page background */
.stApp { background-color: #F7F6F2; }

/* Hide default Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }

/* Top header bar */
.top-bar {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 36px 0 8px;
    border-bottom: 1.5px solid #1A1A1A;
    margin-bottom: 32px;
}
.top-bar h1 {
    font-family: 'DM Serif Display', serif;
    font-size: 2rem;
    font-weight: 400;
    color: #1A1A1A;
    margin: 0;
    letter-spacing: -0.02em;
}
.top-bar span {
    font-size: 0.875rem;
    color: #888;
    font-weight: 400;
}

/* Step labels */
.step-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #888;
    margin-bottom: 6px;
}
.step-title {
    font-family: 'DM Serif Display', serif;
    font-size: 1.25rem;
    color: #1A1A1A;
    margin-bottom: 16px;
}

/* Cards */
.card {
    background: #FFFFFF;
    border: 1px solid #E5E4DF;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 16px;
}

/* Profile summary pills */
.pill-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.pill {
    background: #F0EFE9;
    border-radius: 100px;
    padding: 4px 12px;
    font-size: 0.8rem;
    color: #444;
}
.pill-count {
    background: #1A1A1A;
    color: #F7F6F2;
    border-radius: 100px;
    padding: 4px 12px;
    font-size: 0.8rem;
    font-weight: 600;
}

/* Buttons */
.stButton > button {
    background: #1A1A1A !important;
    color: #F7F6F2 !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    padding: 10px 24px !important;
    cursor: pointer !important;
    transition: background 0.15s !important;
}
.stButton > button:hover {
    background: #333 !important;
}

/* Text areas */
.stTextArea > div > div > textarea {
    border-radius: 8px !important;
    border: 1px solid #E5E4DF !important;
    background: #FAFAF8 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.875rem !important;
}

/* File uploader */
.stFileUploader > div {
    border-radius: 10px !important;
    border: 1.5px dashed #C5C4BE !important;
    background: #FAFAF8 !important;
}

/* Status / success boxes */
.status-box {
    background: #F0FAF4;
    border: 1px solid #A8D5B5;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 0.875rem;
    color: #1A4731;
}
.warn-box {
    background: #FFF8ED;
    border: 1px solid #F0CF8A;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 0.875rem;
    color: #5C3E00;
}
.err-box {
    background: #FEF2F2;
    border: 1px solid #FCA5A5;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 0.875rem;
    color: #7F1D1D;
}

/* Progress steps */
.progress-row {
    display: flex;
    align-items: center;
    gap: 0;
    margin-bottom: 32px;
}
.prog-step {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.8rem;
    color: #AAA;
    font-weight: 500;
}
.prog-step.active { color: #1A1A1A; }
.prog-step.done   { color: #2E8B57; }
.prog-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #DDD;
    flex-shrink: 0;
}
.prog-step.active .prog-dot { background: #1A1A1A; }
.prog-step.done   .prog-dot { background: #2E8B57; }
.prog-line {
    flex: 1; height: 1px;
    background: #E5E4DF;
    margin: 0 12px;
    min-width: 24px;
}

/* Divider */
hr { border: none; border-top: 1px solid #E5E4DF; margin: 24px 0; }

/* Download button override */
.stDownloadButton > button {
    background: #2E8B57 !important;
    color: #fff !important;
    font-size: 1rem !important;
    padding: 14px 32px !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    width: 100%;
}
.stDownloadButton > button:hover { background: #236b42 !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state init ────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "step":        1,        # 1=upload  2=profile  3=generate  4=done
        "db_path":     None,
        "profile":     None,
        "resume":      None,
        "pdf_path":    None,
        "api_key":     "",
        "jd":          "",
        "log":         [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
S = st.session_state

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="top-bar">
  <h1>Resume Builder</h1>
  <span>LinkedIn → tailored 1-page PDF</span>
</div>
""", unsafe_allow_html=True)

# ── Progress bar ──────────────────────────────────────────────────────────────

steps = ["Upload profile", "Review profile", "Generate resume", "Download"]

def _prog_class(i):
    if i + 1 < S["step"]:  return "done"
    if i + 1 == S["step"]: return "active"
    return ""

html_steps = ""
for i, label in enumerate(steps):
    cls = _prog_class(i)
    html_steps += f'<div class="prog-step {cls}"><div class="prog-dot"></div>{label}</div>'
    if i < len(steps) - 1:
        html_steps += '<div class="prog-line"></div>'

st.markdown(f'<div class="progress-row">{html_steps}</div>', unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg: str):
    S["log"].append(msg)

def _show_log():
    if S["log"]:
        with st.expander("📋 Activity log", expanded=False):
            for line in S["log"]:
                st.text(line)

# ── STEP 1: Upload LinkedIn export ────────────────────────────────────────────

if S["step"] == 1:
    col_main, col_side = st.columns([3, 2], gap="large")

    with col_main:
        st.markdown('<div class="step-label">Step 1 of 4</div>', unsafe_allow_html=True)
        st.markdown('<div class="step-title">Upload your LinkedIn export</div>', unsafe_allow_html=True)

        uploaded = st.file_uploader(
            "Drop your LinkedIn .zip file here",
            type=["zip"],
            help="Settings → Data Privacy → Download your data → select Profile → Request archive"
        )

        if uploaded:
            st.markdown('<div class="step-label" style="margin-top:20px">Anthropic API key</div>',
                        unsafe_allow_html=True)
            api_key = st.text_input(
                "API key",
                value=S["api_key"],
                type="password",
                placeholder="sk-ant-...",
                label_visibility="collapsed",
            )
            S["api_key"] = api_key

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Parse profile →", use_container_width=False):
                if not api_key.strip():
                    st.markdown('<div class="err-box">⚠️  Please enter your Anthropic API key.</div>',
                                unsafe_allow_html=True)
                else:
                    with st.spinner("Parsing LinkedIn export…"):
                        try:
                            from linkedin_parser import parse_linkedin_export
                            from storage import ProfileDB

                            # Save upload to a temp file
                            tmp_zip = tempfile.NamedTemporaryFile(
                                suffix=".zip", delete=False)
                            tmp_zip.write(uploaded.read())
                            tmp_zip.flush()

                            # Parse
                            profile_json = parse_linkedin_export(tmp_zip.name)
                            _log(f"Parsed: {uploaded.name}")

                            # Store in DB
                            db_path = tempfile.mktemp(suffix=".db")
                            db = ProfileDB(db_path)

                            # Load from parsed dict directly (skip JSON file)
                            db._clear_all()
                            db._insert_personal(profile_json.get("personal", {}))
                            db._insert_experiences(profile_json.get("experiences", []))
                            db._insert_education(profile_json.get("education", []))
                            db._insert_skills(profile_json.get("skills", []))
                            db._insert_projects(profile_json.get("projects", []))
                            db._insert_certifications(profile_json.get("certifications", []))
                            db.conn.commit()

                            S["db_path"] = db_path
                            S["profile"] = db.get_full_profile()
                            db.close()

                            _log(f"Stored {len(S['profile']['experiences'])} experiences, "
                                 f"{len(S['profile']['skills'])} skills")
                            S["step"] = 2
                            st.rerun()

                        except Exception as e:
                            st.markdown(
                                f'<div class="err-box">❌ Parse failed: {e}</div>',
                                unsafe_allow_html=True)

    with col_side:
        st.markdown("""
        <div class="card" style="margin-top: 56px">
          <div class="step-label">How to get your LinkedIn export</div>
          <ol style="margin: 12px 0 0; padding-left: 18px; font-size: 0.875rem; line-height: 1.8; color: #444;">
            <li>Go to LinkedIn Settings</li>
            <li>Data Privacy → <b>Download your data</b></li>
            <li>Select <b>"Want something in particular?"</b></li>
            <li>Check <b>Profile</b></li>
            <li>Click <b>Request archive</b></li>
            <li>Check your email — link arrives in minutes</li>
          </ol>
        </div>
        """, unsafe_allow_html=True)

# ── STEP 2: Review profile ────────────────────────────────────────────────────

elif S["step"] == 2:
    profile = S["profile"]
    personal = profile.get("personal", {})
    name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()

    st.markdown('<div class="step-label">Step 2 of 4</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="step-title">Profile loaded for {name or "you"}</div>',
                unsafe_allow_html=True)

    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown("**Experiences**")
        for exp in profile.get("experiences", []):
            end = exp.get("end") or "Present"
            st.markdown(f"- **{exp['title']}** at {exp['company']} · {exp.get('start','')}–{end}")

        st.markdown("<br>**Projects**", unsafe_allow_html=True)
        for proj in profile.get("projects", []):
            st.markdown(f"- **{proj['title']}**")

        st.markdown("<br>**Education**", unsafe_allow_html=True)
        for edu in profile.get("education", []):
            st.markdown(f"- {edu.get('degree','')} · {edu.get('school','')}")

    with col2:
        st.markdown("**Skills**")
        pills = "".join(f'<span class="pill">{s}</span>'
                        for s in profile.get("skills", []))
        st.markdown(f'<div class="pill-row">{pills}</div>', unsafe_allow_html=True)

        if profile.get("certifications"):
            st.markdown("<br>**Certifications**", unsafe_allow_html=True)
            for cert in profile["certifications"]:
                st.markdown(f"- {cert['name']}")

    st.markdown("<br>", unsafe_allow_html=True)
    col_back, col_next, _ = st.columns([1, 1, 4])
    with col_back:
        if st.button("← Back"):
            S["step"] = 1
            st.rerun()
    with col_next:
        if st.button("Looks good →"):
            S["step"] = 3
            st.rerun()

    _show_log()

# ── STEP 3: Paste JD & generate ───────────────────────────────────────────────

elif S["step"] == 3:
    st.markdown('<div class="step-label">Step 3 of 4</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-title">Paste the job description</div>', unsafe_allow_html=True)

    jd = st.text_area(
        "Job description",
        value=S["jd"],
        height=280,
        placeholder="Paste the full job posting here — requirements, responsibilities, tech stack…",
        label_visibility="collapsed",
    )
    S["jd"] = jd

    col_back, col_gen, _ = st.columns([1, 1.5, 3])
    with col_back:
        if st.button("← Back"):
            S["step"] = 2
            st.rerun()
    with col_gen:
        generate_clicked = st.button("Generate resume →", use_container_width=False)

    if generate_clicked:
        if not jd.strip():
            st.markdown('<div class="err-box">⚠️  Please paste a job description first.</div>',
                        unsafe_allow_html=True)
        else:
            progress = st.progress(0, text="Starting…")
            status   = st.empty()

            try:
                from storage import ProfileDB
                from resume_generator import ResumeGenerator

                pdf_path = tempfile.mktemp(suffix=".pdf")

                db  = ProfileDB(S["db_path"])
                gen = ResumeGenerator(db, api_key=S["api_key"])

                # Monkey-patch print so pipeline logs appear in the UI
                original_print = __builtins__["print"] if isinstance(__builtins__, dict) else print
                import builtins
                log_lines = []

                def ui_print(*args, **kwargs):
                    line = " ".join(str(a) for a in args)
                    log_lines.append(line)
                    _log(line)
                    status.markdown(f"_{line}_")

                builtins.print = ui_print

                progress.progress(10, text="Extracting ATS keywords…")
                keywords = gen._extract_keywords(jd)
                must_have = keywords.get("must_have", [])

                progress.progress(25, text="Selecting best experiences & projects…")
                profile  = db.get_full_profile()
                selection = gen._select_items(profile, jd)

                n_exp  = len(selection.get("selected_experience_ids", []))
                n_proj = len(selection.get("selected_project_ids", []))
                bpe, bpp = gen._calc_bullet_budget(n_exp, n_proj, profile)

                progress.progress(50, text="Rewriting bullets for ATS optimization…")
                rewrites = gen._rewrite_bullets(
                    profile, selection, jd, must_have, bpe, bpp)

                keywords_used = rewrites.get("keywords_used", [])
                missing = [k for k in must_have
                           if k.lower() not in [u.lower() for u in keywords_used]]

                progress.progress(70, text="Assembling resume…")
                resume = gen._assemble(profile, selection, rewrites, missing)

                progress.progress(85, text="Rendering PDF — measuring fit…")
                resume = gen._enforce_one_page(resume)

                from pdf_renderer import PDFRenderer
                renderer = PDFRenderer()
                renderer._write_pdf(
                    renderer._build_flowables(resume)[0], pdf_path
                )

                builtins.print = original_print

                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                S["resume"]   = resume
                S["pdf_path"] = pdf_path
                S["pdf_bytes"] = pdf_bytes

                db.close()
                progress.progress(100, text="Done!")
                status.empty()

                S["step"] = 4
                st.rerun()

            except Exception as e:
                import builtins
                builtins.print = original_print if 'original_print' in dir() else print
                progress.empty()
                st.markdown(
                    f'<div class="err-box">❌ Generation failed: {e}</div>',
                    unsafe_allow_html=True)

    _show_log()

# ── STEP 4: Download ──────────────────────────────────────────────────────────

elif S["step"] == 4:
    resume  = S["resume"]
    personal = resume.get("personal", {})
    name    = personal.get("name", "resume")

    st.markdown('<div class="step-label">Step 4 of 4</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-title">Your resume is ready</div>', unsafe_allow_html=True)

    col_dl, col_detail = st.columns([1, 2], gap="large")

    with col_dl:
        st.markdown('<div class="card" style="text-align:center">', unsafe_allow_html=True)
        st.markdown("### 📄")
        st.markdown(f"**{name}**")
        st.markdown(f"_{personal.get('headline', '')}_")
        st.markdown("<br>", unsafe_allow_html=True)
        fname = name.lower().replace(" ", "_") + "_resume.pdf"
        st.download_button(
            label="⬇  Download PDF",
            data=S["pdf_bytes"],
            file_name=fname,
            mime="application/pdf",
            use_container_width=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("← Generate for another job"):
            S["step"] = 3
            S["resume"] = None
            S["pdf_path"] = None
            S["pdf_bytes"] = None
            st.rerun()

    with col_detail:
        st.markdown("**What was included**")

        exps = resume.get("experiences", [])
        projs = resume.get("projects", [])
        skills = resume.get("skills", [])

        for exp in exps:
            st.markdown(f"**{exp['title']}** · {exp['company']}")
            for b in exp.get("bullets", []):
                st.markdown(f"  - {b}")

        if projs:
            st.markdown("---")
            for proj in projs:
                st.markdown(f"**{proj['title']}**")
                for b in proj.get("bullets", []):
                    st.markdown(f"  - {b}")

        if skills:
            st.markdown("---")
            st.markdown("**Skills selected**")
            pills = "".join(f'<span class="pill">{s}</span>' for s in skills)
            st.markdown(f'<div class="pill-row">{pills}</div>', unsafe_allow_html=True)

    _show_log()

# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="margin-top: 60px; padding-top: 20px; border-top: 1px solid #E5E4DF;
     font-size: 0.75rem; color: #BBB; text-align: center;">
  Resume Builder · linkedin_parser · storage · resume_generator · pdf_renderer
</div>
""", unsafe_allow_html=True)