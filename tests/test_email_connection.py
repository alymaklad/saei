"""
Gmail through "Sign in with Google" (Sa'ei's shared app, or your own client
file), and the daily report / weekly news emailed through it. The Gmail API
is faked: nothing here sends anything.
"""
import base64
import importlib
import json
from email import message_from_bytes

import pytest
from fastapi.testclient import TestClient


class FakeGmail:
    """Stands in for googleapiclient's Gmail service: records what's sent."""
    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        self.sent.append(body)
        return self

    def execute(self):
        return {"id": f"msg-{len(self.sent)}"}


@pytest.fixture
def gmail(monkeypatch, tmp_path):
    import config
    from agents import email_agent
    fake = FakeGmail()
    fake.accounts = []   # which sign-in each send used (None = your Gmail)
    monkeypatch.setattr(email_agent, "get_gmail_service",
                        lambda token_path=None: fake.accounts.append(token_path) or fake)
    monkeypatch.setattr(config, "GMAIL_REPORT_TOKEN_JSON", str(tmp_path / "no_report_token.json"))
    monkeypatch.setattr(config, "REPORT_EMAIL_TO", "")
    monkeypatch.setattr(config, "APPLICANT_EMAIL", "")
    monkeypatch.setattr(config, "GMAIL_TOKEN_JSON", str(tmp_path / "no_token.json"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    return config, email_agent, fake


def _signed_in(config, tmp_path, email="me@gmail.com"):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c",
                                 "client_secret": "s", "account_email": email}))
    config.GMAIL_TOKEN_JSON = str(token)


def _decoded(body):
    return message_from_bytes(base64.urlsafe_b64decode(body["raw"]))


def test_reports_go_to_your_own_inbox(gmail, tmp_path):
    config, email_agent, fake = gmail
    _signed_in(config, tmp_path)
    result = email_agent.send_report_email("Sa'ei daily report", "Today's applications:\n- A")
    assert result["to"] == "me@gmail.com" and result["gmail_message_id"] == "msg-1"
    message = _decoded(fake.sent[0])
    assert message["to"] == "me@gmail.com" and message["subject"] == "Sa'ei daily report"


def test_reports_can_come_from_an_account_of_their_own(gmail, tmp_path):
    config, email_agent, fake = gmail
    _signed_in(config, tmp_path)
    report_token = tmp_path / "report_token.json"
    report_token.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c",
                                        "client_secret": "s", "account_email": "saei.opensource@gmail.com"}))
    config.GMAIL_REPORT_TOKEN_JSON = str(report_token)

    result = email_agent.send_report_email("Sa'ei daily report", "body")
    assert result["from"] == "saei.opensource@gmail.com"
    assert result["to"] == "me@gmail.com"                 # still to your own inbox
    assert fake.accounts == [str(report_token)]

    email_agent.send_application_email("hr@acme.com", "Application", "Hello", __file__)
    assert fake.accounts[-1] is None                      # applications: your Gmail

    email_agent.disconnect_report_sender()
    assert not report_token.exists()
    assert email_agent.report_sender_address() == "me@gmail.com"


def test_reports_respect_dry_run_and_a_missing_sign_in(gmail):
    config, email_agent, fake = gmail
    assert "error" in email_agent.send_report_email("s", "b")   # nobody signed in
    config.DRY_RUN = True
    assert email_agent.send_report_email("s", "b")["dry_run"] is True
    assert fake.sent == []


def test_application_email_carries_the_cv(gmail, tmp_path):
    config, email_agent, fake = gmail
    _signed_in(config, tmp_path)
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    result = email_agent.send_application_email("jobs@acme.com", "Application", "Hello", str(cv))
    assert result["dry_run"] is False
    message = _decoded(fake.sent[0])
    assert message["to"] == "jobs@acme.com"
    assert any(part.get_filename() == "cv.pdf" for part in message.walk())


# ---- Settings endpoints -------------------------------------------------------------------

@pytest.fixture
def client(gmail, tmp_path, monkeypatch):
    config, _, _ = gmail
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'api.db'}")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    import db
    importlib.reload(db)
    db.Base.metadata.create_all(db.engine)
    import api
    importlib.reload(api)

    # Never touch the real .env, and keep this test's config instead of reloading it.
    def fake_apply(updates):
        for key, value in updates.items():
            setattr(config, key, value or "")
    monkeypatch.setattr(api, "_apply_env", fake_apply)
    _signed_in(config, tmp_path)
    return TestClient(api.app)


def test_status_shows_who_is_sending_and_there_is_no_app_password_route(client):
    body = client.get("/api/email/status").json()
    assert body["authenticated"] and body["address"] == "me@gmail.com"
    assert "method" not in body and "app_password_saved" not in body
    assert client.post("/api/email/app-password", json={}).status_code in (404, 405)


