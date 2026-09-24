"""Wand-, Öffnungs- und Raumerkennung.

Umgesetzt (einfach, aber echt): achsparallele Vektorpläne.
  1. Segmente ab Mindestlänge werden Wände.
  2. Die x-/y-Koordinaten der Wände spannen ein Raster auf.
  3. Flood-Fill über Rasterzellen, getrennt durch Wände → Räume.
  4. Planbeschriftungen werden dem Raum zugeordnet, in dem sie liegen.

STUB – noch nicht umgesetzt:
  - Schräge Wände, Rundungen, Wanddicken aus Doppellinien
  - Tür-/Fenstererkennung (Bogensymbole, Fensterlinien) → hier Platzhalter-Öffnungen
  - KI-Erkennung auf Rasterbildern (Segmentierungsmodell, läuft auf GPU/CPU)
  - Nicht-rechteckige Räume werden durch ihr umschließendes Rechteck angenähert
"""

from __future__ import annotations

from collections.abc import Iterable

from lumira_shared import NonRetryableError
from lumira_shared.models import (
    DoorSwing,
    FloorPlan,
    Opening,
    OpeningType,
    ParsedPlan,
    Point2D,
    Room,
    Segment,
    TextItem,
    Wall,
)

TOL = 1.0  # mm


def _is_vertical(s: Segment) -> bool:
    return abs(s.start.x - s.end.x) <= TOL


def _is_horizontal(s: Segment) -> bool:
    return abs(s.start.y - s.end.y) <= TOL


def _unique(values: Iterable[float]) -> list[float]:
    result: list[float] = []
    for v in sorted(values):
        if not result or v - result[-1] > TOL:
            result.append(v)
    return result


def _covers(walls: list[Segment], *, vertical: bool, at: float, lo: float, hi: float) -> bool:
    """Liegt auf der Linie x=at (bzw. y=at) eine Wand, die [lo, hi] vollständig überdeckt?"""
    for w in walls:
        if vertical and _is_vertical(w) and abs(w.start.x - at) <= TOL:
            a, b = sorted((w.start.y, w.end.y))
        elif not vertical and _is_horizontal(w) and abs(w.start.y - at) <= TOL:
            a, b = sorted((w.start.x, w.end.x))
        else:
            continue
        if a <= lo + TOL and b >= hi - TOL:
            return True
    return False


def _rooms_from_grid(segments: list[Segment]) -> list[tuple[float, float, float, float]]:
    """Räume als Rechtecke (x0, y0, x1, y1)."""
    xs = _unique(p for s in segments if _is_vertical(s) for p in (s.start.x,))
    ys = _unique(p for s in segments if _is_horizontal(s) for p in (s.start.y,))
    if len(xs) < 2 or len(ys) < 2:
        return []

    nx, ny = len(xs) - 1, len(ys) - 1
    seen: set[tuple[int, int]] = set()
    rooms: list[tuple[float, float, float, float]] = []
    for start in ((i, j) for i in range(nx) for j in range(ny)):
        if start in seen:
            continue
        region: list[tuple[int, int]] = []
        stack = [start]
        seen.add(start)
        while stack:
            i, j = stack.pop()
            region.append((i, j))
            neighbours = [
                ((i + 1, j), True, xs[i + 1], ys[j], ys[j + 1]),
                ((i - 1, j), True, xs[i], ys[j], ys[j + 1]),
                ((i, j + 1), False, ys[j + 1], xs[i], xs[i + 1]),
                ((i, j - 1), False, ys[j], xs[i], xs[i + 1]),
            ]
            for (ni, nj), vertical, at, lo, hi in neighbours:
                inside = 0 <= ni < nx and 0 <= nj < ny
                if (
                    inside
                    and (ni, nj) not in seen
                    and not _covers(segments, vertical=vertical, at=at, lo=lo, hi=hi)
                ):
                    seen.add((ni, nj))
                    stack.append((ni, nj))
        x0 = min(xs[i] for i, _ in region)
        x1 = max(xs[i + 1] for i, _ in region)
        y0 = min(ys[j] for _, j in region)
        y1 = max(ys[j + 1] for _, j in region)
        rooms.append((x0, y0, x1, y1))
    return rooms


