"""
Search Agent.

Free sources, in priority order:
  1. Greenhouse public API  — no key needed
  2. Lever public API       — no key needed
  3. RemoteOK JSON feed     — no key needed, official public API (see search_remoteok)
  4. We Work Remotely RSS   — no key needed, official public per-category feed (see search_weworkremotely)
  5. Google Sheets watchlist — free (Google Cloud service account)
  6. Sites added from the dashboard's Search tab — Greenhouse/Lever URLs reuse
     the API-backed paths above; verified job-board templates (Wuzzuf,
     Bayt.com, GulfTalent, SimplyHired, Wellfound -- see
     KNOWN_JOB_BOARD_TEMPLATES) rebuild their URL from the `position` field
     each run; anything else falls back to a best-effort generic scrape (see
     search_generic_site). All five templates are pre-added as default rows
     the first time the app runs (seed_default_search_sites) but are just
     regular rows after that -- removable from the Search tab like any other
     site.
  7. SerpAPI (Google Jobs)  — free tier, 100 searches/month, optional
  8. Himalayas, Remotive, Jobicy, Working Nomads — keyless remote-job APIs,
     always on, screened by config.SEARCH_ELIGIBLE_LOCATIONS
  9. Ashby / SmartRecruiters public job boards — no key, per-company like
     Greenhouse/Lever (config.ASHBY_BOARD_SLUGS / SMARTRECRUITERS_COMPANIES,
     or a pasted board URL on the Search tab)
 10. LinkedIn logged-out job search — opt-in via config.LINKEDIN_LOCATIONS,
     paced, descriptions fetched only for jobs that pass the title filter

RemoteOK and We Work Remotely are always-on structured sources (like
Greenhouse/Lever) rather than SearchSite rows -- they need no per-user
config and aren't Greenhouse/Lever, so they can never be whitelisted for
auto-submit (see WHITELISTABLE_SOURCES), same as google_jobs/generic.
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urljoin, urlparse

import requests

import config

WHITELISTABLE_SOURCES = {"greenhouse", "lever"}  # only these CAN ever be whitelisted for auto-submit

GREENHOUSE_URL_RE = re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)", re.I)
LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", re.I)
ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)", re.I)
SMARTRECRUITERS_URL_RE = re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.I)

# Heuristics for the generic-site scraper -- arbitrary career pages have no
# consistent structure, so this is a best-effort signal, not a guarantee.
JOB_LINK_KEYWORDS = ("job", "career", "position", "opening", "vacan", "apply", "req")
GENERIC_SITE_MAX_CANDIDATES = 15
GENERIC_SITE_TIMEOUT = 10
GENERIC_SITE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobApplicationAgent/1.0)"}

# Seniority filter: job titles are matched against these keyword lists.
# "mid" has no reliable keyword of its own -- most unlabeled titles ("Software
# Engineer" with no qualifier) are mid-level in practice, so it's treated as
# "doesn't match any other level's keywords" rather than its own positive list.
SENIORITY_KEYWORDS = {
    "intern": ["intern", "internship", "co-op", "coop"],
    "entry": ["entry level", "entry-level", "junior", "jr.", "new grad", "new-grad", "graduate"],
    "senior": ["senior", "sr."],
    "lead": ["lead", "staff", "principal", "architect"],
    "manager": ["manager", "director", "head of", "vice president", " vp ", "chief"],
}
SENIORITY_LABELS = {
    "intern": "Intern",
    "entry": "Entry Level",
    "mid": "Mid Level",
    "senior": "Senior",
    "lead": "Lead",
    "manager": "Manager",
}


def _matches_seniority(title: str, level: str) -> bool:
    haystack = (title or "").lower()
    if level == "mid":
        return not any(kw in haystack for kws in SENIORITY_KEYWORDS.values() for kw in kws)
    return any(kw in haystack for kw in SENIORITY_KEYWORDS.get(level, []))


def _cap(jobs: list[dict], limit: int | None) -> list[dict]:
    """Keeps only the first `limit` raw results from one source. None/0/negative
    means no cap -- the original "pull everything" behavior."""
    if limit and limit > 0:
        return jobs[:limit]
    return jobs


# Position matching is token-based rather than a literal substring check,
# because job titles routinely phrase the same role differently than the
# query without ever containing it as one contiguous phrase -- e.g.
# searching "AI Engineer" should match "AI Software Engineer", "AI/ML
# Software Engineer", "Gen AI Engineer", and "Gen AI/Agentic AI Engineer"
# alike, none of which contain the literal substring "ai engineer" (the
# words "AI" and "Engineer" are separated by other words, or in a different
# order). A title matches if it contains every significant word from the
# query, in any order and not necessarily adjacent -- a small, deliberately
# simple heuristic (same spirit as SENIORITY_KEYWORDS below) rather than a
# synonym dictionary, since decomposing into words already covers the
# common "role phrased differently" cases above for free.
_POSITION_STOPWORDS = {"a", "an", "and", "or", "the", "of", "in", "for", "with", "to"}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _position_tokens(text: str) -> set[str]:
    words = _WORD_RE.findall((text or "").lower())
    return {w for w in words if w not in _POSITION_STOPWORDS}


def _matches_position(title: str, position: str) -> bool:
    query_tokens = _position_tokens(position)
    if not query_tokens:
        return True  # blank position, or pure punctuation -- no filter
    return query_tokens.issubset(_position_tokens(title))


def _matches_any_position(title: str, target_roles: list[str]) -> bool:
    """OR across every expanded role phrase (agents/query_expansion_agent.py).

    This is the broadened form of _matches_position: a title only had to match
    the single typed Position before, which is what silently excluded
    "Backend Developer" from a "Software Engineer" search even when the JD was
    a genuine fit. Matching ANY expanded phrase widens that considerably --
    though note it's still a TITLE check, so a job whose title matches no
    phrase at all is still missed here. That remaining gap is what the
    ranking agent's semantic retrieval path exists to close (see
    agents/ranking_agent.py::semantic_retrieval_path).
    """
    if not target_roles:
        return True  # nothing to filter against -- keep everything
    return any(_matches_position(title, role) for role in target_roles)


def _filter_relevant(jobs: list[dict], target_roles: list[str], seniority: str) -> list[dict]:
    """Position/seniority title filters, applied to a source's raw job list
    BEFORE _cap() truncates it -- capping first can silently discard every
    real match on a large board. Confirmed in practice: Greenhouse returns a
    company's jobs in a roughly alphabetical-by-title order, so on a
    545-job board, capping to the first 100 (a completely normal "Max
    results per site" setting) produced zero "Software Engineer" matches
    out of 38 real ones, because every single match sat past index 100 --
    that's what was actually behind a real "search returns nothing" report.

    `target_roles` is the expanded phrase list; passing a single-element list
    reproduces the original single-phrase behavior exactly. Seniority remains
    a HARD filter (deliberate -- it's also a ranking factor, but a "senior"
    search still shouldn't surface internships)."""
    if target_roles:
        jobs = [j for j in jobs if _matches_any_position(j.get("title"), target_roles)]
    if seniority:
        jobs = [j for j in jobs if _matches_seniority(j.get("title"), seniority)]
    return jobs


def _dedupe_by_url(jobs: list[dict]) -> list[dict]:
    """Query expansion means the same posting can legitimately be returned by
    several different phrases hitting the same site ("software engineer" and
    "backend engineer" both surfacing one Wuzzuf listing). First occurrence
    wins; everything after it is dropped before any downstream cost is spent
    on it."""
    seen = set()
    out = []
    for job in jobs:
        url = job.get("url") or job.get("hostedUrl") or job.get("absolute_url")
        if not url:
            out.append(job)  # no URL to dedupe on -- keep rather than discard
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(job)
    return out


# Posted-date extraction: each source exposes "when was this posted"
# differently, so max-age filtering only works where we can actually find a
# date. Jobs with no recognizable date are always kept (never excluded) since
# their age genuinely can't be verified -- this is most relevant for the
# generic scraper, which has no structured date at all.
RELATIVE_DATE_RE = re.compile(r"(\d+)\+?\s*(hour|day|week|month)s?\s+ago", re.I)


def _parse_relative_date(text: str) -> datetime | None:
    """Parses SerpAPI/Google Jobs' detected_extensions.posted_at strings,
    e.g. "3 days ago", "Today", "30+ days ago"."""
    if not text:
        return None
    t = text.strip().lower()
    if t in ("today", "just posted", "just now"):
        return datetime.now(timezone.utc)
    match = RELATIVE_DATE_RE.search(t)
    if not match:
        return None
    n, unit = int(match.group(1)), match.group(2)
    delta = {
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30),
    }[unit]
    return datetime.now(timezone.utc) - delta


def _extract_posted_at(job: dict) -> datetime | None:
    # Greenhouse: ISO 8601 with offset. first_published is the original post
    # date; updated_at changes whenever the listing is edited, so it's only
    # used as a fallback.
    for key in ("first_published", "updated_at"):
        value = job.get(key)
        if value:
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                pass

    # Lever: createdAt is epoch milliseconds.
    created_at = job.get("createdAt")
    if created_at:
        try:
            return datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc)
        except (ValueError, TypeError, OSError, OverflowError):
            pass

    # RemoteOK (ISO 8601, e.g. "2024-06-01T12:00:00+00:00") and We Work
    # Remotely (RFC 822 pubDate, e.g. "Mon, 01 Jan 2024 00:00:00 +0000") both
    # normalize their post date into this same "date" key -- see
    # search_remoteok()/search_weworkremotely() -- so both formats are tried
    # here rather than needing a source-specific branch above.
    date_value = job.get("date")
    if date_value:
        try:
            return datetime.fromisoformat(date_value)
        except (ValueError, TypeError):
            pass
        try:
            return parsedate_to_datetime(date_value)
        except (ValueError, TypeError):
            pass

    # SerpAPI / Google Jobs: relative text, e.g. "3 days ago".
    posted_at = (job.get("detected_extensions") or {}).get("posted_at")
    if posted_at:
        parsed = _parse_relative_date(posted_at)
        if parsed:
            return parsed

    return None


