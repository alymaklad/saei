"""
Regression tests for the "database is locked" production failure: the old
_process_one_job held ONE transaction open across a job's entire orchestrator
call (multiple LLM round-trips), so a batch of dozens of jobs could hold
SQLite's write lock for minutes, and any other process writing during that
window (the scheduler's own run overlapping a manual "Search now" click)
failed outright. jobs/daily_run.py now splits each job into three short,
independent transactions -- insert Job, run the orchestrator with no
transaction open, insert Application/SkillGap -- while preserving the
original guarantees: a failed job leaves no trace (so it's retried on the
next run, not silently skipped by the dedupe check forever), and a
concurrent insert of the same URL from another process is handled cleanly.

Uses a plain (no WAL/busy_timeout tuning) temp SQLite engine deliberately --
if a future change reintroduces holding a transaction open across the
orchestrator call, a second connection writing during that window will fail
FAST with sqlite3.OperationalError instead of the test hanging for a
busy_timeout window, which is what test_no_lock_held_during_orchestrator_call
below relies on to fail loudly rather than silently passing slow.
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
from models import Base, Job, Application


@pytest.fixture()
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    original_engine, original_session = db.engine, db.SessionLocal
    db.engine, db.SessionLocal = engine, Session
    try:
        yield engine
    finally:
        db.engine, db.SessionLocal = original_engine, original_session
        engine.dispose()   # pooled connections hold the file open (Windows won't delete it)
        os.remove(path)


def _raw_job(url="https://boards.greenhouse.io/acme/jobs/1", **overrides):
    job = {"url": url, "title": "Software Engineer", "source": "greenhouse", "description": "desc"}
    job.update(overrides)
    return job


def test_successful_job_writes_job_and_application_rows(temp_db):
    from jobs import daily_run

    fake_result = {
        "status": "pending_review",
        "ats_result": {"score": 0.8, "missing_skills": ["Kubernetes"]},
        "cv_path": None,
    }
    with patch("jobs.daily_run.orchestrator_app.invoke", return_value=fake_result):
        outcome = daily_run._process_one_job(_raw_job(), "cv text")

    assert outcome == {"title": "Software Engineer", "company": "", "status": "pending_review"}
    with db.get_session() as session:
        job_row = session.query(Job).filter(Job.url == "https://boards.greenhouse.io/acme/jobs/1").first()
        assert job_row is not None
        app_row = session.query(Application).filter(Application.job_id == job_row.id).first()
        assert app_row is not None
        assert app_row.status == "pending_review"
        assert app_row.ats_score == 0.8


def test_orchestrator_failure_deletes_job_row_so_it_can_be_retried(temp_db):
    from jobs import daily_run

    with patch("jobs.daily_run.orchestrator_app.invoke", side_effect=RuntimeError("LLM call failed")):
        with pytest.raises(RuntimeError):
            daily_run._process_one_job(_raw_job(), "cv text")

    with db.get_session() as session:
        assert session.query(Job).filter(Job.url == "https://boards.greenhouse.io/acme/jobs/1").first() is None

    # Retried on a later run (as daily_run's own loop would do) -- must not
    # be silently skipped as "already exists" just because the first attempt
    # got as far as inserting before failing.
    fake_result = {"status": "auto_submitted"}  # matches orchestrator's real auto_submit_node shape (no "ats_result" key)
    with patch("jobs.daily_run.orchestrator_app.invoke", return_value=fake_result):
        outcome = daily_run._process_one_job(_raw_job(), "cv text")
    assert outcome["status"] == "auto_submitted"


def test_duplicate_url_is_skipped_not_reinserted(temp_db):
    from jobs import daily_run

    fake_result = {"status": "pending_review"}  # matches orchestrator's real draft_node shape (no "ats_result" key)
    with patch("jobs.daily_run.orchestrator_app.invoke", return_value=fake_result):
        first = daily_run._process_one_job(_raw_job(), "cv text")
        second = daily_run._process_one_job(_raw_job(), "cv text")  # same URL again

    assert first is not None
    assert second is None
    with db.get_session() as session:
        assert session.query(Job).filter(Job.url == "https://boards.greenhouse.io/acme/jobs/1").count() == 1


def test_no_lock_held_during_orchestrator_call(temp_db):
    """
    The actual bug this whole rewrite fixes: while a job's orchestrator call
    is "in flight", a completely separate write to the database must succeed
    immediately -- proving _process_one_job doesn't hold the Job-insert
    transaction open across the orchestrator call. Before this fix, the
    equivalent write here would have deadlocked/failed against the still-open
    outer transaction.
    """
    from jobs import daily_run

    def fake_invoke(state):
        with db.get_session() as session:
            session.add(Job(url="https://other/concurrent-write", title="x", source_site="greenhouse"))
        return {"status": "pending_review"}  # matches orchestrator's real draft_node shape

    with patch("jobs.daily_run.orchestrator_app.invoke", side_effect=fake_invoke):
        outcome = daily_run._process_one_job(_raw_job(), "cv text")

    assert outcome["status"] == "pending_review"
    with db.get_session() as session:
        assert session.query(Job).filter(Job.url == "https://other/concurrent-write").first() is not None
