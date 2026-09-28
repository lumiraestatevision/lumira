"""Außenbereiche (Terrasse, Balkon, Loggia) in CAD-Plänen.

Außenbereiche sind selten gefüllt – gezeichnet werden Umriss, Belag (Dielen-/Plattenraster)
und Stützen. Ablauf:
  1. Anker: Beschriftung „Terrasse“, „Balkon“ … außerhalb der Wände.
  2. Belagsschraffur ausblenden: viele gleich lange, parallele Linien in gleichem Abstand.
  3. Fläche: Flutfüllung ab der Beschriftung zwischen den übrigen Linien, Wänden und
     geschlossenen Öffnungen. Läuft sie bis zum Rand, war der Umriss offen → verworfen.
  4. An die Hauswand anschließen: Zwischen Belag und tragender Wand liegt oft die Dämmschicht
     (Schraffur, keine Wandfläche) – die Lücke wird geschlossen, sonst endet die Terrasse im
     Modell vor einer Kante.
  5. Stützen: Quadrate mit Kreuz (zwei gleich lange, senkrechte Diagonalen mit gemeinsamer
     Mitte) am Rand der Fläche – nicht an der Wand (dort sind es Laibungssymbole).
  6. „überdacht“ / „Loggia“ → Außenbereich mit Dach.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import cv2
import numpy as np

from lumira_shared.models import FilledArea, Point2D, Segment, Stroke, TextItem

OUTDOOR_LABEL = re.compile(r"terrasse|balkon|loggia|freisitz|sitzplatz|dachgarten", re.IGNORECASE)
ROOFED_LABEL = re.compile(r"[üu]e?berdacht|[üu]e?berdeckt|loggia", re.IGNORECASE)
RASTER_MM = 20.0
MARGIN_MM = 12_000.0  # so weit darf ein Außenbereich über das Haus hinausreichen
SNAP_MM = 800.0  # größte Lücke zwischen Belag und Wand (Dämmung + Fassadenlinie)
MIN_AREA_M2 = 1.0
HATCH_MIN_LINES = 5
HATCH_MAX_SPACING_MM = 1_000.0
COLUMN_SIZE_MM = (80.0, 600.0)
COLUMN_REACH_MM = 500.0  # Stützen stehen auf dem Umriss, nicht zwingend darin
COLUMN_WALL_CLEARANCE_MM = 60.0


@dataclass(slots=True)
class ColumnSymbol:
    center: np.ndarray
    size: float
    angle_deg: float


@dataclass(slots=True)
class OutdoorArea:
    polygon: list[Point2D]
    roofed: bool
    columns: list[ColumnSymbol] = field(default_factory=list)


def _points(segment: Segment) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.array([segment.start.x, segment.start.y]),
        np.array([segment.end.x, segment.end.y]),
    )


@dataclass(slots=True)
class _Line:
    index: int
    offset: float  # Abstand quer zur Richtung
    t0: float  # Ausdehnung entlang der Richtung
    t1: float


def _overlap(a: _Line, b: _Line) -> bool:
    shared = min(a.t1, b.t1) - max(a.t0, b.t0)
    return shared >= 0.6 * min(a.t1 - a.t0, b.t1 - b.t0)


def hatch_lines(segments: list[Segment], ignore: set[int] | None = None) -> Hatch:
    """Indizes der Schraffurlinien (Dielen, Plattenfugen): Die Linie gehört zu einer Folge von
    ≥ 5 parallelen, sich überdeckenden Linien in gleichem Abstand. Einzelne Doppel- und
    Dreifachlinien (Umriss, Fassade, Balken) bleiben. Einzelne Dielen dürfen kürzer sein
    (Aussparung an einer Stütze). ``ignore``: Linien, die nicht mitzählen (Beschriftungsrahmen
    über der Schraffur)."""
    by_angle: dict[int, list[_Line]] = defaultdict(list)
    for i, segment in enumerate(segments):
        a, b = _points(segment)
        if (ignore and i in ignore) or float(np.linalg.norm(b - a)) < 200.0:
            continue
        angle = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
        u = np.array([math.cos(angle), math.sin(angle)])
        n = np.array([-u[1], u[0]])
        t0, t1 = sorted((float(a @ u), float(b @ u)))
        by_angle[round(math.degrees(angle)) % 180].append(_Line(i, float(a @ n), t0, t1))

    hatch = Hatch()
    window = HATCH_MIN_LINES * HATCH_MAX_SPACING_MM
    for lines in by_angle.values():
        if len(lines) < HATCH_MIN_LINES:
            continue
        lines.sort(key=lambda line: line.offset)
        offsets = [line.offset for line in lines]
        for line in lines:
            lo = bisect_left(offsets, line.offset - window)
            hi = bisect_right(offsets, line.offset + window)
            # je Abstand nur einmal – CAD-Exporte zeichnen Schraffurlinien oft doppelt
            near = sorted(
                {
                    round(other.offset - line.offset)
                    for other in lines[lo:hi]
                    if abs(other.offset - line.offset) >= 1.0 and _overlap(line, other)
                }
            )
            if len(near) < HATCH_MIN_LINES - 1:
                continue
            # Abstand zum Nachbarn – die letzte Diele liegt dicht am Randbalken, daher die drei
            # nächsten Abstände als Raster probieren
            spacings = sorted({round(abs(d)) for d in near})[:3]
            runs = [_run(near, s) for s in spacings if 1 <= s <= HATCH_MAX_SPACING_MM]
            best = max(runs, default=(0, 0, 0), key=lambda r: r[0] + r[1] + 1)
            if best[0] + best[1] + 1 >= HATCH_MIN_LINES:
                hatch.lines.add(line.index)
                if not (best[0] and best[1]):
                    hatch.ends.add(line.index)
    return hatch


@dataclass(slots=True)
class Hatch:
    lines: set[int] = field(default_factory=set)
    # erste/letzte Linie einer Folge – kann auch die Umrisslinie im Dielenraster sein
    ends: set[int] = field(default_factory=set)


def _run(near: Sequence[float], spacing: float) -> tuple[int, int, float]:
    """Linien der Folge im Abstand ``spacing`` oberhalb und unterhalb der Linie selbst. Liegen
    weitere Linien dazwischen, ist ``spacing`` ein Vielfaches des Rasters (die Linie gehört nicht
    dazu, z. B. Umriss mit drei Dielen Abstand) → leere Folge."""
    tolerance = 0.15 * spacing
    counts = []
    for sign in (1, -1):
        count, expected = 0, sign * spacing
        while any(abs(d - expected) <= tolerance for d in near):
            count += 1
            expected += sign * spacing
        counts.append(count)
    up, down = counts
    span = [d for d in near if -down * spacing - tolerance <= d <= up * spacing + tolerance]
    if len(span) > up + down:
        return 0, 0, spacing
    return up, down, spacing


def label_frames(
    segments: list[Segment], fills: list[FilledArea], texts: list[TextItem]
) -> set[int]:
    """Rahmen von Beschriftungsfeldern: weiß hinterlegte Kästen um einen Text (decken die
    Schraffur ab). Ihre Kanten sind keine Grenzen des Außenbereichs."""
    boxes = []
    for fill in fills:
        rgb = fill.color
        if not rgb or len(rgb) != 7 or min(int(rgb[k : k + 2], 16) for k in (1, 3, 5)) < 235:
            continue
        pts = np.array([[p.x, p.y] for p in fill.polygon], np.float32)
        if len(pts) >= 3 and any(
            cv2.pointPolygonTest(pts, (t.position.x, t.position.y), False) >= 0 for t in texts
        ):
            boxes.append(pts)
    frames: set[int] = set()
    bounds = [(box.min(axis=0) - 5.0, box.max(axis=0) + 5.0) for box in boxes]
    for i, segment in enumerate(segments):
        a, b = _points(segment)
        for box, (lo, hi) in zip(boxes, bounds, strict=True):
            if not (np.all(a >= lo) and np.all(a <= hi) and np.all(b >= lo) and np.all(b <= hi)):
                continue
            if all(
                abs(cv2.pointPolygonTest(box, (float(p[0]), float(p[1])), True)) <= 5.0
                for p in (a, b, (a + b) / 2)
            ):
                frames.add(i)
                break
    return frames


def column_symbols(segments: list[Segment]) -> list[ColumnSymbol]:
    """Quadrate mit Kreuz: zwei gleich lange, senkrechte Linien mit gemeinsamer Mitte."""
    low, high = (s * math.sqrt(2) for s in COLUMN_SIZE_MM)
    candidates = []
    for segment in segments:
        a, b = _points(segment)
        length = float(np.linalg.norm(b - a))
        if low <= length <= high:
            candidates.append(((a + b) / 2, length, (b - a) / length))
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (mid, _, _) in enumerate(candidates):
        buckets[(round(mid[0] / 50), round(mid[1] / 50))].append(i)

    found: list[ColumnSymbol] = []
    for i, (mid, length, u) in enumerate(candidates):
        bx, by = round(mid[0] / 50), round(mid[1] / 50)
        for j in (j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in buckets[(bx + dx, by + dy)]):
            if j <= i:
                continue
            mid2, length2, u2 = candidates[j]
            if (
                np.linalg.norm(mid2 - mid) > 15.0
                or abs(length2 - length) > 0.05 * length
                or abs(float(u @ u2)) > math.sin(math.radians(3))
            ):
                continue
            centre = (mid + mid2) / 2
            if any(np.linalg.norm(c.center - centre) < 20.0 for c in found):
                continue
            # Kanten des Quadrats liegen 45° zu den Diagonalen
            edge = u + (u2 if _cross(u, u2) > 0 else -u2)
            angle = math.degrees(math.atan2(edge[1], edge[0])) % 90
            found.append(ColumnSymbol(centre, (length + length2) / 2 / math.sqrt(2), angle))
    return found


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


@dataclass(slots=True)
class _Grid:
    origin: np.ndarray
    shape: tuple[int, int]  # (Zeilen, Spalten)

    def px(self, pts: np.ndarray) -> np.ndarray:
        return np.round((pts - self.origin) / RASTER_MM).astype(np.int32)

    def mm(self, px: np.ndarray) -> np.ndarray:
        return px.astype(np.float64) * RASTER_MM + self.origin

    def contains(self, col: int, row: int) -> bool:
        return 0 <= row < self.shape[0] and 0 <= col < self.shape[1]


def _free_near(blocked: np.ndarray, col: int, row: int, radius: int = 15) -> tuple[int, int] | None:
    """Nächster freier Rasterpunkt – die Beschriftung kann auf einer Belagslinie sitzen."""
    h, w = blocked.shape
    for r in range(radius + 1):
        for dr in range(-r, r + 1):
            for dc in range(-r, r + 1):
                if max(abs(dr), abs(dc)) != r:
                    continue
                y, x = row + dr, col + dc
                if 0 <= y < h and 0 <= x < w and not blocked[y, x]:
                    return x, y
    return None


def find_outdoor(
    segments: list[Segment],
    curves: list[Stroke],
    fills: list[FilledArea],
    texts: list[TextItem],
    house: list[np.ndarray],
    is_outside: Callable[[float, float], bool],
) -> list[OutdoorArea]:
    """``house``: Wandflächen und geschlossene Öffnungen (Polygone in mm). ``texts``: Anker-
    Kandidaten (Texte in bereits erkannten Räumen sind ausgenommen)."""
    seeds = [
        t for t in texts if OUTDOOR_LABEL.search(t.text) and is_outside(t.position.x, t.position.y)
    ]
    if not seeds or not house:
        return []
    corners = np.vstack(house)
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    house_area_m2 = float(np.prod(hi - lo)) / 1e6
    origin = lo - MARGIN_MM
    cols, rows = (np.ceil((hi + MARGIN_MM - origin) / RASTER_MM).astype(int) + 1).tolist()
    grid = _Grid(origin, (rows, cols))

    walls = np.zeros(grid.shape, np.uint8)
    for polygon in house:
        cv2.fillPoly(walls, [grid.px(polygon)], 1)
    walls = np.asarray(cv2.dilate(walls, np.ones((3, 3), np.uint8)), dtype=np.uint8)
    frames = label_frames(segments, fills, texts)
    hatch = hatch_lines(segments, ignore=frames)

    def draw(indices: set[int] | range, target: np.ndarray) -> np.ndarray:
        for i in indices:
            a, b = _points(segments[i])
            cv2.line(target, tuple(grid.px(a).tolist()), tuple(grid.px(b).tolist()), 1, 1)
        return target

    lines = draw(set(range(len(segments))) - hatch.lines - frames, np.zeros(grid.shape, np.uint8))
    for curve in curves:
        pts = grid.px(np.array([[p.x, p.y] for p in curve.points]))
        cv2.polylines(lines, [pts], False, 1, 1)
    kernel = np.ones((3, 3), np.uint8)
    blocked = np.maximum(np.asarray(cv2.dilate(lines, kernel), dtype=np.uint8), walls)
    # Zweiter Versuch mit den Randlinien der Schraffur: liegt der Umriss genau im Dielenraster,
    # wurde er mit der Schraffur ausgeblendet und die Flutfüllung läuft aus
    ends = cv2.dilate(draw(hatch.ends, lines.copy()), kernel)
    with_ends = np.maximum(np.asarray(ends, dtype=np.uint8), walls)

    def region_from(start: tuple[int, int], barrier: np.ndarray) -> np.ndarray | None:
        flood = barrier.copy()
        mask = np.zeros((rows + 2, cols + 2), np.uint8)
        cv2.floodFill(flood, mask, start, 2)
        region = flood == 2
        if region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any():
            return None  # Umriss offen – Flutfüllung ins Freie gelaufen
        area_m2 = float(region.sum()) * RASTER_MM**2 / 1e6
        return region if MIN_AREA_M2 <= area_m2 <= 1.5 * house_area_m2 else None

    free_of_walls = cv2.distanceTransform((walls == 0).astype(np.uint8), cv2.DIST_L2, 3) * RASTER_MM
    symbols = column_symbols(segments)
    snap = int(SNAP_MM / RASTER_MM) | 1
    claimed = np.zeros(grid.shape, bool)
    areas: list[OutdoorArea] = []
    for seed in seeds:
        col, row = grid.px(np.array([seed.position.x, seed.position.y])).tolist()
        if not grid.contains(col, row) or claimed[row, col]:
            continue
        region = start = None
        for barrier in (blocked, with_ends):
            start = _free_near(barrier, col, row)
            region = region_from(start, barrier) if start is not None else None
            if region is not None:
                break
        if region is None or start is None:
            continue

        # Lücke zur Hauswand schließen, aber nur als Fortsetzung dieser Fläche
        closed = cv2.morphologyEx(
            (region | walls.astype(bool)).astype(np.uint8),
            cv2.MORPH_CLOSE,
            np.ones((snap, snap), np.uint8),
        )
        snapped = (closed > 0) & (walls == 0)
        _, labels = cv2.connectedComponents(snapped.astype(np.uint8), connectivity=4)
        final = labels == labels[start[1], start[0]]
        claimed |= final

        contours, _ = cv2.findContours(
            final.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        contour = max(contours, key=cv2.contourArea)
        simplified = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2)
        if len(simplified) < 3:
            continue
        polygon = [Point2D(x=float(x), y=float(y)) for x, y in grid.mm(simplified)]

        near = cv2.distanceTransform((~region).astype(np.uint8), cv2.DIST_L2, 3) * RASTER_MM
        columns = []
        for symbol in symbols:
            c, r = grid.px(symbol.center).tolist()
            if not grid.contains(c, r) or near[r, c] > COLUMN_REACH_MM:
                continue
            if free_of_walls[r, c] <= symbol.size / 2 + COLUMN_WALL_CLEARANCE_MM:
                continue  # Laibungssymbol an Fenster/Tür
            columns.append(symbol)

        inside = [
            t
            for t in texts
            if grid.contains(*(p := grid.px(np.array([t.position.x, t.position.y])).tolist()))
            and final[p[1], p[0]]
        ]
        roofed = any(ROOFED_LABEL.search(t.text) for t in [seed, *inside])
        areas.append(OutdoorArea(polygon, roofed, columns))
    return areas
