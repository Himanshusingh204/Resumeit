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
    for k in ("AI_PROVIDER", "CLAUDE_BIN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda n: None)


def test_auto_prefers_claude_cli_then_anthropic_then_openai(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert ai.provider() == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert ai.provider() == "anthropic"
    monkeypatch.setenv("CLAUDE_BIN", "claude.exe")
    assert ai.provider() == "claude_cli"


def test_explicit_provider_wins(clean_env, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("CLAUDE_BIN", "claude.exe")
    assert ai.provider() == "openai"


def test_no_provider_gives_clear_error(clean_env):
    with pytest.raises(RuntimeError, match="No AI configured"):
        ai.provider()
