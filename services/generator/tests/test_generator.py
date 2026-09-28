from __future__ import annotations

import stat
import uuid
from pathlib import Path
from typing import Literal

import pytest

from lumira_generator.logic.blender_runner import BakeOptions, BlenderError, run_blender
from lumira_generator.logic.scene import FALLBACK_FLOOR, FALLBACK_OUTDOOR_FLOOR, build_scene
from lumira_shared import NonRetryableError
from lumira_shared.models import (
    BLVResult,
    Column,
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
    Stair,
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
            # Schwelle mit dem Belag des angrenzenden Raums (links der Wand: „bad“)
            "threshold": next(r["floor"] for r in scene["rooms"] if r["id"] == "bad"),
            "threshold_room": "bad",
        }
    ]
    assert scene["variants"] == []  # nur eine Variante im LV
    assert wall["footprint"] is None  # Quader aus Achse und Dicke


def _terrace_plan() -> FloorPlan:
    plan = _plan()
    deck = [
        Point2D(x=4_000, y=0),
        Point2D(x=6_000, y=0),
        Point2D(x=6_000, y=3_000),
        Point2D(x=4_000, y=3_000),
    ]
    plan.rooms.append(
        Room(
            id="terrasse",
            polygon=deck,
            label="Terrasse überdacht",
            room_type=RoomType.BALCONY,
            outdoor=True,
            roofed=True,
        )
    )
    plan.columns = [
        Column(id=f"c{n}", center=Point2D(x=6_050, y=y), size_mm=150)
        for n, y in enumerate((0, 3_000))
    ]
    return plan


def test_terrace_gets_decking_roof_and_columns() -> None:
    plan = _terrace_plan()
    scene = build_scene(plan, _blv(plan.project_id))

    terrace = next(r for r in scene["rooms"] if r["id"] == "terrasse")
    assert terrace["outdoor"] is True
    # ohne Terrassenbelag im LV: Holzdielen statt Estrich
    assert terrace["floor"]["kind"] == "texture"
    assert terrace["floor"]["name"] == FALLBACK_OUTDOOR_FLOOR["name"]
    # Dach über Belag und Stützen (+ Überstand)
    xs = [x for x, _ in terrace["roof"]]
    ys = [y for _, y in terrace["roof"]]
    assert min(xs) == 4_000
    assert max(xs) == pytest.approx(6_050 + 75 + 100)
    assert (min(ys), max(ys)) == (pytest.approx(-175), pytest.approx(3_175))
    assert [c["center"] for c in scene["columns"]] == [[6_050.0, 0.0], [6_050.0, 3_000.0]]
    # Innenräume ohne Dach, keine Möbel auf der Terrasse
    assert all(r["roof"] is None for r in scene["rooms"] if r["id"] != "terrasse")
    assert not any(f["room_id"] == "terrasse" for f in scene["fixtures"])


def test_terrace_floor_from_lv_exterior_material() -> None:
    plan = _terrace_plan()
    blv = _blv(plan.project_id)
    blv.materials.append(
        Material(
            id="wpc",
            category=MaterialCategory.FLOORING,
            location=MaterialLocation.EXTERIOR,
            name="Terrassenbelag WPC-Dielen",
            color_hex="#6B5A4A",
        )
    )
    blv.variants[0].material_ids.append("wpc")

    terrace = next(r for r in build_scene(plan, blv)["rooms"] if r["id"] == "terrasse")

    assert terrace["floor"]["name"] == "Terrassenbelag WPC-Dielen"
    assert terrace["floor"]["texture"] == "plank_flooring_04"


def test_scene_passes_exact_wall_footprint() -> None:
    plan = _plan()
    l_shape = [(0, 0), (3_000, 0), (3_000, 300), (300, 300), (300, 2_000), (0, 2_000)]
    plan.walls[0].footprint = [Point2D(x=x, y=y) for x, y in l_shape]

    [wall] = build_scene(plan, _blv(plan.project_id))["walls"]

    assert wall["footprint"] == [[float(x), float(y)] for x, y in l_shape]


