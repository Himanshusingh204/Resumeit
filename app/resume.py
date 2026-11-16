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
        '"staff nurse icu") for roles they are qualified for, based on their real titles, skills and '
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
         "Write it like a strong recruiter-ready resume: a sharp 2 line summary aimed at THIS role (title + strongest "
         "matching strengths), bullets that start with a strong action verb and say what was done, how, and the result "
         "(only results already in the profile), mirror the JD's exact keywords where the candidate truly has them, "
         "no filler or buzzwords, consistent tense, one idea per bullet. "
         "Put the most relevant skills/experience/projects first (most relevant bullet first inside each role), "
         "drop irrelevant ones. Aim for ONE page: at most 3 bullets for older roles, at most 3 projects, at most 12 skills. "
         "If the candidate has substantial RELEVANT experience, up to TWO pages is fine: then up to 5 bullets for the "
         "two most recent roles. Never pad; never exceed two pages. Group skills with the JD-relevant ones first. "
         "Copy company, title, dates, degrees, certifications and skill names exactly as in the profile; "
         "do not write any number that is not in the profile. "
         'Return JSON: {"resume": <same shape as the profile>, "match": {"score": 0-100, '
         '"matched": [JD keywords the candidate genuinely has], "gaps": [JD requirements the profile lacks]}}. '
         "Profile shape: " + SCHEMA)


def tailor(profile, job, feedback=None):
    jd = f"{job['title']} at {job['company']}\n\n{job['description']}"[:12000]
    fix = f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {feedback}\nFix exactly that. Use only facts already in the profile." if feedback else ""
    out = ai.complete_json(RULES, f"CANDIDATE PROFILE:\n{json.dumps(profile)}\n\nJOB DESCRIPTION:\n{jd}{fix}")
    return out["resume"], out.get("match", {})


# ---------------------------------------------------------------- rendering
BASE = """*{box-sizing:border-box}@page{size:A4;margin:12mm}
body{margin:0 auto;max-width:790px;padding:26px 30px;color:#1a1a1a}
h1{margin:0}p{margin:0 0 6px}ul{margin:3px 0 0;padding-left:18px}li{margin:1px 0}
.row{display:flex;justify-content:space-between;gap:12px}.row .d{white-space:nowrap}.row>span:first-child{min-width:0}
.item{margin-top:8px;break-inside:avoid}body{overflow-wrap:anywhere}"""  # anywhere: a long URL or word wraps instead of leaving the page

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
    # no rules or colour, lots of air: reads clean in any viewer
    "minimal": """body{font:10pt/1.5 Helvetica,Arial,sans-serif;color:#222}
h1{font-size:22pt;font-weight:600;letter-spacing:-.01em}.c{color:#666;font-size:9pt;margin-top:3px}
h2{font-size:9pt;text-transform:uppercase;letter-spacing:.2em;color:#888;margin:18px 0 6px;font-weight:600}
.t{font-weight:600}.d{color:#888;font-size:9pt}.sub{color:#666}""",
    # solid dark name band, strong section bars
    "executive": """body{font:10pt/1.45 'Segoe UI',Arial,sans-serif;color:#1c1c1c;padding-top:0}
header{background:#1b2430;color:#fff;margin:0 -30px;padding:22px 30px}h1{font-size:24pt;font-weight:600;letter-spacing:.02em}
.c{color:#cfd6df;font-size:9pt;margin-top:4px}h2{font-size:10pt;text-transform:uppercase;letter-spacing:.1em;color:#1b2430;border-bottom:2px solid #1b2430;margin:16px 0 6px;padding-bottom:2px}
.t{font-weight:700}.d{color:#555;font-size:9pt}.sub{color:#555}""",
    # monospace accents for engineers, one green accent
    "tech": """body{font:9.5pt/1.45 'Segoe UI',Arial,sans-serif;color:#1a1a1a}
h1{font:700 20pt Consolas,'Courier New',monospace;color:#0b6b4d}.c{font:8.5pt Consolas,'Courier New',monospace;color:#555;margin-top:3px}
header{border-bottom:2px solid #0b6b4d;padding-bottom:8px}h2{font:700 9.5pt Consolas,'Courier New',monospace;color:#0b6b4d;margin:14px 0 5px}h2:before{content:"// "}
.t{font-weight:700}.d{font:8.5pt Consolas,'Courier New',monospace;color:#666}.sub{color:#555}li::marker{color:#0b6b4d}""",
    # refined serif, small caps headings, thin double rule
    "elegant": """body{font:10.5pt/1.45 'Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif;color:#222}
header{text-align:center;border-bottom:3px double #444;padding-bottom:8px}h1{font-size:24pt;font-weight:400;letter-spacing:.08em;font-variant:small-caps}.c{color:#555;font-size:9.5pt;margin-top:4px}
h2{font-size:11pt;font-variant:small-caps;letter-spacing:.1em;font-weight:700;color:#333;margin:15px 0 5px;border-bottom:1px solid #bbb}
.t{font-weight:700}.d{font-style:italic;color:#555}.sub{font-style:italic;color:#555}""",
}


