"""
Reporter Agent -- the daily application report and the weekly news digest,
emailed to you through the same Gmail connection that sends applications
(see agents/email_agent.py). Replaced the earlier Telegram bot: one account
to connect instead of two, and the reports land where job replies do.
"""
from agents import email_agent


def send_report(subject: str, report_text: str) -> dict:
    """Emails a report. Never raises for a missing connection: the result
    carries an "error" instead, which the caller logs."""
    return email_agent.send_report_email(subject, report_text)


def build_daily_report(applications_today: list[dict]) -> str:
    if not applications_today:
        return "No applications submitted today."
    lines = [f"- {a['title']} at {a['company']} ({a['status']})" for a in applications_today]
    return "Today's applications:\n" + "\n".join(lines)
