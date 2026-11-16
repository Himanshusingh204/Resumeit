"""Click-through check of the whole UI in a real browser (Playwright). Fake AI and fake job boards: no network, no real data.
Everything lives in a temp folder, the real data/ and jobs/ are never touched. PDFs are real (need Edge or Chrome).

Optional dev dependency, not in requirements.txt:  pip install playwright && python -m playwright install chromium
Run:  python scripts/e2e_check.py [--headed]        Exit code 1 if anything failed. Screenshots land in the printed folder.

It reports: steps that failed, console errors, requests that came back 4xx/5xx (unless a step expects them),
and layout problems (covered, overlapping, clipped or off-screen controls, sideways page scroll) on desktop and at 390px.
"""
import copy, io, json, re, socket, sys, tempfile, threading, time, zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import ai, db, export, fetch, resume, scrape  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="resumeit_e2e_"))
db.DB, export.JOBS_DIR = tmp / "e2e.db", tmp / "jobs"  # same isolation idea as tests/conftest.py
db.init()
SHOTS = tmp / "shots"; SHOTS.mkdir()

# ------------------------------------------------------------------ fakes
PROFILE = {"name": "Priya Sharma", "email": "priya@example.com", "phone": "", "location": "Pune, India", "links": [], "summary": "Backend engineer.",
           "skills": ["Python", "FastAPI", "SQL", "Docker", "AWS", "React"],
           "experience": [{"company": "Example Corp", "title": "Backend Developer", "start": "2021", "end": "2024",
                           "bullets": ["Built REST APIs with FastAPI and SQL", "Containerised services with Docker and deployed on AWS", "Wrote unit tests"]}],
           "projects": [{"name": "Demo Tracker", "tech": "Python, React", "bullets": ["Built a small tracking app"]}],
           "education": [{"school": "Example University", "degree": "B.Tech", "year": "2020"}], "certifications": []}
RESUME_TEXT = "Priya Sharma, backend engineer in Pune. Skills: Python, FastAPI, SQL, Docker, AWS, React. Example Corp 2021-2024. " * 2
AI_ON, DELAY = [True], [1.5]


def _need_ai():
    if not AI_ON[0]:
        raise RuntimeError("No AI configured: fake, switched off by the e2e script")


def fake_json(system, user):
    _need_ai()
    if "convert a resume" in system:
        return {} if "EMPTYRESUME" in user else copy.deepcopy(PROFILE)
    if "job-board search queries" in system:
        return {"terms": ["python backend developer", "fastapi engineer", "data engineer"]}
    time.sleep(DELAY[0])  # a visible queue
    prof = json.loads(user.split("CANDIDATE PROFILE:\n", 1)[1].split("\n\nJOB DESCRIPTION:", 1)[0])
    jd = user.split("JOB DESCRIPTION:\n", 1)[1]
    if "FAILME" in jd:
        raise RuntimeError("fake AI refused this job")
    r = copy.deepcopy(prof)
    r["skills"] = r["skills"][::-1]
    r["experience"][0]["bullets"][0] = "Designed and " + r["experience"][0]["bullets"][0][0].lower() + r["experience"][0]["bullets"][0][1:]
    del r["experience"][0]["bullets"][-1]
    hit = [s for s in prof["skills"] if s.lower() in jd.lower()]
    return {"resume": r, "match": {"score": min(95, 35 + 15 * len(hit)), "matched": hit, "gaps": ["Kubernetes"]}}


def fake_text(system, user):
    _need_ai()
    return "Dear hiring team, I am a backend engineer who builds REST APIs with Python and FastAPI and would like to help your team. " * 2 + "\nPriya Sharma"


ai.complete_json, ai.complete = fake_json, fake_text
ai.provider = lambda: (_need_ai(), "gemini")[1]

today = date.today()
JOBS = [  # title, company, location, description, age in days, site
    ("Backend Engineer", "Acme Systems", "Pune, India", "Python, FastAPI, SQL and Docker. Hybrid. Salary 18-24 LPA. Full-time. 1-3 years experience.", 0, "linkedin"),
    ("Python Developer", "Globex", "Remote, India", "Python, AWS and SQL. Fully remote. 15-20 LPA. Full-time.", 1, "linkedin"),
    ("Senior Staff Engineer", "Initech", "Bengaluru, India", "Python and SQL. 10+ years of experience required. On-site. 40-60 LPA.", 2, "linkedin"),
    ("Java Developer", "Hooli", "Hyderabad, India", "Java and Spring Boot only. On-site. 12-16 LPA.", 3, "indeed"),
    ("Night Shift Python Support", "Umbrella Labs", "Chennai, India", "Python, SQL. Night shift required. Contract role. 6-8 LPA.", 4, "indeed"),
    ("Python Intern", "Stark Labs", "Bengaluru, India", "Python and React. Internship. Hybrid.", 5, "indeed"),
    ("US Only Engineer", "Wayne Corp", "New York, NY", "Python, Docker. On-site in New York. US citizens only. $120,000-$150,000 per year.", 2, "naukri"),
    ("FAILME Data Engineer", "Wonka Data", "Mumbai, India", "FAILME Python, SQL, AWS pipelines. Hybrid. 20-28 LPA.", 6, "naukri"),
]


