"""What did the AI change? Compares the base profile with a tailored resume (for the side-by-side view)."""
import difflib


def _n(s):
    return " ".join(str(s or "").lower().split())


def _bullets(base, new):
    """Each new bullet: same / reworded (close to a base bullet) / new. Base bullets never matched = removed."""
    used, out = set(), []
    for b in new:
        if b in base:
            used.add(base.index(b)); out.append({"text": b, "state": "same"}); continue
        scores = [(difflib.SequenceMatcher(None, _n(x), _n(b)).ratio(), i) for i, x in enumerate(base) if i not in used]
        r, best = max(scores, default=(0, None))
        if best is not None and r >= .45:
            used.add(best); out.append({"text": b, "state": "reworded", "was": base[best]})
        else:
            out.append({"text": b, "state": "new"})
    return out, [x for i, x in enumerate(base) if i not in used]


def diff(profile, r):
    res = {"summary": {"was": profile.get("summary") or "", "now": r.get("summary") or ""}}
    bs, rs = [_n(s) for s in profile.get("skills") or []], [_n(s) for s in r.get("skills") or []]
    res["skills"] = {"kept": [s for s in r.get("skills") or [] if _n(s) in bs],
                     "added": [s for s in r.get("skills") or [] if _n(s) not in bs],
                     "removed": [s for s in profile.get("skills") or [] if _n(s) not in rs]}
    for key, ident in (("experience", "company"), ("projects", "name")):
        base = {_n(x.get(ident)): x for x in profile.get(key) or []}
        items, seen = [], set()
        for x in r.get(key) or []:
            k = _n(x.get(ident)); seen.add(k)
            bl, removed = _bullets((base.get(k) or {}).get("bullets") or [], x.get("bullets") or [])
            items.append({"name": x.get(ident), "title": x.get("title") or x.get("tech"), "bullets": bl,
                          "removed_bullets": removed, "dropped": False})
        for k, b in base.items():
            if k not in seen:
                items.append({"name": b.get(ident), "title": b.get("title") or b.get("tech"), "bullets": [],
                              "removed_bullets": b.get("bullets") or [], "dropped": True})
        res[key] = items
    return res
