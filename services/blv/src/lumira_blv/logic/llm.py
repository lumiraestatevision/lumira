"""Auswahl des LLM-Anbieters (BLV_PROVIDER): anthropic (Claude) oder gemini (Google).

Beide bekommen denselben Prompt und liefern dasselbe Schema (``BLVExtraction``) –
der Rest der Pipeline merkt keinen Unterschied.
"""

from __future__ import annotations

from lumira_blv.config import BLVSettings
from lumira_blv.logic import claude, gemini
from lumira_blv.logic.extraction import BLVExtraction


async def extract(pdf: bytes, settings: BLVSettings) -> tuple[BLVExtraction, str]:
    """Liefert (Ergebnis, tatsächlich genutztes Modell)."""
    api_key = settings.api_key
    if api_key is None:
        raise RuntimeError(f"Kein API-Key für Anbieter '{settings.blv_provider}'")
    match settings.blv_provider:
        case "anthropic":
            extraction = await claude.extract_with_claude(
                pdf, api_key=api_key, model=settings.model, use_fallbacks=settings.blv_llm_fallbacks
            )
            return extraction, settings.model
        case "gemini":
            return await gemini.extract_with_gemini(
                pdf, api_key=api_key, models=settings.gemini_models
            )
