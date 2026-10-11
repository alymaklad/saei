"""Evening report: today's applications, emailed to you."""
from datetime import datetime, timezone, timedelta

import config
from db import get_session, init_db
from models import Application, Job, ReportLog
from agents.reporter_agent import build_daily_report, send_report


def run_daily_report():
    init_db()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with get_session() as session:
        rows = (
            session.query(Application, Job)
            .join(Job, Application.job_id == Job.id)
            .filter(Application.date_created >= since)
            .all()
        )
        applications_today = [
            {"title": job.title, "company": job.company, "status": application.status}
            for application, job in rows
        ]

    report_text = build_daily_report(applications_today)

    try:
        result = send_report("Sa'ei daily report", report_text)
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
