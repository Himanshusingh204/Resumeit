"""Writes every finished resume to disk: jobs/<date>/<company>_<title>_<id>/{jd.md, match.txt, resume.docx, resume.html, resume.pdf}."""
import re
from datetime import date
from pathlib import Path

from . import resume

JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"


def _slug(s):
    return re.sub(r"\W+", "-", s or "", flags=re.A).strip("-")[:40] or "x"


def folder(job):
    day = (job.get("created") or date.today().isoformat())[:10]
    return JOBS_DIR / day / f"{_slug(job.get('company'))}_{_slug(job.get('title'))}_{job['id']}"


def save(job, r, match, template="classic"):
    """Raises only after every other file is written, so a failed PDF never loses the rest."""
    d = folder(job)
    d.mkdir(parents=True, exist_ok=True)
    (d / "jd.md").write_text(
        f"# {job.get('title')}\n\n- Company: {job.get('company')}\n- Location: {job.get('location') or '-'}\n"
        f"- Link: {job.get('url')}\n\n---\n\n{job.get('description') or ''}\n", encoding="utf-8")
    (d / "match.txt").write_text(
        f"Match: {match.get('score', '?')}%\n\nYou have:\n" + "".join(f"- {x}\n" for x in match.get("matched", []))
        + "\nNot in your resume:\n" + "".join(f"- {x}\n" for x in match.get("gaps", [])), encoding="utf-8")
    (d / "resume.html").write_text(resume.render_html(r, template), encoding="utf-8")
    (d / "resume.docx").write_bytes(resume.render_docx(r))
    (d / "resume.pdf").write_bytes(resume.render_pdf(r, template))
    return d
