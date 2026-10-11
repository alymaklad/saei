"""
New job-board sources: SimplyHired/Wellfound/GulfTalent's build_url templates
(pure functions, no network), RemoteOK's JSON parser, and We Work Remotely's
RSS parser, plus the config.SEARCH_DEFAULT_SITES_SEEDED legacy-bool
migration that lets a NEW template get seeded on an install that already ran
seeding under the old boolean flag.
"""
import importlib
from unittest.mock import patch, Mock

import config
from agents import search_agent


# ---- KNOWN_JOB_BOARD_TEMPLATES.build_url -----------------------------------

def test_simplyhired_build_url_filters_by_position():
    url = search_agent.KNOWN_JOB_BOARD_TEMPLATES["simplyhired"]["build_url"]("software engineer")
    assert url == "https://www.simplyhired.com/search?q=software+engineer"


def test_simplyhired_build_url_blank_position_returns_none():
    assert search_agent.KNOWN_JOB_BOARD_TEMPLATES["simplyhired"]["build_url"]("   ") is None


def test_wellfound_build_url_slugifies_position():
    url = search_agent.KNOWN_JOB_BOARD_TEMPLATES["wellfound"]["build_url"]("Software Engineer")
    assert url == "https://wellfound.com/role/r/software-engineer"


def test_wellfound_build_url_blank_position_returns_none():
    assert search_agent.KNOWN_JOB_BOARD_TEMPLATES["wellfound"]["build_url"]("") is None


def test_gulftalent_build_url_is_position_independent():
    """GulfTalent's own ?keyword= param doesn't filter (verified by testing),
    so this template always points at its Software category page regardless
    of what's searched -- run_search()'s global position filter is what
    actually narrows results, not GulfTalent's own query string."""
    always = "https://www.gulftalent.com/jobs/category/software"
    assert search_agent.KNOWN_JOB_BOARD_TEMPLATES["gulftalent"]["build_url"]("software engineer") == always
    assert search_agent.KNOWN_JOB_BOARD_TEMPLATES["gulftalent"]["build_url"]("") == always


# ---- search_remoteok ---------------------------------------------------

REMOTEOK_SAMPLE = [
    {"legal": "Attribution required if you display these jobs publicly. https://remoteok.com/"},
    {
        "id": "111",
        "position": "Senior Backend Engineer",
        "company": "Acme Corp",
        "url": "https://remoteok.com/remote-jobs/111",
        "description": "Build backend systems.",
        "date": "2026-08-01T00:00:00+00:00",
    },
    {
        "id": "112",
        "position": "Marketing Manager",
        "company": "Widgets Inc",
        "url": "https://remoteok.com/remote-jobs/112",
        "description": "Run campaigns.",
        "date": "2026-08-02T00:00:00+00:00",
    },
    {"tags": ["malformed-entry-missing-id-and-position"]},
]


def _mock_response(json_data=None, content=b""):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json = Mock(return_value=json_data)
    resp.content = content
    return resp


def test_search_remoteok_skips_legal_notice_and_malformed_rows():
    with patch("agents.search_agent.requests.get", return_value=_mock_response(REMOTEOK_SAMPLE)):
        jobs = search_agent.search_remoteok()
    assert len(jobs) == 2
    assert {j["title"] for j in jobs} == {"Senior Backend Engineer", "Marketing Manager"}
    assert all("date" in j and "company" in j and "url" in j for j in jobs)


def test_search_remoteok_filters_by_position():
    with patch("agents.search_agent.requests.get", return_value=_mock_response(REMOTEOK_SAMPLE)):
        jobs = search_agent.search_remoteok(position="backend")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Backend Engineer"


def test_search_remoteok_non_list_response_returns_empty():
    with patch("agents.search_agent.requests.get", return_value=_mock_response({"error": "not a list"})):
        assert search_agent.search_remoteok() == []


# ---- search_weworkremotely ----------------------------------------------

WWR_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <item>
      <title>Acme Corp: Senior Backend Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer</link>
      <pubDate>Sat, 01 Aug 2026 00:00:00 +0000</pubDate>
      <description>&lt;p&gt;Build backend systems.&lt;/p&gt;</description>
    </item>
    <item>
      <title>No colon in this title</title>
      <link>https://weworkremotely.com/remote-jobs/no-colon</link>
      <pubDate>Sun, 02 Aug 2026 00:00:00 +0000</pubDate>
      <description>Some description.</description>
    </item>
  </channel>
