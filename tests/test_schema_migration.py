"""
Base.metadata.create_all() only creates *missing tables* -- it silently does
nothing for a table that already exists with an older column set, which is
exactly the situation every real user's database is in after this session's
ATS-scoring change added five new columns to Application. db._migrate_schema()
patches this with guarded ALTER TABLE ADD COLUMN statements. This test builds
an "old" applications table (matching the schema before this change) and
proves init_db() brings it up to date without touching existing rows or
choking on a second run.
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine

import db


@pytest.fixture()
def old_schema_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY,
                job_id INTEGER,
                ats_score FLOAT,
                cv_version_path VARCHAR,
                status VARCHAR,
                email_sent BOOLEAN,
                date_applied DATETIME,
                date_created DATETIME
            )
        """)
        conn.exec_driver_sql(
            "INSERT INTO applications (id, job_id, ats_score, status) VALUES (1, 1, 0.42, 'pending_review')"
        )

    original_engine = db.engine
    db.engine = engine
    try:
        yield engine
    finally:
        db.engine = original_engine
        engine.dispose()   # pooled connections hold the file open (Windows won't delete it)
        os.remove(path)


def _columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def test_migrate_schema_adds_missing_columns_without_losing_data(old_schema_db):
    before = _columns(old_schema_db, "applications")
    assert "ats_breakdown" not in before

    db._migrate_schema()

    after = _columns(old_schema_db, "applications")
    for col in ("ats_breakdown", "ats_explanation", "tailored_ats_score",
                "tailored_ats_breakdown", "tailored_ats_explanation"):
        assert col in after

    with old_schema_db.connect() as conn:
        row = conn.exec_driver_sql("SELECT ats_score, status FROM applications WHERE id = 1").fetchone()
    assert row == (0.42, "pending_review")  # pre-existing row untouched


def test_migrate_schema_is_idempotent(old_schema_db):
    db._migrate_schema()
    db._migrate_schema()  # must not raise "duplicate column" on a second run
    assert "ats_breakdown" in _columns(old_schema_db, "applications")


def test_migrate_schema_noop_on_a_fresh_database():
    """A brand-new database's applications table already has every column
    (from Base.metadata.create_all(), which runs before _migrate_schema() in
    init_db()) -- this must not error just because there's nothing to add."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    from models import Base
    Base.metadata.create_all(engine)

    original_engine = db.engine
    db.engine = engine
    try:
        db._migrate_schema()  # should be a clean no-op
    finally:
        db.engine = original_engine
        engine.dispose()   # pooled connections hold the file open (Windows won't delete it)
        os.remove(path)