def _job_age_days(job: dict) -> float | None:
    posted_at = _extract_posted_at(job)
    if not posted_at:
        return None
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - posted_at).total_seconds() / 86400


def _within_max_age(job: dict, max_age_days: int | None) -> bool:
    if not max_age_days or max_age_days <= 0:
        return True
    age = _job_age_days(job)
    return age is None or age <= max_age_days  # unknown age -- don't exclude it


def parse_site_url(url: str) -> tuple[str, str | None]:
    """
    Detects a pasted Greenhouse/Lever/Ashby/SmartRecruiters board URL and
    extracts its board token/company slug, so it can reuse the reliable API-backed search
    functions instead of falling back to generic scraping. Anything else is
    tagged "generic".
    """
    match = GREENHOUSE_URL_RE.search(url)
    if match:
        return "greenhouse", match.group(1)
    match = LEVER_URL_RE.search(url)
    if match:
        return "lever", match.group(1)
    match = ASHBY_URL_RE.search(url)
    if match:
        return "ashby", match.group(1)
    match = SMARTRECRUITERS_URL_RE.search(url)
    if match:
        return "smartrecruiters", match.group(1)
    # A known job board (Wuzzuf, Bayt, ...) pasted by hand gets the same
    # per-search URL the seeded row would, not a generic scrape of whatever
    # page was pasted.
    template = board_template_for(url)
    if template:
        return template, None
    return "generic", None


def _registered_domain(url: str) -> str:
    """"egypt.tanqeeb.com" -> "tanqeeb.com". Two labels is enough for every
    board in KNOWN_JOB_BOARD_TEMPLATES; none sits under a two-part TLD."""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower().split(":")[0]
    return ".".join(host.split(".")[-2:])


def board_template_for(url: str) -> str | None:
    """The KNOWN_JOB_BOARD_TEMPLATES key whose site this URL is on, if any."""
    domain = _registered_domain(url)
    for key, meta in KNOWN_JOB_BOARD_TEMPLATES.items():
        if domain and _registered_domain(meta["default_url"]) == domain:
            return key
    return None


