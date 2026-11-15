from app import db, scrape


def row(url="https://j/1", desc="Python job"):
    return {"job_url": url, "description": desc, "title": "Dev " + url[-3:], "company": "Foo", "location": "Pune",
            "date_posted": None, "site": "indeed"}


def kinds():
    return sorted(i["kind"] for i in db.q("SELECT kind FROM issues"))


def test_stores_new_jobs(monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [row("https://j/1"), row("https://j/2")])
    assert len(scrape.scrape("python", sites=["indeed"])) == 2
    assert kinds() == []


def test_duplicate_url_not_stored_twice(monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [row()])
    scrape.scrape("a", sites=["indeed"])
    assert scrape.scrape("b", sites=["indeed"]) == []
    assert db.q("SELECT count(*) n FROM jobs")[0]["n"] == 1


def test_failing_site_is_skipped_and_logged_others_continue(monkeypatch):
    def fake(**kw):
        if kw["site_name"] == ["linkedin"]:
            raise RuntimeError("429 blocked")
        return [row()]
    monkeypatch.setattr(scrape, "_jobspy", fake)
    ids = scrape.scrape("python", sites=["linkedin", "indeed"])
    assert len(ids) == 1
    issue = db.q("SELECT * FROM issues")[0]
    assert issue["kind"] == "scrape_failed" and "linkedin" in issue["ref"] and "429" in issue["msg"]


def test_empty_result_is_logged(monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [])
    assert scrape.scrape("python", sites=["indeed"]) == []
    assert kinds() == ["no_results"]


def test_jobs_without_description_or_url_are_skipped_and_logged(monkeypatch):
    bad = [row(desc=None), {**row("https://j/9"), "description": float("nan")}, {**row(), "job_url": None}]
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: bad + [row("https://j/ok")])
    assert len(scrape.scrape("python", sites=["indeed"])) == 1
    assert kinds() == ["incomplete_jobs"]
    assert "3 job(s)" in db.q("SELECT msg FROM issues")[0]["msg"]


def test_naukri_only_for_india_non_remote(monkeypatch):
    seen = []
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: seen.append(kw["site_name"][0]) or [row()])
    scrape.scrape("x", country="us", sites=["naukri", "indeed"])
    scrape.scrape("x", country="in", remote=True, sites=["naukri"])
    scrape.scrape("y", country="in", sites=["naukri"])
    assert seen == ["indeed", "naukri"]


def test_issue_is_also_written_to_log_file():
    db.issue("scrape_failed", "x @ y", "boom")
    assert "scrape_failed\tx @ y\tboom" in (db.DB.parent / "issues.log").read_text(encoding="utf-8")


def test_same_title_and_company_from_another_board_is_not_stored_twice(monkeypatch):
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: [row("https://linkedin/1"), {**row("https://indeed/9"), "title": "Dev n/1", "company": "FOO"}])
    assert len(scrape.scrape("python", sites=["indeed"])) == 1


def test_location_defaults_to_country_and_google_term_is_set(monkeypatch):
    seen = []
    monkeypatch.setattr(scrape, "_jobspy", lambda **kw: seen.append(kw) or [])
    scrape.scrape("python dev", country="in", sites=["indeed"])
    scrape.scrape("python dev", location="Pune", sites=["indeed"])
    scrape.scrape("python dev", country="in", remote=True, sites=["indeed"])
    assert [k["location"] for k in seen] == ["India", "Pune", None]
    assert seen[1]["google_search_term"] == "python dev jobs Pune" and seen[2]["google_search_term"].endswith("remote")
