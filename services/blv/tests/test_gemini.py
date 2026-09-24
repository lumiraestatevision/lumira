"""Gemini-Anbindung mit einem Fake-Client – ohne Netzwerk und ohne API-Key."""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import errors, types

from lumira_blv.logic import gemini
from lumira_blv.logic.extraction import BLVExtraction
from lumira_shared import NonRetryableError

VALID_JSON = BLVExtraction(materials=[], variants=[], notes=["leer"]).model_dump_json()
MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]


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


def _error(code: int) -> errors.APIError:
    status = {
        400: "INVALID_ARGUMENT",
        404: "NOT_FOUND",
        429: "RESOURCE_EXHAUSTED",
        503: "UNAVAILABLE",
    }[code]
    body = {"error": {"code": code, "message": f"Fehler {code}", "status": status}}
    return errors.ServerError(code, body) if code >= 500 else errors.ClientError(code, body)


class FakeClient:
    """Bildet genai.Client(...).aio.models.generate_content / aio.aclose nach.
    Jeder Aufruf liefert das nächste Element aus ``results``."""

    def __init__(self, results: list[types.GenerateContentResponse | Exception]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.aio = self
        self.models = self

    async def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, *results: Any) -> FakeClient:
    fake = FakeClient(list(results))
    monkeypatch.setattr(gemini.genai, "Client", lambda **kwargs: fake)
    return fake


async def _run(models: list[str] = MODELS) -> tuple[BLVExtraction, str]:
    return await gemini.extract_with_gemini(b"%PDF-1.7 test", api_key="g-test", models=models)


async def test_request_contains_pdf_prompt_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _response(VALID_JSON))

    result, used = await _run()

    assert result.notes == ["leer"]
    assert used == "gemini-3.8-flash"
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


@pytest.mark.parametrize(
    "first_error", [503, 429, 404], ids=["ueberlastet", "kontingent", "nicht-freigeschaltet"]
)
async def test_falls_back_to_next_model(monkeypatch: pytest.MonkeyPatch, first_error: int) -> None:
    fake = _install(monkeypatch, _error(first_error), _response(VALID_JSON))

    _, used = await _run()

    assert used == "gemini-3.5-flash"
    assert [c["model"] for c in fake.calls] == ["gemini-3.8-flash", "gemini-3.5-flash"]


async def test_all_models_busy_is_retried_later(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _error(503), _error(429), _error(503))
    with pytest.raises(gemini.GeminiUnavailableError, match=r"gemini-3\.5-flash-lite: 503"):
        await _run()
    assert len(fake.calls) == 3
    assert fake.closed


async def test_no_model_available_for_account_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _error(404), _error(404), _error(404))
    with pytest.raises(NonRetryableError, match="für dieses Konto"):
        await _run()


async def test_invalid_key_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _error(400), _response(VALID_JSON))
    with pytest.raises(NonRetryableError, match="Fehler 400"):
        await _run()
    assert len(fake.calls) == 1  # ein anderes Modell hilft bei falschem Key nicht


async def test_truncated_answer_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _response('{"materials": [', finish=types.FinishReason.MAX_TOKENS))
    with pytest.raises(NonRetryableError, match="MAX_TOKENS"):
        await _run()


async def test_safety_stop_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _response("", finish=types.FinishReason.SAFETY))
    with pytest.raises(NonRetryableError, match="SAFETY"):
        await _run()
