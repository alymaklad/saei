"""Weekly news digest for the user's field, stored + emailed to you."""
import config
from db import get_session, init_db
from models import NewsDigest, ReportLog
from agents.news_agent import fetch_news, summarize_news
from agents.reporter_agent import send_report

DEFAULT_FIELD = "software engineering"


def run_weekly_news_digest(field: str = DEFAULT_FIELD):
    init_db()
    articles = fetch_news(field)
    summary = summarize_news(field, articles)
    report_text = f"Weekly news digest ({field}):\n\n{summary}"

    with get_session() as session:
        session.add(NewsDigest(field=field, content=summary))

    try:
        result = send_report(f"Sa'ei weekly news: {field}", report_text)
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