def search_greenhouse(board_token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json().get("jobs", [])


def search_lever(company: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def search_remoteok(position: str = "") -> list[dict]:
    """
    RemoteOK's free public JSON feed (https://remoteok.com/api) -- no key,
    no auth required, and explicitly published for this kind of use (linked
    from RemoteOK's own nav as "JSON feed"). Returns RemoteOK's current
    developer-jobs feed; `position` narrows it via `_matches_position`
    (word-based, not a literal phrase match), since this endpoint doesn't
    expose a real query/search parameter (only tag filtering, and there's
    no reliable way to map arbitrary free-text position to its tag
    taxonomy).

    The response's first element is RemoteOK's own legal/attribution
    notice, not a job -- it has no "id"/"position" field, so the
    `job.get("id") and job.get("position")` guard below excludes it
    naturally rather than needing a hardcoded "skip index 0".
    """
    resp = requests.get(
        "https://remoteok.com/api",
        timeout=20,
        headers={**GENERIC_SITE_HEADERS, "Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []

    jobs = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        title = entry.get("position")
        if not entry.get("id") or not title:
            continue  # the leading legal-notice object, or a malformed row
        if not _matches_position(title, position):
            continue
        jobs.append({
            "title": title,
            "company": entry.get("company", ""),
            "url": entry.get("url") or f"https://remoteok.com/remote-jobs/{entry['id']}",
            "description": entry.get("description", ""),
            "date": entry.get("date"),  # ISO 8601 -- see _extract_posted_at
        })
    return jobs


def search_weworkremotely(category: str = "remote-programming-jobs") -> list[dict]:
    """
    We Work Remotely publishes each job category as a plain RSS 2.0 feed
    (e.g. /categories/remote-programming-jobs.rss) -- no key, no auth,
    structured XML rather than an HTML page to scrape. `category` defaults
    to programming jobs since that's the relevant category for this app;
    WWR's other categories (design, devops, marketing, etc.) follow the
    same URL pattern if ever needed.

    WWR titles its listings "Company: Job Title" -- split on the first ": "
    below so title/company come back as separate fields like every other
    source here, falling back to the whole string as the title (blank
    company) if a listing doesn't follow that convention.
    """
    url = f"https://weworkremotely.com/categories/{category}.rss"
    resp = requests.get(url, timeout=20, headers=GENERIC_SITE_HEADERS)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    jobs = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        if ": " in raw_title:
            company, _, title = raw_title.partition(": ")
        else:
            company, title = "", raw_title
        jobs.append({
            "title": title,
            "company": company,
            "url": (item.findtext("link") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "date": (item.findtext("pubDate") or "").strip() or None,  # RFC 822 -- see _extract_posted_at
        })
    return jobs


# ---- Keyless remote-job APIs, ATS boards and LinkedIn -----------------------
#
# Every fetcher below returns the same normalized shape as search_remoteok():
# title / company / url / description / date / location. Descriptions are
# converted to plain text here, since they go straight into LLM prompts.

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
}
LINKEDIN_MAX_JOBS = 25  # descriptions fetched per run -- each one is a paced request
_WORLDWIDE_RE = re.compile(r"\b(worldwide|anywhere|global|international)\b", re.I)


# The query-independent feeds every search runs, for the Search page's source
# list. Each can be switched off there (config.SEARCH_DISABLED_FEEDS).
BUILTIN_FEEDS = {
    "remoteok": {"label": "RemoteOK", "url": "https://remoteok.com/"},
    "weworkremotely": {"label": "We Work Remotely", "url": "https://weworkremotely.com/"},
    "himalayas": {"label": "Himalayas", "url": "https://himalayas.app/jobs"},
    "remotive": {"label": "Remotive", "url": "https://remotive.com/"},
    "jobicy": {"label": "Jobicy", "url": "https://jobicy.com/"},
    "workingnomads": {"label": "Working Nomads", "url": "https://www.workingnomads.com/jobs"},
}


def _feed_on(key: str) -> bool:
    return key not in config.SEARCH_DISABLED_FEEDS


def _html_text(html: str | None) -> str:
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)


def _epoch_to_iso(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _eligible(restriction) -> bool:
    """True when a remote job is open to the candidate: no restriction given,
    open worldwide, or the restriction names one of
    config.SEARCH_ELIGIBLE_LOCATIONS. `restriction` is free text ("USA Only",
    "EMEA") or a list of countries, depending on the board."""
    places = config.SEARCH_ELIGIBLE_LOCATIONS
    if not places:
        return True
    if isinstance(restriction, (list, tuple)):
        text = ", ".join(str(r) for r in restriction)
    else:
        text = str(restriction or "")
    if not text.strip() or _WORLDWIDE_RE.search(text):
        return True
    low = text.lower()
    return any(place.lower() in low for place in places)


def _phrases(target_roles: list[str] | None) -> list[str]:
    return [r for r in (target_roles or []) if r.strip()]


def search_himalayas(target_roles: list[str] | None = None) -> list[dict]:
    """Himalayas' keyless search API (https://himalayas.app/api), one query
    per role phrase. Each job lists the countries it hires from
    (locationRestrictions, empty = worldwide), which is what lets _eligible()
    drop the US-only ones."""
    phrases = _phrases(target_roles)
    requests_to_make = (
        [("https://himalayas.app/jobs/api/search", {"q": p}) for p in phrases]
        or [("https://himalayas.app/jobs/api", {"limit": 20})]
    )
    jobs, seen = [], set()
    for url, params in requests_to_make:
        resp = requests.get(url, params=params, timeout=20, headers=GENERIC_SITE_HEADERS)
        resp.raise_for_status()
        for entry in resp.json().get("jobs", []):
            job_url = entry.get("applicationLink") or entry.get("guid")
            if not job_url or job_url in seen or not entry.get("title"):
                continue
            seen.add(job_url)
            restrictions = entry.get("locationRestrictions") or []
            if not _eligible(restrictions):
                continue
            jobs.append({
                "title": entry["title"],
                "company": entry.get("companyName", ""),
                "url": job_url,
                "description": _html_text(entry.get("description")),
                "date": _epoch_to_iso(entry.get("pubDate")),
                "location": f"Remote ({', '.join(restrictions) if restrictions else 'worldwide'})",
            })
    return jobs


def search_remotive(target_roles: list[str] | None = None) -> list[dict]:
    """Remotive's keyless API (https://remotive.com/api/remote-jobs), one
    server-side search per role phrase. Remotive's terms ask that each job
    links back to its Remotive page, which the stored url does."""
    phrases = _phrases(target_roles) or [""]
    jobs, seen = [], set()
    for phrase in phrases:
        params = {"search": phrase} if phrase else {"limit": 100}
        resp = requests.get("https://remotive.com/api/remote-jobs", params=params,
                            timeout=20, headers=GENERIC_SITE_HEADERS)
        resp.raise_for_status()
        for entry in resp.json().get("jobs", []):
            job_url = entry.get("url")
            if not job_url or job_url in seen or not entry.get("title"):
                continue
            seen.add(job_url)
            where = entry.get("candidate_required_location") or ""
            if not _eligible(where):
                continue
            jobs.append({
                "title": entry["title"],
                "company": entry.get("company_name", ""),
                "url": job_url,
                "description": _html_text(entry.get("description")),
                "date": entry.get("publication_date"),
                "location": f"Remote ({where or 'worldwide'})",
            })
    return jobs


def search_jobicy() -> list[dict]:
    """Jobicy's keyless feed (https://jobicy.com/api/v2/remote-jobs) -- the
    latest 100 remote jobs, title-filtered afterwards like RemoteOK. Its tag
    search only takes single keywords, so the role phrases don't map onto it."""
    resp = requests.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 100},
                        timeout=20, headers=GENERIC_SITE_HEADERS)
    resp.raise_for_status()
    jobs = []
    for entry in resp.json().get("jobs", []):
        if not entry.get("url") or not entry.get("jobTitle"):
            continue
        geo = entry.get("jobGeo") or ""
        if not _eligible(geo):
            continue
        jobs.append({
            "title": _html_text(entry["jobTitle"]),
            "company": entry.get("companyName", ""),
            "url": entry["url"],
            "description": _html_text(entry.get("jobDescription")),
            "date": entry.get("pubDate"),
            "location": f"Remote ({geo or 'worldwide'})",
        })
    return jobs


def search_workingnomads() -> list[dict]:
    """Working Nomads' keyless feed of every open job, title-filtered
    afterwards. Its titles are "Company - Title", so the company prefix is
    stripped to keep the title filter honest."""
    resp = requests.get("https://www.workingnomads.com/api/exposed_jobs/",
                        timeout=30, headers=GENERIC_SITE_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for entry in data if isinstance(data, list) else []:
        title, company = entry.get("title") or "", entry.get("company_name") or ""
        if not entry.get("url") or not title:
            continue
        if company and title.startswith(f"{company} - "):
            title = title[len(company) + 3:]
        where = entry.get("location") or ""
        if not _eligible(where):
            continue
        jobs.append({
            "title": title,
            "company": company,
            "url": entry["url"],
            "description": _html_text(entry.get("description")),
            "date": entry.get("pub_date"),
            "location": where or "Remote",
        })
    return jobs


def search_ashby(slug: str) -> list[dict]:
    """An Ashby-hosted job board, via Ashby's public posting API -- the whole
    board in one response, like Greenhouse."""
    resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                        params={"includeCompensation": "true"}, timeout=20)
    resp.raise_for_status()
    jobs = []
    for entry in resp.json().get("jobs", []):
        if not entry.get("jobUrl") or not entry.get("title") or entry.get("isListed") is False:
            continue
        location = entry.get("location") or ""
        if entry.get("isRemote") and "remote" not in location.lower():
            location = f"Remote ({location})" if location else "Remote"
        jobs.append({
            "title": entry["title"].strip(),
            "company": slug.replace("-", " ").title(),
            "url": entry["jobUrl"],
            "description": entry.get("descriptionPlain") or _html_text(entry.get("descriptionHtml")),
            "date": entry.get("publishedAt"),
            "location": location,
        })
    return jobs


def search_smartrecruiters(company: str, target_roles: list[str] | None = None) -> list[dict]:
    """A SmartRecruiters company's public postings, searched server-side once
    per role phrase (boards like Bosch's run to thousands of jobs, so pulling
    everything isn't practical). The listing has no description; it's fetched
    per job by enrich_smartrecruiters() once the title filter has run."""
    phrases = _phrases(target_roles) or [""]
    jobs, seen = [], set()
    for phrase in phrases:
        params = {"limit": 100, **({"q": phrase} if phrase else {})}
        resp = requests.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                            params=params, timeout=20)
        resp.raise_for_status()
        for entry in resp.json().get("content", []):
            if not entry.get("id") or entry["id"] in seen or not entry.get("name"):
                continue
            seen.add(entry["id"])
            loc = entry.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(
                part for part in (loc.get("city"), loc.get("country")) if part)
            if loc.get("remote"):
                location = f"Remote ({location})" if location else "Remote"
            jobs.append({
                "title": entry["name"],
                "company": (entry.get("company") or {}).get("name") or company,
                "url": f"https://jobs.smartrecruiters.com/{company}/{entry['id']}",
                "description": "",
                "date": entry.get("releasedDate"),
                "location": location,
                "detail_url": entry.get("ref"),
            })
    return jobs


def enrich_smartrecruiters(job: dict) -> dict | None:
    if not job.get("detail_url"):
        return None
    resp = requests.get(job["detail_url"], timeout=20)
    resp.raise_for_status()
    sections = ((resp.json().get("jobAd") or {}).get("sections") or {})
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        section = sections.get(key) or {}
        text = _html_text(section.get("text"))
        if text:
            parts.append(f"{section.get('title') or key}\n{text}")
    return {**job, "description": "\n\n".join(parts)} if parts else None


def search_linkedin(target_roles: list[str] | None = None, locations: list[str] | None = None,
                    max_age_days: int | None = None, should_stop=None) -> list[dict]:
    """LinkedIn's logged-out job search -- the endpoint its own public jobs
    page calls -- once per role phrase per location (first page, 10 cards).
    "Worldwide" is searched as remote-only. Cards carry no description; that
    is fetched by enrich_linkedin() for the few that survive the title
    filter. A 429 ends the sweep early with whatever was already collected
    rather than failing the source. `should_stop` (a no-arg callable) ends the
    sweep early the same way, for a search the user stopped."""
    from bs4 import BeautifulSoup
    phrases = _phrases(target_roles)
    locations = [l for l in (locations or []) if l.strip()]
    jobs, seen, first = [], set(), True
    for phrase in phrases:
        for location in locations:
            if should_stop and should_stop():
                return jobs
            if not first:
                time.sleep(config.LINKEDIN_REQUEST_DELAY)
            first = False
            params = {"keywords": phrase, "location": location, "start": 0}
            if location.lower() == "worldwide":
                params["f_WT"] = 2  # remote
            if max_age_days and max_age_days > 0:
                params["f_TPR"] = f"r{max_age_days * 86400}"
            resp = requests.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                params=params, timeout=20, headers=BROWSER_HEADERS,
            )
            if resp.status_code == 429:
                return jobs
            resp.raise_for_status()
            for card in BeautifulSoup(resp.text, "html.parser").select("div.base-card"):
                job_id = (card.get("data-entity-urn") or "").rsplit(":", 1)[-1]
                title_el = card.select_one(".base-search-card__title")
                if not job_id.isdigit() or job_id in seen or not title_el:
                    continue
                seen.add(job_id)
                company_el = card.select_one(".base-search-card__subtitle")
                location_el = card.select_one(".job-search-card__location")
                time_el = card.select_one("time")
                where = location_el.get_text(strip=True) if location_el else ""
                if params.get("f_WT") == 2 and "remote" not in where.lower():
                    where = f"Remote ({where})" if where else "Remote"
                jobs.append({
                    "title": title_el.get_text(strip=True),
                    "company": company_el.get_text(strip=True) if company_el else "",
                    "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
                    "description": "",
                    "date": time_el.get("datetime") if time_el else None,
                    "location": where,
                    "linkedin_id": job_id,
                })
    return jobs


