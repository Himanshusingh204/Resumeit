import json, sqlite3, threading
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "resumeit.db"
_lock = threading.Lock()


def init():
    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY, url TEXT UNIQUE, site TEXT, title TEXT, company TEXT, location TEXT,
          posted TEXT, description TEXT, status TEXT DEFAULT 'new', error TEXT, resume TEXT, match TEXT,
          created TEXT DEFAULT CURRENT_TIMESTAMP, fit TEXT);
        CREATE TABLE IF NOT EXISTS issues(
          id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, kind TEXT, ref TEXT, msg TEXT);""")
        have = [r[1] for r in c.execute("PRAGMA table_info(jobs)")]
        for col in ("fit", "req", "flag", "track", "tracked_at", "cover", "board", "info"):  # columns added after first release
            if col not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")


def _run(sql, a):
    with _lock, sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, a)
        return cur, [dict(r) for r in cur.fetchall()] if cur.description else []


def q(sql, *a):
    return _run(sql, a)[1]


def x(sql, *a):
    return _run(sql, a)[0].lastrowid


def kv_get(k):
    r = q("SELECT v FROM kv WHERE k=?", k)
    return json.loads(r[0]["v"]) if r else None


def kv_set(k, v):
    x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, json.dumps(v))


def issue(kind, ref, msg):
    """Record something that failed or came out incomplete, so a skipped item is never silent."""
    msg = str(msg)[:500]
    x("INSERT INTO issues(kind,ref,msg) VALUES(?,?,?)", kind, str(ref), msg)
    with open(DB.parent / "issues.log", "a", encoding="utf-8") as f:
        f.write(f"{kind}\t{ref}\t{msg}\n")
