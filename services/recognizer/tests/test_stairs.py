"""Treppen aus CAD-Linien: Stufenflächen, Laufrichtung, Störlinien (Lauflinie, Maß, Schnitt)."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from lumira_recognizer.logic.cad import recognize_cad
from lumira_shared.models import FloorPlan, ParsedPlan, Point2D, Segment, Stroke

CadPlanFactory = Callable[..., ParsedPlan]  # Fixture cad_plan aus conftest.py

# Gerade Treppe im Wohnen (x 500–1500, y 1500–5500): 16 Auftritte à 250 mm, Lauf nach +y.
X0, X1, Y0, Y1 = 500.0, 1_500.0, 1_500.0, 5_500.0
TREAD = 250.0
# Schnittsymbol: zwei eng parallele Schräglinien zwischen den Trittkanten 3250 und 3500,
# die Wangen sind dazwischen unterbrochen.
CUT = [((X0, 3_300.0), (X1, 3_380.0)), ((X0, 3_350.0), (X1, 3_430.0))]


def _seg(x1: float, y1: float, x2: float, y2: float) -> Segment:
    return Segment(start=Point2D(x=x1, y=y1), end=Point2D(x=x2, y=y2), line_width_mm=0.13)


def _stair_lines(
    *, start_symbol: bool = True, arrow_at_top: bool = True
) -> tuple[list[Segment], list[Stroke]]:
    (a0, a1), (b0, b1) = CUT
    segments = [
        _seg(X0, Y0, X1, Y0),
        _seg(X0, Y1, X1, Y1),
        # Wangen, am Schnittsymbol unterbrochen
        _seg(X0, Y0, *a0),
        _seg(*b0, X0, Y1),
        _seg(X1, Y0, *a1),
        _seg(*b1, X1, Y1),
        _seg(*a0, *a1),
        _seg(*b0, *b1),
    ]
    segments += [_seg(X0, Y0 + k * TREAD, X1, Y0 + k * TREAD) for k in range(1, 16)]
    # Lauflinie mittig, endet genau auf den Stufen – kreuzt alle Trittkanten
    mid = (X0 + X1) / 2
    tip, tail = (Y1 - 100, Y0 + 100) if arrow_at_top else (Y0 + 100, Y1 - 100)
    back = -150.0 if arrow_at_top else 150.0
    segments += [
        _seg(mid, tail, mid, tip),
        _seg(mid, tip, mid - 80, tip + back),
        _seg(mid, tip, mid + 80, tip + back),
    ]
    # Maßlinie (Lauflänge) quer durch alle Stufen, Schrägstriche an beiden Enden
    segments += [
        _seg(700, Y0, 700, Y1),
        _seg(630, Y0 - 70, 770, Y0 + 70),
        _seg(630, Y1 - 70, 770, Y1 + 70),
    ]
    curves = []
    if start_symbol:
        circle = [
            Point2D(x=mid + 75 * math.cos(a), y=tail + 75 * math.sin(a))
            for a in (i * math.pi / 8 for i in range(17))
        ]
        curves.append(Stroke(points=circle))
    return segments, curves


def _with_stair(cad_plan: CadPlanFactory, **kwargs: bool) -> FloorPlan:
    parsed = cad_plan()
    segments, curves = _stair_lines(**kwargs)
    parsed.segments.extend(segments)
    parsed.curves.extend(curves)
    plan = recognize_cad(parsed)
    assert plan is not None
    return plan


def _centre_y(polygon: list[Point2D]) -> float:
    return sum(p.y for p in polygon) / len(polygon)


def test_straight_stair_becomes_steps_bottom_to_top(cad_plan: CadPlanFactory) -> None:
    plan = _with_stair(cad_plan)

    assert len(plan.stairs) == 1
    stair = plan.stairs[0]
    # Schnittsymbol nicht als eigene Stufe; Lauf- und Maßlinie teilen die Stufen nicht
    assert len(stair.steps) == 16
    assert stair.rise_mm == pytest.approx(2_750 / 16, abs=0.1)
    assert stair.confidence == pytest.approx(0.8)
    centres = [_centre_y(step) for step in stair.steps]
    assert centres[0] == pytest.approx(Y0 + TREAD / 2, abs=30)
    assert centres[-1] == pytest.approx(Y1 - TREAD / 2, abs=30)
    assert centres == sorted(centres)
    for step in stair.steps:
        assert min(p.x for p in step) == pytest.approx(X0, abs=20)
        assert max(p.x for p in step) == pytest.approx(X1, abs=20)
    walk = [p.y for p in stair.walking_line]
    assert walk == sorted(walk)
    xs, ys = [p.x for p in stair.outline], [p.y for p in stair.outline]
    assert (min(xs), max(xs)) == (pytest.approx(X0, abs=20), pytest.approx(X1, abs=20))
    assert (min(ys), max(ys)) == (pytest.approx(Y0, abs=20), pytest.approx(Y1, abs=20))
    assert plan.metadata["stairs"] == "16 Stufen à 171.9 mm"


@pytest.mark.parametrize("arrow_at_top", [True, False])
def test_direction_from_arrow_without_start_symbol(
    cad_plan: CadPlanFactory, arrow_at_top: bool
) -> None:
    """Ohne Antrittssymbol zeigt der Pfeil der Lauflinie auf den Austritt."""
    plan = _with_stair(cad_plan, start_symbol=False, arrow_at_top=arrow_at_top)

    stair = plan.stairs[0]
    first, last = _centre_y(stair.steps[0]), _centre_y(stair.steps[-1])
    assert (first < last) is arrow_at_top


def test_grid_of_lines_is_no_stair(cad_plan: CadPlanFactory) -> None:
    """Fliesenraster o. Ä.: Flächen mit mehr als zwei Nachbarn sind keine Stufenkette."""
    parsed = cad_plan()
    for k in range(9):
        parsed.segments.append(_seg(6_000 + k * 300, 1_000, 6_000 + k * 300, 3_400))
        parsed.segments.append(_seg(6_000, 1_000 + k * 300, 8_400, 1_000 + k * 300))

    plan = recognize_cad(parsed)

    assert plan is not None
    assert plan.stairs == []
    assert plan.metadata["stairs"] == "keine"
