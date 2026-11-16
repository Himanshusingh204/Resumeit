"""Parse base resume -> profile JSON; tailor profile to a JD; render HTML / PDF / DOCX."""
import copy, html, io, json, re, shutil, subprocess, tempfile, time
from pathlib import Path
from . import ai

SCHEMA = """{"name":"","email":"","phone":"","location":"","links":["url"],"summary":"",
"skills":["..."],"experience":[{"company":"","title":"","start":"","end":"","bullets":["..."]}],
"projects":[{"name":"","tech":"","bullets":["..."]}],"education":[{"school":"","degree":"","year":""}],
"certifications":["..."]}"""


def extract_text(name, data):
    if name.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    if name.lower().endswith(".docx"):
        from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    return data.decode("utf-8", "ignore")


def parse_profile(text):
    return ai.complete_json("You convert a resume into structured JSON. Copy facts exactly; never add anything. "
                            "Use this shape (use empty values if absent): " + SCHEMA, text[:20000])


def extract_terms(profile, n=5):
    """Job-search queries from the resume: titles the candidate fits, most specific first."""
    out = ai.complete_json(
        f"From this candidate profile, give up to {n} short job-board search queries (2-4 words each, e.g. "
        '"python backend developer") for roles they are qualified for, based on their real titles, skills and '
        'projects. Most fitting first. Return {"terms": ["..."]}.', json.dumps(profile))
    terms = [t.strip() for t in out.get("terms", []) if isinstance(t, str) and t.strip()]
    return list(dict.fromkeys(terms))[:n]  # dedupe, keep order


def profile_problem(p):
    """Why a parsed profile is unusable (None if fine). Guards against a blank parse being saved."""
    if not isinstance(p, dict) or not p.get("name"):
        return "no name found"
    if not (p.get("skills") or p.get("experience") or p.get("projects")):
        return "no skills or experience found"
    return None


def relevance(profile, job):
    """Profile skills that literally appear in the job text. Empty = job has nothing to do with the resume."""
    text = f"{job.get('title') or ''} {job.get('description') or ''}"
    return [s for s in profile.get("skills") or []
            if s and re.search(r"(?<![\w+#])" + re.escape(s.strip()) + r"(?![\w+#])", text, re.I)]


def validate(r, profile):
    """Problem string if the tailored resume is unusable/incomplete, else None."""
    if not isinstance(r, dict) or not r.get("name"):
        return "resume has no name"
    if profile.get("experience") and not r.get("experience") and not r.get("projects"):
        return "tailored resume dropped all experience and projects"
    if not r.get("skills") and profile.get("skills"):
        return "tailored resume has no skills"
    return None


RULES = ("You tailor a candidate's resume to one job description. HARD RULES: use ONLY facts present in the "
         "candidate profile - never invent employers, titles, dates, degrees, metrics, tools or skills. "
         "You may reorder, select, and reword bullets to mirror the JD's language when the candidate truly did it. "
         "Put the most relevant skills/experience/projects first, drop irrelevant ones. The resume MUST fit ONE page: "
         "at most 3 bullets per role, at most 3 projects, at most 12 skills, a 2 line summary. "
         "Copy company, title, dates, degrees, certifications and skill names exactly as in the profile; "
         "do not write any number that is not in the profile. "
         'Return JSON: {"resume": <same shape as the profile>, "match": {"score": 0-100, '
         '"matched": [JD keywords the candidate genuinely has], "gaps": [JD requirements the profile lacks]}}. '
         "Profile shape: " + SCHEMA)


def tailor(profile, job):
    jd = f"{job['title']} at {job['company']}\n\n{job['description']}"[:12000]
    out = ai.complete_json(RULES, f"CANDIDATE PROFILE:\n{json.dumps(profile)}\n\nJOB DESCRIPTION:\n{jd}")
    return out["resume"], out.get("match", {})


# ---------------------------------------------------------------- rendering
BASE = """*{box-sizing:border-box}@page{size:A4;margin:12mm}
body{margin:0 auto;max-width:790px;padding:26px 30px;color:#1a1a1a}
h1{margin:0}p{margin:0 0 6px}ul{margin:3px 0 0;padding-left:18px}li{margin:1px 0}
.row{display:flex;justify-content:space-between;gap:12px}.row .d{white-space:nowrap}
.item{margin-top:8px;break-inside:avoid}"""

