"""India-or-remote rule, level rule and skill rule for the job list."""
import pytest
from fastapi.testclient import TestClient

from app import db, details, flags, main, mcp_server
from conftest import PROFILE

client = TestClient(main.app)


@pytest.mark.parametrize("loc,site,region", [
    ("Pune, Maharashtra, India", "indeed", "india"), ("Bengaluru, KA", "linkedin", "india"), ("Gurugram, HR, IN", "indeed", "india"),
    ("Goa", "indeed", "india"), ("Delhi NCR", "naukri", "india"), ("", "naukri", "india"),
    ("New York, NY, US", "indeed", "abroad"), ("London, UK", "linkedin", "abroad"), ("Indiana, USA", "indeed", "abroad"),
    ("Panama City", "indeed", "abroad"), ("Remote", "linkedin", "unknown"), ("", "indeed", "unknown")])
def test_region(loc, site, region):
    assert details.region(loc, site) == region


def test_abroad_must_be_remote_and_not_us_citizen_only():
    ok = details.location_ok
    assert ok("india", "onsite", {}) and ok("india", None, {"us_only": True})
    assert not ok("abroad", "onsite", {}) and not ok("abroad", None, {})
    assert ok("abroad", "remote", {})
    assert not ok("abroad", "remote", {"us_only": True}) and not ok("abroad", "remote", {"clearance": True})
    assert ok("unknown", None, {}) and not ok("unknown", None, {"us_only": True})


def _job(url, loc, desc, site="indeed"):
    return db.x("INSERT INTO jobs(url,site,title,company,location,description) VALUES(?,?,?,?,?,?)", url, site, "Dev", "Foo", loc, desc)


def test_job_list_rules():
    db.kv_set("profile", PROFILE)  # 3 years of experience, skills Python + SQL
    pune = _job("u/1", "Pune, India", "Python developer. 2+ years of experience")
    ny_site = _job("u/2", "New York, NY, US", "Python developer on-site in the office")
    ny_remote = _job("u/3", "New York, NY, US", "Python developer. Fully remote.")
    ny_remote_us = _job("u/4", "Austin, TX, US", "Python developer. Remote. Must be a US citizen.")
    lead = _job("u/5", "Mumbai, India", "Python architect. 20+ years of experience required")
    cook = _job("u/6", "Pune, India", "Chef who bakes bread")
    main._score_fit(); main._score_req(); main._score_info()

    def ids(**q):
        return {x["id"] for x in client.get("/api/jobs", params=q).json()}
    assert ids() == {pune, ny_site, ny_remote, ny_remote_us, lead, cook}  # API default shows everything
    assert ids(india_only=True) == {pune, ny_remote, lead, cook}
    assert ids(hide_senior=True) == {pune, ny_site, ny_remote, ny_remote_us, cook}
    assert ids(only_my_skills=True) == {pune, ny_site, ny_remote, ny_remote_us, lead}
    assert ids(india_only=True, hide_senior=True, only_my_skills=True) == {pune, ny_remote}
    assert {r["id"] for r in mcp_server.list_jobs()} == {pune, ny_remote}  # MCP defaults follow your rules
    row = {x["id"]: x for x in client.get("/api/jobs").json()}
    assert row[lead]["senior"] and "20+ yrs" in row[lead]["flags"] and row[ny_site]["region"] == "abroad"


def test_one_year_slack_on_experience():
    assert not flags.is_senior({"min_years": 4}, 3) and flags.is_senior({"min_years": 5}, 3)


def test_ui_defaults_follow_your_rules():
    html = open(main.Path(__file__).resolve().parent.parent / "static" / "index.html", encoding="utf-8").read()
    for i in ("hsr", "fres", "fskill"):
        assert f'id="{i}" checked' in html
    assert "hideSr=true" in html and "US-only" not in html