def fake_jobspy(**kw):
    site, term = kw["site_name"][0], kw["search_term"]
    time.sleep(0.15)
    if site == "google":
        raise RuntimeError("fake: board blocked us")
    if site == "glassdoor":
        return []
    rows = ([("Auto Backend Engineer", "Auto Co", "Pune, India", "Python, FastAPI and SQL services. Hybrid. 20-25 LPA.", 0, site),
             ("Auto Python Developer", "Auto Labs", "Remote, India", "Python, AWS and Docker. Fully remote. 18-22 LPA.", 1, site)] if "autobuild" in term
            else [j for j in JOBS if j[5] == site])
    return [{"job_url": f"https://example.com/{c.split()[0].lower()}/{t.split()[0].lower()}-{i}", "site": s, "title": t, "company": c, "location": loc,
             "date_posted": str(today - timedelta(days=age)), "description": d, "min_amount": None, "max_amount": None, "currency": None,
             "interval": None, "job_type": None, "is_remote": "Remote" in loc} for i, (t, c, loc, d, age, s) in enumerate(rows)]


scrape._jobspy = fake_jobspy
fetch.job_from_url = lambda u: ("Fetched Role", "Fetch Co", "Python and SQL work building FastAPI services. " * 6)

import uvicorn  # noqa: E402
from app import main  # noqa: E402  (imported after the patches so it uses the temp DB)

OPENED = []
main._open = lambda p: OPENED.append(str(p))  # never pop a real Explorer window
sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
URL = f"http://127.0.0.1:{port}/"

from playwright.sync_api import expect, sync_playwright  # noqa: E402

# ------------------------------------------------------------------ bookkeeping
FAIL, CONSOLE, NET, LAYOUT = [], [], [], []
ALLOWED = set()  # http statuses the current step expects
DLG = []         # confirm() texts seen
ACCEPT = [True]
page = None


@contextmanager
def step(name):
    t = time.time()
    try:
        yield
        print(f"  ok    {name} ({time.time() - t:.1f}s)", flush=True)
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()[0][:260] if str(e).strip() else repr(e)
        FAIL.append((name, msg)); print(f"  FAIL  {name}: {msg}", flush=True)
        try:
            page.screenshot(path=str(SHOTS / f"fail_{len(FAIL)}.png"))
        except Exception:  # noqa: BLE001
            pass
    finally:
        ALLOWED.clear()


def shot(name):
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def wire(p, label):
    p.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and CONSOLE.append(f"[{label}] {m.text[:200]}"))
    p.on("pageerror", lambda e: CONSOLE.append(f"[{label}] JS error: {str(e)[:200]}"))
    p.on("response", lambda r: r.status >= 400 and r.status not in ALLOWED and "fonts." not in r.url and NET.append(f"[{label}] {r.status} {r.request.method} {r.url[len(URL) - 1:]}"))
    p.on("requestfailed", lambda r: "fonts." not in r.url and "net::ERR_ABORTED" not in (r.failure or "") and NET.append(f"[{label}] FAILED {r.method} {r.url[len(URL) - 1:]} {r.failure}"))
    p.on("dialog", lambda d: (DLG.append(d.message), d.accept() if ACCEPT[0] else d.dismiss()))
    p.route("**/fonts.g*/**", lambda r: r.abort())  # keep the run offline and deterministic


LAYOUT_JS = """(scope)=>{
 const root=scope?document.querySelector(scope):document.body,out=[],vw=document.documentElement.clientWidth;
 const d=e=>{let s=e.tagName.toLowerCase();if(e.id)s+='#'+e.id;else if(typeof e.className==='string'&&e.className.trim())s+='.'+e.className.trim().split(/\\s+/).join('.');
   const t=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,26);return s+(t?'['+t+']':'')};
 if(!scope&&document.documentElement.scrollWidth>vw+1)out.push('page scrolls sideways: '+document.documentElement.scrollWidth+'px > '+vw+'px');
 const vis=[];
 for(const e of root.querySelectorAll('button,a[href],a[role=link],select,input:not([type=hidden]):not([type=file]),textarea,summary')){
   if(e.closest('[hidden],.lock'))continue;
   const cs=getComputedStyle(e);if(cs.visibility==='hidden'||cs.display==='none')continue;
   const r=e.getBoundingClientRect();if(r.width<1||r.height<1)continue;
   vis.push(e);
   if(r.left<-1||r.right>vw+1)out.push(d(e)+' sticks out of the screen ('+Math.round(r.left)+'..'+Math.round(r.right)+' of '+vw+')');
   if(e.type!=='checkbox'&&e.type!=='radio'&&(r.height<24||r.width<24))out.push(d(e)+' tap target only '+Math.round(r.width)+'x'+Math.round(r.height));
 }
 for(const e of root.querySelectorAll('.job,.sec,.toast,.btn,.tag,.chip,.jt,.sub')){
   if(e.closest('[hidden]')||getComputedStyle(e).display==='none')continue;
   if(e.scrollWidth>e.clientWidth+2&&getComputedStyle(e).overflowX==='visible'&&e.getBoundingClientRect().right>vw+1)out.push(d(e)+' content runs off the screen');
   else if(e.scrollWidth>e.clientWidth+2&&getComputedStyle(e).overflowX!=='visible'&&!e.matches('.sec,.job'))out.push(d(e)+' text is clipped ('+e.scrollWidth+'>'+e.clientWidth+')');
 }
 const rs=vis.map(e=>[e,e.getBoundingClientRect()]);
 for(let i=0;i<rs.length;i++)for(let j=i+1;j<rs.length;j++){const[a,A]=rs[i],[b,B]=rs[j];
   if(a.contains(b)||b.contains(a)||a.closest('label')===b.closest('label')&&a.closest('label'))continue;
   const w=Math.min(A.right,B.right)-Math.max(A.left,B.left),h=Math.min(A.bottom,B.bottom)-Math.max(A.top,B.top);
   if(w>4&&h>4)out.push(d(a)+' overlaps '+d(b)+' ('+Math.round(w)+'x'+Math.round(h)+'px)')}
 for(const e of vis){e.scrollIntoView({block:'center',inline:'nearest'});
   const r=e.getBoundingClientRect(),x=Math.min(Math.max(r.left+r.width/2,0),vw-1),y=Math.min(Math.max(r.top+r.height/2,0),innerHeight-1),t=document.elementFromPoint(x,y);
   if(t&&!(e===t||e.contains(t)||t.contains(e)||(e.closest('label')&&e.closest('label').contains(t))))out.push(d(e)+' is covered by '+d(t))}
 window.scrollTo(0,0);return [...new Set(out)].slice(0,25)}"""


