"""The evening report: what it collects from the database, and the email and
plain text built from it."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
from models import Application, Base, Job, PendingJob, SkillGap

NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def temp_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'report.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    original = db.engine, db.SessionLocal
    db.engine, db.SessionLocal = engine, Session
    try:
        yield Session
    finally:
        db.engine, db.SessionLocal = original
        engine.dispose()


def _seed(Session):
    with Session() as s:
        today = NOW - timedelta(hours=2)
        jobs = [
            Job(url="https://www.linkedin.com/jobs/view/1", title="AI Engineer", company="Acme",
                source_site="generic", date_found=today),
            Job(url="https://egypt.tanqeeb.com/jobs/2", title="ML Engineer", company="",
                source_site="generic", date_found=today),
            Job(url="https://boards.greenhouse.io/x/3", title="Old role", company="Old Co",
                source_site="greenhouse", date_found=NOW - timedelta(days=3)),
        ]
        s.add_all(jobs)
        s.flush()
        s.add_all([
            Application(job_id=jobs[0].id, match_score=0.82, ats_score=0.55, tailored_ats_score=0.71,
                        status="cv_rewritten_notify_user", date_created=today),
            Application(job_id=jobs[1].id, match_score=0.64, ats_score=0.70,
                        status="sent", email_sent=True, date_created=today),
            Application(job_id=jobs[2].id, match_score=0.9, status="pending_review",
                        date_created=NOW - timedelta(days=3)),
            SkillGap(job_id=jobs[0].id, missing_skills=json.dumps(["AWS", "MLOps"]), date_created=today),
            SkillGap(job_id=jobs[1].id, missing_skills=json.dumps(["AWS"]), date_created=today),
            PendingJob(url="https://x/4", title="Later", reason="unreadable"),
        ])
        s.commit()


def test_collects_the_last_24_hours(temp_db, monkeypatch):
    import config
    monkeypatch.setattr(config, "DRY_RUN", False)
    _seed(temp_db)
    from jobs.daily_report import collect_daily_report
    data = collect_daily_report(NOW)

    assert data["found"] == 2 and data["scored"] == 2 and data["tailored"] == 1 and data["sent"] == 1
    assert [r["title"] for r in data["best"]] == ["AI Engineer", "ML Engineer"]   # by match
    assert [r["title"] for r in data["needs_you"]] == ["AI Engineer"]   # the sent one is done
    assert data["waiting_older"] == 1
    assert data["skills"][0] == ("AWS", 2)
    assert dict(data["sources"]) == {"LinkedIn": 1, "Tanqeeb": 1}
    assert data["set_aside"] == {"unreadable": 1}
    assert data["week"][-1]["count"] == 2 and sum(d["count"] for d in data["week"]) == 3


def test_email_and_text_show_the_day(temp_db, monkeypatch):
    import config
    monkeypatch.setattr(config, "DRY_RUN", False)
    _seed(temp_db)
    from agents.daily_email import render_daily_email
    from agents.reporter_agent import build_daily_report
    from jobs.daily_report import collect_daily_report
    data = collect_daily_report(NOW)

    html = render_daily_email(data, reader="Aly Maklad")
    assert "Good evening, Aly." in html
    assert "Needs your attention" in html and "Review and send" in html
    assert "CV fit 55% &rarr; 71%" in html
    assert "https://www.linkedin.com/jobs/view/1" in html
    assert "Carried to tomorrow" in html and "couldn&#x27;t be read" not in html   # not double-escaped
    assert "Dry run is on" not in html

    text = build_daily_report(data)
    assert text.startswith("2 new roles found")
    assert "- AI Engineer at Acme (82% match)" in text
    assert "- ML Engineer (Tanqeeb)" in text   # no "at" before a missing company


def test_a_quiet_day(temp_db):
    from agents.daily_email import render_daily_email
    from agents.reporter_agent import build_daily_report
    from jobs.daily_report import collect_daily_report
    data = collect_daily_report(NOW)
    assert build_daily_report(data).startswith("A quiet day")
    assert "Today's best matches" not in render_daily_email(data)


def test_board_names():
    from jobs.daily_report import board_name
    assert board_name("https://eg.linkedin.com/jobs/1") == "LinkedIn"
    assert board_name("https://jobs.lever.co/acme/1", "generic") == "Lever"
    assert board_name("https://wellfound.com/jobs/1", "generic") == "Wellfound"
    assert board_name(None, None) == "Other"
