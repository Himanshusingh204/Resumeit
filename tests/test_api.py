import json
import pytest
from fastapi.testclient import TestClient

from app import ai, db, main, resume, scrape
from conftest import PROFILE

client = TestClient(main.app)


@pytest.fixture
def profile():
    db.kv_set("profile", PROFILE)


def good_ai(system, user):
    if "search queries" in system:
        return {"terms": ["python backend developer", "sql analyst"]}
    return {"resume": PROFILE, "match": {"score": 70, "matched": ["Python"], "gaps": ["Kubernetes"]}}


def job_rows(**kw):
    return [{"job_url": f"https://j/{kw.get('t', 'x')}-{site}", "description": "Python", "title": "Dev " + kw.get("t", "x"),
             "company": "Foo", "site": site} for site in ("indeed",)]


def test_search_requires_profile():
    assert client.post("/api/search", json={}).status_code == 400


def test_full_flow_extracts_terms_scrapes_each_and_builds_resumes(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", good_ai)
    searched = []
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: searched.append(kw["search_term"]) or job_rows(t=kw["search_term"].replace(" ", "")))
    main._run_search(main.Search(sites=["indeed"], auto=True))  # no terms given -> from resume
    assert searched == ["python backend developer", "sql analyst"]
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 2 and {j["status"] for j in jobs} == {"done"}
    assert main.state["scraping"] is False and "Done: 2" in main.state["msg"]


def test_manual_terms_override_extraction(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: pytest.fail("AI must not be asked for terms"))
    searched = []
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: searched.append(kw["search_term"]) or [])
    main._run_search(main.Search(terms=["data engineer", " "], sites=["indeed"], auto=False))
    assert searched == ["data engineer"]


def test_keyword_extraction_failure_is_logged_and_run_stops_cleanly(profile, monkeypatch):
    def boom(s, u):
        raise RuntimeError("ai down")
    monkeypatch.setattr(ai, "complete_json", boom)
    main._run_search(main.Search(sites=["indeed"]))
    assert [i["kind"] for i in client.get("/api/issues").json()] == ["keywords_failed"]
    assert main.state["scraping"] is False and "No search terms" in main.state["msg"]


def test_one_failed_tailor_is_logged_and_does_not_stop_others(profile, monkeypatch):
    def flaky(system, user):
        if "Kubernetes" in user:
            raise RuntimeError("timeout")
        return good_ai(system, user)
    monkeypatch.setattr(ai, "complete_json", flaky)
    a = db.x("INSERT INTO jobs(url,site,title,company,description) VALUES('u1','m','A','C','needs Kubernetes')")
    b = db.x("INSERT INTO jobs(url,site,title,company,description) VALUES('u2','m','B','C','plain python')")
    main._tailor(a); main._tailor(b)
    st = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert st[a]["status"] == "error" and "timeout" in st[a]["error"] and st[b]["status"] == "done"
    assert client.get("/api/issues").json()[0]["kind"] == "tailor_failed"


def test_incomplete_resume_is_rejected_and_logged(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: {"resume": {"name": "R", "skills": ["x"]}, "match": {}})
    jid = job_row()
    main._tailor(jid)
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "error" and "incomplete" in j["error"] and j["resume"] is None
    assert client.get("/api/issues").json()[0]["kind"] == "tailor_failed"


def test_second_search_while_running_is_rejected(profile):
    main.state["scraping"] = True
    try:
        assert client.post("/api/search", json={"terms": ["x"]}).status_code == 409
    finally:
        main.state["scraping"] = False