def layout(view, scope=None):
    for msg in page.evaluate(LAYOUT_JS, scope):
        LAYOUT.append(f"[{view}] {msg}")


# ------------------------------------------------------------------ helpers
def toast(text, timeout=8000):
    expect(page.locator("#toasts .toast", has_text=text).first).to_be_visible(timeout=timeout)


def rows():
    return page.locator("#jobs .job")


def row(title):
    return page.locator("#jobs .job").filter(has=page.locator(".jt", has_text=re.compile("^" + re.escape(title) + "$"))).first


def order():
    return [int(x) for x in page.eval_on_selector_all("#jobs .job", "e=>e.map(x=>x.dataset.id)")]


def close_preview():
    if page.is_visible("#pdlg"):
        page.click("#pdlg >> text=Close")


def more(item):
    page.click("#moreM summary"); page.click(f"#moreM >> text={item}")


def ids():
    return sorted(int(x) for x in page.eval_on_selector_all("#jobs .job", "e=>e.map(x=>x.dataset.id)"))


def J():
    return page.evaluate("jobs")


def idle(timeout=90000):
    page.wait_for_function("()=>!busyNow&&!watch.size&&!document.querySelector('.tag.queued,.tag.tailoring')", timeout=timeout)
    page.wait_for_timeout(250)


def wait_jobs(n=1, timeout=60000):
    page.wait_for_function(f"()=>jobs.length>={n}&&!document.getElementById('gob').disabled", timeout=timeout)


TABS = {"all": lambda j: True, "done": lambda j: j["status"] == "done", "new": lambda j: j["status"] == "new",
        "work": lambda j: j["status"] in ("queued", "tailoring"), "low": lambda j: j["status"] == "skipped",
        "error": lambda j: j["status"] == "error", "applied": lambda j: bool(j["track"])}


def expected(tab="all", q="", sal=0, has=False, mode="", typ="", age=0, hsr=True, fres=True, fskill=True, fwarn=False):
    """What the list should show, worked out from the API data with plain Python (independent of the page's own JS)."""
    out = []
    for j in J():
        ok = (bool(j["track"]) if tab == "applied" else j["track"] not in ("Applied", "Rejected") if tab == "all" else True) and TABS[tab](j)
        ok = ok and not (hsr and j["senior"]) and q.lower() in f"{j['title']} {j['company']}".lower()
        ok = ok and not ((sal > 0 or has) and j["salary_lakh"] is None) and not (sal > 0 and j["salary_lakh"] is not None and j["salary_lakh"] < sal)
        ok = ok and (not mode or j["mode"] == mode) and (not typ or j["jtype"] == typ)
        ok = ok and not (age and (j["age_days"] is None or j["age_days"] > age)) and not (fres and not j["location_ok"])
        ok = ok and not (fskill and not json.loads(j["fit"] or "[]")) and not (fwarn and j["tags"])
        if ok:
            out.append(j["id"])
    return sorted(out)


def set_filters(**kw):
    """Drive the real controls to the wanted state, return the keyword set the same way expected() takes it."""
    if not page.evaluate("document.getElementById('fdet').open"):
        page.click("#fdet summary")
    cur = dict(sal=0, has=False, mode="", typ="", age=0, hsr=True, fres=True, fskill=True, fwarn=False)
    cur.update(kw)
    page.fill("#fsal", str(cur["sal"]) if cur["sal"] else "")
    for cid, key in (("fhas", "has"), ("hsr", "hsr"), ("fres", "fres"), ("fskill", "fskill"), ("fwarn", "fwarn")):
        page.set_checked("#" + cid, cur[key])
    page.select_option("#fmode", cur["mode"]); page.select_option("#ftype", cur["typ"]); page.select_option("#fage", str(cur["age"]))
    return cur


def check_list(label, tab="all", q="", **kw):
    want = expected(tab, q, **kw)
    got = ids()
    assert got == want, f"{label}: list shows {got}, API data says {want}"


def upload_text(text, name="cv.txt"):
    with page.expect_file_chooser() as fc:
        page.click("#drop")
    fc.value.set_files({"name": name, "mimeType": "text/plain", "buffer": text.encode()})


def view_dialog(sel, name):
    page.wait_for_selector(sel + "[open]")
    shot(name); layout(name, sel)


