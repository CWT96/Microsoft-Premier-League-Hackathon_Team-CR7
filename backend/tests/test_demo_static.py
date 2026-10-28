"""test_demo_static.py — The Premier X is served at /, the earlier fan app at /classic/, and the API routes still win over both.

Last Modified: 2026-10-27, first version
Inputs:  frontend/premier-x/ and frontend/fan-app/ (static files), hand-built tiny match (fast, no ingest)
Outputs: none

Workflow:
  - GET / returns The Premier X page; GET /classic/ the earlier fan app
  - GET /api/matches still answers from the API, not the static mount

CAUTION: none
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.fixtures.replay_fixtures import FakeSource, tiny_bundle


@pytest.fixture
def client():
    with TestClient(create_app(FakeSource(tiny_bundle()))) as c:
        yield c


def test_root_serves_premier_x(client):
    r = client.get("/")
    assert r.status_code == 200 and "The Premier X" in r.text
    assert client.get("/screens/Live.dc.html").status_code == 200


def test_classic_fan_app_still_served(client):
    r = client.get("/classic/")
    assert r.status_code == 200 and "Matchday Call" in r.text


def test_ops_dashboard_served(client):
    r = client.get("/ops/")
    assert r.status_code == 200 and "Partner report" in r.text
    assert client.get("/ops/data/data.js").status_code == 200
    assert client.get("/ops", follow_redirects=False).headers["location"] == "/ops/"


def test_api_still_wins_over_static(client):
    assert client.get("/api/matches").status_code == 200
