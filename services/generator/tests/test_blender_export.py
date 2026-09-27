"""Echter Blender-Export (FBX + glTF). Läuft nur, wo Blender installiert ist – im
generator-Container bzw. im CI-Job "blender-export":

    docker run --rm -w /app/services/generator lumira/generator:dev pytest -m blender
"""

from __future__ import annotations

import json
import shutil
import struct
import uuid
import zlib
from pathlib import Path

import pytest

from lumira_generator.config import GeneratorSettings
from lumira_generator.logic.blender_runner import BakeOptions, run_blender
from lumira_generator.logic.scene import build_scene
from lumira_shared.models import (
    BLVResult,
    DoorSwing,
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
    Stair,
    Wall,
)

settings = GeneratorSettings()
pytestmark = [
    pytest.mark.blender,
    pytest.mark.skipif(
        shutil.which(settings.blender_bin) is None, reason="Blender nicht installiert"
    ),
]


def _png(path: Path, rgb: tuple[int, int, int], size: int = 8) -> None:
    """Winziges einfarbiges PNG ohne Bildbibliothek – als Ersatz für die Fototexturen."""
    row = b"\x00" + bytes(rgb) * size
    raw = zlib.compress(row * size)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    )


def _fake_textures(root: Path) -> Path:
    folder = root / "textures" / "oak_wood_planks"
    folder.mkdir(parents=True)
    for kind, rgb in (
        ("diff", (150, 100, 60)),
        ("nor_gl", (128, 128, 255)),
        ("rough", (120, 120, 120)),
    ):
        _png(folder / f"oak_wood_planks_{kind}_2k.png", rgb)
    return root / "textures"


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
    # CAD-Fall: L-förmige Wandfläche mit exaktem Grundriss + Öffnung über ein ganzes Wandstück
    l_shape = [(0, 9_000), (3_000, 9_000), (3_000, 9_300), (300, 9_300), (300, 11_000), (0, 11_000)]
    walls.append(
        Wall(
            id="w5",
            start=Point2D(x=0, y=9_150),
            end=Point2D(x=3_000, y=9_150),
            thickness_mm=300,
            footprint=[Point2D(x=x, y=y) for x, y in l_shape],
        )
    )
    walls.append(
        Wall(
            id="gap",
            start=Point2D(x=3_000, y=9_150),
            end=Point2D(x=4_000, y=9_150),
            thickness_mm=300,
        )
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
                swing=DoorSwing.LEFT,
                opens_to="right",
            ),
            Opening(
                id="gap_window",
                type=OpeningType.WINDOW,
                wall_id="gap",
                offset_mm=0,
                width_mm=1_000,
                height_mm=1_385,
                sill_height_mm=900,
            ),
        ],
        rooms=[
            Room(id="wohnen", polygon=_rect(0, 0, 6_000, 8_000), room_type=RoomType.LIVING),
            Room(id="bad", polygon=_rect(6_000, 0, 10_000, 8_000), room_type=RoomType.BATHROOM),
        ],
        # gerade Treppe im Wohnen: 16 Auftritte à 250 mm
        stairs=[
            Stair(
                id="treppe",
                steps=[_rect(500, 1_000 + k * 250, 1_500, 1_250 + k * 250) for k in range(16)],
                rise_mm=2_750 / 16,
                walking_line=[Point2D(x=1_000, y=1_125), Point2D(x=1_000, y=4_875)],
                outline=_rect(500, 1_000, 1_500, 5_000),
            )
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
                format="60 x 60 cm",
                room_types=[RoomType.BATHROOM],
            ),
            Material(
                id="wohnfliese",
                category=MaterialCategory.TILES,
                name="Feinsteinzeug Wohnen",
                color_hex="#8C8780",
                format="60 x 60 cm",
                room_types=[RoomType.LIVING],
            ),
        ],
        variants=[
            EquipmentVariant(name="Standard", material_ids=["eiche", "fliese"]),
            EquipmentVariant(name="Fliesen im Wohnen", material_ids=["fliese", "wohnfliese"]),
        ],
    )
    return build_scene(plan, blv)


def _gltf_json(glb: bytes) -> dict:
    (chunk_length,) = struct.unpack("<I", glb[12:16])
    return json.loads(glb[20 : 20 + chunk_length])


