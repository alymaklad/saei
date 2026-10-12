"""The weekly news digest: topic from your profile (or Settings), keyless
Google News RSS, and a summary whose points link back to the articles.
Network and model are faked."""
import pytest

from agents import news_agent

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Agents ship to production - The Verge</title><link>https://news.google.com/rss/articles/A1</link>
<pubDate>Wed, 07 Oct 2026 15:00:12 GMT</pubDate><source url="https://www.theverge.com">The Verge</source></item>
<item><title>New RAG benchmark released - VentureBeat</title><link>https://news.google.com/rss/articles/B2</link>
<pubDate>Thu, 08 Oct 2026 09:00:00 GMT</pubDate><source url="https://venturebeat.com">VentureBeat</source></item>
<item><title>Unrelated gardening tips - Garden Weekly</title><link>https://news.google.com/rss/articles/C3</link>
<pubDate>Fri, 09 Oct 2026 09:00:00 GMT</pubDate><source url="https://g.example">Garden Weekly</source></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, content=b"", status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def cfg(monkeypatch):
    import config
    monkeypatch.setattr(config, "NEWS_TOPIC", "")
    monkeypatch.setattr(config, "NEWS_MAX_ARTICLES", 15)
    monkeypatch.setattr(config, "SERPAPI_KEY", "")
    return config


def test_topic_follows_settings_then_profile_then_default(cfg, monkeypatch):
    import profile_store
    monkeypatch.setattr(profile_store, "load_profile_dict", lambda: {"contact": {"title": "AI Engineer"}})
    assert news_agent.news_topic() == "AI Engineer"
    cfg.NEWS_TOPIC = "LLM agents, RAG"
    assert news_agent.news_topic() == "LLM agents, RAG"
    cfg.NEWS_TOPIC = ""
    monkeypatch.setattr(profile_store, "load_profile_dict", lambda: None)
    assert news_agent.news_topic() == news_agent.DEFAULT_TOPIC


def test_articles_come_from_the_keyless_feed_with_clean_titles(cfg, monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse(RSS)
    monkeypatch.setattr(news_agent.requests, "get", fake_get)
    articles = news_agent.fetch_news("AI Engineer")
    assert "news.google.com/rss/search" in seen["url"] and "when%3A7d" in seen["url"]
    assert articles[0] == {"title": "Agents ship to production", "source": "The Verge",
                           "link": "https://news.google.com/rss/articles/A1", "date": "2026-10-07", "snippet": ""}
    assert len(articles) == 3


def test_an_unreachable_feed_gives_no_articles_without_a_serpapi_key(cfg, monkeypatch):
    monkeypatch.setattr(news_agent.requests, "get", lambda *a, **k: FakeResponse(status=503))
    assert news_agent.fetch_news("AI Engineer") == []


def test_the_summary_ends_with_links_to_the_articles_it_cites(cfg, monkeypatch):
    class FakeLLM:
        def invoke(self, messages):
            assert "[1] Agents ship to production (The Verge, 2026-10-07)" in messages[1].content
            return type("R", (), {"content": "- Agents are reaching production [1]\n- RAG gets a benchmark [2]"})()
    monkeypatch.setattr(news_agent, "get_llm", lambda **kw: FakeLLM())
    monkeypatch.setattr(news_agent.requests, "get", lambda *a, **k: FakeResponse(RSS))
    digest = news_agent.summarize_news("AI Engineer", news_agent.fetch_news("AI Engineer"))
    assert digest.startswith("- Agents are reaching production [1]")
    assert "Sources:\n[1] Agents ship to production — The Verge\n    https://news.google.com/rss/articles/A1" in digest
    assert "[2] New RAG benchmark released — VentureBeat" in digest
    assert "gardening" not in digest   # not cited, not listed


def test_no_articles_says_so_plainly(cfg):
    assert "No news found for 'AI Engineer'" in news_agent.summarize_news("AI Engineer", [])
