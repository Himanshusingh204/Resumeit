"""Free (no AI) facts to see BEFORE you apply: salary, work mode, job type, age, and warning tags.
Board fields (JobSpy) are used when present; otherwise the job text is read. All of it is a hint, not a promise."""
import json, re
from datetime import date

INR_PER_USD = 83  # rough, only used to compare salaries in one filter (min salary in lakh INR)
_UNIT = {"k": 1e3, "l": 1e5, "lpa": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7, "m": 1e6}
_CUR = {"$": "USD", "usd": "USD", "₹": "INR", "rs": "INR", "inr": "INR", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR"}
_PER = {"hr": 2080, "hour": 2080, "hourly": 2080, "month": 12, "mo": 12, "monthly": 12, "week": 52, "weekly": 52, "day": 260, "daily": 260,
        "year": 1, "yearly": 1, "annum": 1, "annual": 1, "yr": 1, "pa": 1, "lpa": 1}
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
SAL = re.compile(
    r"(?P<c1>[$₹£€]|\b(?:USD|INR|GBP|EUR|Rs)\b\.?)?\s*" + _NUM.replace("(", "(?P<a>", 1) +
    r"\s*(?P<ua>k|lpa|lakhs?|lacs?|cr|crores?|m)?\b\s*(?:-|–|—|to)\s*(?P<c2>[$₹£€]|\b(?:USD|INR|GBP|EUR|Rs)\b\.?)?\s*" +
    _NUM.replace("(", "(?P<b>", 1) +
    r"\s*(?P<ub>k|lpa|lakhs?|lacs?|cr|crores?|m)?\b(?P<per>\s*(?:/|per\s*|a\s+)?\s*(?:year|yr|annum|hour|hr|month|week|day|p\.?a\.?)\b)?",
    re.I)
HYBRID = re.compile(r"\bhybrid\b", re.I)
REMOTE = re.compile(r"\bfully remote\b|\bremote(?:-first)?\b|work from home|\bwfh\b", re.I)
ONSITE = re.compile(r"\bon-?\s?site\b|work from office|\bwfo\b|in-?office|office-based", re.I)
TAGS = {"night shift": r"night shift|rotational shift|graveyard", "bond": r"service (?:agreement|bond)|\bbond\b.{0,20}(?:months|years)",
        "immediate joiner": r"immediate joiner|join immediately|notice period.{0,25}(?:immediate|0-15|15 days)",
        "relocation": r"relocat", "walk-in": r"walk-?in", "unpaid": r"\bunpaid\b", "commission only": r"commission[- ]only"}


def _f(v):
    try:
        v = float(v)
        return v if v == v else None  # NaN
    except (TypeError, ValueError):
        return None


def _money(a, ua, cur):
    return float(a.replace(",", "")) * _UNIT.get((ua or "").lower(), 1)


def salary_from_text(text):
    """-> (min, max, currency) per year, or None. Needs a currency sign/code or an LPA/lakh/crore unit,
    so '2-4 years' or '10-20 people' are never read as money."""
    for m in SAL.finditer(text or ""):
        ua, ub = (m["ua"] or "").lower(), (m["ub"] or "").lower()
        units = ub or ua
        cur = _CUR.get((m["c1"] or m["c2"] or "").lower().rstrip(".")) or ("INR" if units in ("lpa", "lakh", "lakhs", "lac", "lacs", "cr", "crore", "crores") else None)
        if not cur:
            continue
        lo, hi = _money(m["a"], ua or ub, cur), _money(m["b"], ub or ua, cur)
        if lo > hi:
            continue
        per = re.sub(r"[^a-z]", "", (m["per"] or "year").lower().replace("a ", "")) or "year"
        mult = _PER.get(per.replace("per", ""), 1)
        lo, hi = lo * mult, hi * mult
        if cur == "INR" and hi < 1e5 and not mult > 1:
            continue
        if hi >= 5e3:  # below this it is not an annual salary (a count, a year, a price)
            return lo, hi, cur
    return None


def salary_from_board(b):
    lo, hi = _f(b.get("min")), _f(b.get("max"))
    if not (lo or hi):
        return None
    per = _PER.get(str(b.get("interval") or "yearly").lower(), 1)
    cur = _CUR.get(str(b.get("cur") or "").lower(), str(b.get("cur") or "").upper() or "INR")
    lo, hi = (lo or hi) * per, (hi or lo) * per
    return lo, hi, cur


def fmt_salary(lo, hi, cur):
    if cur == "INR":
        a, b = lo / 1e5, hi / 1e5
        n = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
        return f"₹{n(a)} LPA" if abs(a - b) < .05 else f"₹{n(a)}–{n(b)} LPA"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(cur, cur + " ")
    k = lambda v: f"{sym}{v / 1e3:.0f}k"
    return (k(lo) if lo == hi else f"{k(lo)}–{k(hi)}") + "/yr"


def lakh_inr(hi, cur):
    """Max annual salary in lakh INR (rough conversion) so one filter works for any currency."""
    rate = {"INR": 1, "USD": INR_PER_USD, "GBP": INR_PER_USD * 1.25, "EUR": INR_PER_USD * 1.08}.get(cur, 1)
    return round(hi * rate / 1e5, 1)


def details(text, board=None, title=""):
    """Everything readable from one job. Stored as JSON in jobs.info."""
    board, text = board or {}, text or ""
    sal = salary_from_board(board) or salary_from_text(text)
    out = {"salary": fmt_salary(*sal) if sal else None, "salary_lakh": lakh_inr(sal[1], sal[2]) if sal else None,
           "salary_src": ("board" if salary_from_board(board) else "text") if sal else None}
    mode = "hybrid" if HYBRID.search(text) else "remote" if (board.get("remote") or REMOTE.search(f"{title} {text[:1500]}")) else "onsite" if ONSITE.search(text) else None
    out["mode"] = mode
    jt = str(board.get("job_type") or "").lower().replace("_", "-")
    if not jt:
        hay = f"{title} {text[:1200]}".lower()
        jt = ("internship" if re.search(r"\bintern(ship)?\b", hay) else "contract" if re.search(r"\bcontract(or)?\b|freelance|\btemporary\b|\bc2h\b", hay)
              else "part-time" if re.search(r"part[- ]time", hay) else "")
    out["type"] = jt or None
    out["tags"] = [k for k, rx in TAGS.items() if re.search(rx, text, re.I)]
    return out


INDIA = re.compile(
    r"\bindia\b|bengaluru|bangalore|mumbai|navi mumbai|thane|delhi|\bncr\b|gurgaon|gurugram|noida|ghaziabad|faridabad|pune|hyderabad|"
    r"secunderabad|chennai|kolkata|ahmedabad|gandhinagar|jaipur|kochi|cochin|trivandrum|thiruvananthapuram|chandigarh|mohali|indore|"
    r"bhopal|nagpur|coimbatore|lucknow|kanpur|bhubaneswar|vadodara|surat|mysuru|mysore|visakhapatnam|vizag|patna|ranchi|raipur|"
    r"dehradun|nashik|madurai|mangaluru|mangalore|guwahati|goa\b|,\s*(?:in|ind)\s*$|\b(?:maharashtra|karnataka|tamil nadu|telangana|"
    r"kerala|gujarat|rajasthan|haryana|uttar pradesh|west bengal|andhra pradesh)\b", re.I)


def region(location, site=""):
    """'india' | 'abroad' | 'unknown' (no place given, or just 'Remote'). Naukri is India-only."""
    loc = (location or "").strip()
    if INDIA.search(loc) or (site == "naukri" and not loc):
        return "india"
    if not loc or loc.lower() in ("remote", "anywhere", "worldwide", "global", "-"):
        return "unknown"
    return "abroad"


def location_ok(reg, mode, req):
    """Your rule: India jobs are fine; a job abroad must be remote (and not tied to US citizens / clearance)."""
    if reg == "abroad":
        return mode == "remote" and not (req.get("us_only") or req.get("clearance"))
    if reg == "unknown":
        return not (req.get("us_only") or req.get("clearance"))
    return True


def age_days(posted, today=None):
    try:
        return ((today or date.today()) - date.fromisoformat(str(posted)[:10])).days
    except (TypeError, ValueError):
        return None


def parse(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}
