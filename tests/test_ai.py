import pytest

from app import ai


def test_complete_json_strips_fences_and_prose(monkeypatch):
    monkeypatch.setattr(ai, "complete", lambda s, u: 'Sure!\n```json\n{"a": [1, 2]}\n```')
    assert ai.complete_json("s", "u") == {"a": [1, 2]}


def test_complete_json_raises_without_json(monkeypatch):
    monkeypatch.setattr(ai, "complete", lambda s, u: "no json here")
    with pytest.raises(ValueError):
        ai.complete_json("s", "u")


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("AI_PROVIDER", "CLAUDE_BIN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda n: None)


def test_auto_prefers_claude_cli_then_anthropic_then_openai(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert ai.provider() == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert ai.provider() == "anthropic"
    monkeypatch.setenv("CLAUDE_BIN", "claude.exe")
    assert ai.provider() == "claude_cli"


def test_gemini_is_last_in_auto_order_and_accepts_google_key(clean_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    assert ai.provider() == "gemini"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert ai.provider() == "openai"


def test_gemini_request_and_response(clean_env, monkeypatch):
    seen = {}

    class R:
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"candidates": [{"content": {"parts": [{"text": '{"ok": '}, {"text": "true}"}]}}]}

    monkeypatch.setattr(ai.httpx, "post", lambda url, **kw: seen.update(url=url, **kw) or R())
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    assert ai.complete_json("sys", "user") == {"ok": True}
    assert "gemini-test:generateContent" in seen["url"] and "secret" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "secret"
    assert seen["json"]["systemInstruction"]["parts"][0]["text"].startswith("sys")
    assert seen["json"]["contents"][0]["parts"][0]["text"] == "user"


def test_gemini_blocked_answer_is_a_clear_error(clean_env, monkeypatch):
    class R:
        text = "{}"
        def raise_for_status(self): pass
        def json(self): return {"promptFeedback": {"blockReason": "SAFETY"}}
    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: R())
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with pytest.raises(RuntimeError, match="gemini returned no text"):
        ai.complete("s", "u")


def test_explicit_provider_wins(clean_env, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("CLAUDE_BIN", "claude.exe")
    assert ai.provider() == "openai"


def test_no_provider_gives_clear_error(clean_env):
    with pytest.raises(RuntimeError, match="No AI configured"):
        ai.provider()
