"""GeminiService against the real google-genai types, with a fake client.

No network: the SDK client is replaced by an object exposing the same
``client.aio.models`` surface, returning genuine ``GenerateContentResponse``
objects so response parsing is exercised exactly as in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from google.genai import types

from app.services.gemini import (
    GeminiBlockedError,
    GeminiNotConfiguredError,
    GeminiService,
    InlineImage,
    supports_thinking_budget_zero,
)


def _response(text: str, finish: str = "STOP") -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=5, total_token_count=17
        ),
    )


class FakeModels:
    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)

    async def generate_content_stream(
        self, **kwargs: Any
    ) -> AsyncIterator[types.GenerateContentResponse]:
        self.calls.append(kwargs)
        chunks = list(self.responses)

        async def iterator() -> AsyncIterator[types.GenerateContentResponse]:
            for chunk in chunks:
                yield chunk

        return iterator()


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = type("Aio", (), {"models": models})()


def _service(models: FakeModels, **kwargs: Any) -> GeminiService:
    service = GeminiService(api_key="test-key", max_retries=1, **kwargs)
    service._client = FakeClient(models)
    return service


def test_defaults_are_gemini_2_5_flash() -> None:
    service = GeminiService(api_key="")
    assert service.default_model == "gemini-2.5-flash"
    assert service.vision_model == "gemini-2.5-flash"
    assert service.guard_model == "gemini-2.5-flash"


async def test_missing_key_raises_a_clear_error() -> None:
    service = GeminiService(api_key="")
    assert not service.available
    with pytest.raises(GeminiNotConfiguredError, match="GEMINI_API_KEY"):
        await service.generate("hello")


async def test_generate_json_parses_and_disables_thinking() -> None:
    models = FakeModels([_response('{"verdict": "allow", "category": "none"}')])
    service = _service(models)
    payload = await service.generate_json(
        "classify", {"type": "object", "properties": {"verdict": {"type": "string"}}}
    )
    assert payload == {"verdict": "allow", "category": "none"}
    config: types.GenerateContentConfig = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0
    assert len(config.safety_settings or []) == 4


async def test_generate_normalises_text_and_usage() -> None:
    models = FakeModels([_response("Osmosis is the movement of water.")])
    result = await _service(models).generate("What is osmosis?")
    assert result.text == "Osmosis is the movement of water."
    assert result.usage["total_token_count"] == 17
    assert models.calls[0]["model"] == "gemini-2.5-flash"


async def test_safety_block_without_text_raises() -> None:
    blocked = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason="SAFETY")]
    )
    with pytest.raises(GeminiBlockedError):
        await _service(FakeModels([blocked])).generate("something unsafe")


async def test_stream_sends_images_to_the_vision_model() -> None:
    models = FakeModels([_response("A plant "), _response("cell.")])
    service = _service(models, vision_model="vision-model")
    image = InlineImage(data=b"\x89PNG\r\n\x1a\nrest", mime_type="image/png")
    chunks = [chunk async for chunk in service.stream("What is this?", images=[image])]
    assert "".join(chunks) == "A plant cell."

    call = models.calls[0]
    assert call["model"] == "vision-model"
    parts = call["contents"][-1].parts
    assert parts[0].text == "What is this?"
    assert parts[1].inline_data.mime_type == "image/png"
    assert parts[1].inline_data.data == image.data


def test_thinking_budget_only_for_flash_models() -> None:
    assert supports_thinking_budget_zero("gemini-2.5-flash")
    assert supports_thinking_budget_zero("gemini-2.5-flash-lite")
    assert not supports_thinking_budget_zero("gemini-2.5-pro")
