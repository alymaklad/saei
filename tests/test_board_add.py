"""Known job boards are offered under Manage sources and added by a click."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'api.db'}")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    import db
    importlib.reload(db)
    db.Base.metadata.create_all(db.engine)
    import api
    importlib.reload(api)
    return TestClient(api.app)


def _available(tc):
    return {b["key"] for b in tc.get("/api/search/builtin-sources").json()["available_boards"]}


def test_add_moves_a_board_from_offered_to_searched_and_remove_moves_it_back(client):
    assert "wuzzuf" in _available(client)

    assert client.post("/api/search/builtin-sources/add-board", json={"key": "wuzzuf"}).status_code == 200
    sites = client.get("/api/search/sites").json()
    row = next(s for s in sites if s["site_type"] == "wuzzuf")
    assert "wuzzuf" not in _available(client)
    assert client.post("/api/search/builtin-sources/add-board",
                       json={"key": "wuzzuf"}).status_code == 400   # no duplicates

    assert client.delete(f"/api/search/sites/{row['id']}").status_code == 200
    assert "wuzzuf" in _available(client)


def test_an_unknown_board_is_refused(client):
    assert client.post("/api/search/builtin-sources/add-board",
                       json={"key": "nope"}).status_code == 404
