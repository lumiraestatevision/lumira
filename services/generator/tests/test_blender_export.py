"""Echter Blender-Export (FBX + glTF). Läuft nur, wo Blender installiert ist – im
generator-Container bzw. im CI-Job "blender-export":

    docker run --rm -w /app/services/generator lumira/generator:dev pytest -m blender
"""

from __future__ import annotations

import shutil
import struct
import uuid
from pathlib import Path

import pytest

from lumira_generator.config import GeneratorSettings
from lumira_generator.logic.blender_runner import run_blender
from lumira_generator.logic.scene import build_scene
from lumira_shared.models import (
    BLVResult,
    EquipmentVariant,
    FloorPlan,
    Material,
    MaterialCategory,
    Opening,
    OpeningType,
    Point2D,
    Room,
    RoomType,
    SourceFormat,
    Wall,
)

settings = GeneratorSettings()
pytestmark = [
    pytest.mark.blender,
    pytest.mark.skipif(
        shutil.which(settings.blender_bin) is None, reason="Blender nicht installiert"
    ),
]


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[Point2D]:
    return [Point2D(x=x0, y=y0), Point2D(x=x1, y=y0), Point2D(x=x1, y=y1), Point2D(x=x0, y=y1)]


def _scene() -> dict:
    corners = [
        (0, 0, 10_000, 0),
        (10_000, 0, 10_000, 8_000),
        (10_000, 8_000, 0, 8_000),
        (0, 8_000, 0, 0),
    ]
    walls = [
        Wall(
            id=f"w{i}",
            start=Point2D(x=a, y=b),
            end=Point2D(x=c, y=d),
            thickness_mm=240,
            is_exterior=True,
        )
        for i, (a, b, c, d) in enumerate(corners)
    ]
    walls.append(
        Wall(id="w4", start=Point2D(x=6_000, y=0), end=Point2D(x=6_000, y=8_000), thickness_mm=115)
    )
    plan = FloorPlan(
        project_id=uuid.uuid4(),
        source_key="plan.dxf",
        source_format=SourceFormat.DXF,
        walls=walls,
        openings=[
            Opening(
                id="win",
                type=OpeningType.WINDOW,
                wall_id="w0",
                offset_mm=1_000,
                width_mm=1_260,
                height_mm=1_385,
                sill_height_mm=900,
            ),
            Opening(
                id="door",
                type=OpeningType.DOOR,
                wall_id="w4",
                offset_mm=3_000,
                width_mm=885,
                height_mm=2_010,
            ),
        ],
        rooms=[
            Room(id="wohnen", polygon=_rect(0, 0, 6_000, 8_000), room_type=RoomType.LIVING),
            Room(id="bad", polygon=_rect(6_000, 0, 10_000, 8_000), room_type=RoomType.BATHROOM),
        ],
    )
    blv = BLVResult(
        project_id=plan.project_id,
        materials=[
            Material(
                id="eiche",
                category=MaterialCategory.FLOORING,
                name="Eichenparkett",
                color_hex="#B8894F",
            ),
            Material(
                id="fliese",
                category=MaterialCategory.TILES,
                name="Bodenfliesen",
                color_hex="#BDBAB3",
                room_types=[RoomType.BATHROOM],
            ),
        ],
        variants=[EquipmentVariant(name="Standard", material_ids=["eiche", "fliese"])],
    )
    return build_scene(plan, blv)


async def test_real_blender_exports_fbx_and_gltf(tmp_path: Path) -> None:
    result = await run_blender(
        _scene(),
        blender_bin=settings.blender_bin,
        script=settings.blender_script,
        workdir=tmp_path,
        timeout_s=300,
    )

    assert result.stats["walls"] == 5
    assert result.stats["rooms"] == 2
    assert result.stats["openings"] == 2
    assert result.stats["blender"].startswith("4.")

    glb = result.glb.read_bytes()
    magic, version, length = struct.unpack("<4sII", glb[:12])
    assert (magic, version, length) == (b"glTF", 2, len(glb))
    assert result.fbx.read_bytes().startswith(b"Kaydara FBX Binary")
