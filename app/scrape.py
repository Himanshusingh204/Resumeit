"""JobSpy wrapper (same library/approach as Linkit's JobSpyAdapter).

Each (term, site) is scraped on its own: a blocked or broken board loses only itself, and the
failure is written to the issues log instead of aborting the whole run."""
import json

from . import db

COUNTRY = {"in": "India", "us": "usa", "gb": "uk", "ca": "canada", "au": "australia", "de": "germany",
           "ae": "united arab emirates", "sg": "singapore"}


def _jobspy(**kw):
    from jobspy import scrape_jobs  # lazy: heavy import (pandas); tests replace this function
    return scrape_jobs(**kw).to_dict("records")


def _s(v):
    if v is None or (isinstance(v, float) and v != v):  # pandas NaN
        return None
    return str(v).strip() or None


def scrape(term, location="", country="in", sites=("indeed", "linkedin", "naukri"), results=20,
           hours_old=168, remote=False):
    """Returns ids of newly stored jobs. Never raises for a single bad site."""
    new = []
    where = location or (None if remote else COUNTRY.get(country, "India").title())  # boards rank far better with a place
    for site in sites:
        if site == "naukri" and (country != "in" or remote):
            continue  # Naukri only covers India
        ref = f"{term} @ {site}"
        try:
            rows = _jobspy(site_name=[site], search_term=term, location=where, is_remote=remote, google_search_term=f"{term} jobs {where or 'remote'}",
                           results_wanted=results, hours_old=hours_old,
                           country_indeed=COUNTRY.get(country, "india"), description_format="markdown",
                           linkedin_fetch_description=True, verbose=0)
        except Exception as e:
            db.issue("scrape_failed", ref, e)
            continue
        if not rows:
            db.issue("no_results", ref, "board returned nothing (blocked, or no matching jobs)")
            continue
        skipped = 0
        for r in rows:
            url, desc = _s(r.get("job_url")), _s(r.get("description"))
            if not url or not desc:
                skipped += 1  # no JD text = nothing to tailor against
                continue
            if db.q("SELECT 1 FROM jobs WHERE url=?", url) or db.q(
                    "SELECT 1 FROM jobs WHERE lower(title)=lower(?) AND lower(company)=lower(?)", _s(r.get("title")), _s(r.get("company"))):
                continue  # same posting already stored (same URL, or same title+company from another board)
            board = {"min": r.get("min_amount"), "max": r.get("max_amount"), "cur": _s(r.get("currency")),
                     "interval": _s(r.get("interval")), "job_type": _s(r.get("job_type")), "remote": r.get("is_remote") is True}
            board = {k: (None if isinstance(v, float) and v != v else v) for k, v in board.items()}  # NaN is not JSON
            new.append(db.x("INSERT INTO jobs(url,site,title,company,location,posted,description,board) VALUES(?,?,?,?,?,?,?,?)",
                            url, _s(r.get("site")) or site, _s(r.get("title")), _s(r.get("company")),
                            _s(r.get("location")), _s(r.get("date_posted")), desc[:30000], json.dumps(board, default=str)))
        if skipped:
            db.issue("incomplete_jobs", ref, f"{skipped} job(s) skipped: missing URL or description")
    return new
