from __future__ import annotations

import stat
import uuid
from pathlib import Path

import pytest

from lumira_generator.logic.blender_runner import BlenderError, run_blender
from lumira_generator.logic.scene import FALLBACK_FLOOR, build_scene
from lumira_shared import NonRetryableError
from lumira_shared.models import (
    BLVResult,
    EquipmentVariant,
    FloorPlan,
    Material,
    MaterialCategory,
    MaterialLocation,
    Opening,
    OpeningType,
    Point2D,
    Room,
    RoomType,
    SourceFormat,
    Wall,
)

SCRIPT = Path(__file__).resolve().parents[1] / "blender_scripts" / "build_scene.py"


def _plan() -> FloorPlan:
    square = [
        Point2D(x=0, y=0),
        Point2D(x=4_000, y=0),
        Point2D(x=4_000, y=3_000),
        Point2D(x=0, y=3_000),
    ]
    return FloorPlan(
        project_id=uuid.uuid4(),
        source_key="plan.dxf",
        source_format=SourceFormat.DXF,
        walls=[Wall(id="w1", start=square[0], end=square[1], thickness_mm=240)],
        openings=[
            Opening(
                id="o1",
                type=OpeningType.DOOR,
                wall_id="w1",
                offset_mm=500,
                width_mm=885,
                height_mm=2_010,
            )
        ],
        rooms=[
            Room(id="bad", polygon=square, room_type=RoomType.BATHROOM),
            Room(id="flur", polygon=square, room_type=RoomType.HALLWAY),
            Room(id="balkon", polygon=square, room_type=RoomType.BALCONY),
        ],
    )


def _blv(project_id: uuid.UUID) -> BLVResult:
    return BLVResult(
        project_id=project_id,
        materials=[
            Material(
                id="parkett",
                category=MaterialCategory.FLOORING,
                name="Parkett",
                color_hex="#B8894F",
                room_types=[RoomType.HALLWAY, RoomType.BATHROOM],
            ),
            Material(
                id="fliese",
                category=MaterialCategory.TILES,
                name="Fliese",
                color_hex="#BDBDBA",
                room_types=[RoomType.BATHROOM],
            ),
            Material(
                id="weiss", category=MaterialCategory.WALL_FINISH, name="Weiß", color_hex="#F1ECE1"
            ),
        ],
        variants=[EquipmentVariant(name="Standard", material_ids=["parkett", "fliese", "weiss"])],
    )


def test_scene_materials_follow_blv() -> None:
    plan = _plan()
    scene = build_scene(plan, _blv(plan.project_id))

    floors = {r["id"]: r["floor"]["name"] for r in scene["rooms"]}
    assert floors == {"bad": "Fliese", "flur": "Parkett", "balkon": FALLBACK_FLOOR["name"]}
    assert scene["variant"] == "Standard"
    assert scene["wall_finish"]["color"] == "#F1ECE1"
    [wall] = scene["walls"]
    assert wall["openings"] == [
        {
            "id": "o1",
            "type": "door",
            "offset": 500.0,
            "width": 885.0,
            "height": 2010.0,
            "sill": 0.0,
            "swing": None,
            "opens_to": None,
        }
    ]
    assert wall["footprint"] is None  # Quader aus Achse und Dicke


def test_scene_passes_exact_wall_footprint() -> None:
    plan = _plan()
    l_shape = [(0, 0), (3_000, 0), (3_000, 300), (300, 300), (300, 2_000), (0, 2_000)]
    plan.walls[0].footprint = [Point2D(x=x, y=y) for x, y in l_shape]

    [wall] = build_scene(plan, _blv(plan.project_id))["walls"]

    assert wall["footprint"] == [[float(x), float(y)] for x, y in l_shape]


def test_only_visible_interior_surfaces_are_used() -> None:
    """Die Fehler aus der ersten echten LV-Auswertung dürfen nicht im Modell landen."""
    plan = _plan()
    tiles, floor, wall = (
        MaterialCategory.TILES,
        MaterialCategory.FLOORING,
        MaterialCategory.WALL_FINISH,
    )
    materials = [
        Material(
            id="fassade",
            category=wall,
            name="Kratzputz",
            color_hex="#EEEEEE",
            location=MaterialLocation.EXTERIOR,
        ),
        Material(id="innen", category=wall, name="Raufaser weiß", color_hex="#F1EDE1"),
        Material(id="estrich", category=floor, name="Trockenestrich", is_final_surface=False),
        Material(
            id="vinyl",
            category=floor,
            name="Klickvinyl",
            color_hex="#A0825A",
            room_types=[RoomType.HALLWAY],
        ),
        Material(
            id="wandfliese", category=tiles, name="Wandfliesen Bad", room_types=[RoomType.BATHROOM]
        ),
        Material(
            id="bodenfliese",
            category=tiles,
            name="Bodenfliesen 60x60",
            room_types=[RoomType.BATHROOM],
        ),
    ]
    blv = BLVResult(
        project_id=plan.project_id,
        materials=materials,
        variants=[EquipmentVariant(name="Standard", material_ids=[m.id for m in materials])],
    )

    scene = build_scene(plan, blv)

    floors = {r["id"]: r["floor"]["name"] for r in scene["rooms"]}
    assert floors["bad"] == "Bodenfliesen 60x60"  # nicht die Wandfliese
    assert floors["flur"] == "Klickvinyl"  # nicht der Estrich darunter
    assert scene["wall_finish"]["name"] == "Raufaser weiß"  # nicht der Fassadenputz