def _label_for(rect: tuple[float, float, float, float], texts: list[TextItem]) -> str | None:
    x0, y0, x1, y1 = rect
    inside = [t.text for t in texts if x0 <= t.position.x <= x1 and y0 <= t.position.y <= y1]
    return " ".join(inside) if inside else None


def _placeholder_openings(
    walls: list[Wall], bbox: tuple[float, float, float, float]
) -> list[Opening]:
    """STUB: Innenwände bekommen mittig eine Tür, Außenwände ein Fenster."""
    x0, y0, x1, y1 = bbox
    openings: list[Opening] = []
    for wall in walls:
        on_boundary = (
            abs(wall.start.x - wall.end.x) <= TOL
            and min(abs(wall.start.x - x0), abs(wall.start.x - x1)) <= TOL
        ) or (
            abs(wall.start.y - wall.end.y) <= TOL
            and min(abs(wall.start.y - y0), abs(wall.start.y - y1)) <= TOL
        )
        wall.is_exterior = on_boundary
        if on_boundary and wall.length_mm >= 2_000:
            openings.append(
                Opening(
                    id=f"win_{wall.id}",
                    type=OpeningType.WINDOW,
                    wall_id=wall.id,
                    offset_mm=(wall.length_mm - 1_260) / 2,
                    width_mm=1_260,
                    height_mm=1_385,
                    sill_height_mm=900,
                    confidence=0.2,
                )
            )
        elif not on_boundary and wall.length_mm >= 1_500:
            openings.append(
                Opening(
                    id=f"door_{wall.id}",
                    type=OpeningType.DOOR,
                    wall_id=wall.id,
                    offset_mm=(wall.length_mm - 885) / 2,
                    width_mm=885,
                    height_mm=2_010,
                    swing=DoorSwing.LEFT,
                    confidence=0.2,
                )
            )
    return openings


def recognize(
    parsed: ParsedPlan, *, min_wall_length_mm: float, wall_thickness_mm: float
) -> FloorPlan:
    candidates = [s for s in parsed.segments if s.length_mm >= min_wall_length_mm]
    if not candidates:
        raise NonRetryableError(
            "Keine Vektorgeometrie im Plan – Erkennung auf Rasterbildern ist noch nicht umgesetzt (STUB)"
        )

    walls = [
        Wall(
            id=f"wall_{i:03d}",
            start=s.start,
            end=s.end,
            thickness_mm=wall_thickness_mm,
            confidence=0.6,
        )
        for i, s in enumerate(candidates)
    ]
    xs = [p.x for s in candidates for p in (s.start, s.end)]
    ys = [p.y for s in candidates for p in (s.start, s.end)]
    bbox = (min(xs), min(ys), max(xs), max(ys))

    rects = _rooms_from_grid(candidates) or [bbox]
    rooms = [
        Room(
            id=f"room_{i:03d}",
            polygon=[
                Point2D(x=x0, y=y0),
                Point2D(x=x1, y=y0),
                Point2D(x=x1, y=y1),
                Point2D(x=x0, y=y1),
            ],
            label=_label_for((x0, y0, x1, y1), parsed.texts),
            confidence=0.6,
        )
        for i, (x0, y0, x1, y1) in enumerate(rects)
    ]

    return FloorPlan(
        project_id=parsed.project_id,
        source_key=parsed.source_key,
        source_format=parsed.source_format,
        page=parsed.page,
        walls=walls,
        openings=_placeholder_openings(walls, bbox),
        rooms=rooms,
        metadata={"recognizer": "grid-flood-fill", "openings": "placeholder"},
    )
