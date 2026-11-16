"""MCP server: lets Claude (Desktop / Code) drive Resumeit with tools.  Run:  .venv\\Scripts\\python -m app.mcp_server

It works on the same local database as the web app (no web server needed).
By design there is NO tool to apply to a job or to set Applied/Interview/Rejected/Offer: only you do that,
in the web app (plan rule 1.1). Building a resume uses your AI provider and is limited to what you ask for.
"""
import json
import threading

from mcp.server.mcpserver import MCPServer

from . import cover, db, diff, export, flags, main, resume

mcp = MCPServer("resumeit")
_search_lock = threading.Lock()


def _brief(j):
    req = flags.parse(j.get("req"))
    return {"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"], "site": j["site"],
            "url": j["url"], "status": j["status"], "tracking": j["track"], "your_skills_in_job": json.loads(j["fit"] or "[]"),
            "flags": flags.labels(req), "needs_manual_trim": j.get("flag")}


def _job(job_id):
    r = db.q("SELECT * FROM jobs WHERE id=?", job_id)
    if not r:
        raise ValueError(f"no job with id {job_id}")
    return r[0]


@mcp.tool()
def get_profile() -> dict:
    """Your parsed base resume (the only source of facts for tailored resumes)."""
    p = db.kv_get("profile")
    if resume.profile_problem(p):
        raise ValueError("No usable resume uploaded yet: upload it in the Resumeit web app first")
    return p


@mcp.tool()
def list_jobs(status: str = "", hide_senior: bool = False, tracking: str = "open", limit: int = 30) -> list[dict]:
    """List jobs, newest first. status: new|queued|tailoring|done|error|skipped or empty for all.
    tracking: 'open' (hides Applied/Rejected), '' for everything, or Applied|Interview|Rejected|Offer.
    hide_senior hides jobs that need more years than your resume shows."""
    rows = main.jobs(hide_senior=hide_senior, track=tracking)
    full = {r["id"]: r for r in db.q("SELECT id,req FROM jobs")}
    out = []
    for j in rows:
        if status and j["status"] != status:
            continue
        j["req"] = full[j["id"]]["req"]
        out.append(_brief(j))
    return out[:max(1, min(limit, 200))]


@mcp.tool()
def get_job(job_id: int) -> dict:
    """Full job: description, match (score, matched, gaps) and the tailored resume if built."""
    j = _job(job_id)
    return {**_brief(j), "description": j["description"], "match": json.loads(j["match"] or "null"),
            "resume": json.loads(j["resume"] or "null"), "error": j["error"], "cover_letter": j["cover"],
            "folder": str(export.folder(j)) if j["resume"] else None}


@mcp.tool()
def search_jobs(terms: list[str], location: str = "", country: str = "in",
                sites: list[str] = ["indeed", "linkedin", "naukri"], results: int = 20, hours_old: int = 168) -> dict:
    """Scrape job boards for the given search terms and store new jobs. Uses NO AI calls. Slow (minutes)."""
    if main.state["scraping"] or not _search_lock.acquire(blocking=False):
        raise ValueError("A search is already running")
    try:
        main._run_search(main.Search(terms=terms, location=location, country=country, sites=sites,
                                     results=results, hours_old=hours_old, auto=False))
        return {"message": main.state["msg"]}
    finally:
        _search_lock.release()


@mcp.tool()
def estimate_ai_calls(job_ids: list[int]) -> dict:
    """How many AI calls building these jobs would use. Jobs sharing no skill with your resume cost nothing."""
    return main.estimate(main.Ids(ids=job_ids))


@mcp.tool()
def build_resume(job_id: int) -> dict:
    """Build a tailored one-page resume for ONE job (one AI call). Truth guard rejects invented facts."""
    _job(job_id)
    if resume.profile_problem(db.kv_get("profile")):
        raise ValueError("No usable resume uploaded yet")
    main._tailor(job_id)
    j = _job(job_id)
    if j["status"] != "done":
        raise ValueError(f"build failed: {j['error']}")
    return {"status": "done", "match": json.loads(j["match"] or "{}"), "folder": str(export.folder(j)),
            "needs_manual_trim": j["flag"]}


@mcp.tool()
def what_changed(job_id: int) -> dict:
    """Compare your base resume with the tailored one: skills kept/removed, bullets same/reworded/removed."""
    j = _job(job_id)
    if not j["resume"]:
        raise ValueError("Resume not built yet")
    return diff.diff(db.kv_get("profile") or {}, json.loads(j["resume"]))


@mcp.tool()
def write_cover_letter(job_id: int) -> str:
    """Optional cover letter for one job (one AI call, facts from your resume only). Saved in the job folder."""
    j = _job(job_id)
    text = cover.generate(db.kv_get("profile"), j)
    db.x("UPDATE jobs SET cover=? WHERE id=?", text, job_id)
    d = export.folder(j)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cover_letter.md").write_text(text, encoding="utf-8")
    return text


@mcp.tool()
def list_issues(limit: int = 30) -> list[dict]:
    """Things that failed, were skipped or need manual attention."""
    return db.q("SELECT ts,kind,ref,msg FROM issues ORDER BY id DESC LIMIT ?", max(1, min(limit, 200)))


@mcp.tool()
def run_daily_scan(max_resumes: int = 3) -> dict:
    """Find new jobs from the saved search and build resumes for the best few (capped by max_resumes). Never applies."""
    from . import scan
    return scan.run(max_resumes)


if __name__ == "__main__":
    mcp.run()
