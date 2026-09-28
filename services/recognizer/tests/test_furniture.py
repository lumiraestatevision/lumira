"""Im Plan gezeichnete Möbel: Figuren finden, einordnen, ausrichten."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pytest

from lumira_recognizer.logic.furniture import find_furniture
from lumira_shared.models import Furniture, Point2D, Room, TextItem


def _room(label: str, w: float = 4_000, h: float = 4_000) -> Room:
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    return Room(id="r", label=label, polygon=[Point2D(x=x, y=y) for x, y in corners])


def _rect(x: float, y: float, w: float, h: float) -> list[np.ndarray]:
    c = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [np.array([c[i], c[(i + 1) % 4]], float) for i in range(4)]


def _line(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y1]], float)


def _octagon(cx: float, cy: float, size: float = 420) -> np.ndarray:
    r = size / 2 / math.cos(math.pi / 8)
    pts = [
        (
            cx + r * math.cos(math.pi / 8 + k * math.pi / 4),
            cy + r * math.sin(math.pi / 8 + k * math.pi / 4),
        )
        for k in range(9)
    ]
    return np.array(pts, float)


def _by_kind(found: list[Furniture]) -> dict[str, list[Furniture]]:
    kinds: dict[str, list[Furniture]] = {}
    for f in found:
        kinds.setdefault(f.kind, []).append(f)
    return kinds


def test_bedroom_bed_wardrobe_and_label_box() -> None:
    lines = [
        # Doppelbett am oberen Rand: Umriss, zwei Kissen, Deckenumschlag
        *_rect(1_000, 1_980, 1_800, 2_000),
        *_rect(1_100, 3_450, 700, 450),
        *_rect(2_000, 3_450, 700, 450),
        _line(1_000, 3_300, 2_800, 3_300),
        _line(2_200, 3_300, 2_800, 2_700),
        # Schrank an der linken Wand, zwei Felder mit Kreuz
        *_rect(20, 500, 600, 1_000),
        _line(20, 500, 620, 1_500),
        _line(20, 1_500, 620, 500),
        *_rect(20, 1_500, 600, 1_000),
        _line(20, 1_500, 620, 2_500),
        _line(20, 2_500, 620, 1_500),
        # Beschriftungskasten
        *_rect(1_500, 800, 900, 500),
    ]
    texts = [TextItem(text="Schlafen", position=Point2D(x=1_950, y=1_050))]

    kinds = _by_kind(find_furniture([_room("Schlafen 14,6 m²")], lines, [], texts))

    assert set(kinds) == {"bed_double", "wardrobe"}
    [bed] = kinds["bed_double"]
    assert (bed.width_mm, bed.depth_mm) == (
        pytest.approx(1_800, abs=40),
        pytest.approx(2_000, abs=40),
    )
    assert bed.angle_deg == pytest.approx(-90, abs=1)  # Kopfende oben an der Wand, Blick nach unten
    [wardrobe] = kinds["wardrobe"]
    assert wardrobe.width_mm == pytest.approx(2_000, abs=40)
    assert wardrobe.depth_mm == pytest.approx(600, abs=40)
    assert wardrobe.angle_deg == pytest.approx(0, abs=1)  # Rücken an der linken Wand
    assert wardrobe.center.x == pytest.approx(320, abs=30)


def _clipped(
    line: np.ndarray, boxes: Sequence[tuple[float, float, float, float]]
) -> list[np.ndarray]:
    """Achsparallele Fliesenfuge, die an Sanitärobjekten endet (wie im Plan)."""
    (x0, y0), (x1, y1) = line
    vertical = x0 == x1
    pos, lo, hi = (x0, y0, y1) if vertical else (y0, x0, x1)
    cuts = sorted(
        (b[1], b[3]) if vertical else (b[0], b[2])
        for b in boxes
        if (b[0] <= pos <= b[2] if vertical else b[1] <= pos <= b[3])
    )
    pieces, start = [], lo
    for a, b in cuts:
        if a > start:
            pieces.append((start, a))
        start = max(start, b)
    if start < hi:
        pieces.append((start, hi))
    return [_line(pos, a, pos, b) if vertical else _line(a, pos, b, pos) for a, b in pieces]


def test_tiled_bathroom_tub_and_wc() -> None:
    w, h = 2_400, 3_000
    fixtures = [(20, 2_230, 1_720, 2_980), (2_000, 720, 2_380, 1_280)]
    grid = [_line(x, 0, x, h) for x in range(150, w, 150)] + [
        _line(0, y, w, y) for y in range(150, h, 150)
    ]
    tiles = [piece for line in grid for piece in _clipped(line, fixtures)]
    lines = [
        *tiles,
        # Wanne oben, mit innerem Rand und Maßangabe
        *_rect(20, 2_230, 1_700, 750),
        *_rect(120, 2_330, 1_500, 550),
        # WC: Umriss (Spülkasten + Schüssel), darin das Oval als Kurve
        *_rect(2_000, 720, 380, 560),
    ]
    wc = np.array(
        [
            (2_190 + 190 * math.cos(a), 1_000 + 280 * math.sin(a))
            for a in np.linspace(0, 2 * math.pi, 17)
        ]
    )
    texts = [TextItem(text="1,70 x 75", position=Point2D(x=870, y=2_600))]

    kinds = _by_kind(find_furniture([_room("Bad", w, h)], lines, [wc], texts))

    assert set(kinds) == {"bathtub", "wc"}
    [tub] = kinds["bathtub"]
    assert tub.width_mm == pytest.approx(1_700, abs=50)
    assert tub.angle_deg == pytest.approx(-90, abs=1)
    [toilet] = kinds["wc"]
    assert abs(toilet.angle_deg) == pytest.approx(180, abs=1)  # Spülkasten an der rechten Wand


def test_dining_table_with_chairs_facing_it() -> None:
    lines = [*_rect(1_500, 1_500, 1_600, 900)]
    chairs = [_octagon(x, y) for x in (1_900, 2_700) for y in (1_150, 2_750)]

    kinds = _by_kind(find_furniture([_room("Wohnen / Essen")], lines, chairs, []))

    assert set(kinds) == {"dining_table", "chair"}
    assert len(kinds["chair"]) == 4
    for chair in kinds["chair"]:
        facing = 90 if chair.center.y < 1_500 else -90
        assert chair.angle_deg == pytest.approx(facing, abs=1)


def test_stairs_are_no_furniture() -> None:
    steps = [ln for k in range(8) for ln in _rect(1_000, 500 + k * 250, 1_000, 250)]
    stair = np.array([(1_000, 500), (2_000, 500), (2_000, 2_500), (1_000, 2_500)], float)

    assert find_furniture([_room("Diele")], steps, [], [], [stair]) == []
