"""Truth guard: a tailored resume may only contain facts that are already in the profile (rule 1.2)."""
import json, re

_NUM = re.compile(r"\d[\d,.]*\d%?|\d%?")


def _n(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def numbers(text):
    return {m.group(0).rstrip(".,") for m in _NUM.finditer(str(text or ""))}


def _profile_numbers(profile):
    return numbers(json.dumps(profile, ensure_ascii=False))


def truth_problems(r, profile):
    """List of facts in `r` that are not in `profile` (empty list = clean)."""
    out = []
    skills = {_n(s) for s in profile.get("skills") or []}
    for s in r.get("skills") or []:
        if _n(s) not in skills:
            out.append(f"skill not in profile: {s}")
    for f in ("name", "email", "phone"):
        if r.get(f) and _n(r[f]) != _n(profile.get(f)):
            out.append(f"{f} changed: {r[f]}")
    links = {_n(x) for x in profile.get("links") or []}
    out += [f"link not in profile: {x}" for x in r.get("links") or [] if _n(x) not in links]
    exp = {_n(e.get("company")): e for e in profile.get("experience") or []}
    for e in r.get("experience") or []:
        p = exp.get(_n(e.get("company")))
        if not p:
            out.append(f"company not in profile: {e.get('company')}")
            continue
        for f in ("title", "start", "end"):
            if _n(e.get(f)) != _n(p.get(f)):
                out.append(f"{f} differs at {e.get('company')}: {e.get(f)!r} vs {p.get(f)!r}")
    proj = {_n(x.get("name")): x for x in profile.get("projects") or []}
    for x in r.get("projects") or []:
        p = proj.get(_n(x.get("name")))
        if not p:
            out.append(f"project not in profile: {x.get('name')}")
        elif x.get("tech") and not _toks(x["tech"]) <= _toks(p.get("tech")):
            out.append(f"project tech changed: {x.get('name')}")
    edu = {(_n(x.get("school")), _n(x.get("degree")), _n(x.get("year"))) for x in profile.get("education") or []}
    for x in r.get("education") or []:
        if (_n(x.get("school")), _n(x.get("degree")), _n(x.get("year"))) not in edu:
            out.append(f"education not in profile: {x.get('degree')} {x.get('school')}")
    certs = {_n(c) for c in profile.get("certifications") or []}
    out += [f"certification not in profile: {c}" for c in r.get("certifications") or [] if _n(c) not in certs]
    text = " ".join([r.get("summary") or ""] + [b for k in ("experience", "projects") for x in r.get(k) or [] for b in x.get("bullets") or []])
    new = numbers(text) - _profile_numbers(profile)
    if new:
        out.append("numbers not in profile: " + ", ".join(sorted(new)))
    return out


def _toks(s):
    return {_n(t) for t in re.split(r"[,/|;]", s or "") if t.strip()}


def check(r, profile):
    """Raise ValueError (shown on the job and in Issues) if the resume invents anything."""
    bad = truth_problems(r, profile)
    if bad:
        raise ValueError("truth guard rejected resume: " + "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""))
