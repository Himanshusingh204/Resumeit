"""One function: complete(system, user) -> text, over Claude Code CLI, Anthropic API or OpenAI API."""
import json, os, re, shutil, subprocess
import httpx


def _claude_bin():
    return os.getenv("CLAUDE_BIN") or shutil.which("claude")


def _cli(system, user):
    # shortcut: one process per call via stdin; fine for a single user
    r = subprocess.run([_claude_bin(), "-p", "--append-system-prompt", system],
                       input=user, capture_output=True, text=True, timeout=300, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"claude cli: {r.stderr[:300]}")
    return r.stdout


def _anthropic(system, user):
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=180, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 4096,
              "system": system, "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def _openai(system, user):
    r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=180,
                   headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                   json={"model": os.getenv("OPENAI_MODEL", "gpt-4o"),
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def provider():
    p = os.getenv("AI_PROVIDER", "auto").lower()
    if p != "auto":
        return p
    if _claude_bin():
        return "claude_cli"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError("No AI configured: install Claude Code or set ANTHROPIC_API_KEY / OPENAI_API_KEY in .env")


def complete(system, user):
    return {"claude_cli": _cli, "anthropic": _anthropic, "openai": _openai}[provider()](system, user)


def complete_json(system, user):
    text = complete(system + "\nReply with ONE valid JSON object only. No prose, no code fences.", user)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI returned no JSON: " + text[:200])
    return json.loads(m.group(0))
