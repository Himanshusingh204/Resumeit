import json

import pytest
from fastapi.testclient import TestClient

from app import ai, db, export, fetch, main, resume
from conftest import PROFILE

client = TestClient(main.app)
GOOD = {"resume": PROFILE, "match": {"score": 70, "matched": ["Python"], "gaps": ["Kubernetes"]}}


@pytest.fixture
def profile():
    db.kv_set("profile", PROFILE)


def test_finished_resume_is_written_to_a_job_folder(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: GOOD)
    jid = job_row(title="Backend Engineer", company="Foo Ltd")
    main._tailor(jid)
    d = next(export.JOBS_DIR.glob(f"*/Foo-Ltd_Backend-Engineer_{jid}"))
    assert sorted(f.name for f in d.iterdir()) == ["jd.md", "match.txt", "resume.docx", "resume.html", "resume.pdf"]
    assert "Python and Kubernetes" in (d / "jd.md").read_text(encoding="utf-8")
    assert "Match: 70%" in (d / "match.txt").read_text(encoding="utf-8") and "Kubernetes" in (d / "match.txt").read_text(encoding="utf-8")


def test_pdf_failure_keeps_job_done_and_other_files_and_logs_issue(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: GOOD)
    monkeypatch.setattr(resume, "render_pdf", lambda *a: (_ for _ in ()).throw(RuntimeError("no browser")))
    jid = job_row()
    main._tailor(jid)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"
    assert (export.folder(db.q("SELECT * FROM jobs WHERE id=?", jid)[0]) / "resume.docx").exists()
    assert client.get("/api/issues").json()[0]["kind"] == "export_failed"


def test_chosen_template_is_used_for_the_saved_html(profile, monkeypatch, job_row):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: GOOD)
    assert client.post("/api/template", json={"t": "modern"}).status_code == 200
    assert client.get("/api/profile").json()["template"] == "modern"
    assert client.post("/api/template", json={"t": "nope"}).status_code == 400
    jid = job_row()
    main._tailor(jid)
    d = export.folder(db.q("SELECT * FROM jobs WHERE id=?", jid)[0])
    assert "#1f3a5f" in (d / "resume.html").read_text(encoding="utf-8")


def test_open_folder_endpoints(profile, monkeypatch, job_row):
    opened = []
    monkeypatch.setattr(main.os, "startfile", lambda p: opened.append(str(p)), raising=False)
    assert client.post("/api/folder").status_code == 200 and opened[-1] == str(export.JOBS_DIR)
    jid = job_row()
    assert client.post(f"/api/jobs/{jid}/folder").status_code == 404  # nothing built yet


# ---- reading a job from its link
class Resp:
    def __init__(self, text): self.text = text


LD = json.dumps({"@type": "JobPosting", "title": "Data Engineer", "hiringOrganization": {"name": "Qentelli"},
                 "description": "<p>Build <b>pipelines</b> in Python.</p><ul><li>SQL</li></ul>" + "x " * 150})


def test_job_from_url_prefers_structured_data(monkeypatch):
    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: Resp(f'<html><script type="application/ld+json">{LD}</script></html>'))
    t, c, d = fetch.job_from_url("https://x/job")
    assert (t, c) == ("Data Engineer", "Qentelli") and "Build pipelines in Python." in d and "<b>" not in d


def test_job_from_url_falls_back_to_page_text_and_rejects_thin_pages(monkeypatch):
    body = "<html><title>Dev role</title><script>var x=1</script><body><p>" + "Real description text. " * 30 + "</p></body></html>"
    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: Resp(body))
    t, c, d = fetch.job_from_url("https://x/job")
    assert t == "Dev role" and "var x" not in d and "Real description" in d
    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: Resp("<html>Login to continue</html>"))
    with pytest.raises(ValueError, match="Paste the text"):
        fetch.job_from_url("https://x/job")


def test_manual_job_from_link_only(profile, monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: GOOD)
    monkeypatch.setattr(fetch, "job_from_url", lambda u: ("Data Engineer", "Qentelli", "Python and SQL pipelines"))
    jid = client.post("/api/jobs", json={"url": "https://x/job"}).json()["id"]
    main.fast_pool.shutdown(wait=True); main.fast_pool = type(main.fast_pool)(2)
    j = client.get(f"/api/jobs/{jid}").json()
    assert (j["title"], j["company"], j["status"]) == ("Data Engineer", "Qentelli", "done")


def test_manual_job_needs_text_or_link(profile):
    assert client.post("/api/jobs", json={"title": "x"}).status_code == 422
    assert client.post("/api/jobs", json={"url": "not-a-link"}).status_code == 422


def test_real_pdf_export_when_a_browser_exists(monkeypatch):
    if not resume._browser():
        pytest.skip("no Edge/Chrome")
    monkeypatch.undo()
    assert resume.render_pdf(PROFILE, "compact")[:4] == b"%PDF"
