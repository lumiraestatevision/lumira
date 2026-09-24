"""Gemini-Anbindung mit einem Fake-Client – ohne Netzwerk und ohne API-Key."""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import errors, types

from lumira_blv.logic import gemini
from lumira_blv.logic.extraction import BLVExtraction
from lumira_shared import NonRetryableError

VALID_JSON = BLVExtraction(materials=[], variants=[], notes=["leer"]).model_dump_json()


def _response(
    text: str, finish: types.FinishReason | None = types.FinishReason.STOP
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1200, candidates_token_count=300
        ),
    )


class FakeClient:
    """Bildet genai.Client(...).aio.models.generate_content / aio.aclose nach."""

    def __init__(self, result: types.GenerateContentResponse | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.aio = self
        self.models = self

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def aclose(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, result: Any) -> FakeClient:
    fake = FakeClient(result)
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: fake)
    return fake


async def _run() -> BLVExtraction:
    return await gemini.extract_with_gemini(
        b"%PDF-1.7 test", api_key="g-test", model="gemini-3.8-flash"
    )


async def test_request_contains_pdf_prompt_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _response(VALID_JSON))

    result = await _run()

    assert result.notes == ["leer"]
    assert fake.closed
    [call] = fake.calls
    assert call["model"] == "gemini-3.8-flash"
    pdf_part, instruction = call["contents"]
    assert pdf_part.inline_data.mime_type == "application/pdf"
    assert pdf_part.inline_data.data == b"%PDF-1.7 test"
    assert "Leistungsverzeichnis" in instruction
    config: types.GenerateContentConfig = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == BLVExtraction.model_json_schema()
    assert "Leistungsverzeichnisse" in str(config.system_instruction)


async def test_truncated_answer_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _response('{"materials": [', finish=types.FinishReason.MAX_TOKENS))
    with pytest.raises(NonRetryableError, match="MAX_TOKENS"):
        await _run()


async def test_safety_stop_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _response("", finish=types.FinishReason.SAFETY))
    with pytest.raises(NonRetryableError, match="SAFETY"):
        await _run()


async def test_invalid_key_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(
        monkeypatch,
        errors.ClientError(
            400,
            {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}},
        ),
    )
    with pytest.raises(NonRetryableError, match="API key not valid"):
        await _run()
    assert fake.closed


async def test_quota_exhausted_is_retried_later(monkeypatch: pytest.MonkeyPatch) -> None:
    quota = errors.ClientError(
        429, {"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}
    )
    _install(monkeypatch, quota)
    # Kein NonRetryableError → der Stream-Consumer stellt das Event später erneut zu.
    with pytest.raises(errors.ClientError) as caught:
        await _run()
    assert not isinstance(caught.value, NonRetryableError)
