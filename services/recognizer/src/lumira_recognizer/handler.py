"""plan.parsed → plan.recognized"""

from __future__ import annotations

import asyncio
from typing import cast

from lumira_recognizer.config import RecognizerSettings
from lumira_recognizer.logic.cad import recognize_cad
from lumira_recognizer.logic.detector import recognize
from lumira_recognizer.logic.device import resolve_device
from lumira_recognizer.logic.ocr import read_texts
from lumira_recognizer.logic.scale import check_scale, rescale
from lumira_shared import Artifact, Event, EventType, ServiceContext, artifact_key
from lumira_shared.models import FloorPlan, ParsedPlan

STEP = "recognizer"


def _recognize_once(parsed: ParsedPlan, settings: RecognizerSettings) -> FloorPlan:
    # CAD-Pläne mit gefüllten Wänden exakt auslesen, sonst das einfache Linienverfahren.
    plan = recognize_cad(parsed)
    if plan is None:
        plan = recognize(
            parsed,
            min_wall_length_mm=settings.min_wall_length_mm,
            wall_thickness_mm=settings.default_wall_thickness_mm,
        )
    return plan


def recognize_plan(parsed: ParsedPlan, settings: RecognizerSettings) -> FloorPlan:
    """Erkennen und den Maßstab an den Flächenangaben im Plan prüfen (ggf. einmal korrigieren)."""
    plan = _recognize_once(parsed, settings)
    check = check_scale(plan, parsed.plan_scale)
    if check.factor is not None:
        plan = _recognize_once(rescale(parsed, check.factor), settings)
    plan.metadata["scale_check"] = check.note
    return plan


async def handle_plan_parsed(event: Event, ctx: ServiceContext) -> Event:
    settings = cast(RecognizerSettings, ctx.settings)
    parsed = await ctx.storage.get_model(event.artifacts[Artifact.PARSED_PLAN], ParsedPlan)
    device = await asyncio.to_thread(resolve_device, settings.recognizer_device)

    # Gescannter Plan ohne Textebene → Beschriftungen per OCR aus dem Seitenbild lesen.
    if not parsed.texts and parsed.page_image_key:
        png = await ctx.storage.get_bytes(parsed.page_image_key)
        parsed.texts = await asyncio.to_thread(
            read_texts,
            png,
            page_width_mm=parsed.width_mm,
            page_height_mm=parsed.height_mm,
            languages=tuple(settings.ocr_languages.split(",")),
            device=device,
        )
        ctx.log.info("recognizer.ocr_done", texts=len(parsed.texts), device=device)

    plan = await asyncio.to_thread(recognize_plan, parsed, settings)
    key = artifact_key(event.project_id, STEP, "floor_plan.json")
    await ctx.storage.put_json(key, plan)

    ctx.log.info(
        "plan.recognized",
        walls=len(plan.walls),
        rooms=len(plan.rooms),
        openings=len(plan.openings),
        device=device,
    )
    return event.follow_up(
        EventType.PLAN_RECOGNIZED,
        producer=settings.service_name,
        artifacts={Artifact.RECOGNIZED_PLAN: key},
        data={
            "walls": len(plan.walls),
            "rooms": len(plan.rooms),
            "openings": len(plan.openings),
            "device": device,
            "method": plan.metadata.get("recognizer", ""),
        },
    )
