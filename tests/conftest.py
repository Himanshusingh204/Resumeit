import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, export, resume  # noqa: E402

PROFILE = {"name": "Rahul Sen", "email": "r@x.com", "skills": ["Python", "SQL"],
           "experience": [{"company": "Acme", "title": "Dev", "start": "2021", "end": "2024",
                           "bullets": ["Built REST APIs"]}],
           "projects": [], "education": [], "certifications": []}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite file; the real data/ folder is never touched."""
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db.init()


@pytest.fixture(autouse=True)
def isolated_export(tmp_path, monkeypatch):
    """Tests write to a temp jobs/ folder and skip the slow real PDF (one test uses the real browser)."""
    monkeypatch.setattr(export, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(resume, "render_pdf", lambda r, t="classic", scale=1.0: b"%PDF-stub")
    monkeypatch.setattr(resume, "pdf_pages", lambda pdf: 1)


@pytest.fixture
def job_row():
    def make(url="https://j/1", desc="Python and Kubernetes", title="Backend Engineer", company="Foo"):
        return db.x("INSERT INTO jobs(url,site,title,company,description) VALUES(?,?,?,?,?)",
                    url, "indeed", title, company, desc)
    return make
