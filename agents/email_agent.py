"""
Email Agent — sends from your own Gmail account (free, no per-email cost).

Two ways to connect, chosen on the Settings page (config.EMAIL_METHOD):

  app_password  Your Gmail address and a Google App Password, over Gmail's
                SMTP server. The simple route: no Google Cloud project.
                Google refuses an account's normal password from apps, so
                it has to be an App Password (needs 2-Step Verification).
  oauth         The Gmail API, through an OAuth client file from Google
                Cloud Console and a one-time consent screen in the browser.

Everything else -- applications, the daily report, the weekly news -- goes
through _send() and doesn't care which one is in use.
"""
import base64
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import config

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def credentials_configured() -> bool:
    """Whether a Gmail OAuth client file has been downloaded and placed at GMAIL_CREDENTIALS_JSON."""
    return os.path.exists(config.GMAIL_CREDENTIALS_JSON)


METHODS = ("app_password", "oauth")


def method() -> str:
    """The connection in use: the one chosen in Settings, else App Password
    once one is saved, else OAuth."""
    if config.EMAIL_METHOD in METHODS:
        return config.EMAIL_METHOD
    return "app_password" if config.GMAIL_APP_PASSWORD else "oauth"


def is_authenticated() -> bool:
    """Whether sending can work right now, without sending anything or
    opening a browser. Used by GET /api/email/status and before applying."""
    if method() == "app_password":
        return bool(config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD)
    return oauth_authenticated()


def sender_address() -> str:
    if method() == "app_password":
        return config.GMAIL_ADDRESS
    return config.APPLICANT_EMAIL


def report_recipient() -> str:
    return config.REPORT_EMAIL_TO or sender_address()


class AppPasswordRejected(ValueError):
    """Gmail refused the address/App Password pair."""


def verify_app_password(address: str, app_password: str) -> None:
    """Signs in to Gmail's SMTP server and out again, sending nothing.
    Raises AppPasswordRejected with a message the Settings page can show."""
    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
            smtp.login(address, app_password.replace(" ", ""))
    except smtplib.SMTPAuthenticationError as exc:
        raise AppPasswordRejected(
            "Gmail didn't accept that. Use an App Password (16 letters, from "
            "myaccount.google.com/apppasswords), not your normal Gmail password."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise AppPasswordRejected(f"Couldn't reach Gmail to check it: {exc}") from exc


def oauth_authenticated() -> bool:
    """
    Checks whether we already have a usable cached OAuth token, without
    triggering the interactive flow -- so the Settings page can show
    "Connected" without popping open a browser window just to check.
    """
    if not os.path.exists(config.GMAIL_TOKEN_JSON):
        return False
    from google.oauth2.credentials import Credentials
    try:
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_JSON, SCOPES)
    except (ValueError, OSError):
        return False
    return bool(creds and (creds.valid or creds.refresh_token))


GMAIL_AUTH_TIMEOUT_SECONDS = 180  # give up if the user never completes the browser flow


def get_gmail_service():
    """
    First run opens a new browser tab for OAuth consent (needs GMAIL_CREDENTIALS_JSON,
    downloaded free from Google Cloud Console -> OAuth client, Desktop app type).
    Token is cached to GMAIL_TOKEN_JSON afterward — no repeated login.

    This blocks the calling thread until the user finishes (or abandons) the
    browser flow -- api.py's /api/email/authenticate runs it in FastAPI's
    threadpool for exactly that reason. GMAIL_AUTH_TIMEOUT_SECONDS bounds how
    long it'll wait so an abandoned tab can't hang the request forever.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(config.GMAIL_TOKEN_JSON):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_JSON, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not config.GMAIL_INTERACTIVE_AUTH:
            # A server has no browser to open, so the consent flow below would
            # just wait out its timeout (inside a scheduled run, with nobody
            # watching). Say what to do instead.
            raise RuntimeError(
                "Gmail isn't signed in on this server, and it can't open a browser "
                "to sign in. Click Connect Gmail on your own computer, then copy "
                f"{config.GMAIL_TOKEN_JSON} to the server."
            )
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config.GMAIL_CREDENTIALS_JSON, SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,  # explicit rather than relying on the library default
                timeout_seconds=GMAIL_AUTH_TIMEOUT_SECONDS,
                authorization_prompt_message="",  # nothing to print to a console the user isn't watching
                success_message=(
                    "Signed in. You can close this tab and return to the "
                    "Job Application Agent dashboard."
                ),
            )
        os.makedirs(os.path.dirname(config.GMAIL_TOKEN_JSON) or ".", exist_ok=True)
        with open(config.GMAIL_TOKEN_JSON, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def send_application_email(to_email: str, subject: str, body: str, cv_path: str) -> dict:
    """Respects the global DRY_RUN flag — logs intent instead of sending when true."""
    if config.DRY_RUN:
        return {
            "dry_run": True,
            "action": "send_application_email",
            "to": to_email,
            "subject": subject,
            "cv_path": cv_path,
        }

    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    message.attach(MIMEText(body))

    attachment_name = os.path.basename(cv_path) or "CV"
    with open(cv_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=attachment_name)
    part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
    message.attach(part)
    return {"dry_run": False, **_send(message)}


def send_report_email(subject: str, body: str) -> dict:
    """The daily report or weekly news, to your own inbox (or REPORT_EMAIL_TO).
    Respects DRY_RUN like every other send."""
    to_email = report_recipient()
    if config.DRY_RUN:
        return {"dry_run": True, "action": "send_report_email", "to": to_email, "subject": subject}
    if not is_authenticated() or not to_email:
        return {"error": "Gmail isn't connected, so the report wasn't emailed. "
                         "Connect it on the Settings page."}
    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    message.attach(MIMEText(body))
    return {"dry_run": False, "to": to_email, **_send(message)}


def _send(message: MIMEMultipart) -> dict:
    """Sends a built message through whichever connection is in use."""
    if method() == "app_password":
        if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
            raise RuntimeError("Gmail isn't connected. Add your address and App Password in Settings.")
        if "from" not in message:
            message["from"] = formataddr((config.APPLICANT_NAME or "", config.GMAIL_ADDRESS))
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(message)
        return {"via": "app_password"}

    service = get_gmail_service()
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"via": "oauth", "gmail_message_id": result.get("id")}