</rss>
"""


def test_search_weworkremotely_splits_company_and_title():
    with patch("agents.search_agent.requests.get", return_value=_mock_response(content=WWR_RSS_SAMPLE)):
        jobs = search_agent.search_weworkremotely()
    assert len(jobs) == 2
    assert jobs[0]["company"] == "Acme Corp"
    assert jobs[0]["title"] == "Senior Backend Engineer"
    assert jobs[0]["date"] == "Sat, 01 Aug 2026 00:00:00 +0000"


def test_search_weworkremotely_falls_back_when_no_colon():
    with patch("agents.search_agent.requests.get", return_value=_mock_response(content=WWR_RSS_SAMPLE)):
        jobs = search_agent.search_weworkremotely()
    assert jobs[1]["company"] == ""
    assert jobs[1]["title"] == "No colon in this title"


# ---- _extract_posted_at for the two new date formats ------------------

def test_extract_posted_at_handles_remoteok_iso_date():
    posted = search_agent._extract_posted_at({"date": "2026-08-01T00:00:00+00:00"})
    assert posted is not None
    assert posted.year == 2026 and posted.month == 8 and posted.day == 1


def test_extract_posted_at_handles_wwr_rfc822_date():
    posted = search_agent._extract_posted_at({"date": "Sat, 01 Aug 2026 00:00:00 +0000"})
    assert posted is not None
    assert posted.year == 2026 and posted.month == 8 and posted.day == 1


# ---- config.SEARCH_DEFAULT_SITES_SEEDED migration ------------------------

def test_legacy_true_migrates_to_wuzzuf_and_bayt(monkeypatch):
    monkeypatch.setenv("SEARCH_DEFAULT_SITES_SEEDED", "true")
    assert config._seeded_template_keys("SEARCH_DEFAULT_SITES_SEEDED") == {"wuzzuf", "bayt"}


def test_legacy_false_migrates_to_empty_set(monkeypatch):
    monkeypatch.setenv("SEARCH_DEFAULT_SITES_SEEDED", "false")
    assert config._seeded_template_keys("SEARCH_DEFAULT_SITES_SEEDED") == set()


def test_comma_list_parses_to_set(monkeypatch):
    monkeypatch.setenv("SEARCH_DEFAULT_SITES_SEEDED", "bayt,wuzzuf,simplyhired")
    assert config._seeded_template_keys("SEARCH_DEFAULT_SITES_SEEDED") == {"bayt", "wuzzuf", "simplyhired"}


def test_unset_returns_empty_set(monkeypatch):
    monkeypatch.delenv("SEARCH_DEFAULT_SITES_SEEDED", raising=False)
    assert config._seeded_template_keys("SEARCH_DEFAULT_SITES_SEEDED") == set()


def test_new_template_gets_seeded_when_legacy_flag_already_true():
    """The actual bug this migration fixes: an install with the old
    SEARCH_DEFAULT_SITES_SEEDED=true flag must still pick up brand new
    templates (gulftalent/simplyhired/wellfound/tanqeeb) instead of the global flag
    silently skipping seeding forever."""
    already_seeded = {"wuzzuf", "bayt"}  # what "true" migrates to
    to_seed = [k for k in search_agent.KNOWN_JOB_BOARD_TEMPLATES if k not in already_seeded]
    assert set(to_seed) == {"simplyhired", "wellfound", "gulftalent", "tanqeeb"}


def test_a_fresh_install_seeds_only_the_worldwide_boards(monkeypatch):
    """Regional boards (Egypt, the Gulf) wait until someone adds them."""
    monkeypatch.delenv("SEARCH_DEFAULT_BOARDS", raising=False)
    import importlib
    import config as cfg
    importlib.reload(cfg)
    try:
        assert cfg.SEARCH_DEFAULT_BOARDS == {"wellfound", "simplyhired"}
        assert cfg.SEARCH_DEFAULT_BOARDS <= set(search_agent.KNOWN_JOB_BOARD_TEMPLATES)
    finally:
        importlib.reload(cfg)


def test_a_pasted_regional_board_gets_its_template():
    assert search_agent.parse_site_url("https://wuzzuf.net/search/jobs/?q=ai") == ("wuzzuf", None)
    assert search_agent.parse_site_url("https://www.bayt.com/en/egypt/jobs/") == ("bayt", None)
    assert search_agent.parse_site_url("https://egypt.tanqeeb.com/") == ("tanqeeb", None)
    assert search_agent.parse_site_url("https://www.gulftalent.com/") == ("gulftalent", None)
    assert search_agent.parse_site_url("https://careers.example.com/jobs") == ("generic", None)


# ---- keyless remote APIs, ATS boards, LinkedIn --------------------------------

def test_tanqeeb_build_url_filters_by_position():
    url = search_agent.KNOWN_JOB_BOARD_TEMPLATES["tanqeeb"]["build_url"]("machine learning engineer")
    assert url == "https://egypt.tanqeeb.com/jobs/search?keywords=machine+learning+engineer"
    assert search_agent.KNOWN_JOB_BOARD_TEMPLATES["tanqeeb"]["build_url"]("  ") is None


def test_parse_site_url_detects_ashby_and_smartrecruiters():
    assert search_agent.parse_site_url("https://jobs.ashbyhq.com/zapier") == ("ashby", "zapier")
    assert search_agent.parse_site_url("https://jobs.smartrecruiters.com/BoschGroup") == ("smartrecruiters", "BoschGroup")
    assert search_agent.parse_site_url("https://careers.smartrecruiters.com/Canva/") == ("smartrecruiters", "Canva")


def test_eligible_keeps_worldwide_unrestricted_and_listed_places(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ELIGIBLE_LOCATIONS", ["Egypt", "EMEA"])
    assert search_agent._eligible("")
    assert search_agent._eligible([])
    assert search_agent._eligible("Worldwide")
    assert search_agent._eligible(["Egypt", "Jordan"])
    assert search_agent._eligible("EMEA only")
    assert not search_agent._eligible("USA Only")
    assert not search_agent._eligible(["United States", "Canada"])


def test_eligible_keeps_everything_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ELIGIBLE_LOCATIONS", [])
    assert search_agent._eligible("USA Only")


def test_himalayas_normalizes_and_drops_ineligible(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ELIGIBLE_LOCATIONS", ["Egypt"])
    payload = {"jobs": [
        {"title": "ML Engineer", "companyName": "Acme", "applicationLink": "https://h/1",
         "description": "<p>Build <b>models</b></p>", "pubDate": "1789962228", "locationRestrictions": []},
        {"title": "ML Engineer", "companyName": "USCo", "applicationLink": "https://h/2",
         "description": "", "pubDate": "1789962228", "locationRestrictions": ["United States"]},
    ]}
    with patch("agents.search_agent.requests.get", return_value=Mock(json=lambda: payload, raise_for_status=lambda: None)):
        jobs = search_agent.search_himalayas(["ML Engineer"])
    assert [j["company"] for j in jobs] == ["Acme"]
    assert jobs[0]["description"] == "Build\nmodels"
    assert jobs[0]["location"] == "Remote (worldwide)"
    assert search_agent._extract_posted_at(jobs[0]) is not None


def test_workingnomads_strips_company_prefix_from_title(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ELIGIBLE_LOCATIONS", [])
    payload = [{"url": "https://w/1", "title": "Acme - Machine Learning Engineer", "company_name": "Acme",
                "description": "x", "location": "Remote", "pub_date": "2026-09-25T02:36:13-04:00"}]
    with patch("agents.search_agent.requests.get", return_value=Mock(json=lambda: payload, raise_for_status=lambda: None)):
        jobs = search_agent.search_workingnomads()
    assert jobs[0]["title"] == "Machine Learning Engineer"


LINKEDIN_CARD = """
<li><div class="base-card" data-entity-urn="urn:li:jobPosting:123">
  <h3 class="base-search-card__title"> Machine Learning Engineer </h3>
  <h4 class="base-search-card__subtitle"><a>Valeo</a></h4>
  <span class="job-search-card__location">Cairo, Egypt</span>
  <time datetime="2026-09-17">1 week ago</time>
</div></li>"""


def test_linkedin_parses_cards_and_marks_worldwide_as_remote(monkeypatch):
    monkeypatch.setattr(config, "LINKEDIN_REQUEST_DELAY", 0)
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return Mock(status_code=200, text=LINKEDIN_CARD, raise_for_status=lambda: None)

    with patch("agents.search_agent.requests.get", side_effect=fake_get):
        jobs = search_agent.search_linkedin(["ML Engineer"], ["Egypt", "Worldwide"], max_age_days=7)
    assert len(jobs) == 1  # same posting from both sweeps is kept once
    assert jobs[0]["url"] == "https://www.linkedin.com/jobs/view/123/"
    assert jobs[0]["company"] == "Valeo" and jobs[0]["date"] == "2026-09-17"
    assert "f_WT" not in calls[0] and calls[1]["f_WT"] == 2
    assert calls[0]["f_TPR"] == "r604800"


def test_linkedin_stops_quietly_when_rate_limited(monkeypatch):
    monkeypatch.setattr(config, "LINKEDIN_REQUEST_DELAY", 0)
    with patch("agents.search_agent.requests.get", return_value=Mock(status_code=429)):
        assert search_agent.search_linkedin(["ML Engineer"], ["Egypt"]) == []
