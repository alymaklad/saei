"""
Email Agent — sends from your own Gmail account (free, no per-email cost).

You connect it with "Sign in with Google": Google's account chooser and
consent screen open in the browser, then mail goes out through the Gmail API.
That uses Sa'ei's shared Google app (google_oauth_client.json), so a user
sets up nothing -- or a client file of their own, if they add one.

Applications, the daily report and the weekly news all go through _send().
Reports can go out from a second Google account of their own (Settings ->
Email & reports -> "Send reports from"): its sign-in is kept in
GMAIL_REPORT_TOKEN_JSON, and applications always use GMAIL_TOKEN_JSON.
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


def oauth_account_email(token_path: str | None = None) -> str:
    """The Google account "Sign in with Google" used, saved next to its token."""
    try:
        with open(token_path or config.GMAIL_TOKEN_JSON, encoding="utf-8") as f:
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


def report_sender_connected() -> bool:
    """Whether reports have a Google account of their own to send from."""
    return oauth_authenticated(config.GMAIL_REPORT_TOKEN_JSON)


def report_sender_address() -> str:
    """The address reports are sent from."""
    if report_sender_connected():
        return oauth_account_email(config.GMAIL_REPORT_TOKEN_JSON)
    return sender_address()


def disconnect_report_sender() -> None:
    """Back to sending reports from your own Gmail."""
    try:
        os.remove(config.GMAIL_REPORT_TOKEN_JSON)
    except FileNotFoundError:
        pass


def oauth_authenticated(token_path: str | None = None) -> bool:
    """
    Checks whether we already have a usable cached OAuth token, without
    triggering the interactive flow -- so the Settings page can show
    "Connected" without popping open a browser window just to check.
    """
    token_path = token_path or config.GMAIL_TOKEN_JSON
    if not os.path.exists(token_path):
        return False
    from google.oauth2.credentials import Credentials
    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    except (ValueError, OSError):
        return False
    return bool(creds and (creds.valid or creds.refresh_token))


GMAIL_AUTH_TIMEOUT_SECONDS = 180  # give up if the user never completes the browser flow


def get_gmail_service(token_path: str | None = None):
    """`token_path` picks which sign-in: your Gmail (the default) or the
    reports' own account (config.GMAIL_REPORT_TOKEN_JSON).


    First run opens Google's "Sign in with Google" account chooser in a new
    browser tab (through Sa'ei's shared app, or your own client file -- see
    oauth_client()). The token is cached to GMAIL_TOKEN_JSON afterward, so
    there's no repeated login.

    This blocks the calling thread until the user finishes (or abandons) the
    browser flow -- api.py's /api/email/authenticate runs it in FastAPI's
    threadpool for exactly that reason. GMAIL_AUTH_TIMEOUT_SECONDS bounds how
    long it'll wait so an abandoned tab can't hang the request forever.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = token_path or config.GMAIL_TOKEN_JSON
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        account_email = oauth_account_email(token_path)   # a refresh keeps who it was
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # Google no longer accepts this sign-in (revoked, or the
                # 7-day limit for apps still in "Testing"). Set the token
                # aside, so Settings shows Gmail as not connected rather than
                # "Connected" over a sign-in that can never send again.
                expired = f"{token_path}.expired"
                os.replace(token_path, expired)
                raise RuntimeError(
                    "Your Google sign-in has expired. Open Settings → Email & reports "
                    "and click Sign in with Google again."
                ) from exc
        elif not config.GMAIL_INTERACTIVE_AUTH:
            # A server has no browser to open, so the consent flow below would
            # just wait out its timeout (inside a scheduled run, with nobody
            # watching). Say what to do instead.
            raise RuntimeError(
                "Gmail isn't signed in on this server, and it can't open a browser "
                "to sign in. Click Sign in with Google on your own computer, then copy "
                f"{token_path} to the server."
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
        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        token = json.loads(creds.to_json())
        if account_email:
            token["account_email"] = account_email
        with open(token_path, "w") as f:
            json.dump(token, f)
    return build("gmail", "v1", credentials=creds)


def cv_attachment_name(full_name: str, job_title: str, cv_path: str) -> str:
    """What the attached CV is called: Firstname_Lastname_JobTitle.pdf.

    The title keeps its core only -- "[EPM] AI Engineer (Joining Bonus)" and
    "Senior Engineer – Enterprise AI" become AI_Engineer and Senior_Engineer
    -- because a recruiter's downloads folder shows the start of the name.
    Falls back to the file's own name when there's no name to use.
    """
    import re
    ext = os.path.splitext(cv_path)[1] or ".pdf"
    names = [w for w in re.findall(r"[^\W\d_]+", full_name or "")]
    person = "_".join([names[0], names[-1]] if len(names) > 1 else names)
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", job_title or "")
    title = re.split(r"\s[-–—|:]\s|\s[–—]|[–—]\s|,", title)[0]
    words = re.findall(r"[^\W_]+(?:[+#.][^\W_]*)*", title)[:6]
    parts = [p for p in (person, "_".join(words)) if p]
    if not parts:
        return os.path.basename(cv_path) or f"CV{ext}"
    return "_".join(parts) + ext


def applicant_reply_address() -> str:
    """Where a recruiter's reply should go: the email on your profile (or
    APPLICANT_EMAIL). Differs from sender_address() when applications are
    sent from a separate account, such as a project address."""
    try:
        import profile_store
        contact = (profile_store.load_profile_dict() or {}).get("contact") or {}
        address = str(contact.get("email") or "").strip()
    except Exception:  # noqa: BLE001 -- no profile yet: fall back to the setting
        address = ""
    return address or config.APPLICANT_EMAIL


def send_application_email(to_email: str, subject: str, body: str, cv_path: str,
                           *, applicant_name: str = "", job_title: str = "") -> dict:
    """Respects the global DRY_RUN flag — logs intent instead of sending when true.
    The CV goes out named Firstname_Lastname_JobTitle (cv_attachment_name).
    When the sending account isn't your own address, Reply-To carries yours,
    so the recruiter's answer reaches you rather than the sending account."""
    attachment_name = cv_attachment_name(applicant_name, job_title, cv_path)
    reply_to = applicant_reply_address()
    if reply_to.casefold() == sender_address().casefold():
        reply_to = ""
    if config.DRY_RUN:
        return {
            "dry_run": True,
            "action": "send_application_email",
            "to": to_email,
            "subject": subject,
            "cv_path": cv_path,
            "attachment_name": attachment_name,
            "reply_to": reply_to or None,
        }

    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.attach(MIMEText(body))

    with open(cv_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=attachment_name)
    part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
    message.attach(part)
    return {"dry_run": False, **_send(message)}


def send_report_email(subject: str, body: str, html: str | None = None) -> dict:
    """The daily report or weekly news, to your own inbox (or REPORT_EMAIL_TO),
    sent from the reports' own account when one is signed in, else from your
    Gmail. `html` is the designed version; `body` stays as the plain-text part
    for mail apps that don't show HTML. Respects DRY_RUN like every other send."""
    to_email = report_recipient()
    token_path = config.GMAIL_REPORT_TOKEN_JSON if report_sender_connected() else None
    if config.DRY_RUN:
        return {"dry_run": True, "action": "send_report_email", "to": to_email, "subject": subject}
    if not oauth_authenticated(token_path) or not to_email:
        return {"error": "Gmail isn't connected, so the report wasn't emailed. "
                         "Connect it on the Settings page."}
    message = MIMEMultipart("alternative") if html else MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    message.attach(MIMEText(body, "plain", "utf-8"))
    if html:
        message.attach(MIMEText(html, "html", "utf-8"))   # last part = preferred
    return {"dry_run": False, "to": to_email, "from": report_sender_address(),
            **_send(message, token_path)}


def _send(message: MIMEMultipart, token_path: str | None = None) -> dict:
    """Sends a built message through the Gmail API, as the account signed in
    at `token_path` (your Gmail by default)."""
    service = get_gmail_service(token_path)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"gmail_message_id": result.get("id")}
