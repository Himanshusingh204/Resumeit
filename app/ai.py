"""One function: complete(system, user) -> text, over Claude Code CLI, Anthropic API, OpenAI API, Google Gemini API or the OpenCode CLI."""
import json, os, re, shutil, subprocess, tempfile
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


def _opencode_bin():
    return os.getenv("OPENCODE_BIN") or shutil.which("opencode")


def _opencode(system, user):
    # the prompt goes in a file (command lines are length-limited on Windows); run in an empty dir so the agent sees no project files
    if not _opencode_bin():
        raise RuntimeError("OpenCode not found: install it (npm i -g opencode-ai) or set OPENCODE_BIN in .env")
    model = os.getenv("OPENCODE_MODEL")  # provider/model; empty = OpenCode's default (it has free models)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "prompt.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(f"INSTRUCTIONS:\n{system}\n\nINPUT:\n{user}\n")
        try:
            r = subprocess.run([_opencode_bin(), "run", "--archived", *(["--model", model] if model else []), "--file", f,
                                "Follow the INSTRUCTIONS in the attached file for the INPUT. Output only the answer."],
                               capture_output=True, text=True, timeout=300, encoding="utf-8", cwd=d)
        except subprocess.TimeoutExpired:
            raise RuntimeError("opencode timed out after 300 s")
    if r.returncode:
        raise RuntimeError(f"opencode: {(r.stderr or r.stdout)[:300]}")
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)
    return re.sub(r"\A\s*> [^\n]*\n", "", out)  # drop the "> build model" banner


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


GEMINI_FALLBACKS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]  # free-tier models change; 2.5 is closed to new keys


def _gemini(system, user):
    models = [m for m in dict.fromkeys([os.getenv("GEMINI_MODEL"), *GEMINI_FALLBACKS]) if m]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json"} if "ONE valid JSON" in system else {}}
    for model in models:  # 404 = model retired for this key, 429/503 = busy: try the next one
        try:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=45,
                           headers={"x-goog-api-key": _gemini_key()},  # header, not the URL, so the key never lands in logs
                           json=body)
        except httpx.TimeoutException:
            if model == models[-1]:
                raise
            continue
        if r.status_code not in (404, 429, 503) or model == models[-1]:
            break
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
    if _opencode_bin():
        return "opencode"
    raise RuntimeError("No AI configured. Free option: get a Gemini key at https://aistudio.google.com/apikey, put it in .env as GEMINI_API_KEY and restart. "
                       "(Claude Code, OpenCode, ANTHROPIC_API_KEY and OPENAI_API_KEY also work.)")


def complete(system, user):
    name = provider()
    fn = {"claude_cli": _cli, "anthropic": _anthropic, "openai": _openai, "gemini": _gemini, "opencode": _opencode}.get(name)
    if not fn:
        raise RuntimeError(f"Unknown AI_PROVIDER '{name}': use claude_cli, anthropic, openai, gemini, opencode or auto")
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
