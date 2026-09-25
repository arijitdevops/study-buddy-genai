"""Thin async wrapper around the Google Gen AI SDK (``google-genai``).

Everything the application does with Gemini goes through :class:`GeminiService`
so that safety settings, retries, timeouts and the "no API key" degradation
path are implemented exactly once.

The SDK used here is the current ``google-genai`` package::

    from google import genai
    client = genai.Client(api_key=...)
    await client.aio.models.generate_content(model=..., contents=..., config=...)

The older ``google-generativeai`` package is *not* used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard exercised only without the SDK installed
    from google import genai
    from google.genai import types as genai_types

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False


class GeminiError(RuntimeError):
    """Base class for every Gemini failure surfaced to the application."""


class GeminiNotConfiguredError(GeminiError):
    """Raised when no API key (or no SDK) is available.

    The API layer turns this into a friendly 503 with an explanation rather
    than a traceback.
    """


class GeminiTransientError(GeminiError):
    """A retryable failure: rate limit, timeout or 5xx from the API."""


class GeminiBlockedError(GeminiError):
    """The model refused to answer because its own safety filters fired."""


# Substrings that identify a transient failure regardless of SDK exception type.
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "429",
    "resource_exhausted",
    "rate limit",
    "deadline",
    "timeout",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "internal error",
)


def _is_transient(exc: BaseException) -> bool:
    """Heuristically decide whether ``exc`` is worth retrying."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def supports_thinking_budget_zero(model: str) -> bool:
    """True for Gemini 2.5 Flash / Flash-Lite, where thinking can be switched off."""
    name = model.lower()
    return "2.5" in name and "flash" in name


@dataclass(slots=True)
class GenerationResult:
    """Normalised result of a non-streaming generation call."""

    text: str
    model: str
    finish_reason: Optional[str] = None
    blocked: bool = False
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class InlineImage:
    """An image to send to the vision model."""

    data: bytes
    mime_type: str


