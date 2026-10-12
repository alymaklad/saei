"""
"On the Boards": what developers discussed this week, from Hacker News and
Reddit, for the weekly digest. Shown as the threads themselves -- real
titles, real points and comment counts, linked to the discussion -- with no
LLM in between, so nothing here can be a model's paraphrase.

  Hacker News  The Algolia HN search API (free, keyless). This week's
               stories for the topic, at least MIN_HN_POINTS points, ranked
               by points. The topic's words are optional matches, so "AI
               Engineer" also finds stories that say just "AI".
  Reddit       Each community's "top of the week" RSS feed. Reddit refuses
               scripted calls to its JSON API and to its site-wide search
               (403 / 429), but serves these feeds, which are meant for feed
               readers. Communities: NEWS_SUBREDDITS, else a few picked from
               the topic (see default_subreddits).

Every fetch fails soft: a source that can't be reached just contributes
nothing, and the digest goes out without it.
"""
import re
import time
import xml.etree.ElementTree as ET

import requests

import config

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
REDDIT_TOP_RSS = "https://www.reddit.com/r/{sub}/top/.rss?t=week"
USER_AGENT = "Saei-news-digest/1.0 (personal job-search assistant; github.com/alymaklad/Saei)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
MIN_HN_POINTS = 30

# Communities for a topic when NEWS_SUBREDDITS isn't set: the first rule whose
# words appear in the topic wins.
_SUBREDDITS_BY_TOPIC = [
    (("ai", "ml", "machine learning", "llm", "deep learning", "nlp", "data scien", "computer vision"),
     ["MachineLearning", "LocalLLaMA", "artificial"]),
    (("data engineer", "analytics", "data analyst"), ["dataengineering", "datascience"]),
    (("devops", "cloud", "sre", "platform"), ["devops", "kubernetes"]),
    (("security", "cyber"), ["netsec", "cybersecurity"]),
    (("frontend", "front-end", "web", "react", "javascript"), ["webdev", "javascript"]),
    (("mobile", "android", "ios", "flutter"), ["androiddev", "iOSProgramming", "FlutterDev"]),
    (("product manager", "product management"), ["ProductManagement"]),
    (("design", "ux", "ui"), ["UXDesign", "userexperience"]),
    (("software", "developer", "engineer", "backend", "programming"), ["programming", "ExperiencedDevs"]),
]


def default_subreddits(topic: str) -> list[str]:
    t = f" {topic.lower()} "
    for words, subs in _SUBREDDITS_BY_TOPIC:
        if any(re.search(rf"(?<![a-z]){re.escape(w)}", t) for w in words):
            return subs
    return ["technology"]


def subreddits_for(topic: str) -> list[str]:
    chosen = [s.strip().removeprefix("r/").strip("/") for s in config.NEWS_SUBREDDITS]
    return [s for s in chosen if s] or default_subreddits(topic)


def fetch_hacker_news(topic: str, limit: int = 5) -> list[dict]:
    week_ago = int(time.time()) - 7 * 86400
    try:
        resp = requests.get(HN_SEARCH, timeout=20, params={
            "query": topic, "optionalWords": topic, "tags": "story", "hitsPerPage": 30,
            "numericFilters": f"created_at_i>{week_ago},points>={MIN_HN_POINTS}",
        })
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except (requests.RequestException, ValueError):
        return []
    hits.sort(key=lambda h: h.get("points") or 0, reverse=True)
    return [{
        "title": h.get("title") or "",
        "link": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
        "url": h.get("url") or "",
        "community": "Hacker News",
        "points": h.get("points") or 0,
        "comments": h.get("num_comments") or 0,
    } for h in hits[:limit] if h.get("title") and h.get("objectID")]


def _clean_reddit_title(title: str) -> str:
    """Drop r/MachineLearning's [D] / [R] / [P] tags."""
    return re.sub(r"\s*\[(?:D|R|P|N|Discussion|Research|Project|News)\]\s*", " ", title or "").strip()


REDDIT_PACE_SECONDS = 2      # between communities: logged-out feeds are rate-limited
REDDIT_RETRY_SECONDS = 10    # one retry after a 429


def _reddit_feed(sub: str):
    for attempt in range(2):
        resp = requests.get(REDDIT_TOP_RSS.format(sub=sub), timeout=20,
                            headers={"User-Agent": USER_AGENT})
        if resp.status_code == 429 and attempt == 0:
            time.sleep(REDDIT_RETRY_SECONDS)
            continue
        resp.raise_for_status()
        return ET.fromstring(resp.content).findall("a:entry", ATOM)
    return []


def fetch_reddit(subreddits: list[str], per_community: int = 3, limit: int = 6) -> list[dict]:
    posts = []
    for i, sub in enumerate(subreddits):
        if i:
            time.sleep(REDDIT_PACE_SECONDS)
        try:
            entries = _reddit_feed(sub)
        except (requests.RequestException, ET.ParseError):
            continue
        taken = 0
        for entry in entries:
            title = _clean_reddit_title(entry.findtext("a:title", default="", namespaces=ATOM))
            link = entry.find("a:link", ATOM)
            href = link.get("href") if link is not None else ""
            if not title or not href:
                continue
            posts.append({"title": title, "link": href, "url": "", "community": f"r/{sub}",
                          "points": None, "comments": None})
            taken += 1
            if taken >= per_community:
                break
    return posts[:limit]


def fetch_community(topic: str) -> dict:
    """{"hacker_news": [...], "reddit": [...]} -- either may be empty."""
    return {
        "hacker_news": fetch_hacker_news(topic),
        "reddit": fetch_reddit(subreddits_for(topic)),
    }


def community_text(community: dict) -> str:
    """The plain-text "On the boards" section, or "" when there's nothing."""
    lines = []
    for post in (community or {}).get("hacker_news", []):
        lines.append(f"- {post['title']} (Hacker News · {post['points']} points · "
                     f"{post['comments']} comments)\n    {post['link']}")
    for post in (community or {}).get("reddit", []):
        lines.append(f"- {post['title']} ({post['community']})\n    {post['link']}")
    return ("On the boards this week:\n" + "\n".join(lines)) if lines else ""
