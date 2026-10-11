"""
Gmail, connected either with an App Password (SMTP) or through OAuth, and
the daily report / weekly news emailed through the same connection. Gmail's
SMTP server is faked: nothing here sends anything.
"""
import importlib
import smtplib

import pytest
from fastapi.testclient import TestClient


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.sent, self.login_args = host, port, [], None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if password != "abcdefghijklmnop":
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def gmail(monkeypatch, tmp_path):
    import config
    from agents import email_agent
    FakeSMTP.instances = []
    monkeypatch.setattr(email_agent.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(config, "EMAIL_METHOD", "")
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(config, "REPORT_EMAIL_TO", "")
    monkeypatch.setattr(config, "GMAIL_TOKEN_JSON", str(tmp_path / "no_token.json"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    return config, email_agent


def test_method_defaults_to_oauth_until_an_app_password_is_saved(gmail):
    config, email_agent = gmail
    assert email_agent.method() == "oauth" and not email_agent.is_authenticated()
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "me@gmail.com", "abcdefghijklmnop"
    assert email_agent.method() == "app_password" and email_agent.is_authenticated()
    config.EMAIL_METHOD = "oauth"   # an explicit choice wins
    assert email_agent.method() == "oauth"


def test_a_normal_password_is_rejected_with_a_useful_message(gmail):
    _, email_agent = gmail
    with pytest.raises(email_agent.AppPasswordRejected, match="App Password"):
        email_agent.verify_app_password("me@gmail.com", "my-normal-password")
    email_agent.verify_app_password("me@gmail.com", "abcd efgh ijkl mnop")   # spaces as Google shows them


def test_reports_go_to_your_own_inbox_over_smtp(gmail):
    config, email_agent = gmail
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "me@gmail.com", "abcdefghijklmnop"
    result = email_agent.send_report_email("Sa'ei daily report", "Today's applications:\n- A")
    assert result["to"] == "me@gmail.com" and result["via"] == "app_password"
    message = FakeSMTP.instances[-1].sent[0]
    assert message["to"] == "me@gmail.com" and message["subject"] == "Sa'ei daily report"
    assert "me@gmail.com" in message["from"]


def test_reports_respect_dry_run_and_a_missing_connection(gmail):
    config, email_agent = gmail
    assert "error" in email_agent.send_report_email("s", "b")   # nothing connected
    config.DRY_RUN = True
    assert email_agent.send_report_email("s", "b")["dry_run"] is True
    assert FakeSMTP.instances == []


def test_application_email_goes_over_smtp_with_the_cv(gmail, tmp_path):
    config, email_agent = gmail
    config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD = "me@gmail.com", "abcdefghijklmnop"
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    result = email_agent.send_application_email("jobs@acme.com", "Application", "Hello", str(cv))
    assert result["dry_run"] is False and result["via"] == "app_password"
    message = FakeSMTP.instances[-1].sent[0]
    assert message["to"] == "jobs@acme.com"
    assert any(part.get_filename() == "cv.pdf" for part in message.walk())


# ---- Settings endpoints -------------------------------------------------------------------

@pytest.fixture
def client(gmail, tmp_path, monkeypatch):
    config, _ = gmail
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'api.db'}")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    import db
    importlib.reload(db)
    db.Base.metadata.create_all(db.engine)
    import api
    importlib.reload(api)
    written = {}
    # Never touch the real .env, and keep this test's config instead of reloading it.
    def fake_apply(updates):
        written.update(updates)
        for key, value in updates.items():
            attr = {"GMAIL_APP_PASSWORD": "GMAIL_APP_PASSWORD"}.get(key, key)
            setattr(config, attr, value or "")
    monkeypatch.setattr(api, "_apply_env", fake_apply)
    return TestClient(api.app), written


def test_signing_in_with_an_app_password_saves_it_only_once_gmail_accepts_it(client):
    tc, written = client
    bad = tc.post("/api/email/app-password", json={"address": "me@gmail.com", "app_password": "nope"})
    assert bad.status_code == 400 and "App Password" in bad.json()["detail"] and not written

    ok = tc.post("/api/email/app-password",
                 json={"address": "me@gmail.com", "app_password": "abcd efgh ijkl mnop"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["authenticated"] and body["method"] == "app_password"
    assert body["address"] == "me@gmail.com" and body["report_to"] == "me@gmail.com"
    assert written["GMAIL_APP_PASSWORD"] == "abcdefghijklmnop"
    assert "abcdefghijklmnop" not in ok.text   # never echoed back


def test_report_recipient_can_be_changed_and_reset(client):
    tc, _ = client
    tc.post("/api/email/app-password", json={"address": "me@gmail.com", "app_password": "abcdefghijklmnop"})
    assert tc.post("/api/email/report-to", json={"address": "other@x.com"}).json()["report_to"] == "other@x.com"
    assert tc.post("/api/email/report-to", json={"address": ""}).json()["report_to"] == "me@gmail.com"
    assert tc.post("/api/email/report-to", json={"address": "not-an-email"}).status_code == 400
