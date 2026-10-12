"""
What changes when the app runs on a server instead of your own machine:
the login gate, the dashboard served by the API itself, a search that runs
in the background (a proxy would cut a minutes-long request off), and Gmail
refusing to wait on a browser that a server doesn't have.
"""
import base64
import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'api.db'}")
    import db
    importlib.reload(db)
    db.Base.metadata.create_all(db.engine)
    import api
    importlib.reload(api)

    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(api.asyncio, "sleep", no_wait)
    return TestClient(api.app), api, config


def _basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---- login gate ---------------------------------------------------------------

def test_no_password_means_no_login(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert tc.get("/api/status").status_code == 200


def test_a_password_locks_the_api_the_dashboard_and_the_files(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "correct horse")
    for path in ("/api/status", "/api/profile", "/", "/index.html", "/files/cv/current_cv.pdf"):
        response = tc.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"].startswith("Basic")


def test_the_right_credentials_get_in_and_wrong_ones_dont(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_USERNAME", "saei")
    monkeypatch.setattr(config, "APP_PASSWORD", "correct horse")
    assert tc.get("/api/status", headers=_basic("saei", "correct horse")).status_code == 200
    assert tc.get("/api/status", headers=_basic("saei", "wrong")).status_code == 401
    assert tc.get("/api/status", headers=_basic("admin", "correct horse")).status_code == 401
    assert tc.get("/api/status", headers={"Authorization": "Basic !!notbase64"}).status_code == 401


def test_health_stays_open_for_uptime_checks(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "correct horse")
    assert tc.get("/api/health").status_code == 200


# ---- dashboard served by the API ---------------------------------------------------

def test_the_dashboard_is_served_at_the_root(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    response = tc.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()
    assert tc.get("/config.js").status_code == 200
    # API routes registered before the mount still win over it
    assert tc.get("/api/health").json() == {"status": "ok"}


# ---- background search -----------------------------------------------------------------

def _wait_until_done(tc, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = tc.get("/api/search/status").json()
        if not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("search never finished")


def test_search_returns_at_once_and_reports_through_status(hosted, monkeypatch):
    tc, api, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    import threading
    release = threading.Event()

    def fake_run():
        release.wait(5)
        return {"found": 3, "processed": []}
    monkeypatch.setattr(api, "run_daily_search_and_apply", fake_run)

    started = tc.post("/api/search/run")
    assert started.status_code == 200 and started.json()["running"] is True
    assert tc.post("/api/search/run").status_code == 409   # one at a time
    release.set()
    status = _wait_until_done(tc)
    assert status["result"] == {"found": 3, "processed": []}
    assert status["error"] is None and status["finished_at"]


def test_a_setup_problem_comes_back_as_a_plain_error(hosted, monkeypatch):
    tc, api, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "")

    def no_cv():
        raise RuntimeError("Upload a CV first.")
    monkeypatch.setattr(api, "run_daily_search_and_apply", no_cv)
    tc.post("/api/search/run")
    status = _wait_until_done(tc)
    assert status["error"] == "Upload a CV first." and status["result"] is None


# ---- Gmail on a headless server ---------------------------------------------------------------

def test_gmail_on_a_server_says_what_to_do_instead_of_opening_a_browser(tmp_path, monkeypatch):
    import config
    from agents import email_agent
    monkeypatch.setattr(config, "GMAIL_INTERACTIVE_AUTH", False)
    monkeypatch.setattr(config, "GMAIL_TOKEN_JSON", str(tmp_path / "missing_token.json"))
    with pytest.raises(RuntimeError, match="copy"):
        email_agent.get_gmail_service()


# ---- schedule ----------------------------------------------------------------------------

def test_schedule_uses_the_configured_timezone(monkeypatch):
    import config
    import scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(config, "SCHEDULER_TIMEZONE", "Africa/Cairo")
    sched = scheduler.build_scheduler(BackgroundScheduler)
    assert str(sched.timezone) == "Africa/Cairo"
    assert {j.id for j in sched.get_jobs()} == {
        "daily_search_and_apply", "daily_report", "weekly_news"}


def test_dashboard_files_are_revalidated_after_updates(hosted, monkeypatch):
    tc, _, config = hosted
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert tc.get("/ui.js").headers["cache-control"] == "no-cache"
    assert "cache-control" not in tc.get("/api/health").headers