def enrich_linkedin(job: dict) -> dict | None:
    from bs4 import BeautifulSoup
    time.sleep(config.LINKEDIN_REQUEST_DELAY)
    resp = requests.get(
        f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['linkedin_id']}",
        timeout=20, headers=BROWSER_HEADERS,
    )
    resp.raise_for_status()
    page = BeautifulSoup(resp.text, "html.parser")
    body = page.select_one("div.show-more-less-html__markup")
    if not body:
        return None
    criteria = []
    for item in page.select("li.description__job-criteria-item"):
        label, value = item.select_one("h3"), item.select_one("span")
        if label and value:
            criteria.append(f"{label.get_text(strip=True)}: {value.get_text(strip=True)}")
    description = body.get_text("\n", strip=True)
    if criteria:
        description += "\n\n" + "\n".join(criteria)
    # The posting's own top card -- used when the caller has no title or
    # company yet (a pasted link carries only the id). LinkedIn's regular job
    # page can't be used for this: it often serves a sign-in wall instead.
    top = {}
    for key, selector in (("title", ".topcard__title"),
                          ("company", "a.topcard__org-name-link, .topcard__flavor"),
                          ("location", ".topcard__flavor--bullet")):
        el = page.select_one(selector)
        if el and el.get_text(strip=True):
            top[key] = el.get_text(" ", strip=True)
    filled = {k: v for k, v in top.items() if not job.get(k)}
    return {**job, **filled, "description": description}


def _already_saved(urls: list[str]) -> set[str]:
    """URLs already in the jobs table, so a paced per-job description fetch
    isn't spent on a posting the daily run would drop as known anyway."""
    if not urls:
        return set()
    try:
        from db import get_session
        from models import Job
        with get_session() as session:
            return {r[0] for r in session.query(Job.url).filter(Job.url.in_(urls)).all()}
    except Exception:  # noqa: BLE001 -- no DB (e.g. tests): just fetch them all
        return set()


def _looks_like_job_link(href: str, text: str) -> bool:
    haystack = f"{href} {text}".lower()
    return any(keyword in haystack for keyword in JOB_LINK_KEYWORDS)


