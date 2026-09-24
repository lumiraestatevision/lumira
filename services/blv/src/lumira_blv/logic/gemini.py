"""Leistungsverzeichnis per Google Gemini auswerten.

WICHTIG – Gratistarif (Stand der Nutzungsbedingungen 09/2026):
- Nur für lokale Entwicklung: Anwendungen für Nutzer im EWR, in der Schweiz oder im UK
  dürfen laut Gemini-API-Bedingungen ausschließlich kostenpflichtige Dienste nutzen.
- Eingaben und Ausgaben dürfen von Google (auch durch Menschen) gelesen und zur
  Produktverbesserung genutzt werden → keine vertraulichen oder personenbezogenen Daten.

- Das PDF geht direkt an die API (Gemini liest Text und Seitenlayout).
- Strukturierte Ausgabe über ``response_json_schema`` im Schema ``BLVExtraction``.
"""

from __future__ import annotations

from google import genai
from google.genai import errors, types

from lumira_blv.logic.extraction import BLVExtraction
from lumira_blv.logic.prompts import SYSTEM_PROMPT, USER_INSTRUCTION
from lumira_shared import NonRetryableError, get_logger

log = get_logger(__name__)

_TIMEOUT_MS = 600_000
_MAX_OUTPUT_TOKENS = 32_768
_OK_FINISH = {types.FinishReason.STOP, types.FinishReason.FINISH_REASON_UNSPECIFIED, None}


async def extract_with_gemini(pdf: bytes, *, api_key: str, model: str) -> BLVExtraction:
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=_TIMEOUT_MS))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_json_schema=BLVExtraction.model_json_schema(),
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        # Keine Tools im Einsatz → automatisches Function Calling explizit aus.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=pdf, mime_type="application/pdf"),
                USER_INSTRUCTION,
            ],
            config=config,
        )
    except errors.ClientError as exc:
        if exc.code == 429:
            # Kontingent des Gratistarifs erschöpft → später erneut versuchen (Consumer-Retry).
            raise
        # 400/401/403/404: falscher Key, unbekanntes Modell, ungültige Anfrage.
        raise NonRetryableError(f"Gemini-API lehnt die Anfrage ab: {exc}") from exc
    finally:
        await client.aio.aclose()
    # errors.ServerError (5xx) und Netzwerkfehler bleiben normale Exceptions → Retry.

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
