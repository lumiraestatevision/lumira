"""Treppen aus CAD-Linien.

Im Grundriss ist eine Treppe eine Kette schmaler Flächen zwischen den Trittkanten, die Kante an
Kante aneinanderhängen – gerade Läufe ebenso wie gewendelte Stufen. Ablauf (alles in mm):

  1. Linien und Kurven sammeln, doppelte entfernen.
  2. Lauflinie und Maßlinien entfernen, sie würden jede Stufe teilen: ab Antrittssymbol
     (Kreis/Raute) und Pfeilspitzen den Linienzug Ende an Ende verfolgen; Maßlinien an den
     Schrägstrichen an beiden Enden.
  3. Schnittsymbol schließen: zwei eng parallele Linien (Treppenschnitt) unterbrechen die Wange;
     ihre Enden werden verbunden, damit die Stufenflächen geschlossen bleiben.
  4. Rasterbild (1 cm) aus Wänden und Linien; freie, zusammenhängende Flächen sind Kandidaten.
  5. Nachbarschaft über gemeinsame Kanten. Eine unverzweigte Kette aus mindestens sechs Flächen
     mit stufentypischer Tiefe ist eine Treppe; schmale Streifen (Schnittsymbol) werden mit dem
     Nachbarn verschmolzen.
  6. Laufrichtung: Antrittssymbol (Kreis/Raute) am Anfang, sonst Pfeilspitze am Austritt.

Die Steigung ergibt sich aus Geschosshöhe / Anzahl der Flächen; die letzte Fläche (Austritt)
liegt auf Höhe des oberen Geschosses.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from lumira_shared.models import DEFAULT_FLOOR_TO_FLOOR_MM, Point2D, Segment, Stair, Stroke

STAIR_RASTER_MM = 10.0
TOUCH_MM = 2.0  # CAD-Linien treffen sich exakt
MIN_FACE_M2 = 0.02
MAX_FACE_M2 = 1.2
MIN_SHARED_MM = 200.0  # gemeinsame Trittkante zweier Stufen
MIN_STEPS = 6
TREAD_DEPTH_MM = (180.0, 420.0)  # Median der Auftrittstiefen auf der Lauflinie
SLIVER_RATIO = 0.55
CUT_PAIR_MM = (30.0, 300.0)  # Abstand der Doppellinie des Schnittsymbols
CUT_PAIR_MIN_LENGTH_MM = 500.0
RISE_MM = (140.0, 215.0)
SYMBOL_SIZE_MM = (80.0, 500.0)
ARROW_ARM_MM = (80.0, 450.0)
SYMBOL_REACH_MM = 800.0


# ------------------------------------------------------------------ Linien
def _array(points: list[Point2D]) -> np.ndarray:
    return np.array([[p.x, p.y] for p in points], dtype=np.float64)


def _lines(segments: list[Segment], curves: list[Stroke]) -> list[np.ndarray]:
    """Alle Linienzüge, deckungsgleiche nur einmal (Kurven doppeln oft Segmente)."""
    seen: set[tuple[int, ...]] = set()
    lines: list[np.ndarray] = []
    for points in [[s.start, s.end] for s in segments] + [c.points for c in curves]:
        arr = _array(points)
        if len(arr) < 2 or np.linalg.norm(np.diff(arr, axis=0), axis=1).sum() < 1.0:
            continue
        key = tuple(np.round(arr).astype(int).ravel())
        if key in seen or tuple(np.round(arr[::-1]).astype(int).ravel()) in seen:
            continue
        seen.add(key)
        lines.append(arr)
    return lines


def _is_closed(line: np.ndarray) -> bool:
    return len(line) > 3 and float(np.linalg.norm(line[0] - line[-1])) <= TOUCH_MM


# ------------------------------------------------------------------ Raster
@dataclass(slots=True)
class _Grid:
    origin: np.ndarray
    rows: int
    cols: int

    def px(self, pts: np.ndarray) -> np.ndarray:
        return np.round((pts - self.origin) / STAIR_RASTER_MM).astype(np.int32)

    def mm(self, px: np.ndarray) -> np.ndarray:
        return px.astype(np.float64) * STAIR_RASTER_MM + self.origin

    def contains(self, pts: np.ndarray) -> np.ndarray:
        p = self.px(pts)
        return (p[:, 0] >= 0) & (p[:, 0] < self.cols) & (p[:, 1] >= 0) & (p[:, 1] < self.rows)


def _grid(walls: list[np.ndarray]) -> _Grid:
    pts = np.vstack(walls)
    origin = pts.min(axis=0) - 1_000.0
    cols, rows = np.ceil((pts.max(axis=0) + 1_000.0 - origin) / STAIR_RASTER_MM).astype(int) + 1
    return _Grid(origin, int(rows), int(cols))


# ------------------------------------------------------------------ 2. Lauflinie entfernen
def _key(point: np.ndarray) -> tuple[int, int]:
    return round(float(point[0])), round(float(point[1]))


def _endpoint_index(lines: list[np.ndarray]) -> dict[tuple[int, int], list[int]]:
    index: dict[tuple[int, int], list[int]] = {}
    for i, line in enumerate(lines):
        if not _is_closed(line):
            for end in (line[0], line[-1]):
                index.setdefault(_key(end), []).append(i)
    return index


def start_symbols(lines: list[np.ndarray]) -> list[tuple[np.ndarray, float]]:
    """Kleine geschlossene Formen (Kreis, Raute am Antritt): Mittelpunkt und Radius."""
    found = []
    for line in lines:
        if _is_closed(line):
            size = float(np.linalg.norm(line.max(axis=0) - line.min(axis=0)))
            if SYMBOL_SIZE_MM[0] <= size <= SYMBOL_SIZE_MM[1]:
                found.append((line[:-1].mean(axis=0), size / 2))
    return found


def _angle(v: np.ndarray, w: np.ndarray) -> float:
    norm = float(np.linalg.norm(v)) * float(np.linalg.norm(w))
    if norm == 0:
        return math.nan  # Nullvektor (doppelter Punkt) – passt zu keinem Winkelkriterium
    cos = v @ w / norm
    return math.degrees(math.acos(float(np.clip(cos, -1.0, 1.0))))


def arrows(lines: list[np.ndarray]) -> list[tuple[np.ndarray, list[int]]]:
    """Pfeilspitzen: zwei kurze, gleich lange Schenkel mit gemeinsamem Endpunkt, 40–130° gespreizt,
    dazwischen der Schaft (ein weiterer Linienzug endet in der Spitze und läuft zwischen den
    Schenkeln zurück). Liefert Spitze und die Indizes der Schenkel."""
    leaving: dict[tuple[int, int], list[tuple[int, np.ndarray]]] = {}
    for i, line in enumerate(lines):
        if not _is_closed(line):
            leaving.setdefault(_key(line[0]), []).append((i, line[1] - line[0]))
            leaving.setdefault(_key(line[-1]), []).append((i, line[-2] - line[-1]))
    found = []
    for key, out in leaving.items():
        if len(out) < 3:
            continue
        arms = [
            (i, v)
            for i, v in out
            if len(lines[i]) == 2 and ARROW_ARM_MM[0] <= np.linalg.norm(v) <= ARROW_ARM_MM[1]
        ]
        for a in range(len(arms)):
            for b in range(a + 1, len(arms)):
                (i, v), (j, w) = arms[a], arms[b]
                lv, lw = float(np.linalg.norm(v)), float(np.linalg.norm(w))
                if abs(lv - lw) > 0.15 * max(lv, lw) or not 40.0 <= _angle(v, w) <= 130.0:
                    continue
                bisector = v / lv + w / lw
                if any(k not in (i, j) and _angle(s, bisector) <= 15.0 for k, s in out):
                    found.append((np.array(key, dtype=np.float64), [i, j]))
    return found


def walking_lines(
    lines: list[np.ndarray],
    starts: list[tuple[np.ndarray, float]],
    tips: list[tuple[np.ndarray, list[int]]],
) -> set[int]:
    """Indizes der Lauflinien samt Pfeilschenkeln. Die Lauflinie beginnt am Antrittssymbol bzw.
    endet an einer Pfeilspitze und läuft Ende an Ende weiter, bis sich Linien verzweigen. Ihre
    losen Enden liegen oft genau auf Trittkanten – deshalb die Verfolgung ab den Symbolen."""
    index = _endpoint_index(lines)
    removed: set[int] = set()

    def follow(i: int, came_from: tuple[int, int]) -> None:
        while i not in removed:
            removed.add(i)
            line = lines[i]
            far = line[-1] if _key(line[0]) == came_from else line[0]
            came_from = _key(far)
            others = [j for j in index.get(came_from, []) if j != i and j not in removed]
            if len(others) != 1:
                return
            i = others[0]

    for tip, arm_ids in tips:
        removed.update(arm_ids)
        for j in index.get(_key(tip), []):
            if j not in removed:
                follow(j, _key(tip))
    for centre, radius in starts:
        for key, ids in list(index.items()):
            if float(np.linalg.norm(np.array(key) - centre)) <= radius + TOUCH_MM:
                for j in ids:
                    if j not in removed:
                        follow(j, key)
    return removed


def dimension_lines(lines: list[np.ndarray]) -> set[int]:
    """Maßlinien mit Architekten-Schrägstrich: an beiden Enden kreuzt ein kurzer, 30–60° geneigter
    Strich mittig. Liegen sie in der Treppe (Lauflänge), zerteilen sie sonst jede Stufe."""
    ticks: dict[tuple[int, int], list[tuple[int, np.ndarray]]] = {}
    for i, line in enumerate(lines):
        if (
            len(line) == 2
            and ARROW_ARM_MM[0] <= np.linalg.norm(line[1] - line[0]) <= ARROW_ARM_MM[1]
        ):
            ticks.setdefault(_key((line[0] + line[1]) / 2), []).append((i, line[1] - line[0]))

    def tick_at(point: np.ndarray, direction: np.ndarray) -> int | None:
        x, y = _key(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, v in ticks.get((x + dx, y + dy), []):
                    if (
                        30.0 <= _angle(v, direction) <= 60.0
                        or 120.0 <= _angle(v, direction) <= 150.0
                    ):
                        return i
        return None

    removed: set[int] = set()
    for i, line in enumerate(lines):
        if len(line) != 2 or np.linalg.norm(line[1] - line[0]) < 300.0:
            continue
        direction = line[1] - line[0]
        first, last = tick_at(line[0], direction), tick_at(line[1], direction)
        if first is not None and last is not None:
            removed.update((i, first, last))
    return removed


# ------------------------------------------------------------------ 3. Schnittsymbol schließen
def cut_closures(lines: list[np.ndarray]) -> list[np.ndarray]:
    """Verbindungsstücke zwischen den Enden eng paralleler, gleich langer Geraden."""
    straight = [
        line
        for line in lines
        if len(line) == 2 and np.linalg.norm(line[1] - line[0]) >= CUT_PAIR_MIN_LENGTH_MM
    ]
    if len(straight) < 2:
        return []
    a = np.array([line[0] for line in straight])
    b = np.array([line[1] for line in straight])
    u = (b - a) / np.linalg.norm(b - a, axis=1)[:, None]
    parallel = np.abs(u @ u.T) >= math.cos(math.radians(1.5))
    rel = a[None, :, :] - a[:, None, :]  # a_j - a_i
    perp = np.abs(u[:, None, 0] * rel[..., 1] - u[:, None, 1] * rel[..., 0])
    near = (perp >= CUT_PAIR_MM[0]) & (perp <= CUT_PAIR_MM[1])
    reach = CUT_PAIR_MM[1] + 50.0

    def close(p: np.ndarray, q: np.ndarray) -> np.ndarray:
        return np.linalg.norm(p[:, None, :] - q[None, :, :], axis=2) <= reach

    same = close(a, a) & close(b, b)
    swapped = close(a, b) & close(b, a)
    closures: list[np.ndarray] = []
    for i, j in zip(*np.nonzero(parallel & near & (same | swapped)), strict=True):
        if i >= j:
            continue
        if same[i, j]:
            closures += [np.array([a[i], a[j]]), np.array([b[i], b[j]])]
        else:
            closures += [np.array([a[i], b[j]]), np.array([b[i], a[j]])]
    return closures


# ------------------------------------------------------------------ 4./5. Flächen und Ketten
@dataclass(slots=True)
class _Face:
    label: int
    area_mm2: float
    centroid: np.ndarray
    shared: dict[int, tuple[float, np.ndarray]] = field(default_factory=dict)  # Länge, Mitte


def _faces(
    labels: np.ndarray, stats: np.ndarray, centroids: np.ndarray, grid: _Grid
) -> dict[int, _Face]:
    px_mm2 = STAIR_RASTER_MM**2
    faces: dict[int, _Face] = {}
    for k in range(1, len(stats)):
        x, y, w, h, area = stats[k]
        if x == 0 or y == 0 or x + w >= grid.cols or y + h >= grid.rows:
            continue
        if MIN_FACE_M2 * 1e6 <= area * px_mm2 <= MAX_FACE_M2 * 1e6:
            faces[k] = _Face(k, float(area * px_mm2), grid.mm(centroids[k]))
    kernel = np.ones((5, 5), np.uint8)
    for k, face in faces.items():
        x, y, w, h, _ = stats[k]
        x0, y0 = max(x - 3, 0), max(y - 3, 0)
        crop = labels[y0 : y + h + 3, x0 : x + w + 3]
        mask = (crop == k).astype(np.uint8)
        ring = (cv2.dilate(mask, kernel) > 0) & (mask == 0)
        rows, cols = np.nonzero(ring)
        found = crop[rows, cols]
        for other in np.unique(found):
            if other == k or other not in faces:
                continue
            hit = found == other
            count = int(hit.sum())
            if count * STAIR_RASTER_MM >= MIN_SHARED_MM:
                mid = grid.mm(np.array([cols[hit].mean() + x0, rows[hit].mean() + y0]))
                face.shared[int(other)] = (count * STAIR_RASTER_MM, mid)
    # nur beidseitig bestätigte Nachbarn
    for k, face in faces.items():
        face.shared = {o: v for o, v in face.shared.items() if k in faces[o].shared}
    return faces


def _paths(faces: dict[int, _Face]) -> list[list[int]]:
    """Unverzweigte Ketten (Zusammenhangskomponenten mit Grad ≤ 2, keine Ringe)."""
    seen: set[int] = set()
    paths = []
    for start in faces:
        if start in seen or not faces[start].shared:
            continue
        component, queue = [], deque([start])
        seen.add(start)
        while queue:
            k = queue.popleft()
            component.append(k)
            for other in faces[k].shared:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        degrees = {k: len(faces[k].shared) for k in component}
        ends = [k for k, d in degrees.items() if d == 1]
        if len(component) < MIN_STEPS or max(degrees.values()) > 2 or len(ends) != 2:
            continue
        path, previous = [ends[0]], None
        while len(path) < len(component):
            nxt = next(o for o in faces[path[-1]].shared if o != previous)
            previous = path[-1]
            path.append(nxt)
        paths.append(path)
    return paths


@dataclass(slots=True)
class _Step:
    labels: list[int]
    area_mm2: float


def _centre(faces: dict[int, _Face], step: _Step) -> np.ndarray:
    weights = np.array([faces[k].area_mm2 for k in step.labels])
    return np.average([faces[k].centroid for k in step.labels], axis=0, weights=weights)


def _boundary(faces: dict[int, _Face], s1: _Step, s2: _Step) -> tuple[float, np.ndarray]:
    total, weighted = 0.0, np.zeros(2)
    for k in s1.labels:
        for other, (length, mid) in faces[k].shared.items():
            if other in s2.labels:
                total += length
                weighted += length * mid
    return total, weighted / max(total, 1e-9)


def _depths(faces: dict[int, _Face], steps: list[_Step]) -> list[float]:
    """Auftrittstiefe auf der Lauflinie: Abstand der Kantenmitten; Enden: Fläche / Kantenlänge."""
    bounds = [_boundary(faces, steps[i], steps[i + 1]) for i in range(len(steps) - 1)]
    depths = []
    for i, step in enumerate(steps):
        if 0 < i < len(steps) - 1:
            depths.append(float(np.linalg.norm(bounds[i][1] - bounds[i - 1][1])))
        else:
            length = bounds[0][0] if i == 0 else bounds[-1][0]
            depths.append(step.area_mm2 / max(length, 1.0))
    return depths


def merge_slivers(faces: dict[int, _Face], path: list[int]) -> list[_Step]:
    steps = [_Step([k], faces[k].area_mm2) for k in path]
    while len(steps) >= 3:
        depths = _depths(faces, steps)
        median = float(np.median(depths[1:-1]))
        i = int(np.argmin(depths))
        if depths[i] >= SLIVER_RATIO * median:
            break
        neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(steps)]
        j = min(neighbours, key=lambda n: depths[n])
        lo, hi = min(i, j), max(i, j)
        steps[lo : hi + 1] = [
            _Step(steps[lo].labels + steps[hi].labels, steps[lo].area_mm2 + steps[hi].area_mm2)
        ]
    return steps


def _polygon(labels: np.ndarray, step: _Step, grid: _Grid) -> list[Point2D]:
    mask = np.isin(labels, step.labels).astype(np.uint8)
    rows, cols = np.nonzero(mask)
    y0, y1, x0, x1 = rows.min() - 2, rows.max() + 3, cols.min() - 2, cols.max() + 3
    # 1 px aufweiten: schließt die Trittkante zwischen verschmolzenen Flächen, Stufen stoßen stumpf
    crop = cv2.dilate(mask[y0:y1, x0:x1], np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(crop, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    simplified = cv2.approxPolyDP(contour, 1.0, True).reshape(-1, 2) + np.array([x0, y0])
    return [Point2D(x=float(x), y=float(y)) for x, y in grid.mm(simplified)]


def _orient(
    steps: list[_Step], faces: dict[int, _Face], starts: list[np.ndarray], tips: list[np.ndarray]
) -> tuple[list[_Step], bool]:
    """Unten → oben. Liefert (Stufen, Richtung sicher)."""
    first, last = _centre(faces, steps[0]), _centre(faces, steps[-1])

    def nearest(points: list[np.ndarray], target: np.ndarray) -> float:
        return min((float(np.linalg.norm(p - target)) for p in points), default=math.inf)

    for symbols, symbol_at_start in ((starts, True), (tips, False)):
        d_first, d_last = nearest(symbols, first), nearest(symbols, last)
        if min(d_first, d_last) <= SYMBOL_REACH_MM and d_first != d_last:
            at_first = d_first < d_last
            return (steps if at_first == symbol_at_start else steps[::-1]), True
    return steps, False


# ------------------------------------------------------------------ Gesamt
def find_stairs(
    segments: list[Segment],
    curves: list[Stroke],
    walls: list[np.ndarray],
    floor_to_floor_mm: float = DEFAULT_FLOOR_TO_FLOOR_MM,
) -> list[Stair]:
    if not walls:
        return []
    grid = _grid(walls)
    lines = [line for line in _lines(segments, curves) if grid.contains(line).all()]
    if not lines:
        return []
    starts, tips = start_symbols(lines), arrows(lines)
    removed = walking_lines(lines, starts, tips) | dimension_lines(lines)
    kept = [line for i, line in enumerate(lines) if i not in removed and not _is_closed(line)]
    kept += cut_closures(kept)

    blocked = np.zeros((grid.rows, grid.cols), dtype=np.uint8)
    for polygon in walls:
        cv2.fillPoly(blocked, [grid.px(polygon)], 1)
    for line in kept:
        cv2.polylines(blocked, [grid.px(line)], False, 1, thickness=1)
    _, labels, stats, centroids = cv2.connectedComponentsWithStats(
        (blocked == 0).astype(np.uint8), connectivity=4
    )
    faces = _faces(labels, stats, centroids, grid)

    stairs = []
    for path in _paths(faces):
        steps = merge_slivers(faces, path)
        if len(steps) < MIN_STEPS:
            continue
        depths = _depths(faces, steps)
        if not TREAD_DEPTH_MM[0] <= float(np.median(depths[1:-1])) <= TREAD_DEPTH_MM[1]:
            continue
        steps, sure = _orient(steps, faces, [c for c, _ in starts], [t for t, _ in tips])
        polygons = [_polygon(labels, step, grid) for step in steps]
        walking = [Point2D(x=float(p[0]), y=float(p[1])) for p in _walking_line(faces, steps)]
        hull = cv2.convexHull(np.vstack([_array(p) for p in polygons]).astype(np.float32))
        rise = floor_to_floor_mm / len(steps)
        stairs.append(
            Stair(
                id=f"stair_{len(stairs):02d}",
                steps=polygons,
                rise_mm=round(rise, 1),
                floor_to_floor_mm=floor_to_floor_mm,
                walking_line=walking,
                outline=[Point2D(x=float(x), y=float(y)) for x, y in hull.reshape(-1, 2)],
                confidence=0.8 if sure and RISE_MM[0] <= rise <= RISE_MM[1] else 0.5,
            )
        )
    return stairs


def _walking_line(faces: dict[int, _Face], steps: list[_Step]) -> list[np.ndarray]:
    """Schwerpunkt der ersten Stufe → Mitten der Trittkanten → Schwerpunkt des Austritts."""
    mids = [_boundary(faces, steps[i], steps[i + 1])[1] for i in range(len(steps) - 1)]
    return [_centre(faces, steps[0]), *mids, _centre(faces, steps[-1])]
