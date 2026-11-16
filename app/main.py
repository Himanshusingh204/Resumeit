import io, json, os, re, sqlite3, threading, zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from . import ai, cover, db, diff, export, fetch, details, flags, guard, resume, scrape

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
        guard.check(r, profile)  # rule 1.2: nothing that is not already in the profile
        db.x("UPDATE jobs SET status='done', resume=?, match=?, flag=NULL WHERE id=?", json.dumps(r), json.dumps(m), jid)
        try:  # the resume is already safe in the DB; a disk/PDF problem must not undo that
            _, r2, flag = export.save_fit(job, r, m, db.kv_get("template") or "classic")
            db.x("UPDATE jobs SET resume=?, flag=? WHERE id=?", json.dumps(r2), flag, jid)  # r2 = trimmed to one page
            if flag:
                db.issue("manual_trim", f"job {jid}: {job['title']} @ {job['company']}", flag)
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
    rows = db.q("SELECT id,title,description FROM jobs WHERE id IN (%s)" % ",".join(map(str, ids))) if ids else \
        db.q("SELECT id,title,description FROM jobs WHERE fit IS NULL")
    for j in rows:
        db.x("UPDATE jobs SET fit=? WHERE id=?", json.dumps(resume.relevance(profile, j)), j["id"])


def _score_req():
    """Free: years / US-only / clearance / sponsorship read from each new job's text (needs no profile)."""
    for j in db.q("SELECT id,description FROM jobs WHERE req IS NULL"):
        db.x("UPDATE jobs SET req=? WHERE id=?", json.dumps(flags.requirements(j["description"])), j["id"])


def _score_info():
    """Free: salary, work mode, job type and warning tags for jobs that have none yet."""
    for j in db.q("SELECT id,title,description,board FROM jobs WHERE info IS NULL"):
        db.x("UPDATE jobs SET info=? WHERE id=?",
             json.dumps(details.details(j["description"], details.parse(j["board"]), j["title"])), j["id"])


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
_score_req()
_score_info()


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
        _score_req()
        _score_info()
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
    _score_req()
    _score_info()
    fast_pool.submit(_tailor, jid)
    return {"id": jid}


TRACK = ("Applied", "Interview", "Rejected", "Offer")
HIDDEN_BY_DEFAULT = ("Applied", "Rejected")


@app.get("/api/jobs")
def jobs(hide_senior: bool = False, track: str = "", min_salary: float = 0, has_salary: bool = False, mode: str = "",
         type: str = "", max_age_days: int = 0, hide_restricted: bool = False):
    """track: '' = everything, 'open' = hide Applied/Rejected, or one of TRACK.
    min_salary: lakh INR per year (USD etc. converted roughly); mode: remote|hybrid|onsite; type: full-time|contract|internship...;
    hide_restricted hides US-only / clearance / no-sponsorship jobs."""
    rows = db.q("SELECT id,url,site,title,company,location,posted,status,error,match,fit,req,info,flag,track,tracked_at,"
                "(cover IS NOT NULL) AS has_cover FROM jobs ORDER BY id DESC")
    years = flags.profile_years(db.kv_get("profile"))
    out = []
    for j in rows:
        req = flags.parse(j.pop("req"))
        j["flags"], j["senior"] = flags.labels(req), flags.is_senior(req, years)
        info = details.parse(j.pop("info"))
        j.update(salary=info.get("salary"), salary_lakh=info.get("salary_lakh"), salary_src=info.get("salary_src"),
                 mode=info.get("mode"), jtype=info.get("type"), tags=info.get("tags") or [], age_days=details.age_days(j["posted"]))
        j["restricted"] = bool(req.get("us_only") or req.get("clearance") or req.get("no_sponsorship"))
        if hide_senior and j["senior"]:
            continue
        if (has_salary or min_salary) and j["salary_lakh"] is None:
            continue
        if min_salary and j["salary_lakh"] < min_salary:
            continue
        if mode and j["mode"] != mode:
            continue
        if type and j["jtype"] != type:
            continue
        if max_age_days and (j["age_days"] is None or j["age_days"] > max_age_days):
            continue
        if hide_restricted and j["restricted"]:
            continue
        if track == "open" and j["track"] in HIDDEN_BY_DEFAULT:
            continue
        if track in TRACK and j["track"] != track:
            continue
        out.append(j)
    return out


class Track(BaseModel):
    status: str = ""  # one of TRACK, or "" to clear


