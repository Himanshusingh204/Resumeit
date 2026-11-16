"""Tests for PLAN.md items A-H and the MCP server."""
import asyncio, copy, io, json, zipfile

import pytest
from fastapi.testclient import TestClient

from app import ai, cover, db, diff, export, flags, guard, main, mcp_server, resume, scan, scrape
from conftest import PROFILE

client = TestClient(main.app)
REAL_PDF, REAL_PAGES = resume.render_pdf, resume.pdf_pages  # captured before the autouse fixtures stub them

BIG = {"name": "Rahul Sen", "email": "r@x.com", "skills": ["Python", "SQL", "Docker"],
       "experience": [{"company": "Acme", "title": "Dev", "start": "2018", "end": "2024",
                       "bullets": ["Built REST APIs serving 500 users", "Cut cost by 20%", "Wrote tests"]}],
       "projects": [{"name": "Tool", "tech": "Python, SQL", "bullets": ["Made a CLI"]}],
       "education": [{"school": "IIT", "degree": "BTech", "year": "2018"}], "certifications": ["AWS SAA"]}


@pytest.fixture
def profile():
    db.kv_set("profile", PROFILE)


def tailored(**over):
    r = copy.deepcopy(PROFILE)
    r.update(over)
    return {"resume": r, "match": {"score": 70, "matched": ["Python"], "gaps": []}}


# ------------------------------------------------------------------ A. truth guard
@pytest.mark.parametrize("mutate,word", [
    (lambda r: r["skills"].append("Kubernetes"), "skill"),
    (lambda r: r["experience"][0].update(company="Google"), "company"),
    (lambda r: r["experience"][0].update(title="CTO"), "title"),
    (lambda r: r["experience"][0].update(start="2015"), "start"),
    (lambda r: r["experience"][0]["bullets"].append("Made it 40% faster"), "numbers"),
    (lambda r: r.update(summary="10 years of experience"), "numbers"),
    (lambda r: r["education"].append({"school": "MIT", "degree": "PhD", "year": "2020"}), "education"),
    (lambda r: r["certifications"].append("PMP"), "certification"),
    (lambda r: r["projects"].append({"name": "Fake", "tech": "Go", "bullets": []}), "project"),
])
def test_truth_guard_catches_invented_facts(mutate, word):
    r = copy.deepcopy(BIG)
    mutate(r)
    assert any(word in p for p in guard.truth_problems(r, BIG))


def test_truth_guard_allows_reorder_drop_and_reword():
    r = copy.deepcopy(BIG)
    r["skills"] = ["SQL", "Python"]
    r["experience"][0]["bullets"] = ["Wrote tests", "Built REST APIs for 500 users"]
    r["summary"] = "Python developer"
    r["projects"] = []
    assert guard.truth_problems(r, BIG) == []
    assert guard.truth_problems(BIG, BIG) == []


def test_tailor_rejects_invented_skill_and_logs_it(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored(skills=["Python", "Kubernetes"]))
    jid = job_row()
    main._tailor(jid)
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "error" and "truth guard" in j["error"] and "Kubernetes" in j["error"] and j["resume"] is None
    assert client.get("/api/issues").json()[0]["kind"] == "tailor_failed"


# ------------------------------------------------------------------ A. one page
def test_ai_prompt_states_page_limits():
    assert "ONE page" in resume.RULES and "3 bullets" in resume.RULES


def test_fit_one_page_shrinks_type_before_removing_content(monkeypatch):
    seen = []

    def pdf(r, t="classic", scale=1.0):
        seen.append(scale)
        return b"%PDF" + str(scale).encode()
    monkeypatch.setattr(resume, "render_pdf", pdf)
    monkeypatch.setattr(resume, "pdf_pages", lambda b: 1 if float(b[4:]) <= 0.95 else 2)
    r, _, pages, flag = resume.fit_one_page(BIG, "classic")
    assert pages == 1 and flag is None and r == BIG  # nothing removed
    assert min(seen) >= 9 / 10.5 - 1e-6              # never below 9pt


