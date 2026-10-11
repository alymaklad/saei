"""
Email Agent — Gmail API (OAuth, free, uses your own Gmail account, no per-email cost).
Replaces the paid SendGrid option from the original design.
"""
import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import config

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def credentials_configured() -> bool:
    """Whether a Gmail OAuth client file has been downloaded and placed at GMAIL_CREDENTIALS_JSON."""
    return os.path.exists(config.GMAIL_CREDENTIALS_JSON)


def is_authenticated() -> bool:
    """
    Checks whether we already have a usable cached token, without triggering
    the interactive OAuth flow. Used by GET /api/email/status so the frontend
    can show "Connected" without popping open a browser window just to check.
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

    service = get_gmail_service()
    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    message.attach(MIMEText(body))

    attachment_name = os.path.basename(cv_path) or "CV"
    with open(cv_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=attachment_name)
    part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
    message.attach(part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"dry_run": False, "gmail_message_id": result.get("id")}