def search_generic_site(url: str, position: str = "", max_candidates: int | None = None) -> list[dict]:
    """
    Best-effort scraper for career-page URLs that aren't Greenhouse/Lever.
    Finds same-domain links that look job-related (by URL/text keywords),
    optionally narrowed to ones matching `position`, capped to a handful of
    candidates to stay fast and polite (`max_candidates`, falling back to
    GENERIC_SITE_MAX_CANDIDATES if unset -- always capped, since each
    candidate costs a real HTTP fetch), then fetches each candidate's page
    text as its description. Noisier than the Greenhouse/Lever path -- treat
    results as leads to review, not a guaranteed feed. Respect each site's
    Terms of Service before relying on this for a site that prohibits scraping.
    """
    try:
        resp = requests.get(url, timeout=GENERIC_SITE_TIMEOUT, headers=GENERIC_SITE_HEADERS)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    domain = urlparse(url).netloc

    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        absolute = urljoin(url, href)
        if urlparse(absolute).netloc != domain:
            continue  # stay on-site
        if absolute in seen or not _looks_like_job_link(href, text):
            continue
        seen.add(absolute)
        candidates.append((absolute, text))

    if position:
        narrowed = [c for c in candidates if _matches_position(c[1], position)]
        if narrowed:  # only narrow if it doesn't wipe out every candidate
            candidates = narrowed

    limit = max_candidates if max_candidates and max_candidates > 0 else GENERIC_SITE_MAX_CANDIDATES
    jobs = []
    for job_url, link_text in candidates[:limit]:
        try:
            r = requests.get(job_url, timeout=GENERIC_SITE_TIMEOUT, headers=GENERIC_SITE_HEADERS)
            r.raise_for_status()
        except requests.RequestException:
            continue
        page = BeautifulSoup(r.text, "html.parser")
        title = link_text or (page.title.string if page.title else job_url)
        jobs.append({
            "source": "generic",
            "site_url": url,
            "url": job_url,
            "title": title,
            "description": page.get_text(separator="\n"),
        })
    return jobs


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return slug.strip("-")


# Known job boards that (a) are server-rendered -- a plain HTTP GET returns
# real job links, no JavaScript needed -- and (b) don't block unauthenticated
# requests, verified by directly fetching each one's raw HTML and checking for
# real job links before adding it here. Both currently listed are MENA-focused
# (Wuzzuf: Egypt, Bayt: Gulf/MENA); LinkedIn and Indeed were tested too and
# both block unauthenticated requests outright (HTTP 999 / 403), so they're
# deliberately not included -- adding them here would just silently return
# nothing.
#
# These double as the seed data for the Search tab's default site list (see
# seed_default_search_sites() below) -- shown as regular, removable rows
# rather than a hardcoded always-on injection, so a user who doesn't want
# Wuzzuf/Bayt searched can just delete them like any other site. A site row
# whose site_type is one of these keys gets its real per-search URL rebuilt
# from `position` at search time (see run_search()) instead of using the
# stored URL directly, since the useful search URL depends on the query.
#
# Each entry is a build_url(position) -> str|None callable rather than a
# plain "?param={query}" template, because sites vary in how (or whether) a
# query string actually filters results for an unauthenticated request --
# verified per site, not assumed:
#   - Wuzzuf's ?q= query string genuinely filters server-side.
#   - Bayt's ?keyword= query string does NOT filter for a plain request (it
#     silently serves generic, unrelated listings -- confirmed by testing:
#     searching "Ai Engineer" that way returned "Civil Rights Attorney" and
#     similar unrelated jobs). Bayt's real search only works through its
#     canonical SEO slug pages (e.g. /en/international/jobs/ai-engineer-jobs/),
#     confirmed working across several different job titles.
KNOWN_JOB_BOARD_TEMPLATES = {
    "wuzzuf": {
        "label": "Wuzzuf",
        "default_url": "https://wuzzuf.net/search/jobs/",
        # Guard against a blank position explicitly (unlike a plain f-string
        # template, this must return None so the dispatcher in run_search()
        # skips it) -- otherwise ?q= with nothing after it would scrape
        # Wuzzuf's entire unfiltered listing instead of being skipped, same
        # as Bayt already does below.
        "build_url": lambda position: (
            f"https://wuzzuf.net/search/jobs/?q={quote_plus(position)}" if position.strip() else None
        ),
    },
    "bayt": {
        "label": "Bayt.com",
        "default_url": "https://www.bayt.com/en/international/jobs/",
        "build_url": lambda position: (
            f"https://www.bayt.com/en/international/jobs/{_slugify(position)}-jobs/"
            if _slugify(position) else None
        ),
    },
    "simplyhired": {
        "label": "SimplyHired",
        "default_url": "https://www.simplyhired.com/",
        # Confirmed working: ?q= genuinely filters server-side (a "software
        # engineer" query returned real, correctly-matching listings with no
        # JS needed).
        "build_url": lambda position: (
            f"https://www.simplyhired.com/search?q={quote_plus(position)}" if position.strip() else None
        ),
    },
    "wellfound": {
        "label": "Wellfound",
        "default_url": "https://wellfound.com/jobs",
        # Wellfound's search is a curated role taxonomy, not a free-text
        # query -- confirmed working via its role-based URLs (fetching
        # /role/r/software-engineer returned 1,827 real, correctly-filtered
        # remote listings, no JS needed). Slugifying the position is a
        # reasonable mapping for common titles ("Software Engineer" ->
        # "software-engineer") but won't match Wellfound's taxonomy for
        # unusual/oddly-phrased titles -- if the slug doesn't exist,
        # Wellfound just returns an empty/generic page, and the position
        # filter in run_search() then correctly narrows that down to
        # nothing rather than surfacing irrelevant jobs.
        "build_url": lambda position: (
            f"https://wellfound.com/role/r/{_slugify(position)}" if _slugify(position) else None
        ),
    },
    "gulftalent": {
        "label": "GulfTalent",
        "default_url": "https://www.gulftalent.com/jobs/category/software",
        # GulfTalent's own ?keyword= query param does NOT filter for a plain
        # unauthenticated request -- confirmed by testing: searching
        # "software engineer" that way silently redirected to the same
        # unfiltered "all jobs" listing (canonical ?pos_ref=all) as no query
        # at all, the same failure mode Bayt's ?keyword= originally had (see
        # its build_url above). Rather than scrape irrelevant jobs, this
        # always points at GulfTalent's own Software category page (1,975+
        # real listings, confirmed server-rendered) regardless of
        # `position`, and leans on run_search()'s global position-filter
        # safety net to narrow it down -- so unlike the other templates
        # here, this source is only useful while searching for
        # software-adjacent titles.
        "build_url": lambda position: "https://www.gulftalent.com/jobs/category/software",
    },
    "tanqeeb": {
        "label": "Tanqeeb (Egypt)",
        "default_url": "https://egypt.tanqeeb.com/",
        # Confirmed working: ?keywords= genuinely filters server-side, and the
        # results link to server-rendered /jobs-in-egypt/.../<id>.html pages
        # whose link text is the job title, so the generic scraper's
        # position narrowing picks exactly the listings. Other countries use
        # the same pattern on their own subdomain (uae., saudi., qatar., ...).
        "build_url": lambda position: (
            f"https://egypt.tanqeeb.com/jobs/search?keywords={quote_plus(position)}"
            if position.strip() else None
        ),
    },
}


