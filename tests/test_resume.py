import io

import pytest
from docx import Document

from app import ai, resume
from conftest import PROFILE

R = {"name": "A <b>", "email": "a@b.c", "skills": ["Python"],
     "experience": [{"company": "X", "title": "Dev", "start": "2020", "end": "2024", "bullets": ["Built <api> & more"]}],
     "education": [{"school": "S", "degree": "BTech", "year": "2020"}]}


def test_validate_ok():
    assert resume.validate(PROFILE, PROFILE) is None


@pytest.mark.parametrize("bad,why", [
    ({}, "no name"),
    ({"name": "A", "skills": ["x"]}, "dropped all experience"),
    ({"name": "A", "experience": [{"company": "X"}]}, "no skills"),
    ("garbage", "no name"),
])
def test_validate_flags_incomplete(bad, why):
    assert why in resume.validate(bad, PROFILE)


def test_extract_terms_dedupes_caps_and_drops_junk(monkeypatch):
    monkeypatch.setattr(ai, "complete_json", lambda s, u: {"terms": ["python dev", " python dev ", "", 5, "sql analyst", "a", "b", "c"]})
    assert resume.extract_terms(PROFILE, n=3) == ["python dev", "sql analyst", "a"]


def test_html_escapes_user_text():
    h = resume.render_html(R)
    assert "Built &lt;api&gt; &amp; more" in h and "<b>" not in h.split("<h1>")[1].split("</h1>")[0]


def test_docx_contains_content():
    d = Document(io.BytesIO(resume.render_docx(R)))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Built <api> & more" in text and "Python" in text


def test_render_handles_missing_sections():
    assert "Resume" in resume.render_html({"name": "Only Name"})
    resume.render_docx({"name": "Only Name"})


def test_extract_text_txt():
    assert resume.extract_text("cv.txt", "hello".encode()) == "hello"


def test_extract_text_docx():
    buf = io.BytesIO()
    d = Document(); d.add_paragraph("Rahul Sen"); d.save(buf)
    assert resume.extract_text("cv.docx", buf.getvalue()) == "Rahul Sen"


def test_pdf_export_returns_pdf():
    if not resume._browser():
        pytest.skip("no Edge/Chrome")
    assert resume.render_pdf(R)[:4] == b"%PDF"


def test_tailor_passes_jd_and_returns_match(monkeypatch):
    seen = {}
    def fake(system, user):
        seen["user"] = user
        return {"resume": PROFILE, "match": {"score": 60, "gaps": ["Kubernetes"]}}
    monkeypatch.setattr(ai, "complete_json", fake)
    r, m = resume.tailor(PROFILE, {"title": "BE", "company": "Foo", "description": "needs Kubernetes"})
    assert "needs Kubernetes" in seen["user"] and "Rahul Sen" in seen["user"]
    assert r == PROFILE and m["gaps"] == ["Kubernetes"]


def test_profile_problem():
    assert resume.profile_problem(PROFILE) is None
    assert resume.profile_problem({}) == "no name found"
    assert resume.profile_problem({"name": "A"}) == "no skills or experience found"
    assert resume.profile_problem(None) == "no name found"


def test_all_templates_are_distinct():
    out = {t: resume.render_html(R, t) for t in resume.TEMPLATES}
    assert {"classic", "modern", "compact"} <= set(out) and len(set(out.values())) == len(out)
    assert all("Built &lt;api&gt;" in h for h in out.values())


def test_relevance_matches_whole_skills_only():
    p = {"skills": ["Python", "C++", "SQL", "Java"]}
    hit = resume.relevance(p, {"title": "Dev", "description": "python, C++ and JavaScript on mysql"})
    assert hit == ["Python", "C++"]  # not Java (JavaScript) and not SQL (mysql)
    assert resume.relevance(p, {"title": "Chef", "description": "kitchen"}) == []
