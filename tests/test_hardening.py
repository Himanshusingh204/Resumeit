import io, json, os, subprocess, zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app import __main__ as entry, ai, db, fetch, main, resume
from conftest import PROFILE

client = TestClient(main.app)


def test_tailor_and_queue_ignore_deleted_job():
    main._tailor(9999)
    db.kv_set("profile", PROFILE)
    assert main._queue([9999]) == []


def test_manual_job_url_is_stable_across_restarts(monkeypatch):
    db.kv_set("profile", PROFILE)
    monkeypatch.setattr(main.fast_pool, "submit", lambda *a: None)
    desc = "Python developer wanted"
    jid = client.post("/api/jobs", json={"description": desc}).json()["id"]
    import hashlib
    assert db.q("SELECT url FROM jobs WHERE id=?", jid)[0]["url"] == "manual:" + hashlib.sha1(desc.encode()).hexdigest()[:16]
    assert client.post("/api/jobs", json={"description": desc}).status_code == 409


def test_download_with_non_ascii_names(job_row):
    jid = job_row(company="Zoë GmbH", title="Entwickler 東京")
    db.x("UPDATE jobs SET resume=? WHERE id=?", json.dumps({**PROFILE, "name": "Zoë"}), jid)
    r = client.get(f"/api/jobs/{jid}/resume.docx")
    assert r.status_code == 200
    assert 'filename="' in r.headers["content-disposition"]


def test_backup_is_a_zip_and_leaves_no_temp_file(tmp_path):
    db.kv_set("profile", PROFILE)
    r = client.get("/api/backup")
    assert "resumeit.db" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not list(db.DB.parent.glob("*.bak"))


def test_edit_resume_of_missing_job_is_404():
    assert client.put("/api/jobs/9999/resume", json={"name": "x"}).status_code == 404


def test_open_folder_works_without_startfile(monkeypatch):
    calls = []
    monkeypatch.delattr(os, "startfile", raising=False)
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd: calls.append(cmd))
    assert client.post("/api/folder").status_code == 200
    assert calls and calls[0][0] in ("xdg-open", "open")


def _page(body):
    return httpx.Response(200, text=body, request=httpx.Request("GET", "http://x"))


def test_fetch_survives_odd_json_ld(monkeypatch):
    long = "word " * 100
    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: _page(
        f'<script type="application/ld+json">"just a string"</script><title>T</title><p>{long}</p>'))
    assert fetch.job_from_url("http://x")[0] == "T"


def test_fetch_rejects_http_error_pages(monkeypatch):
    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: httpx.Response(
        404, text="not found " * 100, request=httpx.Request("GET", "http://x")))
    with pytest.raises(ValueError, match="Could not open"):
        fetch.job_from_url("http://x")


def test_ai_errors_are_readable(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "bogus")
    with pytest.raises(RuntimeError, match="Unknown AI_PROVIDER"):
        ai.complete("s", "u")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ai.complete("s", "u")
    monkeypatch.setenv("AI_PROVIDER", "claude_cli")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="not found"):
        ai.complete("s", "u")


def test_server_settings(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert entry.settings() == ("127.0.0.1", 8000)
    monkeypatch.setenv("PORT", "9001")
    assert entry.settings() == ("127.0.0.1", 9001)
    monkeypatch.setenv("HOST", "0.0.0.0")
    with pytest.raises(SystemExit):
        entry.settings()
    monkeypatch.setenv("HOST", "localhost")
    monkeypatch.setenv("PORT", "abc")
    with pytest.raises(SystemExit):
        entry.settings()