def seed_default_search_sites() -> None:
    """Populates models.SearchSite with KNOWN_JOB_BOARD_TEMPLATES the first
    time each template exists, so the Search tab's site list isn't empty by
    default and the user can see/remove them like any other site instead of
    them being an invisible hardcoded behavior.

    Tracked per-template-key against config.SEARCH_DEFAULT_SITES_SEEDED (a
    set of keys already seeded), not on "is the table empty" -- a user who'd
    already added their own site(s) before this feature existed (or just
    from using the app for a while) would make an empty-table check false
    immediately, so the defaults would silently never get added. Per-key
    tracking (rather than one global flag) also means adding a brand new
    template later -- e.g. this file gaining a "gulftalent" entry after a
    user's install already had "wuzzuf"/"bayt" seeded -- still gets that new
    one seeded on the next run, without re-adding "wuzzuf"/"bayt" if the
    user had deliberately removed either.
    """
    import config
    already_seeded = config.SEARCH_DEFAULT_SITES_SEEDED
    # Only the boards meant for everyone (config.SEARCH_DEFAULT_BOARDS); a
    # regional board is added when someone asks for it.
    to_seed = [key for key in KNOWN_JOB_BOARD_TEMPLATES
               if key not in already_seeded and key in config.SEARCH_DEFAULT_BOARDS]
    if not to_seed:
        return

    from db import get_session
    from models import SearchSite
    import env_store
    with get_session() as session:
        existing_types = {r[0] for r in session.query(SearchSite.site_type).all()}
        for key in to_seed:
            if key in existing_types:
                continue  # already present (e.g. re-added by hand) -- don't duplicate
            meta = KNOWN_JOB_BOARD_TEMPLATES[key]
            session.add(SearchSite(
                url=meta["default_url"],
                site_type=key,
                identifier=None,
                label=meta["label"],
            ))

    updated = already_seeded | set(to_seed)
    env_store.update_env_file({"SEARCH_DEFAULT_SITES_SEEDED": ",".join(sorted(updated))})
    config.SEARCH_DEFAULT_SITES_SEEDED = updated  # keep this process's config in sync too


def search_serpapi(query: str) -> list[dict]:
    if not config.SERPAPI_KEY:
        return []  # free-tier key not configured — skip rather than error
    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_jobs", "q": query, "api_key": config.SERPAPI_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("jobs_results", [])


def read_watchlist_sheet() -> list[dict]:
    """
    Reads a Google Sheet where you manually list companies/roles of interest.
    Expected columns: company | role_keyword | greenhouse_board_token | lever_company_slug
    """
    if not config.GOOGLE_SHEETS_ID:
        return []
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(config.GOOGLE_SHEETS_ID).sheet1
    return sheet.get_all_records()


def search_from_watchlist(
    max_results_per_site: int | None = None, target_roles: list[str] | None = None, seniority: str = "",
) -> list[dict]:
    target_roles = target_roles or []
    results = []
    for row in read_watchlist_sheet():
        try:
            board = row.get("greenhouse_board_token")
            slug = row.get("lever_company_slug")
            if board:
                jobs = _cap(_filter_relevant(search_greenhouse(board), target_roles, seniority), max_results_per_site)
                results += [{"source": "greenhouse", "watchlist_company": row.get("company"), **j} for j in jobs]
            elif slug:
                jobs = _cap(_filter_relevant(search_lever(slug), target_roles, seniority), max_results_per_site)
                results += [{"source": "lever", "watchlist_company": row.get("company"), **j} for j in jobs]
            else:
                # A watchlist row with neither a board token nor a slug names a
                # company to search by keyword -- its own role_keyword column is
                # the query, so expansion doesn't apply here.
                query = f"{row.get('role_keyword', '')} {row.get('company', '')}".strip()
                if query:
                    jobs = _cap(search_serpapi(query), max_results_per_site)
                    results += [{"source": "google_jobs", "watchlist_company": row.get("company"), **j} for j in jobs]
        except requests.RequestException:
            continue  # one bad watchlist row shouldn't sink the rest
    return results


def get_configured_sites() -> list[dict]:
    """Websites added from the dashboard's Search tab (models.SearchSite)."""
    from db import get_session
    from models import SearchSite
    with get_session() as session:
        rows = session.query(SearchSite).all()
        return [{"url": r.url, "site_type": r.site_type, "identifier": r.identifier} for r in rows]