async def test_real_blender_exports_fbx_and_gltf(tmp_path: Path) -> None:
    result = await run_blender(
        _scene(),
        blender_bin=settings.blender_bin,
        script=settings.blender_script,
        workdir=tmp_path,
        timeout_s=300,
        texture_dir=_fake_textures(tmp_path),
        bake=BakeOptions(samples=4, samples_gpu=4, lightmap_px=256),
    )

    assert result.stats["walls"] == 7
    assert result.stats["rooms"] == 2
    assert result.stats["openings"] == 3
    assert result.stats["blender"].startswith("4.")
    assert "oak_wood_planks" in result.stats["textures"]

    glb = result.glb.read_bytes()
    magic, version, length = struct.unpack("<4sII", glb[:12])
    assert (magic, version, length) == (b"glTF", 2, len(glb))
    gltf = _gltf_json(glb)
    names = {node["name"] for node in gltf["nodes"]}
    # Tür: Zarge + Blatt; Fenster: Rahmen + Glas (auch im CAD-Öffnungswandstück)
    assert {"Tuerblatt_door", "Zarge_door_O", "Glas_win", "Glas_gap_window"} <= names
    materials = {m["name"] for m in gltf["materials"]}
    assert {"Eichenparkett", "Bodenfliesen", "Glas", "Wandkrone"} <= materials
    assert len(gltf["images"]) >= 3  # Holz (Farbe, Normal, Rauheit) + erzeugte Fliese/Putz
    glass = next(m for m in gltf["materials"] if m["name"] == "Glas")
    assert glass["alphaMode"] == "BLEND"
    # Treppe: 16 Stufenblöcke, Auftritt im Bodenbelag des Raums, Decke darüber mit Öffnung
    assert (result.stats["stairs"], result.stats["steps"]) == (1, 16)
    assert {"Stufe_treppe_00", "Stufe_treppe_15"} <= names
    assert "Treppe" in materials

    def positions(node_name: str) -> list[dict]:
        node = next(n for n in gltf["nodes"] if n["name"] == node_name)
        primitives = gltf["meshes"][node["mesh"]]["primitives"]
        return [gltf["accessors"][p["attributes"]["POSITION"]] for p in primitives]

    assert sum(a["count"] for a in positions("Ceiling_bad")) == 4  # schlichte Deckenfläche
    assert sum(a["count"] for a in positions("Ceiling_wohnen")) > 16  # Platte mit Öffnung
    # glTF: y nach oben – letzte Stufe auf Geschosshöhe 2,75 m
    top = max(a["max"][1] for a in positions("Stufe_treppe_15"))
    assert top == pytest.approx(2.75, abs=0.001)

    # Eingebranntes Licht: eine Lightmap als Occlusion auf der zweiten UV-Map der Raumhülle,
    # lose Möbel und Türen ohne (sie werden im Viewer normal beleuchtet)
    lightmap = result.stats["lightmap"]
    assert lightmap["px"] == 256
    assert lightmap["receivers"] >= 7 + 2 + 2 + 16  # Wände, Böden, Decken, Stufen
    extras = gltf["scenes"][0]["extras"]["lumira_lightmap"]
    assert extras["encoding"] == "reinhard"
    assert extras["key"] > 0
    by_name = {m["name"]: m for m in gltf["materials"]}
    for name in ("Eichenparkett", "Bodenfliesen", "Decke", "Treppe", "Wandkrone"):
        occlusion = by_name[name]["occlusionTexture"]
        assert occlusion["texCoord"] == 1
        assert gltf["images"][gltf["textures"][occlusion["index"]]["source"]]["name"] == "Lightmap"
    assert "occlusionTexture" not in by_name["Tür"]
    assert "occlusionTexture" not in by_name["Glas"]
    floor = next(n for n in gltf["nodes"] if n["name"].startswith("Floor_wohnen"))
    primitive = gltf["meshes"][floor["mesh"]]["primitives"][0]
    assert {"TEXCOORD_0", "TEXCOORD_1"} <= set(primitive["attributes"])

    # Ausstattungsvarianten: umschaltbarer Bodenbelag, Variantenmaterial mit Lightmap
    variants = [v["name"] for v in gltf["extensions"]["KHR_materials_variants"]["variants"]]
    assert sorted(variants) == ["Fliesen im Wohnen", "Standard"]
    mappings = primitive["extensions"]["KHR_materials_variants"]["mappings"]
    by_variant = {
        variants[i]: gltf["materials"][m["material"]] for m in mappings for i in m["variants"]
    }
    assert by_variant["Standard"]["name"] == "Eichenparkett"
    tiled = by_variant["Fliesen im Wohnen"]
    assert tiled["name"] == "Feinsteinzeug Wohnen"
    assert tiled["occlusionTexture"]["texCoord"] == 1
    # Texturmaßstab im Material (UVs in Metern): 60-cm-Fliese → 1/0,6 je Meter
    transform = tiled["pbrMetallicRoughness"]["baseColorTexture"]["extensions"][
        "KHR_texture_transform"
    ]
    assert transform["scale"][0] == pytest.approx(1 / 0.6, rel=0.01)
    assert result.stats["variants"] == ["Standard", "Fliesen im Wohnen"]
    assert result.fbx.read_bytes().startswith(b"Kaydara FBX Binary")