@app.post("/api/jobs/{jid}/track")
def set_track(jid: int, b: Track):
    """The ONLY way a job becomes Applied/Interview/...: you press the button. The app never calls this itself."""
    job(jid)
    if b.status and b.status not in TRACK:
        raise HTTPException(400, "status must be one of " + ", ".join(TRACK) + " or empty")
    db.x("UPDATE jobs SET track=?, tracked_at=? WHERE id=?",
         b.status or None, datetime.now().strftime("%Y-%m-%d %H:%M") if b.status else None, jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/diff")
def job_diff(jid: int):
    j = job(jid)
    if not j["resume"]:
        raise HTTPException(409, "Resume not ready")
    return diff.diff(db.kv_get("profile") or {}, json.loads(j["resume"]))


@app.post("/api/jobs/{jid}/cover-letter")
def cover_letter(jid: int):
    """Optional, only when you ask. One AI call, facts from your profile only."""
    _need_profile()
    j = job(jid)
    try:
        text = cover.generate(db.kv_get("profile"), j)
    except Exception as e:
        db.issue("cover_failed", f"job {jid}: {j['title']} @ {j['company']}", e)
        raise HTTPException(422, str(e))
    db.x("UPDATE jobs SET cover=? WHERE id=?", text, jid)
    try:
        d = export.folder(j)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cover_letter.md").write_text(text, encoding="utf-8")
    except Exception as e:
        db.issue("export_failed", f"job {jid} cover letter", e)
    return {"cover": text}


def _refit(jid):
    """Re-render an already built resume to one page. No AI call; only removes content."""
    j = db.q("SELECT * FROM jobs WHERE id=?", jid)[0]
    try:
        _, r2, flag = export.save_fit(j, json.loads(j["resume"]), flags.parse(j["match"]), db.kv_get("template") or "classic")
        db.x("UPDATE jobs SET resume=?, flag=? WHERE id=?", json.dumps(r2), flag, jid)
        if flag:
            db.issue("manual_trim", f"job {jid}: {j['title']} @ {j['company']}", flag)
    except Exception as e:
        db.issue("export_failed", f"job {jid}: refit", e)


@app.post("/api/jobs/refit")
def refit_all():
    """Fix resumes built before the one-page guard existed. No AI calls."""
    ids = [r["id"] for r in db.q("SELECT id FROM jobs WHERE status='done' AND resume IS NOT NULL")]
    for i in ids:
        pool.submit(_refit, i)
    return {"queued": len(ids)}


class Ids(BaseModel):
    ids: list[int] = []


@app.post("/api/estimate")
def estimate(b: Ids):
    """How many AI calls a Build of these jobs would use (jobs sharing no skill with your resume are skipped free)."""
    profile = db.kv_get("profile") or {}
    rows = [r for i in b.ids for r in db.q("SELECT title,description FROM jobs WHERE id=?", i)]
    n = sum(1 for r in rows if resume.relevance(profile, r))
    return {"ai_calls": n, "skipped": len(rows) - n}


class SavedSearch(BaseModel):
    terms: list[str] = []
    location: str = ""
    country: str = "in"
    sites: list[str] = ["indeed", "linkedin", "naukri"]
    results: int = 20
    hours_old: int = 24
    remote: bool = False
    max_resumes: int = 5       # hard cap of AI calls per daily run
    hide_senior: bool = True   # do not spend AI calls on jobs above your level
    min_salary_lakh: float = 0  # skip jobs whose KNOWN salary is below this (lakh INR/yr); unknown salary is kept
    hide_restricted: bool = False  # skip US-only / clearance / no-sponsorship jobs


@app.get("/api/saved-search")
def get_saved_search():
    return db.kv_get("saved_search") or {}


@app.put("/api/saved-search")
def put_saved_search(b: SavedSearch):
    db.kv_set("saved_search", b.model_dump())
    return b


@app.get("/api/backup")
def backup():
    """Zip of the database (a consistent copy) and the jobs/ folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if db.DB.exists():
            tmp = db.DB.with_suffix(".bak")
            src, dst = sqlite3.connect(db.DB), sqlite3.connect(tmp)
            src.backup(dst)
            dst.close(); src.close()
            z.write(tmp, "resumeit.db")
            tmp.unlink()
        if export.JOBS_DIR.exists():
            for f in export.JOBS_DIR.rglob("*"):
                if f.is_file():
                    z.write(f, "jobs/" + f.relative_to(export.JOBS_DIR).as_posix())
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="resumeit_backup_{datetime.now():%Y%m%d_%H%M}.zip"'})


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
