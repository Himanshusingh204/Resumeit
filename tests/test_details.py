"""Salary, work mode, type, age and warnings read before applying."""
import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient

from app import db, details, main, mcp_server, scrape
from conftest import PROFILE

client = TestClient(main.app)


@pytest.mark.parametrize("text,shown", [
    ("CTC: ₹12-18 LPA", "₹12–18 LPA"), ("15-25 LPA", "₹15–25 LPA"), ("Rs. 8 to 12 lakhs", "₹8–12 LPA"),
    ("Salary $120,000 - $150,000 per year", "$120k–$150k/yr"), ("$90k - $110k", "$90k–$110k/yr"),
    ("₹50,000 - ₹70,000 per month", "₹6–8.4 LPA"), ("$50 - $70 per hour", "$104k–$146k/yr")])
def test_salary_read_from_text(text, shown):
    assert details.fmt_salary(*details.salary_from_text(text)) == shown


@pytest.mark.parametrize("text", ["2-4 years of experience", "10-20 people team", "Batch 2020-2024", "competitive salary", "5-10 openings"])
def test_numbers_that_are_not_money_are_ignored(text):
    assert details.salary_from_text(text) is None


def test_board_salary_wins_and_nan_is_safe():
    d = details.details("Pays 5-6 LPA", {"min": 1200000, "max": 1800000, "interval": "yearly", "cur": "INR"})
    assert d["salary"] == "₹12–18 LPA" and d["salary_src"] == "board" and d["salary_lakh"] == 18.0
    assert details.details("nothing", {"min": float("nan"), "max": float("nan")})["salary"] is None


def test_usd_is_converted_for_the_min_salary_filter():
    assert details.details("$120,000 - $150,000 a year")["salary_lakh"] == round(150000 * 83 / 1e5, 1)


def test_mode_type_and_warning_tags():
    d = details.details("Hybrid position. Night shift. Service agreement of 24 months. Unpaid trial.", title="Intern")
    assert d["mode"] == "hybrid" and d["type"] == "internship" and {"night shift", "bond", "unpaid"} <= set(d["tags"])
    assert details.details("Fully remote role")["mode"] == "remote"
    assert details.details("Work from office, Pune")["mode"] == "onsite"
    assert details.details("Plain job")["mode"] is None
    assert details.details("x", {"remote": True})["mode"] == "remote"
    assert details.details("x", {"job_type": "fulltime"})["type"] == "fulltime"


def test_age_days():
    assert details.age_days((date.today() - timedelta(days=3)).isoformat()) == 3
    assert details.age_days(None) is None and details.age_days("garbage") is None


def test_scraper_stores_board_fields_and_info_is_scored(monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [{
        "job_url": "https://j/1", "description": "Python role. Hybrid.", "title": "Dev", "company": "Foo", "site": "indeed",
        "min_amount": 1000000.0, "max_amount": float("nan"), "currency": "INR", "interval": "yearly", "job_type": "fulltime",
        "is_remote": False, "date_posted": date.today().isoformat()}])
    ids = scrape.scrape("python", sites=["indeed"])
    main._score_info()
    j = client.get("/api/jobs").json()[0]
    assert ids and j["salary"] == "₹10 LPA" and j["mode"] == "hybrid" and j["age_days"] == 0 and j["jtype"] == "fulltime"


def _job(url, desc, title="Dev", posted=None):
    return db.x("INSERT INTO jobs(url,site,title,company,description,posted) VALUES(?,?,?,?,?,?)", url, "m", title, "Foo", desc, posted)


def test_jobs_filters():
    db.kv_set("profile", PROFILE)
    today = date.today().isoformat()
    a = _job("u/a", "Python. ₹20-30 LPA. Remote.", posted=today)
    b = _job("u/b", "Python. ₹6-8 LPA. Hybrid. Night shift.", posted=(date.today() - timedelta(days=9)).isoformat())
    c = _job("u/c", "Python. US citizen only. No sponsorship.", title="Contract Dev", posted=today)
    main._score_req(); main._score_info()

    def ids(**q):
        return {x["id"] for x in client.get("/api/jobs", params=q).json()}
    assert ids() == {a, b, c}
    assert ids(min_salary=10) == {a}                    # unknown salary is hidden while filtering by salary
    assert ids(has_salary=True) == {a, b}
    assert ids(mode="hybrid") == {b} and ids(mode="remote") == {a}
    assert ids(type="contract") == {c}
    assert ids(max_age_days=7) == {a, c}
    assert ids(hide_restricted=True) == {a, b}
    row = {x["id"]: x for x in client.get("/api/jobs").json()}
    assert row[b]["tags"] == ["night shift"] and row[a]["salary"] == "₹20–30 LPA"


def test_mcp_list_jobs_shows_salary_and_filters(monkeypatch):
    db.kv_set("profile", PROFILE)
    a = _job("u/a", "Python. ₹20-30 LPA. Remote.")
    _job("u/b", "Python. no pay info")
    main._score_req(); main._score_info()
    rows = mcp_server.list_jobs(min_salary_lakh=10, work_mode="remote")
    assert [r["id"] for r in rows] == [a] and rows[0]["salary"] == "₹20–30 LPA" and rows[0]["work_mode"] == "remote"
    assert len(mcp_server.list_jobs()) == 2


def test_daily_scan_skips_known_low_salary_but_keeps_unknown(monkeypatch):
    from app import ai, scan
    from test_plan import tailored
    db.kv_set("profile", PROFILE)
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [
        {"job_url": "u/low", "description": "Python ₹3-4 LPA", "title": "Low", "company": "A", "site": "indeed"},
        {"job_url": "u/none", "description": "Python", "title": "Unknown", "company": "B", "site": "indeed"},
        {"job_url": "u/high", "description": "Python ₹20-30 LPA", "title": "High", "company": "C", "site": "indeed"}])
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    db.kv_set("saved_search", {"terms": ["python"], "sites": ["indeed"], "max_resumes": 5, "min_salary_lakh": 10})
    out = scan.run()
    assert sorted(b["title"] for b in out["built"]) == ["High", "Unknown"] and out["skipped_filters"] == 1
