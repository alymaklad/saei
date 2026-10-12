"""'On the Boards': Hacker News and Reddit threads for the weekly digest.
Network faked."""
import pytest

from agents import community_news as cn

REDDIT_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Nvidia's erroneous paper accepted as spotlight [D]</title>
<link href="https://www.reddit.com/r/MachineLearning/comments/1/a/"/></entry>
<entry><title>[R] A new scaling law</title><link href="https://www.reddit.com/r/MachineLearning/comments/2/b/"/></entry>
<entry><title>Third post</title><link href="https://www.reddit.com/r/MachineLearning/comments/3/c/"/></entry>
<entry><title>Fourth post</title><link href="https://www.reddit.com/r/MachineLearning/comments/4/d/"/></entry>
</feed>"""


class Resp:
    def __init__(self, content=b"", data=None, status=200):
        self.content, self._data, self.status_code = content, data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cn.requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(cn.time, "sleep", lambda s: None)


@pytest.fixture
def cfg(monkeypatch):
    import config
    monkeypatch.setattr(config, "NEWS_SUBREDDITS", [])
    return config


def test_communities_are_picked_from_the_topic_unless_set(cfg):
    assert cn.subreddits_for("AI Engineer") == ["MachineLearning", "LocalLLaMA", "artificial"]
    assert cn.subreddits_for("Senior Backend Developer") == ["programming", "ExperiencedDevs"]
    assert cn.subreddits_for("Florist") == ["technology"]
    assert cn.subreddits_for("Email marketing") == ["technology"]   # "ai" inside a word doesn't count
    cfg.NEWS_SUBREDDITS = ["r/LocalLLaMA", "MLOps/"]
    assert cn.subreddits_for("AI Engineer") == ["LocalLLaMA", "MLOps"]


def test_hacker_news_returns_the_most_upvoted_threads(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw["params"])
        return Resp(data={"hits": [
            {"objectID": "1", "title": "Small", "points": 40, "num_comments": 3, "url": "https://a"},
            {"objectID": "2", "title": "Big", "points": 900, "num_comments": 300, "url": "https://b"},
        ]})
    monkeypatch.setattr(cn.requests, "get", fake_get)
    posts = cn.fetch_hacker_news("AI Engineer")
    assert [p["title"] for p in posts] == ["Big", "Small"]
    assert posts[0]["link"] == "https://news.ycombinator.com/item?id=2"
    assert seen["optionalWords"] == "AI Engineer" and "points>=30" in seen["numericFilters"]


def test_reddit_reads_each_communitys_weekly_top_feed(monkeypatch):
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        assert "Saei" in kw["headers"]["User-Agent"]
        return Resp(REDDIT_RSS)
    monkeypatch.setattr(cn.requests, "get", fake_get)
    posts = cn.fetch_reddit(["MachineLearning", "LocalLLaMA"], per_community=2, limit=3)
    assert urls[0] == "https://www.reddit.com/r/MachineLearning/top/.rss?t=week"
    assert [p["title"] for p in posts] == [
        "Nvidia's erroneous paper accepted as spotlight", "A new scaling law", "Nvidia's erroneous paper accepted as spotlight"]
    assert posts[0]["community"] == "r/MachineLearning" and posts[2]["community"] == "r/LocalLLaMA"


def test_an_unreachable_source_just_contributes_nothing(monkeypatch, cfg):
    monkeypatch.setattr(cn.requests, "get", lambda *a, **k: Resp(status=403))
    assert cn.fetch_community("AI Engineer") == {"hacker_news": [], "reddit": []}
    assert cn.community_text({"hacker_news": [], "reddit": []}) == ""


def test_the_paper_gets_an_on_the_boards_section():
    from agents import news_email
    community = {
        "hacker_news": [{"title": "Big thread", "link": "https://news.ycombinator.com/item?id=2",
                         "community": "Hacker News", "points": 1354, "comments": 1532}],
        "reddit": [{"title": "Reddit thread", "link": "https://www.reddit.com/r/x/1",
                    "community": "r/MachineLearning", "points": None, "comments": None}],
    }
    html = news_email.render_digest_email({"topic": "AI", "text": "x", "items": [], "community": community})
    assert "On the Boards" in html and "1,354 points &middot; 1,532 comments" in html
    assert 'href="https://www.reddit.com/r/x/1"' in html and "R/MACHINELEARNING" not in html
    assert "Hacker News and Reddit boards" in html
    text = cn.community_text(community)
    assert "Big thread (Hacker News · 1354 points · 1532 comments)" in text and "r/MachineLearning" in text


def test_a_rate_limited_feed_is_retried_once(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return Resp(status=429) if len(calls) == 1 else Resp(REDDIT_RSS)
    monkeypatch.setattr(cn.requests, "get", fake_get)
    assert len(cn.fetch_reddit(["MachineLearning"], per_community=1)) == 1 and len(calls) == 2
