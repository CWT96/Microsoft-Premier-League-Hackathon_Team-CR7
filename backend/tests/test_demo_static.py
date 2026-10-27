"""test_demo_static.py — the fan app is served at / and the API routes still win over it.

Last Modified: 2026-10-27, first version
Inputs:  frontend/fan-app/ (static files), hand-built tiny match (fast, no ingest)
Outputs: none

Workflow:
  - GET / returns the fan app page
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


def test_root_serves_fan_app(client):
    r = client.get("/")
    assert r.status_code == 200 and "Matchday Call" in r.text


def test_api_still_wins_over_static(client):
    assert client.get("/api/matches").status_code == 200
