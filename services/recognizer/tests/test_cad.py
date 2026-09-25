"""CAD-Pläne mit gefüllten Wänden: Wände, Öffnungen, Räume, Maßstabsprüfung."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from lumira_recognizer.config import RecognizerSettings
from lumira_recognizer.handler import recognize_plan
from lumira_recognizer.logic.cad import recognize_cad, wall_color
from lumira_recognizer.logic.scale import check_scale
from lumira_shared.models import (
    DoorSwing,
    FilledArea,
    FloorPlan,
    OpeningType,
    ParsedPlan,
    Point2D,
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


def test_wall_color_needs_several_dark_achromatic_fills() -> None:
    square = [Point2D(x=0, y=0), Point2D(x=100, y=0), Point2D(x=100, y=100)]
    areas = [FilledArea(polygon=square, color="#FFFFFF")] * 10 + [
        FilledArea(polygon=square, color="#FF0000")
    ] * 10
    assert wall_color(areas) is None
    assert wall_color([*areas, *[FilledArea(polygon=square, color="#333333")] * 4]) == "#333333"


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


def test_contradicting_area_labels_do_not_change_scale(cad_plan: CadPlanFactory) -> None:
    plan = _recognize(cad_plan(factor=2.0))
    plan.rooms[0].label = "Wohnen F: 120,00 m²"  # passt zum gestreckten Plan
    check = check_scale(plan, current_scale=100)
    assert check.factor is None
    assert "widersprechen" in check.note