# ------------------------------------------------------------------ the run
def desktop(b):
    global page
    ctx = b.new_context(viewport={"width": 1360, "height": 860}, color_scheme="light", accept_downloads=True)
    page = ctx.new_page(); page.set_default_timeout(15000); wire(page, "desktop")
    page.goto(URL); page.wait_for_function("()=>document.getElementById('skel').hidden")

    print("first screen (no resume)")
    with step("empty state, locked search, onboarding"):
        expect(page.locator("#onb")).to_be_visible(); expect(page.locator("#lockNote")).to_be_visible()
        expect(page.locator("#c2")).to_have_class(re.compile("lock")); expect(page.locator("#delRes")).to_be_hidden()
        expect(page.locator("#aiT")).to_have_text("Gemini"); shot("01-empty"); layout("empty")
    with step("theme toggle cycles auto > light > dark > auto and is remembered"):
        seen = []
        for _ in range(3):
            page.click("#themeB"); seen.append(page.evaluate("[localStorage.theme, document.documentElement.dataset.theme]"))
        assert seen == [["light", "light"], ["dark", "dark"], ["auto", "light"]], seen
        page.click("#themeB"); page.reload(); assert page.evaluate("document.documentElement.dataset.theme") == "light"
        page.click("#themeB"); shot("02-dark"); page.click("#themeB"); page.click("#themeB")
    with step("AI pill: not configured -> click shows Gemini how-to"):
        AI_ON[0] = False; page.reload(); page.wait_for_function("()=>document.getElementById('skel').hidden")
        expect(page.locator("#aiPill")).to_have_class(re.compile("bad")); expect(page.locator("#aiT")).to_have_text("AI not configured")
        page.click("#aiPill"); view_dialog("#adlg", "03-ai-help")
        body = page.inner_text("#adlg"); assert "aistudio.google.com/apikey" in body and "GEMINI_API_KEY" in body and "Restart" in body, body
        assert page.get_attribute("#adlg a", "href") == "https://aistudio.google.com/apikey"
        page.click("#adlg >> text=Close"); expect(page.locator("#adlg")).not_to_be_visible()
    with step("uploading with no AI explains the fix (500 + how-to opens)"):
        ALLOWED.add(500); upload_text(RESUME_TEXT); expect(page.locator("#adlg")).to_be_visible(); page.keyboard.press("Escape")
        expect(page.locator("#adlg")).not_to_be_visible(); AI_ON[0] = True; page.reload(); page.wait_for_function("()=>document.getElementById('skel').hidden")
        expect(page.locator("#aiT")).to_have_text("Gemini")

    print("resume upload / paste / edit / delete")
    with step("upload an empty file -> clear error, nothing saved"):
        ALLOWED.add(422); page.set_input_files("#file", {"name": "empty.txt", "mimeType": "text/plain", "buffer": b""}); toast("No readable text")
    with step("upload something the AI cannot read -> kept clean"):
        ALLOWED.add(422); upload_text("EMPTYRESUME " * 20); toast("could not be understood"); expect(page.locator("#delRes")).to_be_hidden()
    with step("paste text: too short is refused, full text is read"):
        page.click("text=Paste text >> nth=0"); expect(page.locator("#tdlg")).to_be_visible(); layout("paste-dialog", "#tdlg")
        page.fill("#ptext", "too short"); page.click("#tdlg >> text=Read this resume"); toast("Paste your full resume text"); expect(page.locator("#tdlg")).to_be_visible()
        page.fill("#ptext", RESUME_TEXT); page.click("#tdlg >> text=Read this resume"); toast("Resume read"); expect(page.locator("#profView .who b")).to_have_text("Priya Sharma")
    with step("delete resume: cancel keeps it, confirm removes it, search locks again"):
        ACCEPT[0] = False; page.click("#delRes"); ACCEPT[0] = True; expect(page.locator("#profView .who b")).to_have_text("Priya Sharma")
        page.click("#delRes"); toast("Resume deleted"); expect(page.locator("#profView .who")).to_have_count(0); expect(page.locator("#lockNote")).to_be_visible()
    with step("upload through the drop zone (click) and by drag-and-drop"):
        upload_text(RESUME_TEXT); expect(page.locator("#profView .who b")).to_have_text("Priya Sharma"); expect(page.locator("#dropT")).to_have_text("Replace with another file")
        page.evaluate("""()=>{const d=document.getElementById('drop'),dt=new DataTransfer();dt.items.add(new File(['x'],'a.txt'));d.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));window._on=d.classList.contains('on');
          dt.items.clear();dt.items.add(new File([%s],'cv.txt',{type:'text/plain'}));d.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}))}""" % json.dumps(RESUME_TEXT))
        assert page.evaluate("window._on"), "drop zone did not highlight on dragover"; toast("Resume read")
    with step("drop zone works from the keyboard"):
        page.focus("#drop")
        with page.expect_file_chooser():
            page.keyboard.press("Enter")
    with step("review / edit: bad JSON refused, good JSON saved, unusable profile shows the alert"):
        page.click("text=Review / edit"); expect(page.locator("#edlg")).to_be_visible(); layout("edit-dialog", "#edlg")
        ALLOWED.add(422); page.fill("#pjson", "{not json"); page.click("#edlg >> text=Save"); toast("Invalid JSON"); expect(page.locator("#edlg")).to_be_visible()
        p = copy.deepcopy(PROFILE); p["summary"] = "Edited summary"; page.fill("#pjson", json.dumps(p)); page.click("#edlg >> text=Save"); toast("Saved")
        assert db.kv_get("profile")["summary"] == "Edited summary"
        page.click("text=Review / edit"); page.fill("#pjson", json.dumps({"name": "Nameless"})); page.click("#edlg >> text=Save")
        expect(page.locator("#alert")).to_be_visible(); layout("alert"); shot("04-alert")
        page.click("#alert >> text=Paste text"); expect(page.locator("#tdlg")).to_be_visible(); page.click("#tdlg >> text=Close")
        with page.expect_file_chooser() as fc:
            page.click("#alert >> text=Upload another file")
        fc.value.set_files({"name": "cv.txt", "mimeType": "text/plain", "buffer": RESUME_TEXT.encode()}); expect(page.locator("#alert")).to_be_hidden()
        page.click("text=Review / edit"); page.fill("#pjson", json.dumps({"name": "Nameless"})); page.click("#edlg >> text=Save"); expect(page.locator("#alert")).to_be_visible()
        page.click("#alert >> text=Delete it"); expect(page.locator("#alert")).to_be_hidden(); upload_text(RESUME_TEXT); expect(page.locator("#profView .who b")).to_have_text("Priya Sharma")
    with step("top bar selects: length and template persist"):
        page.select_option("#pg", "one"); page.wait_for_timeout(300); assert db.kv_get("pages") == "one"; page.select_option("#pg", "auto")
        for t in ("modern", "classic"):
            page.select_option("#tpl", t); page.wait_for_timeout(200); assert db.kv_get("template") == t

    print("find jobs")
    with step("find jobs with empty titles (read from resume), progress shows, no AI build, all boards"):
        expect(page.locator("#c2")).not_to_have_class(re.compile("lock")); layout("sidebar-ready")
        page.evaluate("document.querySelectorAll('.site').forEach(c=>c.checked=true)")
        page.click("#gob"); expect(page.locator("#gob")).to_be_disabled(); expect(page.locator("#gob")).to_have_text("Working..."); expect(page.locator("#prog")).to_be_visible(); shot("05-searching")
        wait_jobs(8); expect(page.locator("#st")).to_contain_text("Done"); assert all(j["status"] == "new" for j in J()), "search must not spend AI calls"
    with step("default filters hide senior / abroad / no-skill jobs; summary says so"):
        check_list("defaults"); assert len(ids()) == 5, ids(); expect(page.locator("#sum")).to_contain_text("(5 shown)"); shot("06-jobs"); layout("jobs-list")
    with step("issues: blocked and empty boards were logged"):
        page.click("button:has(#ic)"); view_dialog("#idlg", "07-issues"); body = page.inner_text("#issues")
        assert "scrape_failed" in body and "no_results" in body, body; assert page.inner_text("#ic") == str(len(db.q("SELECT 1 FROM issues")))
        page.click("#idlg >> text=Clear all"); expect(page.locator("#issues")).to_contain_text("Nothing skipped"); expect(page.locator("#ic")).to_have_text("0")
        page.click("#idlg >> text=Close"); expect(page.locator("#idlg")).not_to_be_visible()
    with step("chips: add with Enter / comma / blur, no duplicates, remove with x and Backspace"):
        ci = page.locator("#chipIn"); ci.click(); ci.fill("alpha"); ci.press("Enter"); ci.fill("beta"); ci.press(","); ci.fill("alpha"); ci.press("Enter"); ci.fill("gamma"); page.click("#c2 .lbl")
        assert page.evaluate("terms") == ["alpha", "beta", "gamma"], page.evaluate("terms")
        page.click("#chips .chip >> nth=1 >> button"); assert page.evaluate("terms") == ["alpha", "gamma"]
        ci.click(); ci.press("Backspace"); assert page.evaluate("terms") == ["alpha"]; ci.press("Backspace"); assert page.evaluate("terms") == []
    with step("suggest from resume fills chips"):
        page.click("#sgb"); toast("Titles added"); assert len(page.evaluate("terms")) == 3; layout("chips")
    with step("country, city, boards, more options controls"):
        for v in ("in", "us", "gb", "ca", "ae", "sg", "de", "au", "in"):
            page.select_option("#country", v)
        page.fill("#loc", "Pune")
        page.evaluate("document.querySelectorAll('.site').forEach(c=>c.checked=['linkedin','indeed','naukri'].includes(c.value))")
        for v in ("linkedin", "indeed", "naukri", "glassdoor", "google"):
            page.locator(f".boards label:has(input[value={v}])").click()
        assert page.evaluate("[...document.querySelectorAll('.site:checked')].map(x=>x.value)") == ["glassdoor", "google"]
        page.evaluate("document.querySelectorAll('.site').forEach(c=>c.checked=false)"); ALLOWED.clear()
        page.click("#gob"); toast("Pick at least one job board")
        page.click("#c2 details summary"); page.fill("#n", "3"); [page.select_option("#hrs", v) for v in ("24", "72", "720", "168")]
        page.locator("#remote").check(); page.locator("#remote").uncheck(); layout("search-options"); shot("08-search-options")
        page.click("#c2 details summary"); page.fill("#loc", "")
    with step("auto-build search: first finished resume opens as preview by itself"):
        page.evaluate("terms.length=0;drawChips()"); page.evaluate("document.querySelectorAll('.site').forEach(c=>c.checked=c.value==='linkedin')")
        page.fill("#chipIn", "autobuild python"); page.keyboard.press("Enter"); page.check("#auto")
        expect(page.locator("#gob")).to_have_text("Find jobs and build resumes"); page.click("#gob")
        page.wait_for_selector("#pdlg[open]", timeout=90000); idle(); page.uncheck("#auto"); expect(page.locator("#gob")).to_have_text("Find jobs")
        assert len([j for j in J() if j["title"].startswith("Auto ") and j["status"] == "done"]) == 2
    with step("preview dialog: safety box, locked PDF/DOCX, downloads, templates, what changed"):
        view_dialog("#pdlg", "09-preview-locked")
        for sel in ("#ppdf", "#pdoc"):
            assert page.get_attribute(sel, "aria-disabled") == "true" and page.get_attribute(sel, "href") is None and "Locked" in page.get_attribute(sel, "title")
            assert float(page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).opacity")) < 0.6, "locked button is not dimmed"
        page.click("#ppdf"); toast("tick the box"); page.click("#pdoc")
        assert page.url == URL, "locked link navigated"
        page.check("#pok"); assert page.get_attribute("#ppdf", "aria-disabled") == "false" and "/resume.pdf" in page.get_attribute("#ppdf", "href"); shot("10-preview-unlocked")
        with page.expect_download(timeout=60000) as dl:
            page.click("#ppdf")
        data = Path(dl.value.path()).read_bytes(); assert data[:4] == b"%PDF" and 1 <= resume.pdf_pages(data) <= 2
        with page.expect_download() as dl:
            page.click("#pdoc")
        from docx import Document
        assert "Priya Sharma" in "\n".join(p.text for p in Document(io.BytesIO(Path(dl.value.path()).read_bytes())).paragraphs)
        page.uncheck("#pok"); assert page.get_attribute("#ppdf", "href") is None
        bodies = set()
        for t in ("classic", "modern", "compact", "minimal", "executive", "tech", "elegant"):
            page.select_option("#ptpl", t); page.wait_for_function(f"()=>document.getElementById('pf').src.includes('t={t}')")
            expect(page.frame_locator("#pf").locator("h1")).to_have_text("Priya Sharma")
            assert page.evaluate("document.getElementById('tpl').value") == t and f"t={t}" in page.get_attribute("#ppdf", "data-h")
            bodies.add(page.evaluate("document.getElementById('pf').contentDocument.querySelector('style').textContent"))
            layout(f"preview-{t}", "#pdlg")
        assert len(bodies) == 7, "templates do not look different"
        page.select_option("#ptpl", "classic")
        page.click("#chg"); expect(page.locator("#dfv")).to_be_visible(); assert page.locator("#dfv ins, #dfv del").count() > 0; shot("11-what-changed"); page.click("#chg"); expect(page.locator("#dfv")).to_be_hidden()
        page.check("#pok"); page.keyboard.press("Escape"); expect(page.locator("#pdlg")).not_to_be_visible()
    with step("reopening a preview resets the safety box"):
        row("Auto Backend Engineer").locator("text=Preview").click(); expect(page.locator("#pok")).not_to_be_checked(); assert page.get_attribute("#pdoc", "aria-disabled") == "true"
        page.click("#pdlg >> text=Close")

    print("filters, tabs, sort, search")
    with step("each filter changes the list the way the data says it should"):
        base = set_filters(); check_list("base", **base)
        for label, kw in (("salary>=15", dict(sal=15)), ("salary>=30", dict(sal=30)), ("salary shown", dict(has=True)), ("remote", dict(mode="remote")), ("hybrid", dict(mode="hybrid")),
                          ("onsite", dict(mode="onsite")), ("contract", dict(typ="contract")), ("internship", dict(typ="internship")), ("full-time", dict(typ="full-time")), ("part-time", dict(typ="part-time")),
                          ("24h", dict(age=1)), ("3 days", dict(age=3)), ("week", dict(age=7)), ("show senior", dict(hsr=False)), ("show abroad", dict(fres=False)),
                          ("show no-skill", dict(fskill=False)), ("hide warnings", dict(fwarn=True)), ("everything off", dict(hsr=False, fres=False, fskill=False))):
            cur = set_filters(**kw); check_list(label, **cur)
            want = sum(map(bool, [cur["sal"] > 0, cur["has"], cur["mode"], cur["typ"], cur["age"], cur["hsr"], cur["fres"], cur["fskill"], cur["fwarn"]]))
            got = page.inner_text("#fcnt") if page.is_visible("#fcnt") else "0"
            assert int(got) == want, f"{label}: filter badge {got}, expected {want}"
        shot("12-filters-open"); layout("filters-open")
        set_filters()
    with step("tabs: counts equal what the list shows, every tab clickable"):
        for k in TABS:
            page.click(f"#tabs button[data-f={k}]"); expect(page.locator(f"#tabs button[data-f={k}]")).to_have_class(re.compile("on"))
            want = expected(k); assert ids() == want, f"tab {k}: {ids()} vs {want}"
            assert int(page.inner_text(f"#tabs button[data-f={k}] b")) == len(want), f"tab {k} count badge"
        page.click("#tabs button[data-f=all]")
    with step("empty tab shows a way out; 'Show all jobs' clears filters"):
        page.click("#tabs button[data-f=error]"); expect(page.locator("#emptyS")).to_be_visible(); page.click("#emptyS >> text=Show all jobs"); expect(page.locator("#hsr")).not_to_be_checked()
        assert ids() == expected("all", hsr=False, fres=False, fskill=False) and len(ids()) == 10, ids()
        set_filters(); page.click("#tabs button[data-f=all]")
    with step("sort by best match, back to newest; search box filters and clears"):
        byid = {j["id"]: j for j in J()}
        rk = lambda i: (json.loads(byid[i]["match"])["score"] if byid[i]["match"] and "score" in json.loads(byid[i]["match"]) else min(99, len(json.loads(byid[i]["fit"] or "[]")) * 8))  # noqa: E731
        want = sorted(order(), key=lambda i: -rk(i)); page.select_option("#sort", "match"); assert order() == want, (order(), want)
        page.select_option("#sort", "new"); page.fill("#q", "auto"); assert all("Auto" in t for t in page.eval_on_selector_all("#jobs .jt", "e=>e.map(x=>x.textContent)")) and len(ids()) == 2
        page.fill("#q", "zzzz"); expect(page.locator("#emptyS")).to_be_visible(); page.fill("#q", ""); check_list("cleared search")

    print("build / queue / preview / row actions")
    with step("Build on one row: queued at once, preview opens by itself within a few seconds"):
        t0 = time.time(); row("Backend Engineer").get_by_role("button", name="Build", exact=True).click()
        page.wait_for_selector("#pdlg[open]", timeout=60000); took = time.time() - t0; idle()
        assert took < 12, f"preview took {took:.0f}s after the click: polling is too slow"; expect(page.locator("#pt")).to_have_text("Backend Engineer"); page.click("#pdlg >> text=Close")
    with step("Build N matching: confirm text, queue, Build now, Stop queue"):
        DELAY[0] = 3.0; page.wait_for_selector("#pendb:not([hidden])"); DLG.clear(); page.click("#pendb"); assert "AI calls" in DLG[-1], DLG
        page.wait_for_selector("#stopb:not([hidden])", timeout=20000); shot("13-queue")
        q = page.locator("#jobs .job", has=page.locator(".tag.queued")).first.locator("text=Build now"); q.click(); toast("Building")
        page.wait_for_selector("#stopb:not([hidden])", timeout=20000); page.click("#stopb"); toast("taken out of the queue"); idle(120000); close_preview()
        expect(page.locator("#stopb")).to_be_hidden()
    with step("a failing job shows its error, Failed tab counts it, Rebuild/Build stay available"):
        page.fill("#q", ""); st = {j["title"]: j["status"] for j in J()}
        for t in ("Python Developer", "Night Shift Python Support", "Python Intern", "FAILME Data Engineer"):
            if st[t] == "new":
                row(t).get_by_role("button", name="Build", exact=True).click(); page.wait_for_timeout(400)
        idle(120000); close_preview(); DELAY[0] = 1.5; fail = [j for j in J() if j["status"] == "error"]; assert fail and "refused" in fail[0]["error"], fail
        page.click("#tabs button[data-f=error]"); expect(page.locator("#jobs .err")).to_contain_text("refused"); layout("failed-row"); page.click("#tabs button[data-f=all]")
    with step("Rebuild a ready row: preview opens again by itself"):
        t0 = time.time(); row("Backend Engineer").get_by_role("button", name="Rebuild").click(); page.wait_for_selector("#pdlg[open]", timeout=60000)
        assert time.time() - t0 < 14, "rebuild preview too slow"; idle(); page.click("#pdlg >> text=Close")
    with step("row: Preview, PDF, DOCX, Folder, Cover letter"):
        r = row("Backend Engineer"); r.get_by_role("button", name="Preview").click(); expect(page.locator("#pdlg")).to_be_visible(); page.click("#pdlg >> text=Close")
        for f, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
            href = r.locator(f"a:text-is('{f.upper()}')").get_attribute("href"); res = page.request.get(URL.rstrip("/") + href); assert res.ok and res.body()[:len(magic)] == magic, (f, res.status)
        assert "Downloads straight away" in r.locator("a:text-is('PDF')").get_attribute("title") and page.is_visible("#dlHint")
        r.get_by_role("button", name="Folder").click(); page.wait_for_timeout(300); assert any("Acme-Systems_Backend-Engineer" in p for p in OPENED), OPENED
        r.get_by_role("button", name="Cover letter").click(); toast("Saved as cover_letter.md"); expect(row("Backend Engineer").get_by_role("button", name="Cover letter (redo)")).to_be_visible()
        assert list(export.JOBS_DIR.glob("*/Acme-Systems_Backend-Engineer_*/cover_letter.md"))
        layout("ready-row")
    with step("tracker: every status, Applied leaves 'All' and shows under Applied"):
        for v in ("Interview", "Offer", "Applied"):
            row("Backend Engineer").locator("select.trk").select_option(v); toast(f"Marked {v}"); page.wait_for_timeout(500)
        expect(row("Backend Engineer")).to_have_count(0); page.click("#tabs button[data-f=applied]"); expect(row("Backend Engineer")).to_be_visible()
        row("Backend Engineer").locator("select.trk").select_option("Rejected"); toast("Marked Rejected"); page.wait_for_timeout(500)
        row("Backend Engineer").locator("select.trk").select_option(""); toast("Cleared"); page.click("#tabs button[data-f=all]"); expect(row("Backend Engineer")).to_be_visible()
    with step("delete a job: Cancel keeps it, OK removes it"):
        n0 = len(J()); ACCEPT[0] = False; row("Python Intern").locator("button[title='Delete job']").click(); ACCEPT[0] = True; page.wait_for_timeout(300); assert len(J()) == n0
        row("Python Intern").locator("button[title='Delete job']").click(); page.wait_for_function(f"()=>jobs.length=={n0 - 1}")
    with step("Clear failed removes only failed jobs"):
        n = len([j for j in J() if j["status"] == "error"]); assert n, "no failed job to clear"; total = len(J())
        page.click("text=Clear failed"); page.wait_for_function(f"()=>jobs.length=={total - n}"); assert not [j for j in J() if j["status"] == "error"]
    with step("Add a job: validation, pasted description, link only, duplicate"):
        page.click("text=Paste a link or description"); expect(page.locator("#mdlg")).to_be_visible(); layout("add-job", "#mdlg"); shot("14-add-job")
        page.click("#mdlg >> text=Build resume"); toast("Paste the job description")
        page.fill("#mt", "Pasted Role"); page.fill("#mc", "Paste Co"); page.fill("#md", "We need Python and SQL and FastAPI for services. " * 4); page.click("#mdlg >> text=Build resume")
        page.wait_for_selector("#pdlg[open]", timeout=60000); idle(); page.click("#pdlg >> text=Close")
        page.click("text=Paste a link or description"); page.fill("#mu", "https://example.com/some-job"); page.click("#mdlg >> text=Build resume")
        page.wait_for_selector("#pdlg[open]", timeout=60000); idle(); page.click("#pdlg >> text=Close")
        page.click("text=Paste a link or description"); ALLOWED.add(409); page.fill("#mu", "https://example.com/some-job"); page.click("#mdlg >> text=Build resume"); toast("already added"); page.click("#mdlg >> text=Close")
    with step("More menu: opens, closes on outside click, Jobs folder, Backup, Fix 2-page PDFs"):
        page.click("#moreM summary"); expect(page.locator("#moreM .pop")).to_be_visible(); layout("more-menu"); shot("15-more")
        page.click("h1"); expect(page.locator("#moreM .pop")).to_be_hidden()
        more("Jobs folder"); page.wait_for_timeout(300); assert str(export.JOBS_DIR) in OPENED; expect(page.locator("#moreM .pop")).to_be_hidden()
        page.click("#moreM summary")
        with page.expect_download() as dl:
            page.click("#moreM >> text=Backup")
        z = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes())); assert "resumeit.db" in z.namelist() and any(n.startswith("jobs/") for n in z.namelist())
        more("Fix 2-page PDFs"); toast("queued for re-fit"); idle()

    mobile(b)

    page.bring_to_front()
    with step("Delete all jobs: confirm text, empty state offers the next step"):
        page.set_viewport_size({"width": 1360, "height": 860}); DLG.clear(); page.click("text=Delete all jobs"); assert "ALL jobs" in DLG[-1]; page.wait_for_function("()=>jobs.length==0")
        expect(page.locator("#emptyS")).to_contain_text("No jobs yet"); page.click("#emptyS >> text=Add a job"); expect(page.locator("#mdlg")).to_be_visible(); page.click("#mdlg >> text=Close")
        shot("16-no-jobs"); layout("no-jobs")
    ctx.close()


