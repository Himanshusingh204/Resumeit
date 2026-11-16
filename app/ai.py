"""One function: complete(system, user) -> text, over Claude Code CLI, Anthropic API, OpenAI API or Google Gemini API."""
import json, os, re, shutil, subprocess
import httpx


def _claude_bin():
    return os.getenv("CLAUDE_BIN") or shutil.which("claude")


def _cli(system, user):
    # shortcut: one process per call via stdin; fine for a single user
    if not _claude_bin():
        raise RuntimeError("Claude Code CLI not found: install it or set CLAUDE_BIN in .env")
    model = os.getenv("CLAUDE_MODEL")  # e.g. a cheaper/faster model; empty = Claude Code's default
    try:
        r = subprocess.run([_claude_bin(), "-p", *(["--model", model] if model else []), "--append-system-prompt", system],
                           input=user, capture_output=True, text=True, timeout=300, encoding="utf-8")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude cli timed out after 300 s")
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


def _gemini_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _gemini(system, user):
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=180,
                   headers={"x-goog-api-key": _gemini_key()},  # header, not the URL, so the key never lands in logs
                   json={"systemInstruction": {"parts": [{"text": system}]},
                         "contents": [{"role": "user", "parts": [{"text": user}]}],
                         "generationConfig": {"responseMimeType": "application/json"} if "ONE valid JSON" in system else {}})
    r.raise_for_status()
    try:
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise RuntimeError("gemini returned no text (blocked or empty): " + r.text[:200])


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
    if _gemini_key():
        return "gemini"
    raise RuntimeError("No AI configured. Free option: get a Gemini key at https://aistudio.google.com/apikey, put it in .env as GEMINI_API_KEY and restart. "
                       "(Claude Code, ANTHROPIC_API_KEY and OPENAI_API_KEY also work.)")


def complete(system, user):
    name = provider()
    fn = {"claude_cli": _cli, "anthropic": _anthropic, "openai": _openai, "gemini": _gemini}.get(name)
    if not fn:
        raise RuntimeError(f"Unknown AI_PROVIDER '{name}': use claude_cli, anthropic, openai, gemini or auto")
    try:
        return fn(system, user)
    except KeyError as e:  # os.environ[...] of a missing key
        raise RuntimeError(f"{name}: missing setting {e} in .env") from None
    except httpx.HTTPError as e:
        raise RuntimeError(f"{name} request failed: {type(e).__name__} {str(e)[:200]}") from None


def complete_json(system, user):
    text = complete(system + "\nReply with ONE valid JSON object only. No prose, no code fences.", user)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI returned no JSON: " + text[:200])
    return json.loads(m.group(0))
