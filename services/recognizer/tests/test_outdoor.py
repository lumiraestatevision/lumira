"""Außenbereiche: Terrasse aus Umriss, Dielen-Schraffur, Beschriftung und Stützen."""

from __future__ import annotations

import uuid

import pytest

from lumira_recognizer.logic.cad import recognize_cad
from lumira_recognizer.logic.outdoor import column_symbols, hatch_lines
from lumira_shared.models import (
    FilledArea,
    ParsedPlan,
    Point2D,
    Segment,
    SourceFormat,
    TextItem,
)

WALL = "#FEBB40"
FACADE = 150.0  # Dämmschicht: äußere Fassadenlinie 15 cm vor der tragenden Wand


def _rect(x: float, y: float, w: float, h: float) -> list[Point2D]:
    return [
        Point2D(x=x, y=y),
        Point2D(x=x + w, y=y),
        Point2D(x=x + w, y=y + h),
        Point2D(x=x, y=y + h),
    ]


def _line(x0: float, y0: float, x1: float, y1: float) -> Segment:
    return Segment(start=Point2D(x=x0, y=y0), end=Point2D(x=x1, y=y1))


def _column(cx: float, cy: float, size: float = 150.0) -> list[Segment]:
    h = size / 2
    return [
        _line(cx - h, cy - h, cx + h, cy + h),
        _line(cx - h, cy + h, cx + h, cy - h),
        *(_line(*a, *b) for a, b in _square_edges(cx, cy, h)),
    ]


def _square_edges(cx: float, cy: float, h: float) -> list[tuple[tuple[float, float], ...]]:
    c = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return [(c[i], c[(i + 1) % 4]) for i in range(4)]


def terrace_plan(*, roofed: bool = True, first_plank: float = 300.0) -> ParsedPlan:
    """Haus 6 x 4 m (Wände 20 cm, Tür 1 m in der rechten Wand), rechts eine Terrasse 2,35 m
    breit mit Dielen, Beschriftungskasten und drei Stützen am äußeren Rand. ``first_plank``:
    Abstand der ersten Diele vom Umriss."""
    walls = [
        _rect(0, 0, 200, 4_000),
        _rect(0, 0, 6_000, 200),
        _rect(0, 3_800, 6_000, 200),
        _rect(5_800, 200, 200, 1_300),
        _rect(5_800, 2_500, 200, 1_300),
    ]
    x0, x1, y0, y1 = 6_000 + FACADE, 8_500.0, -FACADE, 4_000 + FACADE
    segments = [
        # Fassadenlinie rund ums Haus (vor der Tür die Schwelle)
        _line(-FACADE, y0, x0, y0),
        _line(x0, y0, x0, y1),
        _line(x0, y1, -FACADE, y1),
        _line(-FACADE, y1, -FACADE, y0),
        # Terrassenumriss
        _line(x0, y1, x1, y1),
        _line(x1, y1, x1, y0),
        _line(x1, y0, x0, y0),
        # Dielen quer zur Fassade, 15 cm Raster
        *(
            _line(x0, y, x1, y)
            for y in [y0 + first_plank + 150 * k for k in range(40)]
            if y < y1 - 200
        ),
        # Beschriftungskasten
        _line(6_800, 1_400, 7_800, 1_400),
        _line(7_800, 1_400, 7_800, 2_200),
        _line(7_800, 2_200, 6_800, 2_200),
        _line(6_800, 2_200, 6_800, 1_400),
        *_column(8_350, 0),
        *_column(8_350, 2_000),
        *_column(8_350, 4_000),
    ]
    label = ["Terrasse", "überdacht", "10,8 m²"] if roofed else ["Terrasse", "10,8 m²"]
    return ParsedPlan(
        project_id=uuid.uuid4(),
        source_key="k",
        source_format=SourceFormat.PDF,
        width_mm=29_700,
        height_mm=21_000,
        segments=segments,
        filled_areas=[
            *(FilledArea(polygon=w, color=WALL) for w in walls),
            FilledArea(polygon=_rect(6_800, 1_400, 1_000, 800), color="#FFFFFF"),
        ],
        texts=[
            TextItem(text=t, position=Point2D(x=7_300, y=2_000 - 200 * i), height_mm=300)
            for i, t in enumerate(label)
        ],
    )


def test_terrace_becomes_outdoor_room_up_to_the_wall() -> None:
    plan = recognize_cad(terrace_plan())
    assert plan is not None

    [terrace] = [r for r in plan.rooms if r.outdoor]
    assert terrace.roofed
    assert terrace.label == "Terrasse überdacht 10,8 m²"
    xs = [p.x for p in terrace.polygon]
    # Lücke Fassadenlinie–Wand (Dämmung) geschlossen: Belag bis an die tragende Wand
    assert min(xs) == pytest.approx(6_000, abs=60)
    assert max(xs) == pytest.approx(8_500, abs=60)
    assert terrace.area_m2 == pytest.approx(2.5 * 4.3, rel=0.05)
    assert "Terrasse" in plan.metadata["outdoor"]
    # Innenraum bleibt Innenraum
    assert [r.outdoor for r in plan.rooms].count(False) == 1


def test_outline_in_plank_rhythm_is_found_on_second_try() -> None:
    """Liegt der Umriss genau eine Diele vor der ersten Diele, verschwindet er mit der
    Schraffur – zweiter Versuch mit den Randlinien der Schraffur als Grenze. Der Streifen
    hinter der letzten Diele fehlt dann (≤ eine Dielenbreite je Rand)."""
    plan = recognize_cad(terrace_plan(first_plank=150))
    assert plan is not None
    [terrace] = [r for r in plan.rooms if r.outdoor]
    assert terrace.area_m2 == pytest.approx(2.5 * 4.3, rel=0.15)


def test_terrace_columns_from_crossed_squares() -> None:
    plan = recognize_cad(terrace_plan())
    assert plan is not None
    centres = sorted((round(c.center.x), round(c.center.y)) for c in plan.columns)
    assert centres == [(8_350, 0), (8_350, 2_000), (8_350, 4_000)]
    assert all(c.size_mm == pytest.approx(150) for c in plan.columns)


def test_terrace_without_roof_label_is_open() -> None:
    plan = recognize_cad(terrace_plan(roofed=False))
    assert plan is not None
    [terrace] = [r for r in plan.rooms if r.outdoor]
    assert not terrace.roofed


def test_hatch_keeps_outline_double_lines() -> None:
    planks = [_line(0, 150 * k, 4_000, 150 * k) for k in range(1, 11)]
    planks += [_line(4_000, 150, 0, 150)]  # doppelt gezeichnet (wie im Plan „Test Jann“)
    # Umriss (Dreifachlinie) mit Abstand zur ersten Diele wie im Plan „Test Jann“
    outline = [
        _line(0, -300, 4_500, -300),
        _line(0, -327, 4_500, -327),
        _line(0, -477, 4_500, -477),
    ]
    last = _line(0, 1_650 + 123, 2_000, 1_650 + 123)  # Randbalken dicht an der letzten Diele
    segments = [*planks, *outline, last]

    hatch = hatch_lines(segments)

    assert hatch.lines == set(range(len(planks)))
    assert hatch.ends == {0, 9, 10}  # erste (auch doppelt) und letzte Diele


def test_column_symbols_ignore_single_diagonals() -> None:
    lone = _line(0, 0, 300, 300)
    symbols = column_symbols([lone, *_column(1_000, 1_000, 240)])
    assert len(symbols) == 1
    assert symbols[0].size == pytest.approx(240)
