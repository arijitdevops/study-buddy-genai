"""HTTP-level tests: sessions, file + image upload and the streaming chat.

These run the real FastAPI app (lifespan included, so tables are created) on
the SQLite database configured in conftest.py. Gemini is swapped for the
scripted stub through FastAPI's dependency overrides.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.gemini import get_gemini_service
from tests.conftest import StubGeminiService

# A valid 1x1 transparent PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _new_session(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = {"title": "Biology revision", "subject": "biology", "grade": 7, **overrides}
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines.get("data", "{}"))))
    return events


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------
def test_session_lifecycle(client: TestClient) -> None:
    created = _new_session(client)
    session_id = created["id"]

    listed = client.get("/api/sessions", params={"student_id": created["student_id"]}).json()
    assert any(item["id"] == session_id for item in listed["items"])

    renamed = client.patch(f"/api/sessions/{session_id}", json={"title": "Cells"}).json()
    assert renamed["title"] == "Cells"

    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


# ----------------------------------------------------------------------
# Uploads
# ----------------------------------------------------------------------
def test_upload_text_document_is_chunked(client: TestClient) -> None:
    session = _new_session(client)
    text = "Mitochondria are the powerhouse of the cell.\n\n" * 40
    response = client.post(
        "/api/files",
        data={"session_id": session["id"]},
        files={"file": ("notes.txt", text.encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file"]["status"] == "ready"
    assert body["file"]["is_image"] is False
    assert body["file"]["chunk_count"] >= 1

    fetched = client.get(f"/api/files/{body['file']['id']}").json()
    assert fetched["original_name"] == "notes.txt"


def test_upload_image_is_stored_for_the_vision_model(client: TestClient) -> None:
    session = _new_session(client)
    response = client.post(
        "/api/files",
        data={"session_id": session["id"]},
        files={"file": ("diagram.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file"]["is_image"] is True
    assert body["file"]["status"] == "ready"
    assert body["file"]["mime"] == "image/png"

    file_id = body["file"]["id"]
    assert client.delete(f"/api/files/{file_id}").status_code == 200
    assert client.get(f"/api/files/{file_id}").status_code == 404


def test_upload_rejects_disallowed_type(client: TestClient) -> None:
    session = _new_session(client)
    response = client.post(
        "/api/files",
        data={"session_id": session["id"]},
        files={"file": ("run.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert "aren't supported" in response.json()["message"]


def test_upload_to_unknown_session_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/files",
        data={"session_id": "does-not-exist"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 404


# ----------------------------------------------------------------------
# Chat
# ----------------------------------------------------------------------
def test_chat_without_api_key_returns_a_clear_503(client: TestClient) -> None:
    session = _new_session(client)
    response = client.post(
        "/api/chat", json={"session_id": session["id"], "message": "What is osmosis?"}
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "gemini_not_configured"
    assert "GEMINI_API_KEY" in body["message"]


def test_chat_streams_an_answer_with_an_image_attached(client: TestClient) -> None:
    stub = StubGeminiService(
        json_responses=[
            {"verdict": "allow", "category": "none", "reason": "schoolwork"},
            {"intent": "explain", "reasoning": "concept question"},
        ],
        text_responses=["This diagram shows a plant cell with a large vacuole."],
    )
    app.dependency_overrides[get_gemini_service] = lambda: stub

    session = _new_session(client)
    upload = client.post(
        "/api/files",
        data={"session_id": session["id"]},
        files={"file": ("cell.png", PNG_BYTES, "image/png")},
    ).json()

    response = client.post(
        "/api/chat",
        json={
            "session_id": session["id"],
            "message": "What does this diagram show?",
            "file_ids": [upload["file"]["id"]],
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response.text)
    kinds = [kind for kind, _ in events]
    assert "token" in kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") + kinds.count("error") == 1
    done = events[-1][1]
    assert "plant cell" in done["answer"]
    assert done["message_id"]

    # The image bytes reached the model call.
    images = stub.stream_kwargs[-1].get("images") or []
    assert len(images) == 1
    assert images[0].mime_type == "image/png"
    assert images[0].data == PNG_BYTES

    history = client.get(f"/api/sessions/{session['id']}/messages").json()
    roles = [message["role"] for message in history["items"]]
    assert roles == ["user", "assistant"]


def test_chat_unknown_session_is_404(client: TestClient) -> None:
    app.dependency_overrides[get_gemini_service] = lambda: StubGeminiService()
    response = client.post("/api/chat", json={"session_id": "missing", "message": "hi"})
    assert response.status_code == 404
