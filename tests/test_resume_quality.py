"""Resume safety net: every template x short / medium / very long / hostile profiles must render without
breaking (escaping, overflow, page count, DOCX). Fast tests use no browser; the real-PDF test skips without Edge/Chrome."""
import io
import re

import pytest
from docx import Document

from app import resume

LONGWORD = "Supercalifragilisticexpialidocious" * 4
LONGURL = "https://example.com/" + "path-segment/" * 14 + "x" * 60 + "?a=1&b=2"

SHORT = {"name": "Alex Doe", "skills": ["Python"],
         "experience": [{"company": "Acme", "title": "Dev", "start": "2021", "end": "2024", "bullets": ["Built APIs"]}]}

MEDIUM = {"name": "Priya Sharma", "email": "priya@example.com", "phone": "+91 98765 43210", "location": "Pune, India",
          "links": ["https://github.com/priya", "https://linkedin.com/in/priya"],
          "summary": "Backend engineer with 4 years building Python services.",
          "skills": ["Python", "FastAPI", "SQL", "Docker", "AWS", "React", "Git", "Linux"],
          "experience": [{"company": "Acme Systems", "title": "Backend Developer", "start": "2021", "end": "Present",
                          "bullets": ["Built REST APIs with FastAPI and SQL", "Containerised services with Docker on AWS", "Cut p95 latency by 30%"]},
                         {"company": "Globex", "title": "Intern", "start": "2020", "end": "2021", "bullets": ["Wrote unit tests", "Fixed bugs"]}],
          "projects": [{"name": "Tracker", "tech": "Python, React", "bullets": ["Built a small tracking app"]}],
          "education": [{"school": "Example University", "degree": "B.Tech", "year": "2020"}],
          "certifications": ["AWS Cloud Practitioner"]}

VERY_LONG = {"name": "Dr. Śrīnivāsa Åström-Ñuñez & Co. 李小龍", "email": "", "phone": None, "location": "São Paulo / Zürich / 東京 " * 3,
             "links": [LONGURL, "https://github.com/" + "u" * 90],
             "summary": ("Engineer who ships <fast> & \"safe\" code; 'quoted' text, 100% > 50%. " * 8),
             "skills": ["C++", "C#", "R&D", "<script>alert(1)</script>", LONGWORD, *[f"Skill{i}" for i in range(40)]],
             "experience": [{"company": f"Company {i} & Sons <Ltd>", "title": f"Senior Staff Principal Engineer {i} " + LONGWORD[:40],
                             "start": "Jan 2015", "end": "Present",
                             "bullets": [f"Delivered result {j} using {LONGWORD} and {LONGURL} with Ünïcödé ✓ — “smart quotes” & <tags>" for j in range(7)]}
                            for i in range(6)] + [{"company": "Empty Bullets Inc", "title": "Ghost", "start": "", "end": "", "bullets": []}],
             "projects": [{"name": f"Project {i} <x>", "tech": ", ".join(f"Tech{k}" for k in range(25)), "bullets": [f"Built part {j} " + LONGURL for j in range(4)]}
                          for i in range(6)] + [{"name": "No tech", "tech": "", "bullets": ["only a bullet"]}],
             "education": [{"school": "Universität " + LONGWORD, "degree": "M.Sc. & Ph.D.", "year": "2010"}, {"school": "School", "degree": "", "year": ""}],
             "certifications": [f"Certified {LONGWORD[:30]} Professional {i} & Associate" for i in range(8)]}

HOSTILE = {"name": "Only Name \x00\x0b\ud800", "links": [{"url": "https://x.example/p"}, None, 5], "summary": None,
           "skills": ["Python", None, 3, "", "SQL\x08"],
           "experience": [{"title": None, "company": None, "bullets": [None, "ok \x00 bullet", 7]}, "not a dict"],
           "projects": ["junk"], "education": [{"school": None}], "certifications": "single string cert"}

PROFILES = {"short": SHORT, "medium": MEDIUM, "very_long": VERY_LONG, "hostile": HOSTILE}
ALL = [(t, n) for t in resume.TEMPLATES for n in PROFILES]


def _docx_text(r):
    return "\n".join(p.text for p in Document(io.BytesIO(resume.render_docx(r))).paragraphs)


@pytest.mark.parametrize("tpl,name", ALL)
def test_html_and_docx_never_break(tpl, name):
    r = PROFILES[name]
    h = resume.render_html(r, tpl)
    body = h.split("</style>", 1)[1]
    assert "<script" not in body and "<b>" not in body          # user text is escaped, never markup
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", h)     # no control characters reach the HTML
    assert "None" not in body and "None" not in _docx_text(r)     # missing fields are blank, not the word None
    assert "overflow-wrap" in h                                    # long words / URLs wrap inside the page
    _docx_text(r)                                                  # DOCX opens with python-docx


def test_special_characters_survive_escaped():
    h = resume.render_html(VERY_LONG, "classic")
    assert "R&amp;D" in h and "&lt;script&gt;" in h and "Åström" in h and "李小龍" in h
    assert "Åström" in _docx_text(VERY_LONG)


def test_missing_contact_and_dates_leave_no_stray_separators():
    h = resume.render_html({"name": "A", "skills": ["x"], "experience": [{"title": "Dev", "company": "X", "bullets": ["b"]}]})
    assert "| </div>" not in h and "&nbsp;|&nbsp;</div>" not in h and " - </span>" not in h
    d = _docx_text({"name": "A", "education": [{"school": "S"}, {"degree": "D"}], "experience": [{"title": "Dev"}]})
    assert "None" not in d and ", S" not in d and "(-" not in d and "( - )" not in d


def test_fit_helpers_accept_hostile_profile():
    resume._trim_one(resume._norm(HOSTILE))  # must not raise


@pytest.mark.parametrize("tpl", list(resume.TEMPLATES))
@pytest.mark.parametrize("name", ["short", "very_long"])
def test_real_pdf_stays_within_page_policy(tpl, name, monkeypatch):
    """Real browser: 'one' = exactly one page; 'auto' = one or two. Slow, so only short + very long per template."""
    if not resume._browser():
        pytest.skip("no Edge/Chrome")
    monkeypatch.undo()  # conftest stubs the PDF renderer
    for mode, allowed in (("one", {1}), ("auto", {1, 2})):
        _, pdf, pages, flag = resume.fit_one_page(PROFILES[name], tpl, mode)
        assert pdf[:4] == b"%PDF" and (pages in allowed or flag), (tpl, name, mode, pages)
        assert resume.pdf_pages(pdf) == pages


def test_fresher_leads_with_education_and_skills_do_not_split():
    h = resume.render_html({"name": "N", "skills": ["MS Excel", "Tally ERP 9"], "projects": [{"name": "P", "bullets": ["b"]}],
                            "education": [{"degree": "B.Com", "school": "U", "year": "2024"}]})
    assert h.index("<h2>Education") < h.index("<h2>Projects") and "Experience" not in h
    assert "<span style='white-space:nowrap'>MS Excel</span>" in h