def run_search(
    position: str = "",
    seniority: str = "",
    max_results_per_site: int | None = None,
    max_age_days: int | None = None,
    target_roles: list[str] | None = None,
    trace=None,
    include_title_mismatches: bool = False,
    should_stop=None,
) -> tuple[list[dict], list[dict]]:
    """
    Pulls from every configured free source and tags each job with its source.
    This is the RETRIEVAL half of the matching pipeline, and it's deliberately
    recall-biased -- cheap title/keyword signals only, no embeddings, no LLM.
    Semantic retrieval and scoring happen afterward in agents/ranking_agent.py.

    `target_roles` is the expanded phrase list from
    agents/query_expansion_agent.py (e.g. "AI Engineer" ->
    ["AI Engineer", "Machine Learning Engineer", "Generative AI Engineer", ...]).
    When omitted, it defaults to just [position], which reproduces the exact
    pre-expansion behavior -- so every existing caller keeps working unchanged.
    It's used two ways, matching the two kinds of source here:

      Role 1 (query-DEPENDENT: SerpAPI, Wuzzuf/SimplyHired/Wellfound/Bayt
      templates, generic scraper) -- these need a literal query string to
      fetch anything at all, so EVERY phrase becomes its own fetch and the
      results are merged. SerpAPI is the exception: its free tier is
      100 searches/month, so only the first
      config.SERPAPI_EXPANSION_LIMIT phrases are ever sent there.

      Role 2 (query-INDEPENDENT: Greenhouse/Lever boards, RemoteOK, We Work
      Remotely, watchlist) -- these return everything regardless of query, so
      the phrase list is used as a broadened title filter instead: a job is
      kept if its title token-matches ANY phrase (see _matches_any_position).

    `seniority` stays a HARD filter on titles (SENIORITY_KEYWORDS heuristics)
    and is also folded into the SerpAPI query -- deliberately unchanged even
    though seniority is now also a ranking factor, since a "senior" search
    still shouldn't surface internships.

    `max_results_per_site` caps results per individual source -- None/0 means
    no cap. Applied AFTER the title filters, not before: capping first can
    silently discard every real match on a large board (confirmed in
    practice -- Greenhouse returns a company's jobs in a roughly
    alphabetical-by-title order, so a 100-job cap on a 545-job board zeroed
    out all 38 real "Software Engineer" matches, none of which happened to
    fall in the first 100 raw results). For expanded Role 1 sources the cap is
    applied to that site's COMBINED across-phrases result set, not per phrase
    -- otherwise a position expanding to 8 phrases would silently multiply the
    user's configured per-site ceiling by 8.

    `max_age_days` drops jobs older than that (Greenhouse/Lever/RemoteOK/WWR/
    SerpAPI expose a real posted date; generic scraped sites don't, so they're
    never excluded by this filter).

    `include_title_mismatches` carries jobs whose TITLE matched no target role
    forward anyway (capped at config.SEMANTIC_CANDIDATE_CAP per run) instead
    of discarding them, so the caller's semantic retriever can rescue the ones
    whose description genuinely fits the CV. Without it the semantic path is
    dead weight -- this function would already have thrown away every job it
    exists to recover. Seniority mismatches are still dropped outright, since
    seniority remains a hard filter.

    `should_stop` is an optional no-arg callable; once it returns True (the
    user pressed Stop) no further source is queried and whatever was already
    collected is returned.

    `trace` is an optional agents.search_trace.SearchTrace that records what
    each source returned before and after each filter, for the Excel debug
    report. Purely observational -- it never changes what this function does,
    and defaults to a no-op.

    Returns (jobs, source_errors), deduplicated by URL. A single
    dead/misconfigured board (e.g. a Lever slug that 404s) is skipped and
    reported in source_errors instead of aborting the whole run -- one bad
    source shouldn't block every other one.
    """
    if trace is None:
        from agents.search_trace import NullTrace
        trace = NullTrace()
    stopped = should_stop or (lambda: False)

    # Default keeps every pre-expansion caller working identically.
    if target_roles is None:
        target_roles = [position] if position else []
    # Blank/whitespace phrases would make _matches_any_position match
    # everything (a blank query is "no filter"), silently disabling the title
    # filter entirely -- drop them rather than let one bad LLM output do that.
    target_roles = [r for r in (r.strip() for r in target_roles) if r]

    broad = []  # everything except SerpAPI -- title-filtered below
    semantic_candidates = []  # title-MISMATCHED jobs, for the semantic retriever
    source_errors = []

    def _collect(source_name, identifier, fetch, tag, enrich=None, limit=None):
        """fetch -> filter -> cap, tagging each job with its source and
        recording the count at every step for the debug report. Returns the
        tagged jobs, or [] if the source failed (recorded, never raised).

        Title-mismatched jobs are NOT thrown away when
        `include_title_mismatches` is set -- they're set aside in
        `semantic_candidates` so the semantic retriever downstream can still
        rescue the ones whose description genuinely fits the CV. Seniority
        mismatches ARE discarded outright, since seniority stays a hard filter.

        `enrich` is for sources whose listing has no description (LinkedIn,
        SmartRecruiters): it fetches one job's full posting and runs only on
        the jobs that survive filter + cap and aren't already saved, so its
        per-job requests stay few. Those sources' title mismatches aren't
        offered to the semantic retriever -- with no description there is
        nothing for it to judge. `limit` tightens the per-site cap for them.
        """
        if stopped():
            return []
        started = time.perf_counter()
        try:
            raw = fetch()
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            source_errors.append({"source": source_name, "identifier": identifier, "error": str(exc)})
            trace.record_error(source_name, identifier, exc)
            trace.record_source(source_name, identifier,
                                seconds=time.perf_counter() - started, error=str(exc))
            return []
        filtered = _filter_relevant(raw, target_roles, seniority)
        if enrich:
            known = _already_saved([j["url"] for j in filtered if j.get("url")])
            filtered = [j for j in filtered if j.get("url") not in known]
        caps = [c for c in (max_results_per_site, limit) if c]
        capped = _cap(filtered, min(caps) if caps else None)
        if enrich:
            enriched = []
            for job in capped:
                if stopped():
                    break
                try:
                    full = enrich(job)
                except requests.RequestException as exc:
                    source_errors.append({"source": source_name, "identifier": job.get("url"), "error": str(exc)})
                    trace.record_error(source_name, job.get("url"), exc)
                    if getattr(exc.response, "status_code", None) == 429:
                        break  # rate-limited: stop asking, keep what's done
                    continue
                if full:
                    enriched.append(full)
            capped = enriched
        trace.record_source(source_name, identifier, raw=len(raw),
                            after_filter=len(filtered), after_cap=len(capped),
                            seconds=time.perf_counter() - started)

        kept_ids = {id(j) for j in filtered}
        rescuable = []
        for job in raw:
            if id(job) in kept_ids:
                continue
            # Seniority is a hard filter; only TITLE mismatches are rescuable.
            if seniority and not _matches_seniority(job.get("title"), seniority):
                trace.record_retrieval({**tag, **job}, "token", "dropped",
                                       f"seniority doesn't match '{seniority}' (hard filter)")
                continue
            if include_title_mismatches and not enrich:
                rescuable.append({**tag, **job})
            else:
                trace.record_retrieval({**tag, **job}, "token", "dropped",
                                       "title matched no target role")
        semantic_candidates.extend(rescuable)

        for job in capped:
            trace.record_retrieval({**tag, **job}, "token", "kept", "title matched a target role")
        return [{**tag, **j} for j in capped]

    # ---- Role 2: query-independent sources (full board, filtered by title) --

    for board in config.GREENHOUSE_BOARD_TOKENS:
        broad += _collect("greenhouse", board,
                          lambda b=board: search_greenhouse(b),
                          {"source": "greenhouse", "board": board})

    for company in config.LEVER_COMPANY_SLUGS:
        broad += _collect("lever", company,
                          lambda c=company: search_lever(c),
                          {"source": "lever", "company_slug": company})

    # Always-on structured sources -- no per-user config needed, unlike the
    # Greenhouse/Lever loops above which depend on .env board tokens/slugs.
    # search_remoteok takes no query param of its own here: it's filtered by
    # the same _filter_relevant title pass as every other Role 2 source, so
    # the expanded phrase list applies to it uniformly.
    if _feed_on("remoteok"):
        broad += _collect("remoteok", None, search_remoteok, {"source": "remoteok"})
    if _feed_on("weworkremotely"):
        broad += _collect("weworkremotely", None, search_weworkremotely, {"source": "weworkremotely"})

    # Keyless remote boards. Himalayas and Remotive search server-side, once
    # per role phrase (merged and deduped inside the fetcher, so the per-site
    # cap covers the combined set); Jobicy and Working Nomads are whole feeds
    # filtered by title like RemoteOK. All four drop jobs the candidate can't
    # apply to (config.SEARCH_ELIGIBLE_LOCATIONS).
    if _feed_on("himalayas"):
        broad += _collect("himalayas", None, lambda: search_himalayas(target_roles), {"source": "himalayas"})
    if _feed_on("remotive"):
        broad += _collect("remotive", None, lambda: search_remotive(target_roles), {"source": "remotive"})
    if _feed_on("jobicy"):
        broad += _collect("jobicy", None, search_jobicy, {"source": "jobicy"})
    if _feed_on("workingnomads"):
        broad += _collect("workingnomads", None, search_workingnomads, {"source": "workingnomads"})

    for slug in config.ASHBY_BOARD_SLUGS:
        broad += _collect("ashby", slug, lambda s=slug: search_ashby(s),
                          {"source": "ashby", "board": slug})

    for company in config.SMARTRECRUITERS_COMPANIES:
        broad += _collect("smartrecruiters", company,
                          lambda c=company: search_smartrecruiters(c, target_roles),
                          {"source": "smartrecruiters", "company_slug": company},
                          enrich=enrich_smartrecruiters)

    if config.LINKEDIN_LOCATIONS and target_roles:
        broad += _collect("linkedin", ", ".join(config.LINKEDIN_LOCATIONS),
                          lambda: search_linkedin(target_roles, config.LINKEDIN_LOCATIONS, max_age_days,
                                                  should_stop=stopped),
                          {"source": "linkedin"},
                          enrich=enrich_linkedin, limit=LINKEDIN_MAX_JOBS)

    watchlist_started = time.perf_counter()
    try:
        watchlist_jobs = search_from_watchlist(
            max_results_per_site=max_results_per_site, target_roles=target_roles, seniority=seniority,
        )
        broad += watchlist_jobs
        trace.record_source("watchlist", None, raw=len(watchlist_jobs),
                            after_filter=len(watchlist_jobs), after_cap=len(watchlist_jobs),
                            seconds=time.perf_counter() - watchlist_started)
        for job in watchlist_jobs:
            trace.record_retrieval(job, "token", "kept", "from Google Sheets watchlist")
    except Exception as exc:  # noqa: BLE001 -- e.g. bad service account creds
        source_errors.append({"source": "watchlist", "identifier": None, "error": str(exc)})
        trace.record_error("watchlist", None, exc)
        trace.record_source("watchlist", None,
                            seconds=time.perf_counter() - watchlist_started, error=str(exc))

    # ---- Dashboard-added sites: Role 2 for Greenhouse/Lever, Role 1 for the rest ----

    for site in get_configured_sites():
        if stopped():
            break
        site_started = time.perf_counter()
        try:
            if site["site_type"] == "greenhouse":
                broad += _collect("greenhouse", site["identifier"],
                                  lambda s=site: search_greenhouse(s["identifier"]),
                                  {"source": "greenhouse", "board": site["identifier"],
                                   "site_url": site["url"]})
            elif site["site_type"] == "lever":
                broad += _collect("lever", site["identifier"],
                                  lambda s=site: search_lever(s["identifier"]),
                                  {"source": "lever", "company_slug": site["identifier"],
                                   "site_url": site["url"]})
            elif site["site_type"] == "ashby":
                broad += _collect("ashby", site["identifier"],
                                  lambda s=site: search_ashby(s["identifier"]),
                                  {"source": "ashby", "board": site["identifier"],
                                   "site_url": site["url"]})
            elif site["site_type"] == "smartrecruiters":
                broad += _collect("smartrecruiters", site["identifier"],
                                  lambda s=site: search_smartrecruiters(s["identifier"], target_roles),
                                  {"source": "smartrecruiters", "company_slug": site["identifier"],
                                   "site_url": site["url"]},
                                  enrich=enrich_smartrecruiters)
            elif site["site_type"] in KNOWN_JOB_BOARD_TEMPLATES:
                # Role 1. The stored URL is just a display/landing link -- the
                # real, query-filtered search URL depends on the phrase and is
                # rebuilt fresh for EACH expanded role, then all phrases'
                # results are merged, deduped, and capped as one set.
                per_site = []
                for role in target_roles:
                    if stopped():
                        break
                    built_url = KNOWN_JOB_BOARD_TEMPLATES[site["site_type"]]["build_url"](role)
                    if built_url:
                        found_for_role = search_generic_site(built_url, position=role)
                        per_site += found_for_role
                        trace.record_source(site["site_type"], built_url, role_phrase=role,
                                            raw=len(found_for_role), after_filter=len(found_for_role),
                                            after_cap=len(found_for_role))
                deduped = _cap(_dedupe_by_url(per_site), max_results_per_site)
                for job in deduped:
                    trace.record_retrieval(job, "role1", "kept", f"scraped from {site['site_type']}")
                broad += deduped
            else:
                # Role 1. A plain career page: scraped once per phrase, since
                # its own link-narrowing is what `position` drives there.
                per_site = []
                for role in target_roles:
                    if stopped():
                        break
                    found_for_role = search_generic_site(site["url"], position=role)
                    per_site += found_for_role
                    trace.record_source("generic", site["url"], role_phrase=role,
                                        raw=len(found_for_role), after_filter=len(found_for_role),
                                        after_cap=len(found_for_role))
                if not target_roles:  # no phrases at all -- scrape unfiltered
                    per_site = search_generic_site(site["url"])
                deduped = _cap(_dedupe_by_url(per_site), max_results_per_site)
                for job in deduped:
                    trace.record_retrieval(job, "role1", "kept", "scraped from a configured site")
                broad += deduped
        except requests.RequestException as exc:
            source_errors.append({"source": site["site_type"], "identifier": site["url"], "error": str(exc)})
            trace.record_error(site["site_type"], site["url"], exc)
            trace.record_source(site["site_type"], site["url"],
                                seconds=time.perf_counter() - site_started, error=str(exc))

    # Global title-filter safety net across the whole combined set.
    if target_roles:
        broad = [j for j in broad if _matches_any_position(j.get("title"), target_roles)]

    if seniority:
        broad = [j for j in broad if _matches_seniority(j.get("title"), seniority)]

    # ---- Role 1: SerpAPI, capped to a few phrases to protect its quota -----
    #
    # SerpAPI results are deliberately NOT re-filtered by title: the query
    # already encodes the role + seniority, so Google Jobs' own matching is
    # what narrowed them.
    serpapi_results = []
    serp_roles = target_roles[: max(0, config.SERPAPI_EXPANSION_LIMIT)] if target_roles else []
    seniority_label = SENIORITY_LABELS.get(seniority, "")
    for role in serp_roles:
        if stopped():
            break
        serp_query = " ".join(part for part in (seniority_label, role) if part).strip()
        if not serp_query:
            continue
        serp_started = time.perf_counter()
        try:
            raw = search_serpapi(serp_query)
            jobs = _cap(raw, max_results_per_site)
            serpapi_results += [{"source": "google_jobs", **j} for j in jobs]
            trace.record_source("google_jobs", serp_query, role_phrase=role, raw=len(raw),
                                after_filter=len(raw), after_cap=len(jobs),
                                seconds=time.perf_counter() - serp_started)
            for job in jobs:
                trace.record_retrieval({"source": "google_jobs", **job}, "role1", "kept",
                                       f"Google Jobs query: {serp_query}")
        except requests.RequestException as exc:
            source_errors.append({"source": "google_jobs", "identifier": serp_query, "error": str(exc)})
            trace.record_error("google_jobs", serp_query, exc)
            trace.record_source("google_jobs", serp_query, role_phrase=role,
                                seconds=time.perf_counter() - serp_started, error=str(exc))
    serpapi_results = _dedupe_by_url(serpapi_results)

    if max_age_days:
        before_age = len(broad) + len(serpapi_results)
        broad = [j for j in broad if _within_max_age(j, max_age_days)]
        serpapi_results = [j for j in serpapi_results if _within_max_age(j, max_age_days)]
        dropped = before_age - len(broad) - len(serpapi_results)
        if dropped:
            trace.record_source("(max-age filter)", f"{max_age_days} days",
                                raw=before_age, after_filter=before_age - dropped,
                                after_cap=before_age - dropped)

    # Title-mismatched candidates ride along for the semantic retriever. Age
    # filtering applies to them too, and the cap is global (not per source) so
    # a run's embedding cost has a hard ceiling regardless of how many boards
    # are configured.
    rescuable = semantic_candidates
    if include_title_mismatches and rescuable:
        if max_age_days:
            rescuable = [j for j in rescuable if _within_max_age(j, max_age_days)]
        rescuable = _dedupe_by_url(rescuable)[: max(0, config.SEMANTIC_CANDIDATE_CAP)]

    # Final cross-source dedupe: the same posting can legitimately arrive from
    # two different sources (a company's own careers page and its Greenhouse
    # board), not just from two expanded phrases.
    final = _dedupe_by_url(broad + serpapi_results + rescuable)
    trace.set_meta(retrieved_before_dedupe=len(broad) + len(serpapi_results),
                   retrieved_after_dedupe=len(final),
                   semantic_candidates_carried=len(rescuable))
    return final, source_errors