TEMPLATES = {
    # serif, centred, plain rules: the safest layout for applicant tracking systems
    "classic": """body{font:10.5pt/1.4 Georgia,'Times New Roman',serif}
h1{font-size:23pt;text-align:center;letter-spacing:.02em}.c{text-align:center;color:#444;font-size:9.5pt;margin-top:3px}
h2{font-size:10.5pt;text-transform:uppercase;letter-spacing:.12em;border-bottom:1px solid #1a1a1a;margin:16px 0 6px;padding-bottom:2px}
.t{font-weight:700}.d{font-style:italic}.sub{font-style:italic;color:#444}""",
    # sans, left aligned, one navy accent, generous spacing
    "modern": """body{font:10pt/1.5 'Segoe UI',Helvetica,Arial,sans-serif;color:#222}
h1{font-size:27pt;font-weight:300;letter-spacing:-.01em;color:#1f3a5f}.c{color:#666;font-size:9pt;margin-top:4px}
header{border-bottom:3px solid #1f3a5f;padding-bottom:12px}
h2{font-size:9pt;text-transform:uppercase;letter-spacing:.18em;color:#1f3a5f;margin:18px 0 6px;font-weight:700}
.t{font-weight:600}.d{color:#777;font-size:9pt}.sub{color:#666}li::marker{color:#1f3a5f}""",
    # dense, small type, fits a lot on one page
    "compact": """body{font:9.5pt/1.3 Calibri,Arial,sans-serif;padding:18px 24px}
h1{font-size:18pt}header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap;border-bottom:1px solid #999;padding-bottom:5px}
.c{color:#444;font-size:9pt}h2{font-size:9.5pt;text-transform:uppercase;font-weight:700;background:#f0f0f0;padding:2px 6px;margin:10px 0 4px}
.t{font-weight:700}.d{color:#555;font-size:9pt}.sub{color:#555}ul{padding-left:15px}li{margin:0}p{margin-bottom:3px}.item{margin-top:4px}""",
}


def _e(s):
    return html.escape(str(s or ""))


def _contact(r):
    return [x for x in [r.get("email"), r.get("phone"), r.get("location"), *(r.get("links") or [])] if x]


def _bul(x):
    return "<ul>" + "".join(f"<li>{_e(b)}</li>" for b in x.get("bullets") or []) + "</ul>"


def _item(left, sub, right, x=None):
    return (f"<div class=item><div class=row><span><span class=t>{_e(left)}</span>"
            f"{' <span class=sub>| ' + _e(sub) + '</span>' if sub else ''}</span><span class=d>{_e(right)}</span></div>"
            + (_bul(x) if x else "") + "</div>")


def render_html(r, template="classic", scale=1.0):
    css = BASE + TEMPLATES.get(template, TEMPLATES["classic"]) + (f"html{{zoom:{scale:.3f}}}" if scale != 1.0 else "")
    o = [f"<header><h1>{_e(r.get('name'))}</h1><div class=c>{' &nbsp;|&nbsp; '.join(map(_e, _contact(r)))}</div></header>"]
    if r.get("summary"):
        o.append(f"<h2>Summary</h2><p>{_e(r['summary'])}</p>")
    if r.get("skills"):
        o.append(f"<h2>Skills</h2><p>{_e(' · '.join(r['skills']))}</p>")
    if r.get("experience"):
        o.append("<h2>Experience</h2>" + "".join(
            _item(x.get("title"), x.get("company"), f"{x.get('start') or ''} - {x.get('end') or ''}", x) for x in r["experience"]))
    if r.get("projects"):
        o.append("<h2>Projects</h2>" + "".join(_item(x.get("name"), x.get("tech"), "", x) for x in r["projects"]))
    if r.get("education"):
        o.append("<h2>Education</h2>" + "".join(
            _item(x.get("degree"), x.get("school"), x.get("year")) for x in r["education"]))
    if r.get("certifications"):
        o.append(f"<h2>Certifications</h2><p>{_e('; '.join(r['certifications']))}</p>")
    return (f"<!doctype html><meta charset=utf-8><title>{_e(r.get('name'))} - Resume</title>"
            f"<style>{css}</style>" + "".join(o))


