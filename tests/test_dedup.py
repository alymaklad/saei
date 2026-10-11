"""Running search/ingest twice on the same job list must not create duplicate rows."""
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Job


@pytest.fixture()
def temp_db_session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()   # pooled connections hold the file open (Windows won't delete it)
    os.remove(path)


def _ingest_if_new(session, url: str, title: str):
    if session.query(Job).filter(Job.url == url).first():
        return False
    session.add(Job(url=url, title=title, company="Acme", source_site="greenhouse"))
    session.commit()
    return True


def test_ingesting_same_job_twice_does_not_duplicate(temp_db_session):
    url = "https://boards.greenhouse.io/acme/jobs/123"
    assert _ingest_if_new(temp_db_session, url, "Engineer") is True
    assert _ingest_if_new(temp_db_session, url, "Engineer") is False
    assert temp_db_session.query(Job).filter(Job.url == url).count() == 1


def test_url_unique_constraint_enforced_at_db_level(temp_db_session):
    from sqlalchemy.exc import IntegrityError
    temp_db_session.add(Job(url="https://x/1", title="A", source_site="lever"))
    temp_db_session.commit()
    temp_db_session.add(Job(url="https://x/1", title="B", source_site="lever"))
    with pytest.raises(IntegrityError):
        temp_db_session.commit()
