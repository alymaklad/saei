"""
Reporter Agent -- the daily application report and the weekly news digest,
emailed to you through the same Gmail connection that sends applications
(see agents/email_agent.py). Replaced the earlier Telegram bot: one account
to connect instead of two, and the reports land where job replies do.
"""
from agents import email_agent


def send_report(subject: str, report_text: str, html: str | None = None) -> dict:
    """Emails a report (designed HTML when given, with the text as fallback).
    Never raises for a missing connection: the result carries an "error"
    instead, which the caller logs."""
    return email_agent.send_report_email(subject, report_text, html=html)


def build_daily_report(data: dict) -> str:
    """The plain-text daily report: the email's fallback part and what the
    Reports page's log keeps. `data` is jobs.daily_report.collect_daily_report()."""
    from agents.daily_email import STATUS_LABELS, headline, _pct

    def role(r):
        return f"{r['title']} at {r['company']}" if r["company"] else r["title"]

    lines = [headline(data), ""]
    sent_label = "Would send" if data["dry_run"] else "Sent"
    lines.append(f"Roles found: {data['found']}  |  Scored: {data['scored']}  |  "
                 f"CVs tailored: {data['tailored']}  |  {sent_label}: {data['sent']}")
    if data["needs_you"]:
        lines += ["", "Needs your attention:"]
        lines += [f"- {role(r)} ({_pct(r['match'])} match)" for r in data["needs_you"][:5]]
    if data["best"]:
        lines += ["", "Today's best matches:"]
        for r in data["best"]:
            fit = _pct(r["ats"]) + (f" -> {_pct(r['tailored_ats'])}" if r["tailored_ats"] is not None else "")
            status = STATUS_LABELS.get(r["status"], r["status"] or "")
            lines.append(f"- {role(r)} ({r['board']}): {_pct(r['match'])} match, "
                         f"CV fit {fit}, {status}" + (f"\n  {r['url']}" if r.get("url") else ""))
    if data["skills"]:
        lines += ["", "Skills that kept coming up: "
                  + ", ".join(f"{s} ({n})" for s, n in data["skills"])]
    if data["sources"]:
        lines += ["", "Where today's roles came from: "
                  + ", ".join(f"{name} ({n})" for name, n in data["sources"])]
    if data["set_aside"]:
        lines += ["", "Carried to tomorrow: " + ", ".join(
            f"{n} ({reason})" for reason, n in data["set_aside"].items())]
    lines += ["", f"This week so far: {sum(d['count'] for d in data['week'])} roles scored."]
    return "\n".join(lines)
