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


# ---- the newspaper email -------------------------------------------------------------

def test_the_weekly_email_is_a_newspaper_built_from_real_headlines():
    from datetime import date
    from agents import news_email
    a = {"title": "Agents ship to production", "source": "The Verge", "link": "https://n.example/a"}
    b = {"title": "New RAG benchmark", "source": "VentureBeat", "link": "https://n.example/b"}
    c = {"title": "Chips & <things>", "source": "IEEE", "link": "https://n.example/c"}
    digest = {"topic": "AI Engineer", "text": "x", "items": [
        {"text": "Agents are reaching production.", "sources": [a]},
        {"text": "RAG gets a benchmark.", "sources": [b]},
        {"text": "A chip course launches.", "sources": [c]},
        {"text": "One more story.", "sources": [a]},
    ]}
    html = news_email.render_digest_email(digest, edition=3, today=date(2026, 10, 12), reader="Aly")
    assert "The Sa&#8217;ei Weekly" in html and "VOL. 1 &middot; NO. 3" in html
    assert "MONDAY, 12 OCTOBER 2026" in html and "THIS WEEK IN &middot; AI ENGINEER" in html
    assert "Agents ship to production" in html          # the article's own headline
    assert 'href="https://n.example/b"' in html          # every story links out
    assert "Chips &amp; &lt;things&gt;" in html and "<things>" not in html   # escaped
    assert 'colspan="2"' in html                         # odd last story spans the row
    assert "Compiled for Aly" in html
    assert "<style" in html and "style=\"" in html       # inline styles for mail apps


def test_a_week_without_news_still_renders_the_paper():
    from agents import news_email
    html = news_email.render_digest_email({"topic": "AI", "text": "No news found for 'AI' this week.", "items": []})
    assert "No news found for &#x27;AI&#x27; this week." in html or "No news found for 'AI'" in html