def test_variants_switch_floors_and_skip_variants_without_effect() -> None:
    """LV mit Varianten → umschaltbare Beläge; Varianten ohne Wirkung (Balkon ohne Balkon,
    gleiche Beläge) erscheinen nicht."""
    plan = _plan()
    plan.rooms = [r for r in plan.rooms if r.id == "flur"]
    plan.stairs = [_stair()]
    materials = [
        Material(id="estrich", category=MaterialCategory.FLOORING, name="Estrich"),
        Material(
            id="parkett",
            category=MaterialCategory.FLOORING,
            name="Parkett Eiche",
            room_types=[RoomType.HALLWAY],
        ),
        Material(
            id="balkon",
            category=MaterialCategory.FLOORING,
            name="Betonwerkstein",
            room_types=[RoomType.BALCONY],
        ),
    ]
    blv = BLVResult(
        project_id=plan.project_id,
        materials=materials,
        variants=[
            EquipmentVariant(name="Standard", material_ids=["estrich"]),
            EquipmentVariant(name="Parkett statt Estrich", material_ids=["parkett"]),
            EquipmentVariant(name="Balkon", material_ids=["estrich", "balkon"]),
        ],
    )

    scene = build_scene(plan, blv)

    names = [v["name"] for v in scene["variants"]]
    assert names == ["Standard", "Parkett statt Estrich"]
    standard, parkett = scene["variants"]
    assert standard["floors"]["flur"]["name"] == "Estrich"
    assert parkett["floors"]["flur"]["name"] == "Parkett Eiche"
    assert parkett["floors"]["flur"]["kind"] == "texture"
    # Treppe am Antritt im Flur: Stufenbelag folgt dem Boden der Variante
    assert parkett["treads"]["treppe"]["name"] == "Parkett Eiche"


DOOR_SIDES: list[tuple[Literal["left", "right"], float]] = [("left", 500), ("right", 2_000)]


def test_doors_get_a_threshold_with_the_floor_they_open_into() -> None:
    """Ohne Schwelle klafft in der Wanddicke eine Lücke im Boden (Rundgang: Absturzkante)."""
    square = [Point2D(x=x, y=y) for x, y in ((0, 0), (4_000, 0), (4_000, 3_000), (0, 3_000))]
    below = [Point2D(x=x, y=y) for x, y in ((0, -3_240), (4_000, -3_240), (4_000, -240), (0, -240))]
    plan = FloorPlan(
        project_id=uuid.uuid4(),
        source_key="plan.dxf",
        source_format=SourceFormat.DXF,
        # Innenwand auf y = -120 zwischen Bad (oben, links der Wand) und Flur (unten)
        walls=[
            Wall(id="w", start=Point2D(x=0, y=-120), end=Point2D(x=4_000, y=-120), thickness_mm=240)
        ],
        openings=[
            Opening(
                id=f"o_{side}",
                type=OpeningType.DOOR,
                wall_id="w",
                offset_mm=offset,
                width_mm=885,
                height_mm=2_010,
                opens_to=side,
            )
            for side, offset in DOOR_SIDES
        ]
        + [
            Opening(
                id="fenster",
                type=OpeningType.WINDOW,
                wall_id="w",
                offset_mm=3_000,
                width_mm=800,
                height_mm=1_000,
                sill_height_mm=900,
            )
        ],
        rooms=[
            Room(id="bad", polygon=square, room_type=RoomType.BATHROOM),
            Room(id="flur", polygon=below, room_type=RoomType.HALLWAY),
        ],
    )

    [wall] = build_scene(plan, _blv(plan.project_id))["walls"]

    thresholds = {o["id"]: o["threshold"] and o["threshold"]["name"] for o in wall["openings"]}
    assert thresholds == {"o_left": "Fliese", "o_right": "Parkett", "fenster": None}


def _stair(steps: int = 10) -> Stair:
    def rect(y0: float) -> list[Point2D]:
        return [
            Point2D(x=x, y=y)
            for x, y in ((500, y0), (1_500, y0), (1_500, y0 + 250), (500, y0 + 250))
        ]

    return Stair(
        id="treppe",
        steps=[rect(250 + k * 250) for k in range(steps)],
        rise_mm=2_750 / steps,
        walking_line=[Point2D(x=1_000, y=375), Point2D(x=1_000, y=250 + steps * 250 - 125)],
        outline=[
            Point2D(x=500, y=250),
            Point2D(x=1_500, y=250),
            Point2D(x=1_500, y=250 + steps * 250),
            Point2D(x=500, y=250 + steps * 250),
        ],
    )


def test_stairs_use_lv_tread_or_the_floor_at_the_start() -> None:
    plan = _plan()
    plan.rooms = [r for r in plan.rooms if r.id == "flur"]
    plan.stairs = [_stair()]
    blv = _blv(plan.project_id)

    [stair] = build_scene(plan, blv)["stairs"]

    assert stair["tread"]["name"] == "Parkett"  # Boden der Diele am Antritt
    assert [s["top"] for s in stair["steps"]] == pytest.approx([275.0 * k for k in range(1, 11)])
    assert stair["floor_to_floor"] == 2_750
    assert stair["outline"][2] == [1_500.0, 2_750.0]

    blv.materials += [
        Material(
            id="gel", category=MaterialCategory.STAIRS, name="Stahlgeländer", color_hex="#333333"
        ),
        Material(id="stufe", category=MaterialCategory.STAIRS, name="Stufen Eiche massiv"),
    ]
    blv.variants[0].material_ids += ["gel", "stufe"]

    [stair] = build_scene(plan, blv)["stairs"]

    assert stair["tread"]["name"] == "Stufen Eiche massiv"  # Stufenbelag laut LV, kein Geländer
    assert stair["tread"]["kind"] == "texture"


