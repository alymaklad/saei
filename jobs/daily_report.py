"""Evening report: what Sa'ei did in the last 24 hours, emailed to you as a
designed summary (agents/daily_email.py) with a plain-text fallback."""
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import config
from db import get_session, init_db
from models import Application, EmailLog, Job, PendingJob, ReportLog, SkillGap
from agents.daily_email import render_daily_email
from agents.reporter_agent import build_daily_report, send_report

# Applications whose next step is yours: a draft to review, or a tailored CV
# to look over before it goes out.
NEEDS_YOU = ("pending_review", "cv_rewritten_notify_user")
SENT = ("sent", "auto_submitted")

_BOARDS = {
    "linkedin": "LinkedIn", "indeed": "Indeed", "greenhouse": "Greenhouse",
    "lever": "Lever", "glassdoor": "Glassdoor", "wuzzuf": "Wuzzuf",
    "bayt": "Bayt", "google": "Google Jobs", "workable": "Workable",
    "ashbyhq": "Ashby", "smartrecruiters": "SmartRecruiters", "naukri": "Naukri",
}


def board_name(url: str | None, source_site: str | None = None) -> str:
    """Where a posting came from, as people call it: LinkedIn, not "generic"."""
    host = (urlparse(url or "").hostname or "").lower()
    for key, name in _BOARDS.items():
        if key in host:
            return name
    if source_site and source_site not in ("generic", "manual_link"):
        return _BOARDS.get(source_site, source_site.replace("_", " ").title())
    if host:
        parts = host.removeprefix("www.").split(".")
        return parts[-2].title() if len(parts) > 1 else parts[0].title()
    return "Other"


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive; they were written in UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def collect_daily_report(now: datetime | None = None) -> dict:
    """Everything the evening email shows, read from the database."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    day_before = since - timedelta(hours=24)
    week_start = (now - timedelta(days=6)).date()

    with get_session() as session:
        found_today = session.query(Job).filter(Job.date_found >= since).all()
        found_yesterday = session.query(Job).filter(
            Job.date_found >= day_before, Job.date_found < since).count()
        rows = (session.query(Application, Job).join(Job, Application.job_id == Job.id)
                .filter(Application.date_created >= since).all())
        scored_yesterday = session.query(Application).filter(
            Application.date_created >= day_before, Application.date_created < since).count()
        week_rows = (session.query(Application.date_created)
                     .filter(Application.date_created >= datetime.combine(
                         week_start, datetime.min.time(), tzinfo=timezone.utc)).all())
        gaps = session.query(SkillGap).filter(SkillGap.date_created >= since).all()
        pending = session.query(PendingJob).all()
        emails = (session.query(EmailLog).filter(EmailLog.sent_at >= since)
                  .order_by(EmailLog.sent_at.desc()).all())
        waiting_older = (session.query(Application)
                         .filter(Application.status.in_(NEEDS_YOU),
                                 Application.email_sent.isnot(True),
                                 Application.date_created < since,
                                 Application.date_created >= now - timedelta(days=7))
                         .count())

        roles = [{
            "id": a.id,
            "title": j.title or "Untitled role",
            "company": j.company or "",
            "board": board_name(j.url, j.source_site),
            "url": j.url,
            "match": a.match_score,
            "ats": a.ats_score,
            "tailored_ats": a.tailored_ats_score,
            "status": a.status,
            "email_sent": bool(a.email_sent),
            "apply_email": a.apply_email,
        } for a, j in rows]
        sources = Counter(board_name(j.url, j.source_site) for j in found_today)
        set_aside = Counter(p.reason or "limit" for p in pending)
        sent_emails = [{"to": e.to_email, "subject": e.subject, "status": e.status}
                       for e in emails if e.application_id is not None]

    def by_match(r):
        return r["match"] if r["match"] is not None else -1

    roles.sort(key=by_match, reverse=True)
    skills = Counter()
    for gap in gaps:
        try:
            skills.update(set(json.loads(gap.missing_skills or "[]")))
        except (TypeError, ValueError):
            pass

    per_day = Counter(_aware(d).date() for (d,) in week_rows if d)
    week = [{"day": week_start + timedelta(days=i),
             "count": per_day.get(week_start + timedelta(days=i), 0)} for i in range(7)]

    threshold = config.MATCH_SCORE_THRESHOLD
    return {
        "now": now,
        "found": len(found_today),
        "found_delta": len(found_today) - found_yesterday,
        "scored": len(roles),
        "scored_delta": len(roles) - scored_yesterday,
        "strong": sum(1 for r in roles if (r["match"] or 0) >= max(threshold, 0.6)),
        "tailored": sum(1 for r in roles if r["tailored_ats"] is not None),
        "sent": sum(1 for r in roles if r["status"] in SENT or r["email_sent"]),
        "dry_run": config.DRY_RUN,
        "needs_you": [r for r in roles if r["status"] in NEEDS_YOU and not r["email_sent"]],
        "waiting_older": waiting_older,
        "best": roles[:5],
        "skills": skills.most_common(5),
        "skill_roles": len(gaps),
        "sources": sources.most_common(),
        "set_aside": dict(set_aside),
        "emails": sent_emails,
        "week": week,
    }


def _reader_name() -> str:
    try:
        import profile_store
        return str(((profile_store.load_profile_dict() or {}).get("contact") or {}).get("full_name") or "")
    except Exception:  # noqa: BLE001 -- the greeting is optional
        return ""


def run_daily_report():
    init_db()
    data = collect_daily_report()
    report_text = build_daily_report(data)
    html = render_daily_email(data, reader=_reader_name())

    try:
        # Dated, so each day's report is its own conversation: Gmail files
        # mail with a repeated subject into the earlier thread, where a new
        # report is easy to miss.
        now = data["now"]
        subject = f"Sa'ei daily report · {now.strftime('%A')} {now.day} {now.strftime('%b')}"
        result = send_report(subject, report_text, html=html)
        if result.get("dry_run"):
            status = "dry_run"
        elif "error" in result:
            status = "failed"
        else:
            status = "sent"
        error_message = result.get("error")
    except Exception as exc:  # noqa: BLE001 -- log the failure rather than crash the scheduler
        result = {"error": str(exc)}
        status = "failed"
        error_message = str(exc)

    with get_session() as session:
        session.add(ReportLog(
            report_type="daily",
            content=report_text,
            dry_run=result.get("dry_run", config.DRY_RUN),
            status=status,
            error_message=error_message,
        ))

    return result


if __name__ == "__main__":
    print(run_daily_report())
