from __future__ import annotations

import pytest

from lumira_recognizer.logic.detector import recognize
from lumira_shared import NonRetryableError
from lumira_shared.models import OpeningType, ParsedPlan


def _recognize(plan: ParsedPlan):
    return recognize(plan, min_wall_length_mm=300, wall_thickness_mm=175)


def test_rooms_are_found_by_flood_fill(parsed_plan: ParsedPlan) -> None:
    plan = _recognize(parsed_plan)
    rooms = {r.label.split()[0] if r.label else None: r.area_m2 for r in plan.rooms}
    assert rooms == {"Wohnen/Essen": 36.0, "Schlafen": 14.0, "Bad": 18.0, "Flur": 12.0}
    assert len(plan.walls) == 7


def test_placeholder_openings(parsed_plan: ParsedPlan) -> None:
    plan = _recognize(parsed_plan)
    windows = [o for o in plan.openings if o.type is OpeningType.WINDOW]
    doors = [o for o in plan.openings if o.type is OpeningType.DOOR]
    assert len(windows) == 4  # alle Außenwände ≥ 2 m
    assert len(doors) == 3  # alle Innenwände ≥ 1,5 m
    exterior = {w.id for w in plan.walls if w.is_exterior}
    assert {o.wall_id for o in windows} == exterior


def test_short_segments_are_ignored(parsed_plan: ParsedPlan) -> None:
    plan = recognize(parsed_plan, min_wall_length_mm=6_500, wall_thickness_mm=175)
    assert len(plan.walls) == 5  # nur Wände ab 6,5 m


def test_plan_without_vectors_is_not_retryable(parsed_plan: ParsedPlan) -> None:
    parsed_plan.segments = []
    with pytest.raises(NonRetryableError, match="Vektorgeometrie"):
        _recognize(parsed_plan)