_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|[%s-%s%s%s]" % tuple(map(chr, (0xD800, 0xDFFF, 0xFFFE, 0xFFFF))))  # PDFs leave these behind; they break DOCX/XML and UTF-8 files


def _s(v):
    """Any value -> clean text ('' for None). AI output does not always match the shape asked for, so lists/dicts are flattened."""
    if isinstance(v, dict):
        v = v.get("url") or v.get("name") or " ".join(map(str, v.values()))
    elif isinstance(v, (list, tuple)):
        v = ", ".join(map(_s, v))
    return _BAD.sub("", "" if v is None else str(v)).strip()


def _strs(v):
    return [t for t in map(_s, v if isinstance(v, list) else [v]) if t]


def _norm(r):
    """Same resume with every field the right type and free of characters that break rendering. Idempotent; the stored resume is never changed."""
    r = r if isinstance(r, dict) else {}
    o = {k: _s(r.get(k)) for k in ("name", "email", "phone", "location", "summary")}
    o.update(links=_strs(r.get("links")), skills=_strs(r.get("skills")), certifications=_strs(r.get("certifications")))
    for key, fields in (("experience", ("company", "title", "start", "end")), ("projects", ("name", "tech")), ("education", ("school", "degree", "year"))):
        o[key] = [{**{f: _s(x.get(f)) for f in fields}, "bullets": _strs(x.get("bullets"))}
                  for x in (r.get(key) if isinstance(r.get(key), list) else []) if isinstance(x, dict)]
    return o


def _e(s):
    return html.escape(_s(s))


def _contact(r):
    return [x for x in [r.get("email"), r.get("phone"), r.get("location"), *(r.get("links") or [])] if x]


def _bul(x):
    return "<ul>" + "".join(f"<li>{_e(b)}</li>" for b in x.get("bullets") or []) + "</ul>"


def _item(left, sub, right, x=None):
    return (f"<div class=item><div class=row><span><span class=t>{_e(left)}</span>"
            f"{' <span class=sub>| ' + _e(sub) + '</span>' if sub else ''}</span><span class=d>{_e(right)}</span></div>"
            + (_bul(x) if x else "") + "</div>")


def render_html(r, template="classic", scale=1.0):
    r = _norm(r)
    css = BASE + TEMPLATES.get(template, TEMPLATES["classic"]) + (f"html{{zoom:{scale:.3f}}}" if scale != 1.0 else "")
    o = [f"<header><h1>{_e(r.get('name'))}</h1><div class=c>{' &nbsp;|&nbsp; '.join(map(_e, _contact(r)))}</div></header>"]
    if r.get("summary"):
        o.append(f"<h2>Summary</h2><p>{_e(r['summary'])}</p>")
    if r.get("skills"):
        o.append(f"<h2>Skills</h2><p>{_e(' · '.join(r['skills']))}</p>")
    if r.get("experience"):
        o.append("<h2>Experience</h2>" + "".join(
            _item(x.get("title"), x.get("company"), " - ".join(filter(None, (x["start"], x["end"]))), x) for x in r["experience"]))
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
    for n in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(n):
            return shutil.which(n)


def render_pdf(r, template="classic", scale=1.0):
    browser = _browser()
    if not browser:
        raise RuntimeError("Edge/Chrome not found for PDF export; use the HTML or DOCX download")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        src, out = Path(d) / "r.html", Path(d) / "r.pdf"
        src.write_text(render_html(r, template, scale), encoding="utf-8")
        try:  # DEVNULL: Edge's child processes can hold a pipe open and stall us; if it hangs after writing the PDF, we still use it
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={d}/prof",
                            f"--print-to-pdf={out}", src.as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        for _ in range(100):  # Edge can exit before the PDF is flushed
            if out.exists() and out.stat().st_size:
                time.sleep(0.2)
                return out.read_bytes()
            time.sleep(0.2)
        raise RuntimeError("PDF export timed out; use the HTML or DOCX download")


