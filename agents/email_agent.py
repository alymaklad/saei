"""
Email Agent — sends from your own Gmail account (free, no per-email cost).

You connect it with "Sign in with Google": Google's account chooser and
consent screen open in the browser, then mail goes out through the Gmail API.
That uses Sa'ei's shared Google app (google_oauth_client.json), so a user
sets up nothing -- or a client file of their own, if they add one.

Applications, the daily report and the weekly news all go through _send().
"""
import base64
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import config

# What sending needs. A saved token is always loaded with just this, so a
# token from before the sign-in scopes below were added keeps refreshing.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
# Asked for at sign-in: sending, plus which account was picked -- so Settings
# can say "Sending as you@gmail.com" without asking for more access.
SIGN_IN_SCOPES = SCOPES + ["openid", "https://www.googleapis.com/auth/userinfo.email"]


def _read_client_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    section = data.get("installed") or data.get("web") or {}
    if not (section.get("client_id") and section.get("client_secret")):
        return None   # the shipped placeholder, before the app is created
    return {"installed" if "installed" in data else "web": section}


def oauth_client() -> tuple[dict | None, str | None]:
    """(client config, where it came from): your own file first, then Sa'ei's
    shared app, else (None, None): Google sign-in isn't available."""
    own = _read_client_file(config.GMAIL_CREDENTIALS_JSON)
    if own:
        return own, "own"
    bundled = _read_client_file(config.SAEI_OAUTH_CLIENT_JSON)
    if bundled:
        return bundled, "bundled"
    return None, None


def credentials_configured() -> bool:
    """Whether "Sign in with Google" can run on this install."""
    return oauth_client()[0] is not None


def _email_from_id_token(id_token: str | None) -> str:
    """The signed-in account's address, read from the ID token Google just
    returned over TLS to this process (so its signature isn't re-checked)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except (AttributeError, IndexError, ValueError):
        return ""


def oauth_account_email() -> str:
    """The Google account "Sign in with Google" used, saved next to its token."""
    try:
        with open(config.GMAIL_TOKEN_JSON, encoding="utf-8") as f:
            return json.load(f).get("account_email", "")
    except (OSError, ValueError):
        return ""


def is_authenticated() -> bool:
    """Whether sending can work right now, without sending anything or
    opening a browser. Used by GET /api/email/status and before applying."""
    return oauth_authenticated()


def sender_address() -> str:
    return oauth_account_email() or config.APPLICANT_EMAIL


def report_recipient() -> str:
    return config.REPORT_EMAIL_TO or sender_address()


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
    First run opens Google's "Sign in with Google" account chooser in a new
    browser tab (through Sa'ei's shared app, or your own client file -- see
    oauth_client()). The token is cached to GMAIL_TOKEN_JSON afterward, so
    there's no repeated login.

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
        account_email = oauth_account_email()   # a refresh keeps who it was
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not config.GMAIL_INTERACTIVE_AUTH:
            # A server has no browser to open, so the consent flow below would
            # just wait out its timeout (inside a scheduled run, with nobody
            # watching). Say what to do instead.
            raise RuntimeError(
                "Gmail isn't signed in on this server, and it can't open a browser "
                "to sign in. Click Sign in with Google on your own computer, then copy "
                f"{config.GMAIL_TOKEN_JSON} to the server."
            )
        else:
            client, _source = oauth_client()
            if client is None:
                raise RuntimeError(
                    "Sign in with Google isn't set up in this copy of Sa'ei. Add an "
                    "OAuth client file at "
                    f"{config.GMAIL_CREDENTIALS_JSON}.")
            flow = InstalledAppFlow.from_client_config(client, SIGN_IN_SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,  # explicit rather than relying on the library default
                timeout_seconds=GMAIL_AUTH_TIMEOUT_SECONDS,
                authorization_prompt_message="",  # nothing to print to a console the user isn't watching
                success_message=(
                    "Signed in to Sa'ei. You can close this tab and go back to "
                    "the dashboard."
                ),
            )
            account_email = _email_from_id_token(getattr(creds, "id_token", None))
        os.makedirs(os.path.dirname(config.GMAIL_TOKEN_JSON) or ".", exist_ok=True)
        token = json.loads(creds.to_json())
        if account_email:
            token["account_email"] = account_email
        with open(config.GMAIL_TOKEN_JSON, "w") as f:
            json.dump(token, f)
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
    """Sends a built message through the Gmail API."""
    service = get_gmail_service()
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"gmail_message_id": result.get("id")}
