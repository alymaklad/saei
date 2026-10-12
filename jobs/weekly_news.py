"""Weekly news digest for the user's field: stored, and emailed to you as
"The Sa'ei Weekly", a newspaper-style front page (agents/news_email.py)."""
import config
from db import get_session, init_db
from models import NewsDigest, ReportLog
from agents.news_agent import fetch_news, make_digest, news_topic
from agents.news_email import render_digest_email
from agents.reporter_agent import send_report


def _reader_name() -> str:
    try:
        import profile_store
        return str(((profile_store.load_profile_dict() or {}).get("contact") or {}).get("full_name") or "")
    except Exception:  # noqa: BLE001 -- the greeting is optional
        return ""


def run_weekly_news_digest(field: str | None = None):
    """`field` overrides the topic for one run; normally it comes from
    news_topic() -- your NEWS_TOPIC setting, else your profile title."""
    init_db()
    field = field or news_topic()
    articles = fetch_news(field)
    digest = make_digest(field, articles)
    summary = digest["text"]
    report_text = f"Weekly news digest ({field}):\n\n{summary}"

    with get_session() as session:
        edition = session.query(NewsDigest).count() + 1
        session.add(NewsDigest(field=field, content=summary))

    # The designed email; report_text stays as its plain-text fallback part.
    html = render_digest_email(digest, edition=edition, reader=_reader_name())

    try:
        result = send_report(f"The Sa'ei Weekly · {field}", report_text, html=html)
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
            report_type="weekly",
            content=report_text,
            dry_run=result.get("dry_run", config.DRY_RUN),
            status=status,
            error_message=error_message,
        ))

    return summary


if __name__ == "__main__":
    print(run_weekly_news_digest())
