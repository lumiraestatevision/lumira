from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from lumira_parser.handler import handle_project_created
from lumira_parser.samples import sample_dxf, sample_pdf
from lumira_shared import Artifact, EventType, NonRetryableError, ServiceContext, project_created
from lumira_shared.models import ParsedPlan, SourceFormat


async def _run(ctx: ServiceContext, filename: str, data: bytes):
    project_id = uuid.uuid4()
    key = f"projects/{project_id}/upload/{filename}"
    await ctx.storage.put_bytes(key, data)
    return await handle_project_created(project_created(project_id, floor_plan_key=key), ctx)


async def test_dxf_produces_plan_parsed(ctx: ServiceContext) -> None:
    event = await _run(ctx, "floor_plan.dxf", sample_dxf())

    assert event.type is EventType.PLAN_PARSED
    assert event.data["format"] == "dxf"
    assert Artifact.PLAN_PAGE_IMAGE not in event.artifacts
    parsed = await ctx.storage.get_model(event.artifacts[Artifact.PARSED_PLAN], ParsedPlan)
    assert parsed.source_format is SourceFormat.DXF
    assert len(parsed.segments) == 7
    assert parsed.project_id == event.project_id


async def test_pdf_also_stores_page_image(ctx: ServiceContext) -> None:
    event = await _run(ctx, "floor_plan.pdf", sample_pdf())

    image_key = event.artifacts[Artifact.PLAN_PAGE_IMAGE]
    assert (await ctx.storage.get_bytes(image_key)).startswith(b"\x89PNG")
    parsed = await ctx.storage.get_model(event.artifacts[Artifact.PARSED_PLAN], ParsedPlan)
    assert parsed.page_image_key == image_key


async def test_dwg_is_rejected_without_retry(ctx: ServiceContext) -> None:
    with pytest.raises(NonRetryableError, match="DWG"):
        await _run(ctx, "floor_plan.dwg", b"AC1032")


def test_health() -> None:
    from lumira_parser.main import app

    assert TestClient(app).get("/health").json()["service"] == "parser"
