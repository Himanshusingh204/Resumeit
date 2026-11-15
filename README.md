# Resumeit

A local, single-user tool that finds jobs and builds a **tailored one-page resume for each job description** from your own resume.

You upload your resume once. Resumeit reads your keywords, scrapes job boards (LinkedIn, Indeed, Naukri, Glassdoor, Google) with [JobSpy](https://github.com/speedyapply/JobSpy), and writes a separate resume per job. You review it and apply yourself.

## Rules the tool follows

- **It never applies for you.** No forms, no emails, no "Easy Apply". It only prepares; you press apply on the original site.
- **No lies in the resume.** Only facts from your own resume are used. Skills you lack are shown as gaps, never added.
- **One page.** Resumes are meant to fit a single A4 page. See [docs/PLAN.md](docs/PLAN.md) for what is done and what is planned.

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

Edit `.env`. `AI_PROVIDER=auto` picks the first one available.

| Provider | What you need |
|---|---|
| `claude_cli` | [Claude Code](https://claude.com/claude-code) installed and logged in (set `CLAUDE_BIN` if `claude` is not on PATH). No API key. |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` (not tested end to end) |

Each resume costs one AI call, so build the jobs you care about instead of everything at once.

## How to use

1. Drop your resume (PDF, DOCX or TXT) on the left. If a PDF has no readable text, use **Paste text**.
2. Press **Suggest from resume** for job titles, then **Find jobs**. No AI is used yet.
3. Each job shows how many of your skills it mentions. Press **Build** on the ones you want.
4. Preview opens when it is ready. Download PDF/DOCX, or open the job's folder.

Anything that was skipped or failed is listed under **Issues** and saved in `data/issues.log`.

## Tests

```
.venv\Scripts\python -m pytest -q
```

Tests use fake scrapers and a fake AI, so they need no network and never touch your `data/` or `jobs/`.

## Known limits

- Scraping is fragile: LinkedIn and Naukri block quickly, so some searches return nothing.
- Windows only for the "open folder" buttons.
- Read each resume before sending. The tool checks that it is complete, but you are the final check.

## License

MIT, see [LICENSE](LICENSE).