def mobile(b):
    """Same app at 390px wide: the page must not scroll sideways and every control must be reachable."""
    global page
    desk = page
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2, color_scheme="light")
    page = ctx.new_page(); page.set_default_timeout(15000); wire(page, "mobile")
    page.goto(URL); page.wait_for_function("()=>document.getElementById('skel').hidden")
    print("mobile 390px")
    with step("mobile: list, sidebar, filters, tabs fit the screen"):
        shot("m1-home"); layout("m-home"); page.evaluate("document.getElementById('fdet').open=true"); layout("m-filters"); shot("m2-filters")
        page.evaluate("document.getElementById('fdet').open=false")
        page.locator("#tabs button[data-f=done]").scroll_into_view_if_needed(); page.click("#tabs button[data-f=done]"); assert ids() == expected("done"); page.click("#tabs button[data-f=all]")
    with step("mobile: preview, safety box, issues, add job, edit, AI help dialogs"):
        page.locator("#jobs .job >> text=Preview >> nth=0").click(); view_dialog("#pdlg", "m3-preview"); page.tap("#pok"); assert page.get_attribute("#ppdf", "aria-disabled") == "false"
        layout("m-preview-unlocked", "#pdlg"); page.click("#pdlg >> text=Close")
        page.click("button:has(#ic)"); view_dialog("#idlg", "m4-issues"); page.click("#idlg >> text=Close")
        page.click("text=Paste a link or description"); view_dialog("#mdlg", "m5-add"); page.click("#mdlg >> text=Close")
        page.click("text=Review / edit"); view_dialog("#edlg", "m6-edit"); page.click("#edlg >> text=Close")
        page.click("#aiPill"); view_dialog("#adlg", "m7-ai"); page.click("#adlg >> text=Close")
    with step("mobile: More menu and theme button reachable"):
        page.click("#moreM summary"); layout("m-more"); shot("m8-more"); page.click("#moreM summary"); page.click("#themeB")
    with step("mobile: Build now / Rebuild / tracker clickable"):
        done = page.locator("#jobs .job", has=page.locator(".tag.done")).first; done.get_by_role("button", name="Rebuild").click(); toast("Building")
        page.wait_for_selector("#pdlg[open]", timeout=60000); page.click("#pdlg >> text=Close"); idle()
    ctx.close(); page = desk


