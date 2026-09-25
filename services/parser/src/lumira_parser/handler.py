"""project.created → plan.parsed"""

from __future__ import annotations

import asyncio
from pathlib import PurePath
from typing import cast

from lumira_parser.config import ParserSettings
from lumira_parser.logic import RawPlan, parse_dxf, parse_pdf
from lumira_shared import (
    Artifact,
    Event,
    EventType,
    NonRetryableError,
    ServiceContext,
    artifact_key,
)
from lumira_shared.models import ParsedPlan, SourceFormat

STEP = "parser"


def detect_format(key: str) -> SourceFormat:
    suffix = PurePath(key).suffix.lower().lstrip(".")
    try:
        return SourceFormat(suffix)
    except ValueError as exc:
        raise NonRetryableError(f"Unbekanntes Grundrissformat: '{suffix}'") from exc


def parse(data: bytes, fmt: SourceFormat, settings: ParserSettings) -> RawPlan:
    match fmt:
        case SourceFormat.DXF:
            return parse_dxf(data)
        case SourceFormat.PDF:
            return parse_pdf(
                data, assumed_scale=settings.pdf_assumed_scale, render_dpi=settings.pdf_render_dpi
            )
        case SourceFormat.DWG:
            # STUB: DWG ist ein proprietäres Binärformat. Geplant: Konvertierung nach DXF mit
            # dem ODA File Converter oder LibreDWG (dwg2dxf) im Container, dann parse_dxf().
            raise NonRetryableError("DWG wird noch nicht unterstützt – bitte als DXF exportieren")


async def handle_project_created(event: Event, ctx: ServiceContext) -> Event:
    settings = cast(ParserSettings, ctx.settings)
    source_key = event.artifacts[Artifact.FLOOR_PLAN_SOURCE]
    fmt = detect_format(source_key)
    data = await ctx.storage.get_bytes(source_key)

    # CPU-lastig → nicht im Event-Loop ausführen.
    raw = await asyncio.to_thread(parse, data, fmt, settings)

    artifacts: dict[str, str] = {}
    page_image_key = None
    if raw.page_png:
        page_image_key = artifact_key(event.project_id, STEP, "page-1.png")
        await ctx.storage.put_bytes(page_image_key, raw.page_png, content_type="image/png")
        artifacts[Artifact.PLAN_PAGE_IMAGE] = page_image_key

    parsed = ParsedPlan(
        project_id=event.project_id,
        source_key=source_key,
        source_format=fmt,
        page=0 if fmt is SourceFormat.PDF else None,
        page_count=raw.page_count,
        width_mm=raw.width_mm,
        height_mm=raw.height_mm,
        plan_scale=raw.plan_scale,
        segments=raw.segments,
        filled_areas=raw.filled_areas,
        curves=raw.curves,
        texts=raw.texts,
        page_image_key=page_image_key,
        notes=raw.notes,
    )
    parsed_key = artifact_key(event.project_id, STEP, "parsed_plan.json")
    await ctx.storage.put_json(parsed_key, parsed)
    artifacts[Artifact.PARSED_PLAN] = parsed_key

    counts = {
        "segments": len(raw.segments),
        "filled_areas": len(raw.filled_areas),
        "curves": len(raw.curves),
        "texts": len(raw.texts),
    }
    ctx.log.info("plan.parsed", format=str(fmt), scale=raw.plan_scale, **counts)
    return event.follow_up(
        EventType.PLAN_PARSED,
        producer=settings.service_name,
        artifacts=artifacts,
        data={"format": str(fmt), "scale": raw.plan_scale, **counts},
    )
