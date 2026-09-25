"""Einrichtung: plausibel platziert, Türen frei, LV bestimmt die Sanitärobjekte."""

from __future__ import annotations

import math
import uuid
from collections import Counter
from itertools import combinations

from lumira_generator.logic.furnish import Rect, furnish, overlaps, rect_in_polygon
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
