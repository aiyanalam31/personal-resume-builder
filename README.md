# Personal Resume Builder

An AI-powered resume builder that parses your LinkedIn profile, matches your experiences and skills to any job description, and exports a tailored 1-page ATS-optimized PDF.

## How it works

1. **Upload** your LinkedIn data export (`.zip`)
2. **Review** your parsed profile — experiences, projects, skills, education
3. **Paste** a job description
4. **Download** a tailored 1-page PDF with ATS-optimized bullet points

The LLM extracts keywords from the job description, selects your most relevant experiences and projects, and rewrites your bullet points to match. A PDF renderer measures the actual content height and trims until everything fits on one page.

## Tech stack

- **Python** — core logic
- **Streamlit** — web UI
- **SQLite** — local profile storage
- **Groq API** (Llama 3.3) — keyword extraction, selection, and bullet rewriting
- **ReportLab** — PDF rendering with overflow detection

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/aiyanalam31/personal-resume-builder.git
cd personal-resume-builder
```

**2. Create and activate a virtual environment**
```bash
python -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Get a free Groq API key**

Sign up at [console.groq.com](https://console.groq.com) and create an API key. It's free.

**5. Run the app**
```bash
python -m streamlit run app.py
```

Opens at `http://localhost:8501`.

## Getting your LinkedIn export

1. Go to LinkedIn → Settings & Privacy → Data Privacy → **Download your data**
2. Select **"Download larger data archive"**
3. Click **Request archive**
4. Check your email — LinkedIn sends a download link within minutes

## Project structure

```
resume-builder/
├── app.py                  # Streamlit web UI
├── linkedin_parser.py      # Parses LinkedIn .zip export into structured JSON
├── storage.py              # SQLite profile storage layer
├── resume_generator.py     # LLM pipeline — keyword extraction, selection, rewriting
├── pdf_renderer.py         # ReportLab PDF renderer with overflow detection
└── requirements.txt
```

## License

Copyright (c) 2026 Aiyan Alam. All rights reserved.