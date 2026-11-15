import json, os, re, threading
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from . import ai, db, export, fetch, resume, scrape

app = FastAPI()
db.init()
pool = ThreadPoolExecutor(2)       # bulk queue (search / "build all"); shortcut: 2 parallel AI calls
fast_pool = ThreadPoolExecutor(2)  # single clicks never wait behind a long bulk queue
_futs = {}                         # job id -> queued bulk future, so the queue can be stopped
state = {"scraping": False, "msg": ""}
db.x("UPDATE jobs SET status='new', error=NULL WHERE status IN ('queued','tailoring')")  # queue does not survive a restart


def _tailor(jid):
    job = db.q("SELECT * FROM jobs WHERE id=?", jid)[0]
    db.x("UPDATE jobs SET status='tailoring', error=NULL WHERE id=?", jid)
    try:
        profile = db.kv_get("profile")
        if resume.profile_problem(profile):  # fail locally, do not spend an AI call on an empty profile
            raise ValueError("resume profile is empty: re-upload your resume")
        r, m = resume.tailor(profile, job)
        bad = resume.validate(r, profile)
        if bad:
            raise ValueError("incomplete: " + bad)
        db.x("UPDATE jobs SET status='done', resume=?, match=? WHERE id=?", json.dumps(r), json.dumps(m), jid)
        try:  # the resume is already safe in the DB; a disk/PDF problem must not undo that
            export.save(job, r, m, db.kv_get("template") or "classic")
        except Exception as e:
            db.issue("export_failed", f"job {jid}: {job['title']} @ {job['company']}", e)
    except Exception as e:
        db.x("UPDATE jobs SET status='error', error=? WHERE id=?", str(e)[:500], jid)
        db.issue("tailor_failed", f"job {jid}: {job['title']} @ {job['company']}", e)


def _score_fit(ids=None):
    """Free, no-AI check: which of your skills a job mentions. Lets you pick jobs before spending AI calls."""
    profile = db.kv_get("profile")
    if resume.profile_problem(profile):
        return
    rows = db.q("SELECT id,title,description FROM jobs WHERE id IN (%s)" % ",".join(map(str, ids))) if ids else         db.q("SELECT id,title,description FROM jobs WHERE fit IS NULL")
    for j in rows:
        db.x("UPDATE jobs SET fit=? WHERE id=?", json.dumps(resume.relevance(profile, j)), j["id"])


def _queue(ids):
    """Tailor only jobs that share at least one skill with the resume; mark the rest 'skipped' (no AI call)."""
    profile, futures = db.kv_get("profile"), []
    for i in ids:
        job = db.q("SELECT * FROM jobs WHERE id=?", i)[0]
        if resume.relevance(profile, job):
            db.x("UPDATE jobs SET status='queued', error=NULL WHERE id=?", i)
            f = _futs[i] = pool.submit(_tailor, i)
            f.add_done_callback(lambda fut, i=i: _futs.pop(i, None) if _futs.get(i) is fut else None)  # no stale futures
            futures.append(f)
        else:
            db.x("UPDATE jobs SET status='skipped', error='Low match: none of your skills appear in this job' WHERE id=?", i)
    return futures


def _need_profile():
    bad = resume.profile_problem(db.kv_get("profile"))
    if bad:
        raise HTTPException(400, f"Upload your base resume first ({bad})")


def _ai_name():
    try:
        return ai.provider()
    except Exception as e:
        return f"NOT CONFIGURED: {e}"


_score_fit()  # jobs scraped before the fit column existed


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent.parent / "static" / "index.html")


@app.get("/api/profile")
def get_profile():
    p = db.kv_get("profile")
    return {"profile": p, "ai": _ai_name(), "problem": resume.profile_problem(p), "template": db.kv_get("template") or "classic"}


class Tpl(BaseModel):
    t: str


@app.post("/api/template")
def set_template(b: Tpl):
    if b.t not in resume.TEMPLATES:
        raise HTTPException(400, "unknown template")
    db.kv_set("template", b.t)  # used for the PDFs written to the jobs folder
    return {"ok": True}


