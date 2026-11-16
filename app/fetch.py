"""Read a job posting from its link. Prefers the page's schema.org JobPosting data, else visible text."""
import html, json, re
from html.parser import HTMLParser

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, _):
        self.skip += tag in ("script", "style", "noscript", "svg", "nav", "footer", "header")
        if tag in ("br", "p", "li", "div", "h1", "h2", "h3", "tr"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self.skip > 0

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def to_text(markup):
    p = _Text()
    p.feed(markup)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape("".join(p.out))).strip()


def _jobposting(page):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("@graph", [data])
        for d in data if isinstance(data, list) else []:
            if isinstance(d, dict) and d.get("@type") == "JobPosting" and d.get("description"):
                org = d.get("hiringOrganization")
                return d.get("title"), (org.get("name") if isinstance(org, dict) else org), to_text(d["description"])
    return None


def job_from_url(url):
    """-> (title, company, description). Raises ValueError with a user-facing reason."""
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=20)
        r.raise_for_status()  # an error page is not a job posting
        page = r.text
    except httpx.HTTPError as e:
        raise ValueError(f"Could not open the link ({type(e).__name__})")
    title, company, desc = _jobposting(page) or (None, None, None)
    if not desc:
        t = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        title, desc = (html.unescape(t.group(1)).strip() if t else None), to_text(page)
    if len(desc or "") < 200:
        raise ValueError("Could not read a job description from that page (it may need a login). Paste the text instead.")
    return title or "Job", company or "-", desc[:30000]