def _browser():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return shutil.which("msedge") or shutil.which("chrome")


def render_pdf(r, template="classic", scale=1.0):
    browser = _browser()
    if not browser:
        raise RuntimeError("Edge/Chrome not found for PDF export; use the HTML or DOCX download")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        src, out = Path(d) / "r.html", Path(d) / "r.pdf"
        src.write_text(render_html(r, template, scale), encoding="utf-8")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={d}/prof",
                        f"--print-to-pdf={out}", src.as_uri()], check=True, capture_output=True, timeout=60)
        for _ in range(100):  # Edge can exit before the PDF is flushed
            if out.exists() and out.stat().st_size:
                time.sleep(0.2)
                return out.read_bytes()
            time.sleep(0.2)
        raise RuntimeError("PDF export timed out; use the HTML or DOCX download")


# ---------------------------------------------------------------- one page (rule 1.3)
BASE_PT = {"classic": 10.5, "modern": 10.0, "compact": 9.5}
MIN_PT = 9.0  # never shrink body text below this


def pdf_pages(pdf):
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _trim_one(r):
    """Remove the least relevant item (the AI orders most relevant first, so the last one). Never adds anything."""
    def pop(lst):
        lst.pop()
        return True

    def bullets(keep):
        c = [x for x in r.get("experience") or [] if len(x.get("bullets") or []) > keep]
        return pop(max(c, key=lambda x: len(x["bullets"]))["bullets"]) if c else False

    skills, projects = r.get("skills") or [], r.get("projects") or []
    steps = (lambda: len(projects) > 2 and pop(projects), lambda: bullets(2), lambda: len(skills) > 10 and pop(skills),
             lambda: bool(projects) and pop(projects), lambda: bullets(1), lambda: len(skills) > 6 and pop(skills))
    return any(s() for s in steps)


def fit_one_page(r, template="classic"):
    """-> (resume, pdf bytes, pages, flag). Shrinks type to 9pt first, then drops least relevant content.
    flag is None when it fits on one A4 page, else a 'manual trim needed' message."""
    r = copy.deepcopy(r)
    floor = max(MIN_PT / BASE_PT.get(template, 10.5), 0.8)
    scales = [1.0] + [round(1.0 - (1.0 - floor) * i / 3, 3) for i in (1, 2, 3)]

    def attempt(res, steps):
        pdf = pages = None
        for sc in steps:
            pdf = render_pdf(res, template, sc)
            pages = pdf_pages(pdf)
            if pages == 1:
                break
        return pdf, pages

    pdf, pages = attempt(r, scales)
    trimmed = False
    while pages > 1 and _trim_one(r):
        trimmed = True
        pdf, pages = attempt(r, [floor])
    if trimmed and pages == 1:  # fits at the smallest type: see if a bigger one also fits
        pdf, pages = attempt(r, scales)
    flag = None if pages == 1 else f"Manual trim needed: still {pages} pages at {MIN_PT:g}pt"
    return r, pdf, pages, flag


def render_docx(r):
    from docx import Document
    d = Document()
    d.add_heading(r.get("name") or "", 0)
    d.add_paragraph(" | ".join(_contact(r)))
    if r.get("summary"):
        d.add_heading("Summary", 2); d.add_paragraph(r["summary"])
    if r.get("skills"):
        d.add_heading("Skills", 2); d.add_paragraph(", ".join(r["skills"]))
    for key, title, head in (
            ("experience", "Experience", lambda x: f"{x.get('title')}, {x.get('company')} ({x.get('start')} - {x.get('end')})"),
            ("projects", "Projects", lambda x: f"{x.get('name')} - {x.get('tech')}")):
        if r.get(key):
            d.add_heading(title, 2)
            for x in r[key]:
                d.add_paragraph().add_run(head(x)).bold = True
                for b in x.get("bullets") or []:
                    d.add_paragraph(b, style="List Bullet")
    if r.get("education"):
        d.add_heading("Education", 2)
        for x in r["education"]:
            d.add_paragraph(f"{x.get('degree')}, {x.get('school')} {x.get('year') or ''}")
    if r.get("certifications"):
        d.add_heading("Certifications", 2); d.add_paragraph("; ".join(r["certifications"]))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
