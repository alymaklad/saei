"""
Apply Agent — the single gate any job passes through before it can be
marked for real auto-submission.

decide_apply_path(job) is governed by config.AUTO_APPLY_MODE (Settings
page's "Auto-Apply Behavior" panel), one of three values:

  "off"       — never auto-submit. Every job drafts for review, full stop.
  "any"       — auto-submit regardless of source, ignoring
                WHITELISTABLE_SOURCES/WHITELISTED_SOURCES entirely. This is
                a deliberate, explicit opt-in escape hatch from the
                whitelist-only default below -- see the module-level note in
                config.py for what it does and doesn't currently enable
                (auto_submit_greenhouse is still a Greenhouse-only stub, so
                this mode changes *routing*, not what a non-Greenhouse job
                can actually have submitted for it).
  "whitelist" — (default) the original design: only auto-submit on sources
                you've explicitly whitelisted in .env (WHITELISTED_SOURCES).
                Everywhere else it prepares a complete draft and waits for
                your approval. See agents/search_agent.py:WHITELISTABLE_SOURCES
                for which source types can ever be whitelisted at all (only
                Greenhouse/Lever, since they're the only ones with a
                documented public API to submit against).

This is also the single gate a job re-entering here via orchestrator.py's
route_after_rewrite (a rewritten CV whose tailored score cleared the fit
threshold) passes through -- so AUTO_APPLY_MODE applies identically whether
a job arrived here directly or via a CV rewrite.

The email route: many postings (most in Egypt and the Gulf) say "send your CV
to hr@company.com". find_application_email() reads that address out of the
posting, and when config.AUTO_APPLY_EMAIL is on the gate returns
"email_apply" -- the tailored CV and a cover letter go out through the user's
Gmail (orchestrator.py's email_apply_node). AUTO_APPLY_MODE="off" still wins:
that mode promises nothing is sent without the user, so it drafts instead,
with the address ready in the draft.
"""
import re

import config
from agents.search_agent import WHITELISTABLE_SOURCES

# ---- "apply by email" detection ----------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Words that, near an address, say "this is where applications go".
_APPLY_CUES = re.compile(
    r"\b(apply|applying|application|applications|send|sending|submit|email|e-mail|mail|"
    r"cv|c\.v|resume|résumé|portfolio|interested|candidates?)\b|"
    r"السيرة الذاتية|سيرتك|ارسال|إرسال|أرسل|ارسل|للتقديم|التقديم|تقديم",
    re.I)
# Addresses that are never an application inbox.
_NOT_AN_INBOX = re.compile(
    r"^(no-?reply|do-?not-?reply|privacy|gdpr|dpo|legal|abuse|support|help|"
    r"press|media|security|unsubscribe|marketing|sales|billing|info@example)|"
    r"@(example|sentry|wixpress|domain)\.|\.(png|jpe?g|gif|svg|webp)$",
    re.I)
_CUE_WINDOW = 160  # characters either side of the address
# Text right before an address that makes it a help line, not an inbox for
# CVs: "if you need a reasonable accommodation ... contact X". Postings on
# Greenhouse and similar boards almost all carry one of these.
_HELP_CONTEXT = re.compile(
    r"accommodat|disabilit|accessib|alternative (method|format)|assistance|"
    r"equal (employment )?opportunit|eeo|privacy|data protection|personal data|"
    r"unable to use|special needs",
    re.I)
_HELP_WINDOW = 220   # characters before the address


def find_application_email(text: str | None) -> str | None:
    """The address a posting asks applications to be sent to, or None.

    An address only counts when apply-words ("send your CV", "apply",
    "السيرة الذاتية") sit within a short window around it -- a careers page
    also carries privacy@, support@ and the like, and emailing a CV there would
    be worse than not applying. With several candidates, the one with the most
    cue words nearby wins.
    """
    if not text:
        return None
    best, best_score = None, 0
    for match in _EMAIL_RE.finditer(text):
        address = match.group(0).strip(".").lower()
        if _NOT_AN_INBOX.search(address):
            continue
        if _HELP_CONTEXT.search(address) or _HELP_CONTEXT.search(text[max(0, match.start() - _HELP_WINDOW): match.start()]):
            continue  # an accommodations / privacy / help address
        window = text[max(0, match.start() - _CUE_WINDOW): match.end() + _CUE_WINDOW]
        score = len(_APPLY_CUES.findall(window))
        if re.match(r"(hr|jobs?|careers?|recruit\w*|talent|hiring|cv|resumes?|apply)[._@-]", address):
            score += 2  # an inbox named for hiring
        if score > best_score:
            best, best_score = address, score
    return best if best_score >= 2 else None


def _job_whitelist_key(job: dict) -> str:
    """Builds the 'source:identifier' key checked against WHITELISTED_SOURCES."""
    source = job.get("source", "")
    identifier = job.get("board") or job.get("company_slug") or job.get("watchlist_company") or ""
    return f"{source}:{identifier}" if identifier else source


def decide_apply_path(job: dict) -> str:
    """"auto_submit" | "email_apply" | "draft_for_review" (see module docstring)."""
    mode = config.AUTO_APPLY_MODE
    if mode == "off":
        return "draft_for_review"
    if config.AUTO_APPLY_EMAIL and job.get("apply_email"):
        return "email_apply"
    if mode == "any":
        return "auto_submit"
    # mode == "whitelist" (also the fallback config.py uses for any
    # unrecognized value, so this branch is the safe default either way).
    source = job.get("source", "")
    if source not in WHITELISTABLE_SOURCES:
        return "draft_for_review"
    key = _job_whitelist_key(job)
    if key in config.WHITELISTED_SOURCES or source in config.WHITELISTED_SOURCES:
        return "auto_submit"
    return "draft_for_review"


def auto_submit_greenhouse(job_url: str, cv_path: str, cover_letter: str, applicant_info: dict):
    """
    STUB — intentionally not implemented.

    Every Greenhouse/Lever job board can define different custom application
    fields (screening questions, EEO fields, etc). Before enabling true
    auto-submit for a board:
      1. Inspect that specific board's application form schema (browser dev
         tools, or the board's public API) to see its exact required fields.
      2. Hand-build the submit payload for that board.
      3. Test end-to-end on one real (or throwaway) application.
      4. Only then add "greenhouse:<board_token>" to WHITELISTED_SOURCES in .env.
    This is the one part of the system meant to be verified per employer,
    not automated blindly.
    """
    raise NotImplementedError(
        "auto_submit_greenhouse is a stub — verify this board's form schema "
        "and implement the payload before enabling auto-submit for it."
    )


def draft_for_review(job: dict, cv_path: str, cover_letter: str, note: str | None = None) -> dict:
    return {
        "job_url": job.get("url") or job.get("hostedUrl"),
        "cv_path": cv_path,
        "cover_letter": cover_letter,
        "apply_email": job.get("apply_email"),
        "status": "pending_review",
        "note": note,
    }