def test_manual_job_and_downloads(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", good_ai)
    jid = client.post("/api/jobs", json={"title": "BE", "company": "Foo", "description": "Python"}).json()["id"]
    main.fast_pool.shutdown(wait=True); main.fast_pool = type(main.fast_pool)(2)  # wait for background tailor
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"
    r = client.get(f"/api/jobs/{jid}/resume.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "Rahul_Sen_Foo_BE.docx" in r.headers["content-disposition"]
    assert "Rahul Sen" in client.get(f"/api/jobs/{jid}/resume.html").text
    assert client.get(f"/api/jobs/{jid}/resume.exe").status_code == 404


def test_duplicate_manual_job_rejected(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", good_ai)
    body = {"url": "https://same", "description": "Python"}
    assert client.post("/api/jobs", json=body).status_code == 200
    assert client.post("/api/jobs", json=body).status_code == 409


def test_download_before_ready_is_409(job_row):
    assert client.get(f"/api/jobs/{job_row()}/resume.pdf").status_code == 409


def test_unknown_job_404():
    assert client.get("/api/jobs/999").status_code == 404


def test_issues_clear():
    db.issue("x", "y", "z")
    assert len(client.get("/api/issues").json()) == 1
    client.delete("/api/issues")
    assert client.get("/api/issues").json() == []


def test_terms_endpoint(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", good_ai)
    assert client.get("/api/terms").json()["terms"][0] == "python backend developer"


# ---- empty/unreadable resume must never be saved, and resume can be deleted ----
def test_upload_with_no_readable_text_is_rejected_and_keeps_old_profile(profile):
    r = client.post("/api/profile/upload", files={"f": ("scan.pdf", b"", "application/pdf")})
    assert r.status_code == 422 and "Could not open" in r.json()["detail"]
    assert db.kv_get("profile") == PROFILE


def test_blank_parse_is_rejected_and_keeps_old_profile(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: {"name": "", "skills": [], "experience": []})
    r = client.post("/api/profile/text", json={"text": "x" * 200})
    assert r.status_code == 422 and "previous profile was kept" in r.json()["detail"]
    assert db.kv_get("profile") == PROFILE


def test_good_text_is_saved(monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: PROFILE)
    assert client.post("/api/profile/text", json={"text": "x" * 200}).status_code == 200
    assert client.get("/api/profile").json()["problem"] is None


def test_empty_saved_profile_blocks_search_and_flags_problem():
    db.kv_set("profile", {"name": "", "skills": []})
    assert client.post("/api/search", json={}).status_code == 400
    assert client.get("/api/profile").json()["problem"] == "no name found"


def test_tailor_with_empty_profile_fails_locally_without_calling_ai(monkeypatch, job_row):
    db.kv_set("profile", {"name": ""})
    monkeypatch.setattr(ai, "complete_json", lambda s, u: pytest.fail("AI must not be called"))
    jid = job_row()
    main._tailor(jid)
    assert "profile is empty" in client.get(f"/api/jobs/{jid}").json()["error"]


def test_delete_resume_keeps_built_jobs(profile, job_row):
    jid = job_row()
    assert client.delete("/api/profile").status_code == 200
    assert client.get("/api/profile").json()["profile"] is None
    assert client.get(f"/api/jobs/{jid}").status_code == 200


def test_clear_failed_then_all_jobs(job_row):
    ok, bad = job_row("u1"), job_row("u2")
    db.x("UPDATE jobs SET status='error' WHERE id=?", bad)
    client.delete("/api/jobs?status=error")
    assert [j["id"] for j in client.get("/api/jobs").json()] == [ok]
    client.delete("/api/jobs")
    assert client.get("/api/jobs").json() == []
    assert client.delete("/api/jobs?status=bogus").status_code == 400


def test_tailor_pending_queues_new_and_failed_only(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", good_ai)
    a, b, c = job_row("a"), job_row("b"), job_row("c")
    db.x("UPDATE jobs SET status='done' WHERE id=?", c)
    db.x("UPDATE jobs SET status='error' WHERE id=?", b)
    assert client.post("/api/jobs/tailor-pending").json() == {"queued": 2}
    main.pool.shutdown(wait=True); main.pool = type(main.pool)(2)
    assert {j["id"]: j["status"] for j in client.get("/api/jobs").json()} == {a: "done", b: "done", c: "done"}


def test_template_query_changes_html(profile, job_row):
    jid = job_row()
    db.x("UPDATE jobs SET resume=? WHERE id=?", __import__("json").dumps(PROFILE), jid)
    classic = client.get(f"/api/jobs/{jid}/resume.html").text
    modern = client.get(f"/api/jobs/{jid}/resume.html?t=modern").text
    assert classic != modern and "#1f3a5f" in modern
    assert client.get(f"/api/jobs/{jid}/resume.html?t=nope").text == classic  # unknown -> classic


def test_text_file_with_too_little_text_is_rejected(profile):
    r = client.post("/api/profile/upload", files={"f": ("cv.txt", b"hi", "text/plain")})
    assert r.status_code == 422 and "No readable text" in r.json()["detail"]
    assert db.kv_get("profile") == PROFILE


def test_irrelevant_jobs_are_skipped_without_calling_ai(profile, monkeypatch):
    calls = []
    def fake(system, user):
        calls.append(system)
        return good_ai(system, user)
    monkeypatch.setattr(ai, "complete_json", fake)
    rows = [{"job_url": "https://j/rel", "description": "We need Python and SQL", "title": "Backend", "company": "A", "site": "indeed"},
            {"job_url": "https://j/irr", "description": "Aerospace stress analysis in CATIA", "title": "Stress Engineer", "company": "B", "site": "indeed"}]
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: rows)
    main._run_search(main.Search(terms=["backend"], sites=["indeed"], auto=True))
    st = {j["title"]: j for j in client.get("/api/jobs").json()}
    assert st["Backend"]["status"] == "done"
    assert st["Stress Engineer"]["status"] == "skipped" and "Low match" in st["Stress Engineer"]["error"]
    assert len(calls) == 1  # only the relevant job cost an AI call
    assert client.post("/api/jobs/tailor-pending").json() == {"queued": 0}  # skipped stays skipped until asked


# ---- finding jobs must not build resumes unless asked
def test_search_does_not_build_resumes_by_default_but_scores_fit_for_free(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: pytest.fail("no AI call without auto"))
    rows = [{"job_url": "https://j/1", "description": "Python and SQL", "title": "Backend", "company": "A", "site": "indeed"}]
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: rows)
    main._run_search(main.Search(terms=["backend"], sites=["indeed"]))
    j = client.get("/api/jobs").json()[0]
    assert j["status"] == "new" and json.loads(j["fit"]) == ["Python", "SQL"]
    assert "Pick the ones you want" in main.state["msg"]


def test_stop_queue_returns_waiting_jobs_to_not_built(profile, monkeypatch, job_row):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(ai, "complete_json", lambda s, u: gate.wait(5) and good_ai(s, u))
    ids = [job_row(f"u{i}", desc="Python", title=f"T{i}", company=f"C{i}") for i in range(6)]
    main._queue(ids)
    assert client.post("/api/queue/stop").json()["stopped"] >= 3  # 2 workers busy, the rest were waiting
    st = [j["status"] for j in client.get("/api/jobs").json()]
    assert "queued" not in st and st.count("new") >= 3
    gate.set()
    main.pool.shutdown(wait=True); main.pool = type(main.pool)(2)


def test_single_build_does_not_wait_behind_bulk_queue(profile, monkeypatch, job_row):
    import threading
    gate = threading.Event()
    def ai_fn(s, u):
        if "T0" in u:  # bulk jobs hang until released; the clicked job (T9) must finish regardless
            gate.wait(5)
        return good_ai(s, u)
    monkeypatch.setattr(ai, "complete_json", ai_fn)
    bulk = [job_row(f"b{i}", desc="Python T0", title="T0", company=f"C{i}") for i in range(4)]
    main._queue(bulk)
    one = job_row("single", desc="Python", title="T9", company="Z")
    client.post(f"/api/jobs/{one}/tailor")
    import time
    for _ in range(50):
        if client.get(f"/api/jobs/{one}").json()["status"] == "done":
            break
        time.sleep(0.1)
    assert client.get(f"/api/jobs/{one}").json()["status"] == "done"
    gate.set()
    main.pool.shutdown(wait=True); main.pool = type(main.pool)(2)