def test_railing_on_open_sides_only() -> None:
    """Geländer an den offenen Seiten der Treppe; Antritt und Austritt bleiben frei."""
    plan = _plan()
    plan.stairs = [_stair()]  # x 500–1500, Lauf nach +y von y 250 bis 2750

    [stair] = build_scene(plan, _blv(plan.project_id))["stairs"]

    railing = stair["railing"]
    posts = railing["posts"]
    xs = sorted({round(p[0]) for p in posts})
    assert xs == [500, 1500]  # beide Wangen frei
    # nichts vor dem Antritt / nach dem Austritt (keine Stäbe auf den Querkanten)
    assert min(p[1] for p in posts) > 250
    assert max(p[1] for p in posts) < 2750
    heights = sorted({p[2] for p in posts})
    assert heights[0] == pytest.approx(275)
    assert heights[-1] == pytest.approx(2_750)
    # je Seite ein durchgehender Handlauf: n Stäbe -> n - 1 Stücke
    per_side = len(posts) // 2
    assert len(railing["rails"]) == 2 * (per_side - 1)
    assert stair["railing_finish"]["color"]

    # Wand an der linken Wange (x 300–500) → nur noch rechts ein Geländer
    plan.walls.append(
        Wall(id="w2", start=Point2D(x=400, y=0), end=Point2D(x=400, y=3_000), thickness_mm=200)
    )
    [stair] = build_scene(plan, _blv(plan.project_id))["stairs"]
    assert sorted({round(p[0]) for p in stair["railing"]["posts"]}) == [1500]


def test_tiled_stairs_get_solid_step_plates() -> None:
    """Muster1-LV: „Treppenbelag Feinsteinzeug anthrazit“ – Stufen ohne Fugenraster."""
    plan = _plan()
    plan.stairs = [_stair()]
    blv = _blv(plan.project_id)
    blv.materials.append(
        Material(
            id="stufe",
            category=MaterialCategory.STAIRS,
            name="Treppenbelag Feinsteinzeug anthrazit",
            color_hex="#404040",
        )
    )
    blv.variants[0].material_ids.append("stufe")

    [stair] = build_scene(plan, blv)["stairs"]

    assert stair["tread"] == {
        "name": "Treppenbelag Feinsteinzeug anthrazit",
        "color": "#404040",
        "kind": "plain",
        "roughness": 0.35,
    }


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
        'echo "$@" > "$(dirname "$0")/args.txt"\n'
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


@pytest.mark.parametrize(
    ("bake", "expected"),
    [
        (None, ""),
        (BakeOptions(samples=0, samples_gpu=512, lightmap_px=2048), ""),
        (
            BakeOptions(samples=64, samples_gpu=512, lightmap_px=2048),
            "--bake-samples 64 --bake-samples-gpu 512 --lightmap-px 2048",
        ),
    ],
)
async def test_runner_passes_bake_options(
    tmp_path: Path, bake: BakeOptions | None, expected: str
) -> None:
    """Licht einbrennen nur mit Samples > 0 – sonst baut Blender ohne Lightmap."""
    blender = _fake_blender(tmp_path, 'echo fbx > "$FBX"; echo glb > "$GLB"\n')
    work = tmp_path / "work"
    work.mkdir()

    await run_blender({}, blender_bin=blender, script=SCRIPT, workdir=work, timeout_s=10, bake=bake)

    args = (tmp_path / "args.txt").read_text()
    assert ("--bake-samples" in args) is bool(expected)
    assert expected in args


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

    scene = build_scene(plan, blv)
    floors = {r["id"]: r["floor"] for r in scene["rooms"]}

    assert floors["flur"]["name"] == "Eichenparkett"
    assert floors["flur"]["kind"] == "texture"
    assert floors["flur"]["from_variant"] == "Parkett statt Estrich"
    # Die Parkett-Variante sieht genauso aus wie die Grundansicht → kein Umschalter
    assert scene["variants"] == []

    # Einzeloption, die sichtbar etwas ändert: Fliesen im Flur statt Parkett
    blv.materials.append(
        Material(
            id="fliese",
            category=MaterialCategory.TILES,
            name="Feinsteinzeug Flur",
            room_types=[RoomType.HALLWAY],
        )
    )
    blv.variants.append(
        EquipmentVariant(
            name="Feinsteinzeug Treppenhaus", material_ids=["fliese"], is_default=False
        )
    )

    variants = build_scene(plan, blv)["variants"]

    assert [v["name"] for v in variants] == ["Musterausstattung", "Feinsteinzeug Treppenhaus"]
    assert variants[0]["floors"]["flur"]["name"] == "Eichenparkett"
    assert variants[1]["floors"]["flur"]["name"] == "Feinsteinzeug Flur"
    assert variants[1]["floors"]["bad"] == variants[0]["floors"]["bad"]  # übrige Räume gleich


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
