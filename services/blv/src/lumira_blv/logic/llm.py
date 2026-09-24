"""Leistungsverzeichnis per Claude auswerten.

- Das PDF geht als Dokument an die API (Claude liest Text UND Tabellen/Layout).
- Strukturierte Ausgabe (``output_format``) liefert JSON exakt im Schema ``BLVExtraction``.
- Prompt Caching auf dem Dokument: Wiederholungen nach Fehlern sind deutlich günstiger.
- Streaming: große Dokumente und lange Antworten laufen nicht in HTTP-Timeouts.
- Server-seitiger Refusal-Fallback (optional, BLV_LLM_FALLBACKS).
"""

from __future__ import annotations

import base64
from typing import Any

import anthropic

from lumira_blv.logic.extraction import BLVExtraction
from lumira_shared import NonRetryableError, get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """\
Du wertest deutsche Bau- und Ausstattungsbeschreibungen bzw. Leistungsverzeichnisse (BLV/LV) \
für Wohnungen aus. Aus dem Ergebnis entsteht ein fotorealistisches 3D-Modell der Wohnung.

Erfasse alle sichtbaren Oberflächen und Ausstattungen: Bodenbeläge, Wandoberflächen, Fliesen, \
Decken, Sanitärobjekte, Innentüren, Fenster, Küche und Beleuchtung. Übernimm Hersteller, \
Produkt, Farbe, Oberfläche und Format so, wie sie im Dokument stehen, und zitiere die \
zugehörige Textstelle kurz in source_excerpt. Ordne Materialien über room_types den Räumen \
zu, für die das Dokument sie vorsieht; gilt ein Material überall, bleibt die Liste leer. \
Setze color_hex nur, wenn das Dokument eine eindeutige Farbe nennt (z. B. einen RAL-Ton).

Lege Ausstattungsvarianten (z. B. Standard, Komfort, Premium oder Sonderwünsche mit Aufpreis) \
als variants mit den IDs ihrer Materialien an. Kennt das Dokument nur eine Ausstattung, gibt \
es genau eine Variante "Standard". Übernimm nur Angaben, die im Dokument stehen; \
Unklarheiten gehören in notes."""

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


async def extract_with_claude(
    pdf: bytes, *, api_key: str, model: str, use_fallbacks: bool
) -> BLVExtraction:
    document: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf).decode("ascii"),
        },
        "title": "Leistungsverzeichnis",
        "cache_control": {"type": "ephemeral"},
    }
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 64_000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    document,
                    {"type": "text", "text": "Werte dieses Leistungsverzeichnis aus."},
                ],
            }
        ],
        "output_format": BLVExtraction,
    }
    if use_fallbacks:
        request |= {"betas": [_FALLBACK_BETA], "fallbacks": "default"}

    try:
        async with (
            anthropic.AsyncAnthropic(api_key=api_key, max_retries=3) as client,
            client.beta.messages.stream(**request) as stream,
        ):
            message = await stream.get_final_message()
    except (
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        anthropic.NotFoundError,
        anthropic.BadRequestError,
    ) as exc:
        # Wiederholen hilft nicht: falscher Key, falsches Modell, ungültige Anfrage.
        raise NonRetryableError(f"Claude-API lehnt die Anfrage ab: {exc}") from exc
    # RateLimitError, 5xx und Verbindungsfehler bleiben normale Exceptions → der Consumer
    # stellt das Event später erneut zu (zusätzlich zu den SDK-internen Retries).

    usage = message.usage
    log.info(
        "blv.llm_usage",
        model=message.model,
        stop_reason=message.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
    )

    if message.stop_reason == "refusal":
        raise NonRetryableError("Claude hat die Auswertung abgelehnt (stop_reason=refusal)")
    if message.stop_reason == "max_tokens":
        raise NonRetryableError("Antwort abgeschnitten (max_tokens) – Dokument aufteilen")

    parsed = getattr(message, "parsed_output", None)
    if isinstance(parsed, BLVExtraction):
        return parsed
    text = next((b.text for b in message.content if b.type == "text"), "")
    return BLVExtraction.model_validate_json(text)
