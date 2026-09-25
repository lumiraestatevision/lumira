"""Auswahl des LLM-Anbieters (BLV_PROVIDER): anthropic (Claude), gemini (Google) oder
ollama (lokales Modell).

Alle bekommen denselben Prompt und liefern dasselbe Schema (``BLVExtraction``) –
der Rest der Pipeline merkt keinen Unterschied.
"""

from __future__ import annotations

from lumira_blv.config import BLVSettings
from lumira_blv.logic import claude, gemini, ollama
from lumira_blv.logic.extraction import BLVExtraction


def _require_key(settings: BLVSettings) -> str:
    if settings.api_key is None:
        raise RuntimeError(f"Kein API-Key für Anbieter '{settings.blv_provider}'")
    return settings.api_key


async def extract(pdf: bytes, settings: BLVSettings) -> tuple[BLVExtraction, str]:
    """Liefert (Ergebnis, tatsächlich genutztes Modell)."""
    match settings.blv_provider:
        case "anthropic":
            extraction = await claude.extract_with_claude(
                pdf,
                api_key=_require_key(settings),
                model=settings.model,
                use_fallbacks=settings.blv_llm_fallbacks,
            )
            return extraction, settings.model
        case "gemini":
            return await gemini.extract_with_gemini(
                pdf, api_key=_require_key(settings), models=settings.gemini_models
            )
        case "ollama":
            return await ollama.extract_with_ollama(
                pdf,
                base_url=settings.ollama_url,
                model=settings.blv_ollama_model,
                num_ctx=settings.blv_ollama_num_ctx,
                think=settings.blv_ollama_think,
                timeout_s=settings.blv_ollama_timeout_s,
            )