def _open(path):
    if not hasattr(os, "startfile"):  # shortcut: Windows only, which is where this tool runs
        raise HTTPException(501, "Opening folders is only supported on Windows")
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)  # noqa: S606


@app.post("/api/folder")
def open_jobs_folder():
    _open(export.JOBS_DIR)
    return {"path": str(export.JOBS_DIR)}


@app.post("/api/jobs/{jid}/folder")
def open_job_folder(jid: int):
    d = export.folder(job(jid))
    if not d.exists():
        raise HTTPException(404, "No folder yet for this job (resume not built)")
    _open(d)
    return {"path": str(d)}


def _save_profile(text):
    """Parse resume text and save it. A file that yields nothing never overwrites a good profile."""
    if len(text.strip()) < 80:
        raise HTTPException(422, "No readable text found (scanned or image PDF?). Upload a DOCX/TXT or use 'Paste text'.")
    try:
        p = resume.parse_profile(text)
    except Exception as e:
        raise HTTPException(500, str(e))
    bad = resume.profile_problem(p)
    if bad:
        raise HTTPException(422, f"The resume could not be understood ({bad}). Your previous profile was kept.")
    db.kv_set("profile", p)
    return p


@app.post("/api/profile/upload")
async def upload(f: UploadFile):
    try:
        text = resume.extract_text(f.filename or "", await f.read())
    except Exception as e:  # corrupt, empty or password-protected file
        raise HTTPException(422, f"Could not open this file ({type(e).__name__}). Try a DOCX/TXT or 'Paste text'.")
    return _save_profile(text)


class Text(BaseModel):
    text: str


@app.post("/api/profile/text")
def profile_text(t: Text):
    return _save_profile(t.text)


@app.delete("/api/profile")
def delete_profile():
    db.x("DELETE FROM kv WHERE k='profile'")  # tailored resumes already built are kept
    return {"ok": True}


@app.put("/api/profile")
def put_profile(p: dict):
    db.kv_set("profile", p)
    return p


class Search(BaseModel):
    terms: list[str] = []  # empty = extract from the resume
    location: str = ""
    country: str = "in"
    sites: list[str] = ["indeed", "linkedin", "naukri"]
    results: int = 20
    hours_old: int = 168
    remote: bool = False
    auto: bool = False  # off by default: finding jobs must not spend AI calls unless asked


def _run_search(s):
    state.update(scraping=True, msg="Scraping job boards...")
    try:
        terms = [t.strip() for t in s.terms if t.strip()]
        if not terms:
            state["msg"] = "Reading keywords from your resume..."
            try:
                terms = resume.extract_terms(db.kv_get("profile"))
            except Exception as e:
                db.issue("keywords_failed", "resume", e)
        if not terms:
            state["msg"] = "No search terms: add some manually (see Issues)"
            return
        ids = []
        for i, t in enumerate(terms, 1):
            state["msg"] = f"Scraping {i}/{len(terms)}: {t}"
            ids += scrape.scrape(t, s.location, s.country, s.sites, s.results, s.hours_old, s.remote)
        _score_fit(ids)
        state["msg"] = f"{len(ids)} new jobs" + (", building resumes..." if s.auto else "")
        if s.auto:
            wait(_queue(ids))
        state["msg"] = f"Done: {len(ids)} new jobs" + ("" if s.auto else ". Pick the ones you want and press Build.")
    except Exception as e:
        state["msg"] = f"Scrape failed: {e}"
    finally:
        state["scraping"] = False


@app.get("/api/terms")
def terms():
    _need_profile()
    try:
        return {"terms": resume.extract_terms(db.kv_get("profile"))}
    except Exception as e:
        db.issue("keywords_failed", "resume", e)
        raise HTTPException(500, str(e))


@app.get("/api/issues")
def issues():
    return db.q("SELECT * FROM issues ORDER BY id DESC LIMIT 200")


@app.delete("/api/issues")
def clear_issues():
    db.x("DELETE FROM issues")
    return {"ok": True}


@app.post("/api/search")
def search(s: Search):
    _need_profile()
    if state["scraping"]:
        raise HTTPException(409, "A search is already running")
    state["scraping"] = True  # claim before the thread starts, else two quick clicks both pass
    threading.Thread(target=_run_search, args=(s,), daemon=True).start()
    return state