# ---------------------------------------------------------------- one page (rule 1.3)
BASE_PT = {"classic": 10.5, "modern": 10.0, "compact": 9.5, "minimal": 10.0, "executive": 10.0, "tech": 9.5, "elegant": 10.5}
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
        c = [x for x in r.get("experience") or [] if isinstance(x, dict) and len(x.get("bullets") or []) > keep]
        return pop(max(c, key=lambda x: len(x["bullets"]))["bullets"]) if c else False

    skills, projects = r.get("skills") or [], r.get("projects") or []
    steps = (lambda: len(projects) > 2 and pop(projects), lambda: bullets(2), lambda: len(skills) > 10 and pop(skills),
             lambda: bool(projects) and pop(projects), lambda: bullets(1), lambda: len(skills) > 6 and pop(skills))
    return any(s() for s in steps)


def page_fill(pdf):
    """How full the last page is compared with the first (0..1), by text length."""
    from pypdf import PdfReader
    p = PdfReader(io.BytesIO(pdf)).pages
    first = len(p[0].extract_text() or "") or 1
    return min(1.0, len(p[-1].extract_text() or "") / first)


MIN_SECOND_PAGE = 0.30  # a second page must be at least this full, or the resume is trimmed back to one page


def fit_one_page(r, template="classic", pages="one"):
    """-> (resume, pdf bytes, pages, flag).
    pages='one': always one page (shrinks type to 9pt, then drops the least relevant content).
    pages='auto': one page when it fits at a comfortable size; otherwise TWO pages if the second page is
    substantial (>=30% full), so relevant content is kept at normal size instead of being cut. Never more than two.
    flag is None when the result is fine, else a 'manual trim needed' message."""
    if pages == "auto":
        res = _fit_auto(r, template)
        if res:
            return res
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


def _fit_auto(r, template):
    """Comfortable 1 page, or a well-filled 2 pages at normal size. None = use the strict one-page fit."""
    r = copy.deepcopy(r)
    floor = max(MIN_PT / BASE_PT.get(template, 10.5), 0.8)
    for sc in (1.0, max(0.96, floor), max(0.92, floor)):  # at most ~8% smaller: still easy to read
        pdf = render_pdf(r, template, sc)
        if pdf_pages(pdf) == 1:
            return r, pdf, 1, None
    pdf = render_pdf(r, template, 1.0)
    n = pdf_pages(pdf)
    while n > 2 and _trim_one(r):  # never more than two pages
        pdf = render_pdf(r, template, 1.0)
        n = pdf_pages(pdf)
    if n == 2 and page_fill(pdf) >= MIN_SECOND_PAGE:
        return r, pdf, 2, None
    return None


def render_docx(r):
    from docx import Document
    r = _norm(r)
    d = Document()
    d.add_heading(r.get("name") or "", 0)
    d.add_paragraph(" | ".join(_contact(r)))
    if r.get("summary"):
        d.add_heading("Summary", 2); d.add_paragraph(r["summary"])
    if r.get("skills"):
        d.add_heading("Skills", 2); d.add_paragraph(", ".join(r["skills"]))
    for key, title, head in (
            ("experience", "Experience", lambda x: ", ".join(filter(None, (x["title"], x["company"]))) + (f" ({' - '.join(filter(None, (x['start'], x['end'])))})" if x["start"] or x["end"] else "")),
            ("projects", "Projects", lambda x: " - ".join(filter(None, (x["name"], x["tech"]))))):
        if r.get(key):
            d.add_heading(title, 2)
            for x in r[key]:
                d.add_paragraph().add_run(head(x)).bold = True
                for b in x.get("bullets") or []:
                    d.add_paragraph(b, style="List Bullet")
    if r.get("education"):
        d.add_heading("Education", 2)
        for x in r["education"]:
            d.add_paragraph(f"{', '.join(filter(None, (x['degree'], x['school'])))} {x['year']}".strip())
    if r.get("certifications"):
        d.add_heading("Certifications", 2); d.add_paragraph("; ".join(r["certifications"]))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
