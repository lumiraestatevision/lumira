"""CAD-Pläne mit gefüllten Wänden: Wände, Öffnungen, Räume, Maßstabsprüfung."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable

import pytest

from lumira_recognizer.config import RecognizerSettings
from lumira_recognizer.handler import recognize_plan
from lumira_recognizer.logic.cad import recognize_cad, wall_color
from lumira_recognizer.logic.scale import check_scale, label_area
from lumira_shared.models import (
    DoorSwing,
    FilledArea,
    FloorPlan,
    OpeningType,
    ParsedPlan,
    Point2D,
    SourceFormat,
)

CadPlanFactory = Callable[..., ParsedPlan]  # Fixture cad_plan aus conftest.py
SETTINGS = RecognizerSettings(_env_file=None)  # pyright: ignore[reportCallIssue]


def _recognize(parsed: ParsedPlan) -> FloorPlan:
    plan = recognize_cad(parsed)
    assert plan is not None
    return plan


def test_filled_walls_become_walls_with_exact_footprint(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan())

    pieces = [w for w in plan.walls if w.footprint]
    assert len(pieces) == 8
    assert plan.metadata["recognizer"] == "cad-fills"
    assert plan.metadata["wall_color"] == "#808080"
    thickness = sorted({round(w.thickness_mm) for w in pieces})
    assert thickness == [115, 300]
    interior = [w for w in pieces if round(w.thickness_mm) == 115]
    assert interior
    assert not any(w.is_exterior for w in interior)
    assert all(w.is_exterior for w in pieces if round(w.thickness_mm) == 300)


def test_duplicate_wall_fills_become_one_wall(cad_plan: CadPlanFactory) -> None:
    """Echter Plan: Gartenmauer je Haushälfte deckungsgleich doppelt gezeichnet."""
    parsed = cad_plan()
    walls = [a for a in parsed.filled_areas if a.color == "#808080"]
    duplicate = walls[0].model_copy(update={"polygon": list(reversed(walls[0].polygon))})
    parsed.filled_areas.append(duplicate)

    plan = _recognize(parsed)

    assert len([w for w in plan.walls if w.footprint]) == 8


def test_openings_are_found_between_wall_ends(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan())

    by_type = {o.type: o for o in plan.openings}
    assert Counter(o.type for o in plan.openings) == {OpeningType.WINDOW: 2, OpeningType.DOOR: 1}
    door = by_type[OpeningType.DOOR]
    assert door.width_mm == pytest.approx(900)
    assert door.swing is not None
    # Tür schlägt ins Wohnen auf (links im Plan = kleinere x); Richtung hängt an der Wandachse
    wall = plan.wall(door.wall_id)
    direction = (wall.end.x - wall.start.x, wall.end.y - wall.start.y)
    left_normal_x = -direction[1]
    assert door.opens_to == ("left" if left_normal_x < 0 else "right")
    windows = sorted(o.width_mm for o in plan.openings if o.type is OpeningType.WINDOW)
    assert windows == [pytest.approx(1_000), pytest.approx(1_500)]
    # Öffnungen sitzen auf eigenen Wandstücken über die volle Lücke → Sturz/Brüstung im 3D-Modell
    for opening in plan.openings:
        wall = plan.wall(opening.wall_id)
        assert wall.footprint is None
        assert wall.length_mm == pytest.approx(opening.width_mm)
        assert opening.offset_mm == 0


def test_without_door_arc_an_interior_gap_is_a_passage(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan(door_arc=False))
    [inner] = [o for o in plan.openings if not plan.wall(o.wall_id).is_exterior]
    assert inner.type is OpeningType.PASSAGE
    assert inner.swing is None


def test_rooms_from_colored_fills_with_labels(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan())

    rooms = {r.label: r.area_m2 for r in plan.rooms}
    # Raumname (größte Schrift) zuerst, reine Maßzahlen („3,00“) nicht im Label
    assert rooms == {"Wohnen F: 30,00 m²": 30.0, "Bad F: 18,00 m²": 18.0}
    assert plan.metadata["rooms_from"] == "farbige Raumflächen"


def test_without_room_fills_rooms_are_enclosed_areas(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan(fills=False))

    assert plan.metadata["rooms_from"] == "von Wänden umschlossene Flächen"
    areas = sorted(r.area_m2 for r in plan.rooms)
    assert len(areas) == 2
    # Raster 20 mm: Fläche bis auf wenige Prozent genau
    assert areas[0] == pytest.approx(18.0, rel=0.05)
    assert areas[1] == pytest.approx(30.0, rel=0.05)
    assert any(r.label and r.label.startswith("Wohnen") for r in plan.rooms)


def test_plan_without_filled_walls_is_left_to_other_method(cad_plan: CadPlanFactory) -> None:
    parsed = cad_plan()
    parsed.filled_areas = [a for a in parsed.filled_areas if a.color != "#808080"]
    assert recognize_cad(parsed) is None


def _rect(x: float, y: float, w: float, h: float) -> list[Point2D]:
    return [
        Point2D(x=x, y=y),
        Point2D(x=x + w, y=y),
        Point2D(x=x + w, y=y + h),
        Point2D(x=x, y=y + h),
    ]


def test_wall_color_is_found_by_shape_not_hue() -> None:
    """Büro-Konvention „Test Jann“: Wände orange, schwarze Schraffurpunkte, weiße Textfelder,
    farbige Raumflächen. Wand ist die Farbe der Streifen mit Wanddicke."""
    dots = [FilledArea(polygon=_rect(i * 50, 0, 10, 10), color="#000000") for i in range(200)]
    labels = [
        FilledArea(polygon=_rect(0, i * 2_000, 1_500, 400), color="#FFFFFF") for i in range(5)
    ]
    rooms = [
        FilledArea(polygon=_rect(i * 5_000, 0, 4_000, 4_000), color="#C8E6FF") for i in range(3)
    ]
    walls = [FilledArea(polygon=_rect(i * 3_000, 0, 240, 3_000), color="#FEBB40") for i in range(4)]

    assert wall_color([*dots, *labels, *rooms]) is None
    assert wall_color([*dots, *labels, *rooms, *walls]) == "#FEBB40"


def test_opening_between_branches_of_one_wall_polygon() -> None:
    """Ein einziges C-förmiges Wandpolygon (ganzer Raum als ein Wandzug): die 0,9-m-Lücke
    oben ist eine Öffnung, obwohl beide Laibungen zum selben Polygon gehören und dessen
    Schwerpunkt mitten im Raum liegt."""
    c_shape = [
        Point2D(x=x, y=y)
        for x, y in [
            (2_150, 4_200),
            (3_400, 4_200),
            (3_400, 0),
            (0, 0),
            (0, 4_200),
            (1_250, 4_200),
            (1_250, 4_000),
            (200, 4_000),
            (200, 200),
            (3_200, 200),
            (3_200, 4_000),
            (2_150, 4_000),
        ]
    ]
    stubs = [_rect(-6_000 - i * 4_000, 0, 200, 3_000) for i in range(3)]  # freistehende Mauern
    parsed = ParsedPlan(
        project_id=uuid.uuid4(),
        source_key="k",
        source_format=SourceFormat.PDF,
        width_mm=29_700,
        height_mm=21_000,
        filled_areas=[FilledArea(polygon=p, color="#FEBB40") for p in (c_shape, *stubs)],
    )

    plan = _recognize(parsed)

    [opening] = plan.openings
    assert opening.width_mm == pytest.approx(900)
    assert opening.type is OpeningType.WINDOW  # führt ins Freie
    [room] = plan.rooms
    assert room.area_m2 == pytest.approx(3.0 * 3.8, rel=0.05)


# ------------------------------------------------------------------ Maßstab
def test_scale_is_confirmed_by_area_labels(cad_plan: CadPlanFactory) -> None:
    check = check_scale(_recognize(cad_plan()), current_scale=100)
    assert check.factor is None
    assert check.note.startswith("Maßstab bestätigt: 2 Raumflächen")


def test_wrong_scale_is_corrected_via_area_labels(cad_plan: CadPlanFactory) -> None:
    # Plan ist 1:50 gezeichnet, aber mit 1:100 umgerechnet → alle Längen doppelt so groß
    parsed = cad_plan(factor=2.0)
    parsed.plan_scale = 100

    plan = recognize_plan(parsed, SETTINGS)

    assert plan.metadata["scale_check"] == (
        "Maßstab korrigiert: 1:100 → 1:50 (laut Raumflächen im Plan)"
    )
    assert sorted(r.area_m2 for r in plan.rooms) == [18.0, 30.0]
    door = next(o for o in plan.openings if o.type is OpeningType.DOOR)
    assert door.width_mm == pytest.approx(900)
    assert door.swing in (DoorSwing.LEFT, DoorSwing.RIGHT)


def test_merged_room_labels_are_ignored_for_scale_check() -> None:
    assert label_area("Küche F:12,73 m2") == pytest.approx(12.73)
    assert label_area("Bad 6,6 m² Diele 7,6 m²") is None  # zwei Räume in einem Polygon


def test_contradicting_area_labels_do_not_change_scale(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan(factor=2.0))
    plan.rooms[0].label = "Wohnen F: 120,00 m²"  # passt zum gestreckten Plan
    check = check_scale(plan, current_scale=100)
    assert check.factor is None
    assert "widersprechen" in check.note
