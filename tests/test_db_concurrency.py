"""
Regression test for a real "database is locked" failure seen in production:
multiple processes (api.py + scheduler.py, or a manual CLI run on top of
either) writing to the same SQLite file within the same few seconds. The
default sqlite3 busy_timeout is 0 and the default journal mode locks the
whole file per writer, so any overlap failed instantly with
sqlite3.OperationalError: database is locked. db.py now sets a 30s
busy_timeout and WAL journal mode on every connection -- this test proves
concurrent writers actually succeed instead of just checking the pragma
value.
"""
import os
import tempfile
import threading

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from models import Base, Job


@pytest.fixture()
def temp_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(path and f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()   # pooled connections hold the file open (Windows won't delete it)
    for suffix in ("", "-wal", "-shm"):
        candidate = path + suffix
        if os.path.exists(candidate):
            os.remove(candidate)


def test_journal_mode_is_wal(temp_engine):
    with temp_engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert mode.lower() == "wal"


def test_concurrent_writers_do_not_raise_database_locked(temp_engine):
    Session = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    errors = []

    def writer(n):
        try:
            for i in range(20):
                session = Session()
                session.add(Job(url=f"https://x/{n}-{i}", title="t", source_site="greenhouse"))
                session.commit()
                session.close()
        except Exception as exc:  # noqa: BLE001 -- we want to see ANY failure, not just OperationalError
            errors.append(str(exc))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with Session() as session:
        assert session.query(Job).count() == 6 * 20
