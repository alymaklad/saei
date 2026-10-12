"""
News Digest Agent -- the week's news for your field, summarised with links.

  topic     news_topic(): NEWS_TOPIC if you set one in Settings, else the
            professional title on your profile ("AI Engineer"), else
            "software engineering".
  articles  fetch_news(): Google News' public RSS search feed, last 7 days.
            Free and keyless. SerpAPI's Google News engine is used only if
            the feed can't be reached and a SERPAPI_KEY is set, since every
            SerpAPI call spends the same 100-a-month quota job search uses.
  summary   summarize_news(): one LLM call. The articles go in numbered; the
            model cites the numbers it used, and the digest ends with those
            articles' titles, sources and links, so every point can be opened.
"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from langchain_core.messages import HumanMessage, SystemMessage

import config
from agents.llm import get_llm

DEFAULT_TOPIC = "software engineering"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def news_topic() -> str:
    if config.NEWS_TOPIC:
        return config.NEWS_TOPIC
    try:
        import profile_store
        profile = profile_store.load_profile_dict() or {}
        title = str((profile.get("contact") or {}).get("title") or "").strip()
        if title:
            return title
    except Exception:  # noqa: BLE001 -- no profile yet is not a reason to skip the digest
        pass
    return DEFAULT_TOPIC


def _clean_title(title: str, source: str) -> str:
    """Google News appends " - Source" to every headline; the source is kept
    separately, so drop the copy."""
    title = (title or "").strip()
    if source and title.endswith(f" - {source}"):
        title = title[: -len(source) - 3].strip()
    return title


def _from_google_news_rss(topic: str, limit: int) -> list[dict]:
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(f"{topic} when:7d"))
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Sa'ei weekly news digest)"})
    resp.raise_for_status()
    articles = []
    for item in ET.fromstring(resp.content).findall("./channel/item"):
        source = (item.findtext("source") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            published = ""
        articles.append({
            "title": _clean_title(item.findtext("title"), source),
            "source": source,
            "link": (item.findtext("link") or "").strip(),
            "date": published,
            "snippet": "",   # the feed carries only a link here, no summary text
        })
        if len(articles) >= limit:
            break
    return articles


def _from_serpapi(topic: str, limit: int) -> list[dict]:
    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_news", "q": topic, "api_key": config.SERPAPI_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    return [{
        "title": a.get("title", ""),
        "source": (a.get("source") or {}).get("name", "") if isinstance(a.get("source"), dict) else str(a.get("source") or ""),
        "link": a.get("link", ""),
        "date": a.get("date", ""),
        "snippet": a.get("snippet", ""),
    } for a in resp.json().get("news_results", [])[:limit]]


def fetch_news(topic: str, limit: int | None = None) -> list[dict]:
    """This week's articles for `topic`: [{title, source, link, date, snippet}]."""
    limit = limit or config.NEWS_MAX_ARTICLES
    try:
        articles = _from_google_news_rss(topic, limit)
        if articles:
            return articles
    except (requests.RequestException, ET.ParseError):
        pass
    if config.SERPAPI_KEY:
        try:
            return _from_serpapi(topic, limit)
        except requests.RequestException:
            pass
    return []


_CITATION = re.compile(r"\[(\d{1,2})\]")


def summarize_news(topic: str, articles: list[dict]) -> str:
    if not articles:
        return f"No news found for '{topic}' this week (the news feed couldn't be reached)."
    numbered = "\n".join(
        f"[{i}] {a['title']}" + (f" ({a['source']}" + (f", {a['date']}" if a.get("date") else "") + ")" if a.get("source") else "")
        + (f" -- {a['snippet']}" if a.get("snippet") else "")
        for i, a in enumerate(articles, 1)
    )
    messages = [
        SystemMessage(content=(
            f"Summarize this week's most important news for someone working as / in: {topic}. "
            "Group it into themes, 5-8 concise bullet points. Use only the numbered articles "
            "given. End every bullet with the numbers of the articles it draws on, in square "
            "brackets, like [2] or [3][7]. Skip articles that aren't relevant to the field. "
            "You see only headlines: say what they say, and never add details, numbers or "
            "claims a headline doesn't state. "
            "No introduction and no closing remarks."
        )),
        HumanMessage(content=numbered),
    ]
    summary = str(get_llm(temperature=0.2).invoke(messages).content).strip()

    cited = sorted({int(n) for n in _CITATION.findall(summary) if 1 <= int(n) <= len(articles)})
    listed = cited or range(1, len(articles) + 1)
    sources = "\n".join(
        f"[{i}] {articles[i - 1]['title']}"
        + (f" — {articles[i - 1]['source']}" if articles[i - 1].get("source") else "")
        + f"\n    {articles[i - 1]['link']}"
        for i in listed if articles[i - 1].get("link")
    )
    return f"{summary}\n\nSources:\n{sources}" if sources else summary
