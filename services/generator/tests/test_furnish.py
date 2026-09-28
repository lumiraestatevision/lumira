"""Einrichtung: plausibel platziert, Türen frei, LV bestimmt die Sanitärobjekte."""

from __future__ import annotations

import math
import uuid
from collections import Counter
from itertools import combinations

import pytest

from lumira_generator.logic.furnish import Rect, furnish, hull_rect, overlaps, rect_in_polygon
from lumira_shared.models import (
    BLVResult,
    EquipmentVariant,
    FloorPlan,
    Furniture,
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

T = 200.0  # Wanddicke


def _plan(
    width: float,
    depth: float,
    room_type: RoomType,
    openings: list[tuple[str, OpeningType, float, float, float]],
    label: str | None = None,
) -> FloorPlan:
    """Rechteckiger Raum (0,0)–(width,depth); Wände außen herum, Achse T/2 vor der Fläche.
    openings: (Wand unten|rechts|oben|links, Typ, Abstand ab Wandanfang, Breite, Brüstung)."""
    h = T / 2
    ends = {
        "unten": ((-h, -h), (width + h, -h)),
        "rechts": ((width + h, -h), (width + h, depth + h)),
        "oben": ((width + h, depth + h), (-h, depth + h)),
        "links": ((-h, depth + h), (-h, -h)),
    }
    walls = [
        Wall(id=name, start=Point2D(x=a[0], y=a[1]), end=Point2D(x=b[0], y=b[1]), thickness_mm=T)
        for name, (a, b) in ends.items()
    ]
    return FloorPlan(
        project_id=uuid.uuid4(),
        source_key="plan.pdf",
        source_format=SourceFormat.PDF,
        walls=walls,
        openings=[
            Opening(
                id=f"o{i}",
                type=kind,
                wall_id=wall,
                offset_mm=offset + h,
                width_mm=w,
                height_mm=2_010,
                sill_height_mm=sill,
            )
            for i, (wall, kind, offset, w, sill) in enumerate(openings)
        ],
        rooms=[
            Room(
                id="r",
                polygon=[
                    Point2D(x=0, y=0),
                    Point2D(x=width, y=0),
                    Point2D(x=width, y=depth),
                    Point2D(x=0, y=depth),
                ],
                room_type=room_type,
                label=label,
            )
        ],
    )


def _blv(*materials: Material) -> BLVResult:
    return BLVResult(
        project_id=uuid.uuid4(),
        materials=list(materials),
        variants=[EquipmentVariant(name="Standard", material_ids=[m.id for m in materials])],
    )


def _rect(item: dict) -> Rect:
    return Rect(item["x"], item["y"], item["angle"], item["w"], item["d"])


def _square(plan: FloorPlan) -> list[tuple[float, float]]:
    return [(p.x, p.y) for p in plan.rooms[0].polygon]


def test_living_room_is_furnished_inside_without_collisions() -> None:
    plan = _plan(
        6_000,
        5_000,
        RoomType.LIVING,
        [
            ("unten", OpeningType.WINDOW, 1_000, 2_400, 0),  # Terrassentür
            ("links", OpeningType.DOOR, 3_600, 900, 0),
        ],
        label="Wohnen/Essen",
    )

    items = furnish(plan, _blv())

    kinds = Counter(i["kind"] for i in items)
    assert kinds["sofa"] == 1
    assert kinds["dining_table"] == 1
    assert kinds["chair"] == 4
    assert all(i["loose"] for i in items)
    polygon = _square(plan)
    assert all(rect_in_polygon(_rect(i), polygon) for i in items)
    solid = [i for i in items if i["kind"] != "chair"]  # Stühle stehen halb unter dem Tisch
    assert not any(overlaps(_rect(a), _rect(b)) for a, b in combinations(solid, 2))


def test_walkways_in_front_of_doors_stay_free() -> None:
    plan = _plan(
        6_000,
        5_000,
        RoomType.LIVING,
        [
            ("unten", OpeningType.WINDOW, 1_000, 2_400, 0),
            ("links", OpeningType.DOOR, 3_600, 900, 0),
        ],
        label="Wohnen/Essen",
    )
    terrace = Rect(1_000 + 1_200, 500, 1.5708, 2_400, 1_000)  # vor der Terrassentür
    door = Rect(500, 3_600 + 450, 0.0, 900, 1_000)  # vor der Zimmertür

    for item in furnish(plan, _blv()):
        assert not overlaps(_rect(item), terrace, margin=50), item["kind"]
        assert not overlaps(_rect(item), door, margin=50), item["kind"]


def test_stair_and_its_landing_stay_free() -> None:
    """Treppe im Wohnraum: keine Möbel auf den Stufen und 1 m vor dem Antritt."""
    plan = _plan(
        6_000,
        5_000,
        RoomType.LIVING,
        [
            ("unten", OpeningType.WINDOW, 1_000, 2_400, 0),
            ("links", OpeningType.DOOR, 3_600, 900, 0),
        ],
        label="Wohnen/Essen",
    )
    # Lauf an der rechten Wand, Antritt bei y 3000, Austritt an der Außenwand unten
    steps = [
        [
            Point2D(x=x, y=y)
            for x, y in ((5_000, y0), (6_000, y0), (6_000, y0 + 250), (5_000, y0 + 250))
        ]
        for y0 in range(2_750, -250, -250)
    ]
    corners = ((5_000, 0), (6_000, 0), (6_000, 3_000), (5_000, 3_000))
    plan.stairs = [
        Stair(
            steps=steps,
            rise_mm=2_750 / len(steps),
            walking_line=[Point2D(x=5_500, y=2_875), Point2D(x=5_500, y=2_750)],
            outline=[Point2D(x=x, y=y) for x, y in corners],
        )
    ]
    stair = Rect(5_500, 1_500, math.pi / 2, 1_000, 3_000)
    landing = Rect(5_500, 3_500, math.pi / 2, 1_000, 1_000)

    items = furnish(plan, _blv())

    assert Counter(i["kind"] for i in items)["sofa"] == 1
    for item in items:
        assert not overlaps(_rect(item), stair, margin=20), item["kind"]
        assert not overlaps(_rect(item), landing, margin=20), item["kind"]


def test_hull_rect_is_the_tight_box() -> None:
    diamond = [(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)]
    rect = hull_rect(diamond)
    assert rect.w * rect.d == pytest.approx(2.0)
    assert (rect.cx, rect.cy) == (pytest.approx(0.0, abs=1e-9), pytest.approx(0.0, abs=1e-9))


def test_bed_head_avoids_the_window_wall() -> None:
    plan = _plan(
        4_000,
        3_600,
        RoomType.BEDROOM,
        [("oben", OpeningType.WINDOW, 1_400, 1_200, 900), ("links", OpeningType.DOOR, 300, 900, 0)],
    )

    [bed] = [i for i in furnish(plan, _blv()) if i["kind"] == "bed_double"]

    # Kopfende = Rückseite; die Front zeigt vom Kopfende weg. Nicht zur Fensterwand oben.
    head_y = bed["y"] - 0.5 * bed["d"] * math.sin(bed["angle"])
    assert head_y < 3_600 - 300


def test_bathroom_follows_the_lv() -> None:
    sanitary = MaterialCategory.SANITARY
    blv = _blv(
        Material(id="wanne", category=sanitary, name="Acryl-Badewanne"),
        Material(id="dusche", category=sanitary, name="Bodengleiche Duschwanne"),
        Material(id="wc", category=sanitary, name="Tiefspül-WC spülrandlos"),
        Material(id="wt", category=sanitary, name="Waschtisch Keramik"),
    )
    plan = _plan(3_200, 2_600, RoomType.BATHROOM, [("links", OpeningType.DOOR, 200, 800, 0)])

    items = furnish(plan, blv)

    assert {i["kind"] for i in items} == {"bathtub", "shower", "wc", "washbasin"}
    assert not any(i["loose"] for i in items)  # feste Ausstattung laut LV, nicht ausblendbar


def test_bathroom_without_bathtub_in_lv_gets_shower_only() -> None:
    blv = _blv(Material(id="d", category=MaterialCategory.SANITARY, name="Duschwanne superflach"))
    plan = _plan(2_400, 2_200, RoomType.BATHROOM, [("links", OpeningType.DOOR, 200, 800, 0)])
    kinds = {i["kind"] for i in furnish(plan, blv)}
    assert "bathtub" not in kinds
    assert {"shower", "wc", "washbasin"} <= kinds


def test_guest_wc_and_kitchen() -> None:
    wc = _plan(1_400, 2_000, RoomType.WC, [("unten", OpeningType.DOOR, 300, 800, 0)])
    assert Counter(i["kind"] for i in furnish(wc, _blv())) == {"wc": 1, "handbasin": 1}

    kitchen = _plan(3_800, 3_000, RoomType.KITCHEN, [("links", OpeningType.DOOR, 200, 900, 0)])
    blv = _blv(
        Material(id="k", category=MaterialCategory.KITCHEN, name="Küche", color_hex="#DDE3E0")
    )
    [run] = furnish(kitchen, blv)
    assert run["kind"] == "kitchen"
    assert run["loose"] is False
    assert run["color"] == "#DDE3E0"
    assert run["w"] >= 2_400
    assert run["hob"] is True


def test_open_kitchen_gets_a_counter_instead_of_a_run_in_the_opening() -> None:
    """Muster1: Küche ohne Wand zum Wohnbereich – die Zeile gehört an eine echte Wand, an die
    offene Kante eine Theke (Rücken zum Wohnbereich), der Durchgang bleibt frei."""
    kitchen = _plan(3_800, 3_600, RoomType.KITCHEN, [("links", OpeningType.DOOR, 200, 900, 0)])
    kitchen.walls = [w for w in kitchen.walls if w.id != "oben"]  # offen nach oben

    items = {i["kind"]: i for i in furnish(kitchen, _blv())}

    assert set(items) == {"kitchen", "kitchen_counter"}
    run, counter = items["kitchen"], items["kitchen_counter"]
    assert run["hob"] is False  # Kochfeld sitzt in der Theke
    # L-Küche: Zeile an der Wand, an der die Theke anstößt (hier rechts), bis an die Theke
    counter_left = counter["x"] - counter["w"] / 2
    counter_right = counter["x"] + counter["w"] / 2
    wall_side = "rechts" if counter_right > 3_800 - 10 else "links"
    if wall_side == "rechts":
        assert run["x"] == pytest.approx(3_800 - run["d"] / 2 - 20, abs=5)
    else:
        assert counter_left < 10
        assert run["x"] == pytest.approx(run["d"] / 2 + 20, abs=5)
    assert run["y"] + run["w"] / 2 == pytest.approx(3_600 - counter["d"], abs=60)
    # Theke an der offenen Kante, Front zeigt in die Küche (nach unten), Rest bleibt Durchgang
    assert counter["y"] == pytest.approx(3_600 - counter["d"] / 2, abs=5)
    assert counter["angle"] == pytest.approx(-math.pi / 2, abs=1e-3)
    assert counter["w"] <= 3_800 - 900
    polygon = _square(kitchen)
    assert rect_in_polygon(_rect(counter), polygon)
    assert not overlaps(_rect(counter), _rect(run))


# ------------------------------------------------------------------ Möbel aus dem Plan
def test_drawn_furniture_replaces_rules() -> None:
    """Im Plan gezeichnetes Bett: genau dort, so groß, so ausgerichtet – keine Regel-Möbel."""
    plan = _plan(4_000, 4_000, RoomType.BEDROOM, [("unten", OpeningType.DOOR, 300, 900, 0)])
    plan.furniture = [
        Furniture(
            id="f0",
            kind="bed_double",
            center=Point2D(x=1_900, y=2_980),
            width_mm=1_600,
            depth_mm=2_000,
            angle_deg=-90,
            room_id="r",
        )
    ]

    items = furnish(plan, _blv())

    [bed] = items
    assert (bed["kind"], bed["x"], bed["y"], bed["w"], bed["d"]) == (
        "bed_double",
        1_900,
        2_980,
        1_600,
        2_000,
    )
    assert bed["angle"] == pytest.approx(-math.pi / 2, abs=1e-4)
    assert bed["loose"] is True


def test_rules_add_missing_main_furniture() -> None:
    """Wohnzimmer mit gezeichnetem Esstisch, aber ohne erkanntes Sofa: das Sofa kommt dazu
    und überschneidet den Tisch nicht."""
    plan = _plan(6_000, 4_500, RoomType.LIVING, [("unten", OpeningType.DOOR, 300, 900, 0)])
    plan.furniture = [
        Furniture(
            id="f0",
            kind="dining_table",
            center=Point2D(x=4_500, y=3_300),
            width_mm=1_600,
            depth_mm=900,
            angle_deg=90,
            room_id="r",
        )
    ]

    items = furnish(plan, _blv())

    kinds = Counter(i["kind"] for i in items)
    assert kinds["dining_table"] == 1  # nur der gezeichnete
    assert kinds["sofa"] == 1
    table = next(i for i in items if i["kind"] == "dining_table")
    sofa = next(i for i in items if i["kind"] == "sofa")
    assert not overlaps(_rect(table), _rect(sofa))


def test_drawn_kitchen_gets_no_upper_cabinets_below_a_window() -> None:
    plan = _plan(4_000, 3_000, RoomType.KITCHEN, [("oben", OpeningType.WINDOW, 1_400, 1_200, 900)])
    plan.furniture = [
        Furniture(
            id="k",
            kind="kitchen",
            center=Point2D(x=2_000, y=2_690),
            width_mm=3_000,
            depth_mm=620,
            angle_deg=-90,
            room_id="r",
        )
    ]

    [kitchen] = furnish(plan, _blv())

    assert kitchen["upper"] is False
    assert kitchen["loose"] is False
