"""Leistungsverzeichnis per Google Gemini auswerten.

WICHTIG – Gratistarif (Stand der Nutzungsbedingungen 09/2026):
- Nur für lokale Entwicklung: Anwendungen für Nutzer im EWR, in der Schweiz oder im UK
  dürfen laut Gemini-API-Bedingungen ausschließlich kostenpflichtige Dienste nutzen.
- Eingaben und Ausgaben dürfen von Google (auch durch Menschen) gelesen und zur
  Produktverbesserung genutzt werden → keine vertraulichen oder personenbezogenen Daten.

- Das PDF geht direkt an die API (Gemini liest Text und Seitenlayout).
- Strukturierte Ausgabe über ``response_json_schema`` im Schema ``BLVExtraction``.
- Modellkette: Ist ein Modell überlastet (503), sein Kontingent erschöpft (429) oder für das
  Konto nicht freigeschaltet (404), wird das nächste Modell versucht.
"""

from __future__ import annotations

from collections.abc import Sequence

from google import genai
from google.genai import errors, types

from lumira_blv.logic.extraction import BLVExtraction
from lumira_blv.logic.prompts import SYSTEM_PROMPT, USER_INSTRUCTION
from lumira_shared import NonRetryableError, get_logger

log = get_logger(__name__)

_TIMEOUT_MS = 600_000
_MAX_OUTPUT_TOKENS = 32_768
_OK_FINISH = {types.FinishReason.STOP, types.FinishReason.FINISH_REASON_UNSPECIFIED, None}
# 429: Kontingent je Modell erschöpft, 404: Modell für dieses Konto nicht freigeschaltet
_TRY_NEXT_CLIENT_CODES = {404, 429}


class GeminiUnavailableError(RuntimeError):
    """Alle Modelle überlastet oder Kontingent erschöpft – später erneut versuchen."""


async def extract_with_gemini(
    pdf: bytes, *, api_key: str, models: Sequence[str]
) -> tuple[BLVExtraction, str]:
    """Probiert die Modelle der Reihe nach. Liefert (Ergebnis, tatsächlich genutztes Modell)."""
    if not models:
        raise ValueError("Mindestens ein Gemini-Modell angeben")
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_json_schema=BLVExtraction.model_json_schema(),
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        # Keine Tools im Einsatz → automatisches Function Calling explizit aus.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents: types.ContentListUnion = [
        types.Part.from_bytes(data=pdf, mime_type="application/pdf"),
        USER_INSTRUCTION,
    ]
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=_TIMEOUT_MS))
    failures: list[str] = []
    retryable = False
    try:
        for model in models:
            try:
                response = await client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except errors.ServerError as exc:  # 503 Überlastung, 500 …
                retryable = True
                failures.append(f"{model}: {exc.code} {exc.status}")
                log.warning("blv.gemini_model_unavailable", model=model, code=exc.code)
                continue
            except errors.ClientError as exc:
                if exc.code not in _TRY_NEXT_CLIENT_CODES:
                    # 400/401/403: falscher Key oder ungültige Anfrage – ein anderes Modell hilft nicht.
                    raise NonRetryableError(f"Gemini-API lehnt die Anfrage ab: {exc}") from exc
                retryable = retryable or exc.code == 429
                failures.append(f"{model}: {exc.code} {exc.status}")
                log.warning("blv.gemini_model_unavailable", model=model, code=exc.code)
                continue
            return _parse(response, model), model
    finally:
        await client.aio.aclose()

    summary = "; ".join(failures)
    if retryable:
        # Normale Exception → der Stream-Consumer stellt das Event später erneut zu.
        raise GeminiUnavailableError(f"Kein Gemini-Modell verfügbar ({summary})")
    raise NonRetryableError(f"Keines der Gemini-Modelle ist für dieses Konto verfügbar ({summary})")


def _parse(response: types.GenerateContentResponse, model: str) -> BLVExtraction:
    usage = response.usage_metadata
    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate else None
    log.info(
        "blv.llm_usage",
        provider="gemini",
        model=response.model_version or model,
        finish_reason=str(finish),
        input_tokens=usage.prompt_token_count if usage else None,
        output_tokens=usage.candidates_token_count if usage else None,
        thinking_tokens=usage.thoughts_token_count if usage else None,
    )

    if candidate is None:
        reason = response.prompt_feedback.block_reason if response.prompt_feedback else None
        raise NonRetryableError(f"Gemini hat keine Antwort geliefert (block_reason={reason})")
    if finish == types.FinishReason.MAX_TOKENS:
        raise NonRetryableError("Antwort abgeschnitten (MAX_TOKENS) – Dokument aufteilen")
    if finish not in _OK_FINISH:
        raise NonRetryableError(f"Gemini hat die Antwort abgebrochen (finish_reason={finish})")

    return BLVExtraction.model_validate_json(response.text or "")
