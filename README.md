# Resumeit

A local, single-user tool that finds jobs and builds a **tailored one-page resume for each job description** from your own resume.

You upload your resume once. Resumeit reads your keywords, scrapes job boards (LinkedIn, Indeed, Naukri, Glassdoor, Google) with [JobSpy](https://github.com/speedyapply/JobSpy), and writes a separate resume per job. You review it and apply yourself.

## Rules the tool follows

- **It never applies for you.** No forms, no emails, no "Easy Apply". It only prepares; you press apply on the original site.
- **No lies in the resume.** Only facts from your own resume are used. Skills you lack are shown as gaps, never added.
- **One page.** Every PDF is page-counted and trimmed to a single A4 page, or flagged "manual trim needed". See [docs/PLAN.md](docs/PLAN.md).

## Your data stays on your computer

Everything runs on `127.0.0.1`. Your resume, jobs and generated files live in two folders that are git-ignored:

- `data/` your parsed resume and the job database (SQLite)
- `jobs/` one folder per job: `jd.md`, `match.txt`, `resume.pdf`, `resume.docx`, `resume.html`

Nothing is uploaded anywhere except the text sent to the AI provider you choose (see below). The page loads fonts from Google Fonts; offline it falls back to system fonts.

## Setup (Windows)

Needs Python 3.12+ and Edge or Chrome (used to make the PDF).

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
run.bat
```

`run.bat` opens http://127.0.0.1:8000.

## Choose your AI

Edit `.env`. `AI_PROVIDER=auto` picks the first one available (Claude Code, Anthropic, OpenAI, then Gemini).

| Provider | What you need |
|---|---|
| `claude_cli` | [Claude Code](https://claude.com/claude-code) installed and logged in (set `CLAUDE_BIN` if `claude` is not on PATH). No API key. |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` (not tested end to end) |
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), model via `GEMINI_MODEL` (default `gemini-2.5-flash`). Not tested against the live API. |

Optional `CLAUDE_MODEL` / `ANTHROPIC_MODEL` / `OPENAI_MODEL` pick a cheaper or faster model. Each resume costs one AI call, so build the jobs you care about instead of everything at once.

## How to use

1. Drop your resume (PDF, DOCX or TXT) on the left. If a PDF has no readable text, use **Paste text**.
2. Press **Suggest from resume** for job titles, then **Find jobs**. No AI is used yet.
3. Each job shows how many of your skills it mentions. Press **Build** on the ones you want.
4. Preview opens when it is ready. Download PDF/DOCX, or open the job's folder.

Anything that was skipped or failed is listed under **Issues** and saved in `data/issues.log`.

Before you apply, each job card shows (read from the posting, no AI): **salary** (board field or text, e.g. `₹12–18 LPA`), work mode, job type, how old it is and warnings like `night shift` / `bond` / `unpaid`. Filters: min salary, salary shown, remote/hybrid/on-site, type, posted date, hide above my level, hide US-only/clearance/no-sponsorship, hide warnings. Many postings list no salary; the card then says "salary not listed".

More: **What changed** in the preview (your base resume vs the tailored one), a per-job **Applied / Interview / Rejected / Offer** tracker (set by you only), badges like `5+ yrs` / `US only`, an optional cover letter, **Backup** and **Fix 2-page PDFs** buttons.

## Daily scan (never applies)

```
PUT /api/saved-search   {"terms":["python developer"],"location":"Pune","max_resumes":5}
scripts\install_daily_task.bat     # Task Scheduler, 09:00 daily
.venv\Scripts\python -m app.scan   # or run by hand
```

It finds new jobs and builds at most `max_resumes` resumes, then writes `jobs/_daily.md`. Create `data\STOP` to halt a run.

## MCP server

Use Resumeit from Claude Desktop / Claude Code:

```
claude mcp add resumeit -- D:\path\to\resumeit\.venv\Scripts\python.exe -m app.mcp_server
```

Tools: `get_profile`, `list_jobs`, `get_job`, `search_jobs`, `estimate_ai_calls`, `build_resume`, `what_changed`, `write_cover_letter`, `run_daily_scan`, `list_issues`. There is deliberately no tool to apply or to set the tracker status.

## Tests

```
.venv\Scripts\python -m pytest -q
```

Tests use fake scrapers and a fake AI, so they need no network and never touch your `data/` or `jobs/`.

## Known limits

- Scraping is fragile: LinkedIn and Naukri block quickly, so some searches return nothing.
- Windows only for the "open folder" buttons.
- Read each resume before sending. The truth guard catches invented facts, not a true fact bent out of shape (e.g. "contributed" to "led"). You are the final check.
- The DOCX page count cannot be verified; only the PDF is checked.

## License

MIT, see [LICENSE](LICENSE).