@app.post("/api/jobs/tailor-pending")
def tailor_pending():
    """Build resumes for jobs still queued or failed (e.g. after fixing a bad profile)."""
    _need_profile()
    if state["scraping"]:
        raise HTTPException(409, "A search is running; its jobs are already queued")
    ids = [r["id"] for r in db.q("SELECT id FROM jobs WHERE status IN ('new','error') ORDER BY id DESC")]
    return {"queued": len(_queue(ids))}  # jobs unrelated to the resume are skipped, not counted


@app.get("/api/status")
def status():
    return state


class Manual(BaseModel):
    title: str = ""
    company: str = ""
    url: str = ""
    description: str = ""  # empty + a url = read the posting from the link


@app.post("/api/jobs")
def add_job(m: Manual):
    _need_profile()
    title, company, desc = m.title, m.company, m.description.strip()
    if not desc:
        if not m.url.startswith("http"):
            raise HTTPException(422, "Paste the job description or give a job link")
        try:
            t, c, desc = fetch.job_from_url(m.url)
        except ValueError as e:
            raise HTTPException(422, str(e))
        title, company = title or t, company or c
    url = m.url or "manual:" + str(abs(hash(desc)))
    if db.q("SELECT 1 FROM jobs WHERE url=?", url):
        raise HTTPException(409, "Job already added")
    jid = db.x("INSERT INTO jobs(url,site,title,company,description) VALUES(?,?,?,?,?)",
               url, "manual", title or "Job", company or "-", desc)
    fast_pool.submit(_tailor, jid)
    return {"id": jid}


@app.get("/api/jobs")
def jobs():
    return db.q("SELECT id,url,site,title,company,location,posted,status,error,match,fit FROM jobs ORDER BY id DESC")


@app.get("/api/jobs/{jid}")
def job(jid: int):
    r = db.q("SELECT * FROM jobs WHERE id=?", jid)
    if not r:
        raise HTTPException(404)
    return r[0]


@app.post("/api/jobs/{jid}/tailor")
def retailor(jid: int):
    _need_profile()
    job(jid)
    f = _futs.pop(jid, None)
    if f:
        f.cancel()  # was waiting in the bulk queue: this click wins
    fast_pool.submit(_tailor, jid)
    return {"ok": True}


@app.post("/api/queue/stop")
def stop_queue():
    n = 0
    for jid, f in list(_futs.items()):
        if f.cancel():
            db.x("UPDATE jobs SET status='new' WHERE id=? AND status='queued'", jid)
            n += 1
        _futs.pop(jid, None)
    return {"stopped": n}


@app.put("/api/jobs/{jid}/resume")
def edit(jid: int, r: dict):
    db.x("UPDATE jobs SET resume=? WHERE id=?", json.dumps(r), jid)
    return {"ok": True}


@app.delete("/api/jobs")
def clear_jobs(status: str = ""):
    """status=error clears failed jobs; empty clears every job."""
    if status not in ("", "error"):
        raise HTTPException(400, "status must be empty or 'error'")
    db.x("DELETE FROM jobs WHERE status='error'" if status else "DELETE FROM jobs")
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def delete(jid: int):
    db.x("DELETE FROM jobs WHERE id=?", jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/resume.{fmt}")
def download(jid: int, fmt: str, t: str = "classic"):
    if fmt not in ("html", "pdf", "docx"):
        raise HTTPException(404)
    j = job(jid)
    if not j["resume"]:
        raise HTTPException(409, "Resume not ready")
    r = json.loads(j["resume"])
    name = re.sub(r"[^\w-]+", "_", f"{r.get('name', 'resume')}_{j['company']}_{j['title']}")[:80]
    try:
        if fmt == "html":
            return HTMLResponse(resume.render_html(r, t))
        if fmt == "pdf":
            body, mt = resume.render_pdf(r, t), "application/pdf"
        else:
            body, mt = resume.render_docx(r), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except Exception as e:
        raise HTTPException(500, str(e))
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})
