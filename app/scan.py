"""Daily scan (plan E): find new jobs and prepare resumes. NEVER applies or submits anything (rule 1.1).

Run by hand or from Windows Task Scheduler:  .venv\\Scripts\\python -m app.scan
Limits come from the saved search (Settings -> PUT /api/saved-search): max_resumes = AI calls per run.
Create the file data/STOP to make a running scan stop before its next resume.
"""
import json, logging
from datetime import datetime

from . import db, details, export, flags, main, resume, scrape


def stop_file():
    return db.DB.parent / "STOP"


def run(max_resumes=None):
    cfg = db.kv_get("saved_search") or {}
    out = {"new": 0, "built": [], "skipped_senior": 0, "skipped_filters": 0, "stopped": False, "error": None}
    profile = db.kv_get("profile")
    if resume.profile_problem(profile):
        out["error"] = "no usable resume profile"
        return _write(out)
    cap = cfg.get("max_resumes", 5) if max_resumes is None else max_resumes
    try:
        terms = [t for t in cfg.get("terms", []) if t.strip()] or resume.extract_terms(profile)
    except Exception as e:
        db.issue("keywords_failed", "daily scan", e)
        out["error"] = f"no search terms: {e}"
        return _write(out)
    ids = []
    for t in terms:
        try:
            ids += scrape.scrape(t, cfg.get("location", ""), cfg.get("country", "in"),
                                 cfg.get("sites") or ["indeed", "linkedin", "naukri"], cfg.get("results", 20),
                                 cfg.get("hours_old", 24), cfg.get("remote", False))
        except Exception as e:
            db.issue("scrape_failed", f"daily scan: {t}", e)
    out["new"] = len(ids)
    main._score_fit(ids)
    main._score_req()
    main._score_info()
    years = flags.profile_years(profile)
    picks = []
    for jid in ids:
        j = db.q("SELECT id,fit,req,info,location,site FROM jobs WHERE id=?", jid)[0]
        info = details.parse(j["info"])
        if cfg.get("min_salary_lakh") and info.get("salary_lakh") is not None and info["salary_lakh"] < cfg["min_salary_lakh"]:
            out["skipped_filters"] += 1  # only when a salary is known and too low; unknown salary is kept
            continue
        if cfg.get("india_only", True) and not details.location_ok(
                details.region(j["location"], j["site"]), info.get("mode"), flags.parse(j["req"])):
            out["skipped_filters"] += 1
            continue
        if not json.loads(j["fit"] or "[]"):
            continue  # none of your skills: no AI call
        if cfg.get("hide_senior", True) and flags.is_senior(flags.parse(j["req"]), years):
            out["skipped_senior"] += 1
            continue
        picks.append((len(json.loads(j["fit"])), jid))
    for _, jid in sorted(picks, reverse=True)[:max(cap, 0)]:  # best skill overlap first, never more than the cap
        if stop_file().exists():
            out["stopped"] = True
            break
        main._tailor(jid)
        j = db.q("SELECT id,title,company,status,flag FROM jobs WHERE id=?", jid)[0]
        out["built"].append({k: j[k] for k in ("id", "title", "company", "status", "flag")})
    return _write(out)


def _write(out):
    """Short summary appended to jobs/_daily.md so you can see what the scan did."""
    export.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"\n## {datetime.now():%Y-%m-%d %H:%M}\n", f"- New jobs found: {out['new']}\n",
             f"- Skipped (above your level): {out['skipped_senior']}\n"]
    if out["error"]:
        lines.append(f"- Problem: {out['error']}\n")
    if out["stopped"]:
        lines.append("- Stopped early (data/STOP exists)\n")
    for b in out["built"]:
        lines.append(f"- Resume {b['status']}: {b['title']} @ {b['company']} (job {b['id']})" + (f" - {b['flag']}" if b["flag"] else "") + "\n")
    lines.append("- Nothing was applied to. Open each job and apply yourself.\n")
    with open(export.JOBS_DIR / "_daily.md", "a", encoding="utf-8") as f:
        f.writelines(lines)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("resumeit").info("scan result %s", json.dumps(run(), indent=2))