def test_report_recipient_can_be_changed_and_reset(client):
    assert client.post("/api/email/report-to", json={"address": "other@x.com"}).json()["report_to"] == "other@x.com"
    assert client.post("/api/email/report-to", json={"address": ""}).json()["report_to"] == "me@gmail.com"
    assert client.post("/api/email/report-to", json={"address": "not-an-email"}).status_code == 400


# ---- Sign in with Google: the shared app ---------------------------------------------

def _client_file(path, client_id="id-123.apps.googleusercontent.com", secret="s3cret"):
    path.write_text(json.dumps({"installed": {
        "client_id": client_id, "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}}))
    return str(path)


def test_the_shared_app_is_used_unless_you_bring_your_own(gmail, tmp_path, monkeypatch):
    config, email_agent, _ = gmail
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_JSON", str(tmp_path / "missing.json"))
    monkeypatch.setattr(config, "SAEI_OAUTH_CLIENT_JSON", _client_file(tmp_path / "shared.json", "shared-id"))
    client, source = email_agent.oauth_client()
    assert source == "bundled" and client["installed"]["client_id"] == "shared-id"

    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_JSON", _client_file(tmp_path / "own.json", "own-id"))
    client, source = email_agent.oauth_client()
    assert source == "own" and client["installed"]["client_id"] == "own-id"


def test_an_empty_client_file_means_google_sign_in_is_off(gmail, tmp_path, monkeypatch):
    config, email_agent, _ = gmail
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_JSON", str(tmp_path / "missing.json"))
    monkeypatch.setattr(config, "SAEI_OAUTH_CLIENT_JSON", _client_file(tmp_path / "blank.json", "", ""))
    assert email_agent.oauth_client() == (None, None) and not email_agent.credentials_configured()


def test_the_shipped_shared_app_is_filled_in(gmail, monkeypatch):
    import pathlib
    config, email_agent, _ = gmail
    shipped = pathlib.Path(__file__).resolve().parent.parent / "google_oauth_client.json"
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_JSON", "missing.json")
    monkeypatch.setattr(config, "SAEI_OAUTH_CLIENT_JSON", str(shipped))
    client, source = email_agent.oauth_client()
    assert source == "bundled" and client["installed"]["client_id"].endswith(".apps.googleusercontent.com")


def test_the_signed_in_account_is_read_from_googles_id_token(gmail):
    _, email_agent, _ = gmail
    payload = base64.urlsafe_b64encode(json.dumps({"email": "me@gmail.com"}).encode()).decode().rstrip("=")
    assert email_agent._email_from_id_token(f"header.{payload}.signature") == "me@gmail.com"
    assert email_agent._email_from_id_token(None) == ""
    assert email_agent._email_from_id_token("garbage") == ""


def test_sender_address_comes_from_the_saved_token(gmail, tmp_path):
    config, email_agent, _ = gmail
    _signed_in(config, tmp_path)
    assert email_agent.sender_address() == "me@gmail.com"


def test_an_expired_google_sign_in_says_so_and_stops_showing_connected(tmp_path, monkeypatch):
    import json
    import config
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from agents import email_agent
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                                 "expiry": "2020-01-01T00:00:00Z", "account_email": "me@gmail.com"}))
    monkeypatch.setattr(config, "GMAIL_TOKEN_JSON", str(token))

    def refuse(self, request):
        raise RefreshError("invalid_grant: Bad Request")
    monkeypatch.setattr(Credentials, "refresh", refuse)
    with pytest.raises(RuntimeError, match="sign-in has expired"):
        email_agent.get_gmail_service()
    assert not token.exists() and (tmp_path / "token.json.expired").exists()
    assert not email_agent.oauth_authenticated()


@pytest.mark.parametrize("title,expected", [
    ("AI Engineer (Python, Machine Learning, Generative AI/LLMs)", "Aly_Maklad_AI_Engineer.pdf"),
    ("[EPM] AI Engineer (Joining Bonus)", "Aly_Maklad_AI_Engineer.pdf"),
    ("Senior Forward Deployed Engineer – Enterprise AI & Agentic Solutions", "Aly_Maklad_Senior_Forward_Deployed_Engineer.pdf"),
    ("Machine Learning Engineer, Payments", "Aly_Maklad_Machine_Learning_Engineer.pdf"),
])
def test_the_cv_is_named_firstname_lastname_jobtitle(title, expected):
    from agents.email_agent import cv_attachment_name
    assert cv_attachment_name("Aly Tarek Maklad", title, "cv_output/tailored_123.pdf") == expected


def test_the_sent_cv_carries_that_name(gmail, tmp_path):
    config, email_agent, fake = gmail
    _signed_in(config, tmp_path)
    cv = tmp_path / "tailored_123.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    email_agent.send_application_email("jobs@acme.com", "Application", "Hello", str(cv),
                                       applicant_name="Aly Maklad", job_title="AI Engineer")
    names = [p.get_filename() for p in _decoded(fake.sent[0]).walk() if p.get_filename()]
    assert names == ["Aly_Maklad_AI_Engineer.pdf"]
