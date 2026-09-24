"""rooms.classified → blv.processed"""

from __future__ import annotations

from typing import cast

import pypdfium2 as pdfium  # Apache-2.0/BSD-3 – bewusst nicht PyMuPDF (AGPL)

from lumira_blv.config import BLVSettings
from lumira_blv.logic import llm
from lumira_blv.logic.extraction import to_blv_result
from lumira_blv.logic.stub import default_result
from lumira_shared import (
    Artifact,
    Event,
    EventType,
    NonRetryableError,
    ServiceContext,
    artifact_key,
)
from lumira_shared.models import BLVResult

STEP = "blv"


def check_pdf(data: bytes, settings: BLVSettings) -> int:
    if len(data) > settings.blv_max_pdf_mb * 1024 * 1024:
        raise NonRetryableError(f"LV-PDF größer als {settings.blv_max_pdf_mb} MB")
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise NonRetryableError("LV-PDF ist passwortgeschützt") from exc
        raise NonRetryableError(f"LV-PDF nicht lesbar: {exc}") from exc
    pages = len(doc)
    doc.close()
    if pages > settings.blv_max_pages:
        raise NonRetryableError(f"LV-PDF hat {pages} Seiten (max. {settings.blv_max_pages})")
    return pages


async def evaluate(event: Event, ctx: ServiceContext) -> BLVResult:
    settings = cast(BLVSettings, ctx.settings)
    source_key = event.artifacts.get(Artifact.BLV_SOURCE)

    if source_key is None:
        return default_result(
            event.project_id,
            source_key=None,
            note="Kein Leistungsverzeichnis hochgeladen – Standardausstattung (STUB)",
        )
    if not settings.use_llm or settings.api_key is None:
        return default_result(
            event.project_id,
            source_key=source_key,
            note="Stub-Modus: LV nicht ausgewertet (kein ANTHROPIC_API_KEY)",
        )

    pdf = await ctx.storage.get_bytes(source_key)
    pages = check_pdf(pdf, settings)
    ctx.log.info("blv.llm_start", model=settings.blv_model, pages=pages)
    extraction = await llm.extract_with_claude(
        pdf,
        api_key=settings.api_key,
        model=settings.blv_model,
        use_fallbacks=settings.blv_llm_fallbacks,
    )
    return to_blv_result(
        extraction,
        project_id=event.project_id,
        source_key=source_key,
        extracted_by=settings.blv_model,
    )


async def handle_rooms_classified(event: Event, ctx: ServiceContext) -> Event:
    result = await evaluate(event, ctx)
    key = artifact_key(event.project_id, STEP, "blv_result.json")
    await ctx.storage.put_json(key, result)

    ctx.log.info("blv.processed", extracted_by=result.extracted_by, materials=len(result.materials))
    return event.follow_up(
        EventType.BLV_PROCESSED,
        producer=ctx.settings.service_name,
        artifacts={Artifact.BLV_RESULT: key},
        data={
            "extracted_by": result.extracted_by,
            "materials": len(result.materials),
            "variants": [v.name for v in result.variants],
        },
    )
