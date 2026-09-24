from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import pytest

from lumira_recognizer.handler import handle_plan_parsed
from lumira_recognizer.logic import ocr
from lumira_recognizer.logic.device import resolve_device
from lumira_shared import Artifact, EventType, ServiceContext, project_created
from lumira_shared.models import FloorPlan, ParsedPlan


async def test_handler_stores_floor_plan(ctx: ServiceContext, parsed_plan: ParsedPlan) -> None:
    parsed_key = f"projects/{parsed_plan.project_id}/parser/parsed_plan.json"
    await ctx.storage.put_json(parsed_key, parsed_plan)
    event = project_created(
        parsed_plan.project_id, floor_plan_key=parsed_plan.source_key
    ).follow_up(
        EventType.PLAN_PARSED, producer="parser", artifacts={Artifact.PARSED_PLAN: parsed_key}
    )

    result = await handle_plan_parsed(event, ctx)

    assert result.type is EventType.PLAN_RECOGNIZED
    assert result.data == {"walls": 7, "rooms": 4, "openings": 7, "device": "cpu"}
    plan = await ctx.storage.get_model(result.artifacts[Artifact.RECOGNIZED_PLAN], FloorPlan)
    assert len(plan.rooms) == 4


def test_cpu_preference_never_touches_cuda() -> None:
    assert resolve_device("cpu") == "cpu"


def test_auto_falls_back_to_cpu_without_gpu() -> None:
    # Die lokale Test-Umgebung nutzt CPU-PyTorch → auto muss auf CPU zurückfallen.
    import torch

    expected = "cuda" if torch.cuda.is_available() else "cpu"
    assert resolve_device("auto") == expected


def test_ocr_maps_pixels_to_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeReader:
        def readtext(self, image: Any) -> list[tuple[list[list[int]], str, float]]:
            return [
                ([[90, 40], [110, 40], [110, 60], [90, 60]], "Küche", 0.93),
                ([[0, 0], [5, 0], [5, 5], [0, 5]], "?", 0.1),  # zu unsicher → verworfen
            ]

    monkeypatch.setattr(ocr, "_reader", lambda languages, device: FakeReader())
    ok, png = cv2.imencode(".png", np.full((100, 200), 255, dtype=np.uint8))
    assert ok

    [item] = ocr.read_texts(
        png.tobytes(), page_width_mm=20_000, page_height_mm=10_000, languages=("de",), device="cpu"
    )
    assert item.text == "Küche"
    assert item.position.x == pytest.approx(10_000)  # 100 px von 200 px Breite
    assert item.position.y == pytest.approx(5_000)  # Bildmitte, y nach oben gedreht