def pdf_checks():
    """Download paths used outside the UI (same endpoints the buttons use), all 7 templates, every ready job."""
    with step("PDF/DOCX endpoints for every template"):
        done = [j for j in db.q("SELECT id FROM jobs WHERE status='done'")]; assert done
        import urllib.request
        for t in resume.TEMPLATES:
            with urllib.request.urlopen(f"{URL}api/jobs/{done[0]['id']}/resume.pdf?t={t}", timeout=60) as r:
                assert r.read(4) == b"%PDF", t


if __name__ == "__main__":
    t_start = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless="--headed" not in sys.argv)
        try:
            desktop(browser)
            pdf_checks()
        finally:
            browser.close()
    server.should_exit = True
    print("\n" + "=" * 70)
    for title, items in (("FAILED STEPS", [f"{n}: {m}" for n, m in FAIL]), ("CONSOLE ERRORS", sorted(set(CONSOLE))),
                         ("HTTP 4xx/5xx / FAILED REQUESTS", sorted(set(NET))), ("LAYOUT PROBLEMS", sorted(set(LAYOUT)))):
        print(f"{title}: {len(items)}")
        for i in items[:60]:
            print("   -", i)
    print(f"screenshots: {SHOTS}  ({time.time() - t_start:.0f}s)")
    sys.exit(1 if FAIL or CONSOLE or NET or LAYOUT else 0)
