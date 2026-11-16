"""Cheap guards for things the page promises (real clicking is scripts/e2e_check.py)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai, db, main
from conftest import PROFILE

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
client = TestClient(main.app)


def test_preview_downloads_are_locked_until_the_safety_box_is_ticked():
    assert 'id="pok"' in HTML and "I have read this resume and checked it is true" in HTML
    assert 'id="ppdf"' in HTML and 'id="pdoc"' in HTML and HTML.count('aria-disabled="true"') >= 2
    assert "$('pok').checked=false" in HTML          # resets every time a preview opens
    assert "a.removeAttribute('href')" in HTML       # locked = no link at all, so no route around the box


def test_ai_pill_opens_the_free_gemini_howto():
    assert '<button type="button" class="ai" id="aiPill" onclick="aiHelp()"' in HTML
    assert 'id="adlg"' in HTML and "https://aistudio.google.com/apikey" in HTML and "GEMINI_API_KEY" in HTML


def test_env_example_points_to_free_gemini_and_not_ollama():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "https://aistudio.google.com/apikey" in env and "ollama" not in env.lower()
    assert env.index("GEMINI_API_KEY") < env.index("OPENAI_API_KEY")  # free option first


def test_no_ai_error_tells_you_how_to_get_the_free_key(monkeypatch):
    for k in ("AI_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai, "_claude_bin", lambda: None)
    with pytest.raises(RuntimeError, match="No AI configured") as e:
        ai.provider()
    assert "aistudio.google.com/apikey" in str(e.value)


def test_rebuild_shows_queued_at_once_so_the_page_does_not_miss_the_finish(monkeypatch, job_row):
    db.kv_set("profile", PROFILE)
    monkeypatch.setattr(main.fast_pool, "submit", lambda *a: None)
    jid = job_row()
    db.x("UPDATE jobs SET status='done' WHERE id=?", jid)
    assert client.post(f"/api/jobs/{jid}/tailor").status_code == 200
    assert db.q("SELECT status FROM jobs WHERE id=?", jid)[0]["status"] == "queued"
