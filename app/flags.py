"""Free (no AI) reading of a job text: years required, US-only, clearance, no sponsorship."""
import json, re
from datetime import date

_YRS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
_EXP = re.compile(r"exp", re.I)
US = re.compile(r"\bu\.?s\.?\s*(?:citizen|citizenship|person)|united states citizen|citizen(?:ship)? of the (?:u\.?s|united states)", re.I)
CLEAR = re.compile(r"security clearance|\bts/sci\b|secret clearance|clearance required", re.I)
NOSPON = re.compile(r"(?:no|not|cannot|unable to|without)\s+(?:\w+\s+){0,3}sponsorship|sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)|will not sponsor", re.I)


def requirements(text):
    text = text or ""
    yrs = []
    for m in _YRS.finditer(text):
        if _EXP.search(text[max(0, m.start() - 40): m.end() + 45]):
            yrs.append(int(m.group(1)))  # for "8-10 years" the lower number is the requirement
    return {"min_years": max(yrs) if yrs else None, "us_only": bool(US.search(text)),
            "clearance": bool(CLEAR.search(text)), "no_sponsorship": bool(NOSPON.search(text))}


def profile_years(profile):
    """Years of experience from the first start year to the last end year; None if no dates."""
    ys = []
    for e in (profile or {}).get("experience") or []:
        s = re.findall(r"(?:19|20)\d\d", str(e.get("start") or ""))
        en = re.findall(r"(?:19|20)\d\d", str(e.get("end") or ""))
        if s:
            ys += [int(s[0]), int(en[-1]) if en else date.today().year]
    return max(ys) - min(ys) if ys else None


def labels(req):
    """Short badges for the job card."""
    out = []
    if req.get("min_years"):
        out.append(f"{req['min_years']}+ yrs")
    if req.get("clearance"):
        out.append("clearance")
    if req.get("no_sponsorship"):
        out.append("no sponsorship")
    return out


SLACK = 1  # a job asking 1 year more than you have is still shown


def is_senior(req, have_years):
    """Needs clearly more years than the profile has (e.g. 20+ yrs). Unknown profile years -> never flagged."""
    n = req.get("min_years")
    return bool(n) and have_years is not None and n > have_years + SLACK


def parse(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}
