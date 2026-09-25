"""The health endpoint must report status without leaking secrets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_a_payload(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert {d["name"] for d in body["dependencies"]} == {
        "database", "gemini", "web_search", "upload_dir"
    }


def test_health_never_returns_the_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSyTOPSECRETVALUE", raising=False)
    body = client.get("/api/health").text
    assert "AIzaSyTOPSECRETVALUE" not in body


def test_health_lists_missing_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    body = client.get("/api/health").json()
    assert "GEMINI_API_KEY" in body["missing_configuration"]
    assert body["status"] == "degraded"


def test_health_is_exempt_from_rate_limiting(client: TestClient) -> None:
    for _ in range(settings.rate_limit_per_minute + 5):
        assert client.get("/api/health").status_code == 200


def test_root_points_at_the_docs(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["health"] == "/api/health"