def _fake_blender(tmp_path: Path, body: str) -> str:
    """Kleines Shell-Skript, das sich wie Blender verhält (Argumente nach '--')."""
    exe = tmp_path / "fake-blender"
    exe.write_text(
        "#!/usr/bin/env bash\n"
        'while [ "$1" != "--" ]; do shift; done; shift\n'
        'while [ $# -gt 0 ]; do case "$1" in --fbx) FBX=$2;; --glb) GLB=$2;; esac; shift 2; done\n'
        + body
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


async def test_runner_collects_outputs(tmp_path: Path) -> None:
    blender = _fake_blender(
        tmp_path, 'echo fbx > "$FBX"; echo glb > "$GLB"; echo \'LUMIRA_RESULT {"walls": 1}\'\n'
    )
    work = tmp_path / "work"
    work.mkdir()
    result = await run_blender(
        {"walls": []}, blender_bin=blender, script=SCRIPT, workdir=work, timeout_s=10
    )
    assert result.glb.read_text().strip() == "glb"
    assert result.stats == {"walls": 1}


async def test_runner_reports_blender_errors(tmp_path: Path) -> None:
    blender = _fake_blender(tmp_path, 'echo "Error: Python script failed"; exit 1\n')
    with pytest.raises(BlenderError, match="Python script failed"):
        await run_blender({}, blender_bin=blender, script=SCRIPT, workdir=tmp_path, timeout_s=10)


async def test_runner_timeout(tmp_path: Path) -> None:
    blender = _fake_blender(tmp_path, "sleep 5\n")
    with pytest.raises(BlenderError, match="abgebrochen"):
        await run_blender({}, blender_bin=blender, script=SCRIPT, workdir=tmp_path, timeout_s=0.3)


async def test_missing_blender_is_not_retryable(tmp_path: Path) -> None:
    with pytest.raises(NonRetryableError, match="nicht gefunden"):
        await run_blender(
            {}, blender_bin="/gibt/es/nicht", script=SCRIPT, workdir=tmp_path, timeout_s=1
        )


def test_standard_without_visible_floor_borrows_from_variant() -> None:
    """Echte LV-Auswertung: Standard = Estrich (Untergrund), Parkett nur als Variante."""
    plan = _plan()
    floor = MaterialCategory.FLOORING
    blv = BLVResult(
        project_id=plan.project_id,
        materials=[
            Material(id="estrich", category=floor, name="Zementestrich", is_final_surface=False),
            Material(id="parkett", category=floor, name="Eichenparkett", color_hex="#B8894F"),
        ],
        variants=[
            EquipmentVariant(name="Standard", material_ids=["estrich"]),
            EquipmentVariant(
                name="Parkett statt Estrich", material_ids=["parkett", "estrich"], is_default=False
            ),
        ],
    )

    floors = {r["id"]: r["floor"] for r in build_scene(plan, blv)["rooms"]}

    assert floors["flur"]["name"] == "Eichenparkett"
    assert floors["flur"]["kind"] == "texture"
    assert floors["flur"]["from_variant"] == "Parkett statt Estrich"


def test_door_and_window_colors_are_the_inside_ones() -> None:
    """Echte LV-Auswertung: Haustür anthrazit, Innentüren weiß, Fenster „weiß innen /
    anthrazit außen“ – im Innenraum müssen Türen und Rahmen weiß sein."""
    plan = _plan()
    door, window = MaterialCategory.DOOR, MaterialCategory.WINDOW
    blv = BLVResult(
        project_id=plan.project_id,
        materials=[
            Material(
                id="haustuer", category=door, name="Haustüre Alutürelement", color_hex="#383E42"
            ),
            Material(id="innen", category=door, name="Innentüren Röhrenspan", color_hex="#FFFFFF"),
            Material(
                id="fenster",
                category=window,
                name="Kunststofffenster",
                color="Weiß innen / Anthrazit RAL 7016 außen",
                color_hex="#383E42",
            ),
        ],
        variants=[EquipmentVariant(name="Standard", material_ids=["haustuer", "innen", "fenster"])],
    )

    scene = build_scene(plan, blv)

    assert scene["door_finish"]["color"] == "#FFFFFF"
    assert scene["window_frame"]["color"] == "#F4F4F2"
