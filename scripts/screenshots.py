"""Regenerate docs/img/*.png with fake demo data (no real personal info, real data/ and jobs/ never touched).
Optional dev dependency, not in requirements.txt:  pip install playwright && python -m playwright install chromium
Run:  python scripts/screenshots.py
"""
import json, socket, sys, tempfile, threading, time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db, export  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="resumeit_demo_"))
db.DB, export.JOBS_DIR = tmp / "demo.db", tmp / "jobs"  # same isolation idea as tests/conftest.py
db.init()

PROFILE = {"name": "Alex Doe", "email": "alex.doe@example.com", "phone": "", "location": "Pune, India", "links": [], "summary": "Backend engineer.",
           "skills": ["Python", "FastAPI", "SQL", "Docker", "AWS", "React"],
           "experience": [{"company": "Example Corp", "title": "Backend Developer", "start": "2021", "end": "2024",
                           "bullets": ["Built REST APIs with FastAPI and SQL", "Containerised services with Docker and deployed on AWS"]}],
           "projects": [{"name": "Demo Tracker", "tech": "Python, React", "bullets": ["Built a small tracking app"]}],
           "education": [{"school": "Example University", "degree": "B.Tech", "year": "2020"}], "certifications": []}
db.kv_set("profile", PROFILE)
today = date.today()
JOBS = [("Backend Engineer", "Acme Systems", "Pune, India", "Python, FastAPI, SQL and Docker. Hybrid. Salary 18-24 LPA. Full-time.", "done"),
        ("Python Developer", "Globex", "Bengaluru, India", "Python, AWS and SQL. Remote. 15-20 LPA. 1-3 years experience.", "new"),
        ("Full Stack Developer", "Initech", "Remote, India", "React, Python and Docker. Remote. Full-time. 2+ years.", "new"),
        ("Data Engineer", "Umbrella Labs", "Hyderabad, India", "SQL, Python, AWS. Night shift may be required.", "new")]
for i, (t, c, loc, d, st) in enumerate(JOBS):
    jid = db.x("INSERT INTO jobs(url,site,title,company,location,posted,description,status) VALUES(?,?,?,?,?,?,?,?)",
               f"https://example.com/job/{i}", "linkedin", t, c, loc, str(today - timedelta(days=i)), d, st)
    if st == "done":
        db.x("UPDATE jobs SET resume=?, match=? WHERE id=?", json.dumps(PROFILE),
             json.dumps({"score": 86, "matched": ["Python", "FastAPI", "SQL"], "gaps": ["Kubernetes"]}), jid)

import uvicorn  # noqa: E402
from app.main import app  # noqa: E402  (imported after the patch so it uses the temp DB)

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)

from playwright.sync_api import sync_playwright  # noqa: E402

out = ROOT / "docs" / "img"; out.mkdir(parents=True, exist_ok=True)
url = f"http://127.0.0.1:{port}/"
with sync_playwright() as p:
    b = p.chromium.launch()
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        pg = b.new_page(viewport={"width": 1360, "height": 860}, color_scheme=theme)
        pg.goto(url); pg.wait_for_selector(".job"); pg.wait_for_timeout(500)
        pg.screenshot(path=str(out / f"home{suffix}.png"))
        if not suffix:
            pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(300)
            pg.screenshot(path=str(out / "mobile.png"))
            pg.set_viewport_size({"width": 1360, "height": 860})
        pg.click("text=Preview"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(out / f"preview{suffix}.png"))
        pg.close()
    b.close()
server.should_exit = True
print("saved to", out)