def test_fit_one_page_trims_least_relevant_then_flags_if_impossible(monkeypatch):
    monkeypatch.setattr(resume, "render_pdf", lambda r, t="classic", scale=1.0: b"x")
    # fits only once the project and a bullet are gone
    monkeypatch.setattr(resume, "pdf_pages", lambda b: 1)
    calls = {"n": 0}

    def pages(b):
        calls["n"] += 1
        return 2 if calls["n"] < 8 else 1
    monkeypatch.setattr(resume, "pdf_pages", pages)
    r, _, p, flag = resume.fit_one_page(BIG, "classic")
    assert p == 1 and flag is None and len(r["projects"]) == 0 or len(r["experience"][0]["bullets"]) < 3
    assert guard.truth_problems(r, BIG) == []  # trimming only removes

    monkeypatch.setattr(resume, "pdf_pages", lambda b: 2)
    r, _, p, flag = resume.fit_one_page(BIG, "classic")
    assert p == 2 and "Manual trim needed" in flag


def test_trim_never_adds_and_terminates():
    r = copy.deepcopy(BIG)
    n = 0
    while resume._trim_one(r):
        n += 1
        assert n < 50
    assert guard.truth_problems(r, BIG) == []


def test_unfittable_resume_is_flagged_on_job_and_in_issues(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    monkeypatch.setattr(resume, "pdf_pages", lambda b: 2)
    jid = job_row()
    main._tailor(jid)
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "done" and "Manual trim" in j["flag"]
    assert "manual_trim" in [i["kind"] for i in client.get("/api/issues").json()]


def test_refit_fixes_old_two_page_resumes_without_ai(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete", lambda *a: pytest.fail("no AI call allowed"))
    monkeypatch.setattr(ai, "complete_json", lambda *a: pytest.fail("no AI call allowed"))
    jid = job_row()
    db.x("UPDATE jobs SET status='done', resume=? WHERE id=?", json.dumps(BIG), jid)
    main._refit(jid)
    assert client.get(f"/api/jobs/{jid}").json()["flag"] is None
    assert (export.folder(db.q("SELECT * FROM jobs WHERE id=?", jid)[0]) / "resume.pdf").exists()


@pytest.mark.skipif(not resume._browser(), reason="needs Edge/Chrome")
def test_real_pdf_of_a_long_profile_is_exactly_one_page(monkeypatch):
    monkeypatch.setattr(resume, "render_pdf", REAL_PDF)  # the real renderer, not the conftest stubs
    monkeypatch.setattr(resume, "pdf_pages", REAL_PAGES)
    long = copy.deepcopy(BIG)
    long["experience"] = [{"company": f"Co{i}", "title": "Engineer", "start": "2010", "end": "2012",
                           "bullets": [f"Did thing {j} with Python and SQL across the platform for customers" for j in range(6)]}
                          for i in range(6)]
    long["projects"] = [{"name": f"P{i}", "tech": "Python", "bullets": ["Built it " * 12] * 4} for i in range(6)]
    for t in resume.TEMPLATES:
        r, pdf, pages, flag = resume.fit_one_page(long, t)
        assert pages == 1 and flag is None and resume.pdf_pages(pdf) == 1
        assert guard.truth_problems(r, long) == []


# ------------------------------------------------------------------ B. tracker
def test_tracker_marks_filters_and_validates(job_row):
    a, b = job_row("https://j/a"), job_row("https://j/b")
    assert client.post(f"/api/jobs/{a}/track", json={"status": "Applied"}).status_code == 200
    assert client.post(f"/api/jobs/{b}/track", json={"status": "Hired!"}).status_code == 400
    j = {x["id"]: x for x in client.get("/api/jobs").json()}
    assert j[a]["track"] == "Applied" and j[a]["tracked_at"] and j[b]["track"] is None
    assert [x["id"] for x in client.get("/api/jobs?track=open").json()] == [b]
    assert [x["id"] for x in client.get("/api/jobs?track=Applied").json()] == [a]
    client.post(f"/api/jobs/{a}/track", json={"status": ""})
    assert client.get(f"/api/jobs/{a}").json()["track"] is None


def test_app_never_sets_tracking_by_itself(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    jid = job_row()
    main._queue([jid])[0].result()
    main._tailor(jid)
    main.tailor_pending()
    main.refit_all()
    assert client.get(f"/api/jobs/{jid}").json()["track"] is None
    import inspect
    for mod in (scan, mcp_server, main):  # only the one explicit endpoint writes it
        src = inspect.getsource(mod)
        assert src.count("SET track") == (1 if mod is main else 0)


# ------------------------------------------------------------------ C. seniority / location flags
@pytest.mark.parametrize("text,years", [
    ("5+ years of experience in Python", 5), ("Minimum 3 years' experience", 3), ("8-10 years experience", 8),
    ("experience: 4 yrs", 4), ("at least 6 years of relevant experience", 6), ("2 to 4 years of experience", 2),
    ("0 years experience needed", 0)])
def test_years_required_written_many_ways(text, years):
    assert flags.requirements(text)["min_years"] == years


def test_zero_or_no_years_does_not_flag_senior():
    assert not flags.is_senior(flags.requirements("Freshers welcome, 0 years experience"), 3)
    assert not flags.is_senior(flags.requirements("Great team, 10 years in business"), 1)  # not experience
    assert flags.requirements("Great team")["min_years"] is None


def test_location_flags():
    r = flags.requirements("Must be a US citizen. Security clearance required. No visa sponsorship available.")
    assert r["us_only"] and r["clearance"] and r["no_sponsorship"]
    assert flags.labels(r) == ["clearance", "no sponsorship"]  # no "US only" badge: location is judged by details.region
    assert not any(flags.requirements("Remote, India").values())


def test_profile_years_and_senior_filter(job_row):
    db.kv_set("profile", PROFILE)  # 2021-2024 = 3 years
    assert flags.profile_years(PROFILE) == 3
    a = job_row("https://j/a", desc="Python. 7+ years of experience")
    b = job_row("https://j/b", desc="Python. 2+ years of experience")
    main._score_req()
    allj = {x["id"]: x for x in client.get("/api/jobs").json()}
    assert allj[a]["senior"] and "7+ yrs" in allj[a]["flags"] and not allj[b]["senior"]
    assert [x["id"] for x in client.get("/api/jobs?hide_senior=true").json()] == [b]
    assert client.get(f"/api/jobs/{a}").status_code == 200  # flagged jobs are never deleted


# ------------------------------------------------------------------ D. what changed
def test_diff_highlights_removed_reworded_and_same():
    r = copy.deepcopy(BIG)
    r["skills"] = ["SQL", "Python"]
    r["experience"][0]["bullets"] = ["Wrote tests", "Built REST APIs serving 500 users daily"]
    r["projects"] = []
    d = diff.diff(BIG, r)
    assert d["skills"]["removed"] == ["Docker"] and d["skills"]["added"] == []
    states = {b["text"]: b["state"] for b in d["experience"][0]["bullets"]}
    assert states == {"Wrote tests": "same", "Built REST APIs serving 500 users daily": "reworded"}
    assert d["experience"][0]["removed_bullets"] == ["Cut cost by 20%"]
    assert d["projects"][0]["dropped"] is True


def test_diff_endpoint(profile, job_row):
    jid = job_row()
    assert client.get(f"/api/jobs/{jid}/diff").status_code == 409
    db.x("UPDATE jobs SET resume=? WHERE id=?", json.dumps(PROFILE), jid)
    assert client.get(f"/api/jobs/{jid}/diff").json()["skills"]["removed"] == []


# ------------------------------------------------------------------ E. daily scan
def _rows(n):
    return lambda **kw: [{"job_url": f"https://j/{i}", "description": "Python and SQL work", "title": f"Dev {i}",
                          "company": "Foo", "site": "indeed"} for i in range(n)]


def test_daily_scan_respects_resume_cap_and_skips_senior_and_unrelated(profile, monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [
        *[{"job_url": f"https://j/{i}", "description": "Python SQL", "title": f"Dev {i}", "company": "Foo", "site": "indeed"} for i in range(6)],
        {"job_url": "https://j/sen", "description": "Python 9+ years of experience", "title": "Sr", "company": "Foo", "site": "indeed"},
        {"job_url": "https://j/cook", "description": "Cooking and baking", "title": "Chef", "company": "Foo", "site": "indeed"}])
    built = []
    monkeypatch.setattr(ai, "complete_json", lambda s, u: built.append(1) or tailored())
    db.kv_set("saved_search", {"terms": ["python"], "sites": ["indeed"], "max_resumes": 3, "hide_senior": True})
    out = scan.run()
    assert out["new"] == 8 and len(built) == 3 and len(out["built"]) == 3 and out["skipped_senior"] == 1
    statuses = {j["title"]: j["status"] for j in client.get("/api/jobs").json()}
    assert statuses["Sr"] == "new" and statuses["Chef"] == "new"
    daily = (export.JOBS_DIR / "_daily.md").read_text(encoding="utf-8")
    assert "Nothing was applied to" in daily and "New jobs found: 8" in daily
    assert all(j["track"] is None for j in client.get("/api/jobs").json())


def test_daily_scan_stop_file_and_no_profile(profile, monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", _rows(3))
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    db.kv_set("saved_search", {"terms": ["python"], "sites": ["indeed"], "max_resumes": 3})
    scan.stop_file().write_text("x")
    out = scan.run()
    assert out["stopped"] and out["built"] == []
    scan.stop_file().unlink()
    db.x("DELETE FROM kv WHERE k='profile'")
    assert "no usable" in scan.run()["error"]


def test_scan_code_has_no_apply_path():
    import inspect
    src = inspect.getsource(scan).lower()
    for word in ("easy_apply", "easy apply", "smtplib", "sendmail", "click(", "requests.post", "httpx.post"):
        assert word not in src.replace("nothing was applied to", "")


def test_saved_search_roundtrip():
    assert client.get("/api/saved-search").json() == {}
    client.put("/api/saved-search", json={"terms": ["x"], "max_resumes": 2})
    assert client.get("/api/saved-search").json()["max_resumes"] == 2


# ------------------------------------------------------------------ F. cover letter
LETTER = "Dear Hiring Team, I build REST APIs with Python and SQL at Acme and would enjoy bringing that to Foo. Regards, Rahul Sen"


def test_cover_letter_is_opt_in_saved_and_guarded(profile, monkeypatch, job_row):
    jid = job_row()
    monkeypatch.setattr(ai, "complete", lambda s, u: LETTER)
    assert client.get(f"/api/jobs/{jid}").json()["cover"] is None  # nothing generated by itself
    assert client.post(f"/api/jobs/{jid}/cover-letter").json()["cover"] == LETTER
    assert (export.folder(db.q("SELECT * FROM jobs WHERE id=?", jid)[0]) / "cover_letter.md").exists()
    monkeypatch.setattr(ai, "complete", lambda s, u: LETTER + " I raised revenue 300% last year.")
    r = client.post(f"/api/jobs/{jid}/cover-letter")
    assert r.status_code == 422 and "300" in r.json()["detail"]


# ------------------------------------------------------------------ G. model option + estimate
def test_claude_model_env_is_passed_to_cli(monkeypatch):
    import subprocess
    got = {}
    monkeypatch.setattr(ai, "_claude_bin", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: got.update(cmd=cmd) or type("R", (), {"returncode": 0, "stdout": "{}"})())
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    ai._cli("s", "u")
    assert got["cmd"][got["cmd"].index("--model") + 1] == "claude-haiku-5-5"
    monkeypatch.delenv("CLAUDE_MODEL")
    ai._cli("s", "u")
    assert "--model" not in got["cmd"]


def test_estimate_counts_only_jobs_that_cost_an_ai_call(profile, job_row):
    a, b = job_row("https://j/a", desc="Python"), job_row("https://j/b", desc="Cooking")
    assert client.post("/api/estimate", json={"ids": [a, b]}).json() == {"ai_calls": 1, "skipped": 1}


# ------------------------------------------------------------------ H. backup
def test_backup_zip_has_db_and_jobs(profile, job_row):
    job_row()
    (export.JOBS_DIR / "d").mkdir(parents=True)
    (export.JOBS_DIR / "d" / "x.txt").write_text("hi")
    r = client.get("/api/backup")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert r.headers["content-type"] == "application/zip" and "resumeit.db" in names and "jobs/d/x.txt" in names


def test_run_bat_does_not_start_a_second_server():
    bat = (export.Path(__file__).resolve().parent.parent / "run.bat").read_text()
    assert "PORT" in bat and "LISTENING" in bat and bat.index("LISTENING") < bat.index("-m app")


@pytest.mark.skipif(not __import__("os").getenv("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
def test_openai_live(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert ai.complete_json("Reply with JSON", 'Return {"ok": true}')["ok"] is True


# ------------------------------------------------------------------ MCP server
def _tools():
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_mcp_exposes_tools_but_no_way_to_apply_or_track():
    names = set(_tools())
    assert {"list_jobs", "get_job", "search_jobs", "build_resume", "what_changed", "write_cover_letter",
            "run_daily_scan", "get_profile", "estimate_ai_calls", "list_issues"} <= names
    assert not any(k in n for n in names for k in ("apply", "track", "mark", "submit", "applied"))


def test_mcp_list_get_and_build(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    jid = job_row(desc="Python and Kubernetes, 9+ years of experience")
    main._score_fit(); main._score_req()
    assert mcp_server.get_profile()["name"] == "Rahul Sen"
    assert mcp_server.list_jobs() == []  # default: only your level, so 9+ yrs is hidden
    rows = mcp_server.list_jobs(hide_senior=False)
    assert rows[0]["id"] == jid and rows[0]["your_skills_in_job"] == ["Python"] and "9+ yrs" in rows[0]["flags"]
    assert mcp_server.estimate_ai_calls([jid]) == {"ai_calls": 1, "skipped": 0}
    out = mcp_server.build_resume(jid)
    assert out["status"] == "done"
    g = mcp_server.get_job(jid)
    assert g["resume"]["name"] == "Rahul Sen" and g["match"]["score"] == 70
    assert mcp_server.what_changed(jid)["skills"]["removed"] == []
    assert g["tracking"] is None and mcp_server.list_jobs(status="done", hide_senior=False)[0]["id"] == jid
    with pytest.raises(ValueError):
        mcp_server.get_job(999)


def test_mcp_build_failure_is_reported(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored(skills=["Python", "Rust"]))
    with pytest.raises(ValueError, match="truth guard"):
        mcp_server.build_resume(job_row())


def test_mcp_search_uses_no_ai(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda *a: pytest.fail("search must not call AI"))
    monkeypatch.setattr(scrape, "_jobspy", _rows(2))
    out = mcp_server.search_jobs(["python"], sites=["indeed"])
    assert "2 new jobs" in out["message"]


def test_seven_templates_each_render_and_are_selectable():
    assert set(resume.TEMPLATES) == {"classic", "modern", "compact", "minimal", "executive", "tech", "elegant"}
    assert set(resume.TEMPLATES) <= set(resume.BASE_PT)  # one-page guard knows each template's type size
    styles = {resume.render_html(PROFILE, t).split("<style>")[1] for t in resume.TEMPLATES}
    assert len(styles) == 7
    for t in resume.TEMPLATES:
        assert client.post("/api/template", json={"t": t}).status_code == 200
    html = open(export.Path(__file__).resolve().parent.parent / "static" / "index.html", encoding="utf-8").read()
    assert all(f'value="{t}"' in html for t in resume.TEMPLATES)


# ------------------------------------------------------------------ length policy: 1 page, or 2 when really needed
def _pdf_stub(monkeypatch, pages_at, fill=0.6):
    """pages_at(scale, r) -> page count. PDF bytes encode the scale and bullet count."""
    monkeypatch.setattr(resume, "render_pdf", lambda r, t="classic", scale=1.0: f"{scale}|{sum(len(x['bullets']) for x in r['experience'])}".encode())
    monkeypatch.setattr(resume, "pdf_pages", lambda b: pages_at(float(b.split(b"|")[0]), int(b.split(b"|")[1])))
    monkeypatch.setattr(resume, "page_fill", lambda b: fill)


def test_auto_keeps_one_page_when_it_fits_comfortably(monkeypatch):
    _pdf_stub(monkeypatch, lambda sc, n: 1)
    r, pdf, pages, flag = resume.fit_one_page(BIG, "classic", "auto")
    assert pages == 1 and flag is None and r == BIG and pdf.startswith(b"1.0")


def test_auto_allows_two_full_pages_instead_of_cutting_content(monkeypatch):
    _pdf_stub(monkeypatch, lambda sc, n: 2, fill=0.6)
    r, pdf, pages, flag = resume.fit_one_page(BIG, "classic", "auto")
    assert pages == 2 and flag is None and r == BIG  # nothing removed, normal type size
    assert pdf.startswith(b"1.0")


def test_auto_trims_back_to_one_page_when_second_page_would_be_nearly_empty(monkeypatch):
    # 2 pages with 3 bullets, 1 page once a bullet is gone and type is small
    _pdf_stub(monkeypatch, lambda sc, n: 1 if (n < 3 and sc < 1) else 2, fill=0.1)
    r, pdf, pages, flag = resume.fit_one_page(BIG, "classic", "auto")
    assert pages == 1 and flag is None and guard.truth_problems(r, BIG) == []


def test_auto_never_exceeds_two_pages(monkeypatch):
    _pdf_stub(monkeypatch, lambda sc, n: 3 if n >= 3 else 2, fill=0.6)
    r, pdf, pages, flag = resume.fit_one_page(BIG, "classic", "auto")
    assert pages == 2 and len(r["experience"][0]["bullets"]) < 3


def test_strict_one_page_policy_never_allows_two(monkeypatch):
    _pdf_stub(monkeypatch, lambda sc, n: 2, fill=0.9)
    assert resume.fit_one_page(BIG, "classic", "one")[3].startswith("Manual trim needed")


def test_default_policy_is_auto_and_can_be_switched():
    assert client.get("/api/profile").json()["pages"] == "auto"
    assert client.post("/api/pages", json={"p": "one"}).status_code == 200
    assert client.get("/api/profile").json()["pages"] == "one"
    assert client.post("/api/pages", json={"p": "three"}).status_code == 400


def test_prompt_asks_for_strong_bullets_and_allows_two_pages_when_relevant():
    assert "action verb" in resume.RULES and "TWO pages" in resume.RULES and "Never pad" in resume.RULES


# ------------------------------------------------------------------ production hardening
def test_health_endpoint():
    r = client.get("/api/health").json()
    assert r["ok"] is True and "ai" in r and r["jobs"] == 0


def test_foreign_host_is_blocked_dns_rebinding():
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403
    assert client.get("/api/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/health", headers={"host": "localhost:8000"}).status_code == 200


def test_foreign_origin_cannot_change_data_but_can_be_ignored_on_reads(job_row):
    jid = job_row()
    bad = {"origin": "https://evil.example.com"}
    assert client.delete(f"/api/jobs/{jid}", headers=bad).status_code == 403
    assert client.post(f"/api/jobs/{jid}/track", json={"status": "Applied"}, headers=bad).status_code == 403
    assert client.get(f"/api/jobs/{jid}", headers=bad).status_code == 200  # reads carry no side effects
    assert client.delete(f"/api/jobs/{jid}", headers={"origin": "http://127.0.0.1:8000"}).status_code == 200


def test_oversized_upload_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD", 100)
    r = client.post("/api/profile/upload", files={"f": ("r.txt", b"x" * 500)})
    assert r.status_code == 413 and "10 MB" in r.json()["detail"]


def test_rejected_resume_is_retried_once_with_the_reason(profile, monkeypatch, job_row):
    users = []

    def fake(s, u):
        users.append(u)
        return tailored(skills=["Python", "Kubernetes"]) if len(users) == 1 else tailored()
    monkeypatch.setattr(ai, "complete_json", fake)
    jid = job_row()
    main._tailor(jid)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"
    assert len(users) == 2 and "REJECTED" in users[1] and "Kubernetes" in users[1]


# ------------------------------------------------------------------ chat editing
def _built(job_row, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: tailored())
    jid = job_row()
    main._tailor(jid)
    return jid


def test_chat_edit_applies_change_and_keeps_user_stated_skill(profile, monkeypatch, job_row):
    jid = _built(job_row, monkeypatch)
    monkeypatch.setattr(ai, "complete_json", lambda s, u: {"resume": tailored(skills=["Python", "Tally"], summary="Short.")["resume"], "reply": "Added Tally."})
    r = client.post(f"/api/jobs/{jid}/chat", json={"message": "add Tally to skills and shorten the summary"})
    assert r.status_code == 200 and "Tally" in r.json()["resume"]["skills"]
    assert "Tally" in client.get(f"/api/jobs/{jid}").json()["resume"]


def test_chat_edit_rejects_invented_facts_and_keeps_old_resume(profile, monkeypatch, job_row):
    jid = _built(job_row, monkeypatch)
    before = client.get(f"/api/jobs/{jid}").json()["resume"]
    monkeypatch.setattr(ai, "complete_json", lambda s, u: {"resume": tailored(skills=["Python", "Kubernetes"])["resume"], "reply": "ok"})
    r = client.post(f"/api/jobs/{jid}/chat", json={"message": "make it sound more senior"})  # Kubernetes was not typed by the user
    assert r.status_code == 422 and "Kubernetes" in r.json()["detail"]
    assert client.get(f"/api/jobs/{jid}").json()["resume"] == before


def test_chat_edit_needs_a_built_resume_and_a_message(profile, job_row):
    jid = job_row()
    assert client.post(f"/api/jobs/{jid}/chat", json={"message": "x"}).status_code == 400
    assert client.post(f"/api/jobs/{jid}/chat", json={"message": "  "}).status_code == 400
    assert client.post("/api/jobs/9999/chat", json={"message": "x"}).status_code == 404
