import time

import pytest
from litestar.testing import TestClient

import db_manager
from app import app, session_config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(db_manager, "_connection_established", True)
    with TestClient(app=app, raise_server_exceptions=False, session_config=session_config) as c:
        yield c


FETCH_HEADERS = {"sec-fetch-mode": "cors"}


def test_expired_session_answers_fetch_with_json(client):
    client.set_session_data(
        {"user_id": 1, "db_epoch": db_manager.get_target_epoch(), "last_activity": time.time() - 99999}
    )

    response = client.post("/settings/db/add", files={"name": (None, "x")}, headers=FETCH_HEADERS)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["relogin"] is True


def test_stale_db_epoch_answers_fetch_with_json(client):
    client.set_session_data({"user_id": 1, "db_epoch": -1, "last_activity": time.time()})

    response = client.post("/settings/db/add", files={"name": (None, "x")}, headers=FETCH_HEADERS)

    assert response.status_code == 401
    assert response.json()["location"] == "/auth/login"


def test_navigation_still_redirects(client):
    client.set_session_data({"user_id": 1, "db_epoch": -1, "last_activity": time.time()})

    response = client.get("/settings/", headers={"sec-fetch-mode": "navigate"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