class GeminiService:
    """Async facade over the Gemini API used by every node and guard."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        default_model: Optional[str] = None,
        vision_model: Optional[str] = None,
        guard_model: Optional[str] = None,
        max_retries: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        """Create the service.

        Args:
            api_key: Gemini API key. Defaults to ``settings.gemini_api_key``.
            default_model: Text model id, e.g. ``"gemini-2.5-flash"``.
            vision_model: Model id used when images are attached.
            guard_model: Small/cheap model id used by the guardrail classifier.
            max_retries: Attempts for transient failures (including the first).
            timeout_seconds: Per-call wall-clock timeout.
        """
        self._api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.default_model = default_model or settings.gemini_model
        self.vision_model = vision_model or settings.gemini_vision_model
        self.guard_model = guard_model or settings.gemini_guard_model
        self.max_retries = max_retries if max_retries is not None else settings.gemini_max_retries
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.gemini_timeout_seconds
        )
        self._client: Any = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True when both the SDK and an API key are present."""
        return _SDK_AVAILABLE and bool(self._api_key)

    def _require_client(self) -> Any:
        """Return the lazily-created SDK client or raise a friendly error."""
        if not _SDK_AVAILABLE:
            raise GeminiNotConfiguredError(
                "The google-genai package is not installed. "
                "Run: pip install -r requirements.txt"
            )
        if not self._api_key:
            raise GeminiNotConfiguredError(
                "GEMINI_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and add your key from https://aistudio.google.com/apikey"
            )
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Safety configuration
    # ------------------------------------------------------------------
    @staticmethod
    def default_safety_settings() -> list[Any]:
        """Explicit safety settings applied to every request.

        The thresholds are deliberately stricter than the API defaults because
        the audience is school students.
        """
        if not _SDK_AVAILABLE:  # pragma: no cover
            return []
        block = genai_types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE
        categories = (
            genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        )
        return [
            genai_types.SafetySetting(category=category, threshold=block)
            for category in categories
        ]

    def _build_config(
        self,
        *,
        system_instruction: Optional[str],
        temperature: float,
        max_output_tokens: Optional[int],
        response_schema: Optional[dict[str, Any]],
        model: Optional[str] = None,
        fast: bool = False,
    ) -> Any:
        """Assemble a ``GenerateContentConfig`` for one call.

        ``fast=True`` (guardrail and router classification) switches off
        Gemini 2.5 Flash's "thinking" step, which is unnecessary for a one-word
        JSON verdict and adds latency to every turn. 2.5 Pro cannot disable
        thinking, so the budget is only set for Flash / Flash-Lite models.
        """
        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "safety_settings": self.default_safety_settings(),
        }
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if response_schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = response_schema
        if fast and model and supports_thinking_budget_zero(model):
            kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
        return genai_types.GenerateContentConfig(**kwargs)

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------
    async def _with_retries(self, operation_name: str, factory: Any) -> Any:
        """Run ``factory()`` with exponential backoff on transient failures.

        Args:
            operation_name: Name used in log lines.
            factory: Zero-argument coroutine function to call.

        Returns:
            Whatever ``factory()`` resolves to.

        Raises:
            GeminiTransientError: Every attempt failed with a retryable error.
            GeminiError: A non-retryable failure.
        """
        attempts = max(1, self.max_retries)
        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(factory(), timeout=self.timeout_seconds)
            except GeminiError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalised below
                last_exc = exc
                if not _is_transient(exc) or attempt == attempts:
                    break
                delay = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.25)
                logger.warning(
                    "%s failed (attempt %d/%d): %s. Retrying in %.2fs.",
                    operation_name,
                    attempt,
                    attempts,
                    exc.__class__.__name__,
                    delay,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        if _is_transient(last_exc):
            raise GeminiTransientError(
                f"{operation_name} failed after {attempts} attempts: {last_exc}"
            ) from last_exc
        raise GeminiError(f"{operation_name} failed: {last_exc}") from last_exc

    # ------------------------------------------------------------------
    # Public generation API
    # ------------------------------------------------------------------
    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_output_tokens: Optional[int] = None,
        images: Optional[Sequence[InlineImage]] = None,
        history: Optional[Sequence[dict[str, str]]] = None,
    ) -> GenerationResult:
        """Generate a complete response.

        Args:
            prompt: The user-visible turn text.
            system_instruction: System prompt controlling tone and depth.
            model: Override the model id.
            temperature: Sampling temperature.
            max_output_tokens: Optional output cap.
            images: Inline images; when present the vision model is used.
            history: Prior turns as ``{"role": "user"|"model", "text": ...}``.

        Returns:
            A :class:`GenerationResult`.
        """
        client = self._require_client()
        chosen_model = model or (self.vision_model if images else self.default_model)
        contents = self._build_contents(prompt, history=history, images=images)
        config = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=None,
        )

        async def _call() -> Any:
            return await client.aio.models.generate_content(
                model=chosen_model, contents=contents, config=config
            )

        response = await self._with_retries(f"generate[{chosen_model}]", _call)
        return self._normalise_response(response, chosen_model)

    async def generate_json(
        self,
        prompt: str,
        response_schema: dict[str, Any],
        *,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Generate a structured JSON response validated against a schema.

        Args:
            prompt: Instruction for the model.
            response_schema: OpenAPI-subset schema passed as ``response_schema``.
            system_instruction: Optional system prompt.
            model: Model id; defaults to the guard model.
            temperature: Sampling temperature (0 by default for determinism).

        Returns:
            The parsed JSON object.

        Raises:
            GeminiError: The response was not parseable JSON.
        """
        client = self._require_client()
        chosen_model = model or self.guard_model
        config = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=None,
            response_schema=response_schema,
            model=chosen_model,
            fast=True,
        )

        async def _call() -> Any:
            return await client.aio.models.generate_content(
                model=chosen_model, contents=prompt, config=config
            )

        response = await self._with_retries(f"generate_json[{chosen_model}]", _call)
        raw = (getattr(response, "text", "") or "").strip()
        if not raw:
            raise GeminiError("Structured generation returned an empty response.")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GeminiError(f"Structured generation returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise GeminiError("Structured generation did not return a JSON object.")
        return parsed

    async def stream(
        self,
        prompt: str,
        *,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.4,
        images: Optional[Sequence[InlineImage]] = None,
        history: Optional[Sequence[dict[str, str]]] = None,
    ) -> AsyncIterator[str]:
        """Yield response text incrementally.

        Yields:
            Text deltas in arrival order.

        Raises:
            GeminiNotConfiguredError: No API key or SDK.
            GeminiTransientError: The stream could not be established.
        """
        client = self._require_client()
        chosen_model = model or (self.vision_model if images else self.default_model)
        contents = self._build_contents(prompt, history=history, images=images)
        config = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=None,
            response_schema=None,
        )

        async def _open_stream() -> Any:
            return await client.aio.models.generate_content_stream(
                model=chosen_model, contents=contents, config=config
            )

        # Only opening the stream is retried; a mid-stream failure is surfaced
        # to the caller so partial text is not silently duplicated.
        stream = await self._with_retries(f"stream[{chosen_model}]", _open_stream)
        try:
            async for chunk in stream:
                delta = getattr(chunk, "text", None)
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gemini stream interrupted.")
            raise GeminiError(f"Streaming interrupted: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_contents(
        self,
        prompt: str,
        *,
        history: Optional[Sequence[dict[str, str]]],
        images: Optional[Sequence[InlineImage]],
    ) -> Any:
        """Build the ``contents`` argument from history, prompt and images."""
        contents: list[Any] = []
        for turn in history or []:
            role = "model" if turn.get("role") in {"model", "assistant"} else "user"
            text = turn.get("text") or turn.get("content") or ""
            if not text:
                continue
            contents.append(
                genai_types.Content(role=role, parts=[genai_types.Part(text=text)])
            )

        parts: list[Any] = [genai_types.Part(text=prompt)]
        for image in images or []:
            parts.append(
                genai_types.Part.from_bytes(data=image.data, mime_type=image.mime_type)
            )
        contents.append(genai_types.Content(role="user", parts=parts))
        return contents

    @staticmethod
    def _normalise_response(response: Any, model: str) -> GenerationResult:
        """Convert an SDK response object into a :class:`GenerationResult`."""
        text = getattr(response, "text", None) or ""
        finish_reason: Optional[str] = None
        blocked = False

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            raw_reason = getattr(candidates[0], "finish_reason", None)
            if raw_reason is not None:
                finish_reason = getattr(raw_reason, "name", str(raw_reason))
                blocked = finish_reason.upper() in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"}

        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            blocked = True

        usage: dict[str, int] = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            for key in ("prompt_token_count", "candidates_token_count", "total_token_count"):
                value = getattr(meta, key, None)
                if isinstance(value, int):
                    usage[key] = value

        if blocked and not text:
            raise GeminiBlockedError(
                "The model declined to answer this request because it tripped "
                "Gemini's built-in safety filters."
            )
        return GenerationResult(
            text=text, model=model, finish_reason=finish_reason, blocked=blocked, usage=usage
        )


_service: Optional[GeminiService] = None


def get_gemini_service() -> GeminiService:
    """Return the process-wide :class:`GeminiService` singleton."""
    global _service
    if _service is None:
        _service = GeminiService()
    return _service
