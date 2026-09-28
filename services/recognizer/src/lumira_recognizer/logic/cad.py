"""Erkennung für CAD-Vektorpläne mit gefüllten Wänden (typischer Export von Archicad,
Allplan, Vectorworks & Co.).

Ablauf – alles in Millimetern, ohne Lernverfahren:
  1. Wandfarbe: die Füllfarbe mit der größten Fläche an Streifen in Wanddicke (Grau, Orange …).
  2. Jede Wandfläche wird eine Wand mit exaktem Grundriss (``footprint``, auch Gehrung/L-Form
     oder ein ganzer verzweigter Wandzug als ein Polygon).
  3. Öffnungen: Zwei Wandstirnseiten, die sich über eine Lücke von 0,45–3,6 m exakt gegenüber
     stehen, begrenzen eine Öffnung. Die Lücke wird eine eigene Wand mit Öffnung – so entstehen
     im 3D-Modell Sturz und Brüstung.
     Tür: ein Aufschlagbogen im Radius der Lückenbreite. Fenster: Lücke in einer Außenwand.
     Sonst: Durchgang. Scheinöffnungen, die nur einen Zwickel abschließen, fallen weg.
  4. Außen/innen: Rasterbild aus Wänden + geschlossenen Öffnungen, Flutfüllung vom Rand.
  5. Räume: farbig hinterlegte Flächen; ohne solche die von Wänden umschlossenen Flächen.
  6. Beschriftung: alle Texte im Raum („Küche“, „F: 13,39 m²“), reine Maßzahlen ausgenommen.
  7. Treppen: Ketten von Stufenflächen zwischen Trittkanten (stairs.py).
  8. Terrassen, Balkone: Fläche um die Beschriftung außerhalb der Wände, Stützen (outdoor.py).

Grenzen: Wände ohne Füllung (nur Doppellinien, Schraffur) → Rückfall auf detector.py.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import cv2
import numpy as np

from lumira_recognizer.logic.furniture import find_furniture
from lumira_recognizer.logic.outdoor import find_outdoor, hatch_lines
from lumira_recognizer.logic.stairs import find_stairs
from lumira_shared.models import (
    Column,
    DoorSwing,
    FilledArea,
    FloorPlan,
    Furniture,
    Opening,
    OpeningType,
    ParsedPlan,
    Point2D,
    Room,
    Segment,
    Stair,
    Stroke,
    TextItem,
    Wall,
    polygon_area_mm2,
)

MIN_OPENING_MM = 450.0
MAX_OPENING_MM = 3_600.0
MIN_ROOM_M2 = 1.0
DOOR_HEIGHT_MM = 2_010.0
WINDOW_SILL_MM = 900.0
WINDOW_HEIGHT_MM = 1_385.0
FRENCH_WINDOW_MIN_MM = 1_800.0  # breite Außenöffnungen sind meist bodentief (Terrasse, Balkon)
FRENCH_WINDOW_HEIGHT_MM = 2_135.0
RASTER_MM = 20.0  # Auflösung für Außen-/Innen-Bestimmung und Raumsuche

_NUMBERS_ONLY = re.compile(r"^[\d\s.,:;+\-±/²³⁵]*$")


# ------------------------------------------------------------------ Farben
def _rgb(color: str | None) -> tuple[int, int, int] | None:
    if not color or len(color) != 7:
        return None
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _is_achromatic_dark(color: str | None) -> bool:
    rgb = _rgb(color)
    return rgb is not None and max(rgb) - min(rgb) <= 12 and max(rgb) <= 200


def _is_room_color(color: str | None, wall_color: str) -> bool:
    rgb = _rgb(color)
    if rgb is None or color == wall_color:
        return False
    return max(rgb) - min(rgb) >= 20  # bunt (nicht weiß/grau/schwarz)


WALL_THICKNESS_MM = (50.0, 600.0)  # mittlere Dicke eines Wandstreifens
MIN_WALL_FILL_MM2 = 50_000.0  # 0,05 m² – kleinere Flächen sind Symbole, Schraffurpunkte …


def _mean_thickness(points: np.ndarray, area: float) -> float:
    """Mittlere Dicke eines Streifens: 2 · Fläche / Umfang (auch für verzweigte Wandzüge)."""
    perimeter = float(np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1).sum())
    return 2 * area / perimeter if perimeter else 0.0


def _is_near_white(color: str) -> bool:
    rgb = _rgb(color)
    return rgb is not None and min(rgb) >= 235


def wall_color(areas: list[FilledArea]) -> str | None:
    """Wandfarbe nach Form statt nach Farbton: die Füllfarbe, deren Flächen am meisten
    Wandstreifen sind (mittlere Dicke 5–60 cm). Büros zeichnen Wände grau (Muster1), orange,
    schwarz …; Räume sind dagegen breite Flächen, Beschriftungsfelder weiß.

    Gleichstand: dunkle unbunte Farbe bevorzugt (klassischer CAD-Export)."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for area in areas:
        if not area.color or _is_near_white(area.color):
            continue
        size = polygon_area_mm2(area.polygon)
        if size < MIN_WALL_FILL_MM2:
            continue
        thickness = _mean_thickness(_array(area.polygon), size)
        if WALL_THICKNESS_MM[0] <= thickness <= WALL_THICKNESS_MM[1]:
            totals[area.color] += size
            counts[area.color] += 1
    candidates = [c for c in totals if counts[c] >= 3]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (totals[c], _is_achromatic_dark(c)))


# ------------------------------------------------------------------ Geometrie
def _array(points: list[Point2D]) -> np.ndarray:
    return np.array([[p.x, p.y] for p in points], dtype=np.float64)


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    """z-Komponente des Kreuzprodukts zweier 2D-Vektoren (NumPy 2 kennt nur 3D)."""
    return float(a[0] * b[1] - a[1] * b[0])


def _centroid(points: np.ndarray) -> np.ndarray:
    return points.mean(axis=0)


def _inside(points: np.ndarray, x: float, y: float) -> bool:
    return cv2.pointPolygonTest(points.astype(np.float32), (float(x), float(y)), False) > 0


@dataclass(slots=True)
class Piece:
    """Eine gefüllte Wandfläche."""

    index: int
    points: np.ndarray
    wall: Wall


def _axis(points: np.ndarray) -> tuple[Point2D, Point2D, float]:
    """Mittelachse und Dicke über das kleinste umschließende Rechteck."""
    (cx, cy), (w, h), angle = cv2.minAreaRect(points.astype(np.float32))
    length, thickness = (w, h) if w >= h else (h, w)
    rad = math.radians(angle if w >= h else angle + 90)
    dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
    area = polygon_area_mm2([Point2D(x=float(x), y=float(y)) for x, y in points])
    # L-Formen: umschließendes Rechteck ist zu dick → mittlere Dicke aus Fläche/Länge
    thickness = min(thickness, area / length) if length > 0 else thickness
    return (
        Point2D(x=cx - dx, y=cy - dy),
        Point2D(x=cx + dx, y=cy + dy),
        max(float(thickness), 10.0),
    )


def _shape_key(points: np.ndarray) -> frozenset[tuple[int, int]]:
    """Gleiche Fläche unabhängig von Startpunkt und Umlaufsinn (auf 5 mm gerundet)."""
    return frozenset((round(x / 5), round(y / 5)) for x, y in points)


def _pieces(areas: list[FilledArea], color: str) -> list[Piece]:
    pieces: list[Piece] = []
    seen: set[frozenset[tuple[int, int]]] = set()
    for area in areas:
        if area.color != color:
            continue
        points = _array(area.polygon)
        # CAD-Exporte zeichnen gemeinsame Bauteile oft doppelt (z. B. Gartenmauer je
        # Haushälfte). Deckungsgleiche Körper stören Licht und Schatten im 3D-Modell.
        key = _shape_key(points)
        if key in seen:
            continue
        seen.add(key)
        start, end, thickness = _axis(points)
        if start.distance_to(end) < 20.0:
            continue
        index = len(pieces)
        pieces.append(
            Piece(
                index,
                points,
                Wall(
                    id=f"wall_{index:03d}",
                    start=start,
                    end=end,
                    thickness_mm=min(thickness, 2_000.0),
                    footprint=area.polygon,
                    confidence=0.9,
                ),
            )
        )
    return pieces


# ------------------------------------------------------------------ Öffnungen
@dataclass(slots=True)
class Edge:
    piece: int
    a: np.ndarray
    b: np.ndarray

    @property
    def mid(self) -> np.ndarray:
        return (self.a + self.b) / 2

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.b - self.a))

    @property
    def direction(self) -> np.ndarray:
        return (self.b - self.a) / self.length


@dataclass(slots=True)
class Gap:
    left: Edge
    right: Edge

    @property
    def width(self) -> float:
        return float(np.linalg.norm(self.right.mid - self.left.mid))

    @property
    def thickness(self) -> float:
        return (self.left.length + self.right.length) / 2

    def quad(self) -> np.ndarray:
        a, b = self.left.a, self.left.b
        c, d = self.right.a, self.right.b
        # gegenüberliegende Kanten laufen gegensinnig → c/d so ordnen, dass ein Viereck entsteht
        if np.linalg.norm(a - c) < np.linalg.norm(a - d):
            c, d = d, c
        return np.array([a, b, c, d])


def _jamb_edges(pieces: list[Piece]) -> list[Edge]:
    edges: list[Edge] = []
    for piece in pieces:
        pts = piece.points
        for i in range(len(pts)):
            edge = Edge(piece.index, pts[i], pts[(i + 1) % len(pts)])
            if 60.0 <= edge.length <= 600.0:
                edges.append(edge)
    return edges


def _wall_behind(edge: Edge, away: np.ndarray, pieces: list[Piece]) -> bool:
    """Liegt die Wandfläche direkt hinter der Stirnseite (von der Lücke abgewandt)?
    Lokal geprüft statt über den Schwerpunkt – verzweigte Wandzüge (ein Polygon für den
    ganzen Grundriss) haben ihren Schwerpunkt irgendwo."""
    x, y = edge.mid + away / np.linalg.norm(away) * 20.0
    return _inside(pieces[edge.piece].points, x, y)


def _faces(e1: Edge, e2: Edge, pieces: list[Piece]) -> bool:
    """Stehen sich zwei Stirnseiten über eine Lücke gegenüber? Beide dürfen zum selben
    Wandzug gehören (Tür zwischen zwei Ästen eines verzweigten Polygons)."""
    if e1 is e2:
        return False
    if abs(_cross(e1.direction, e2.direction)) > math.sin(math.radians(3)):
        return False
    if min(e1.length, e2.length) / max(e1.length, e2.length) < 0.6:
        return False
    delta = e2.mid - e1.mid
    lateral = abs(float(np.dot(delta, e1.direction)))  # Versatz entlang der Stirnseite
    width = abs(_cross(e1.direction, delta))  # Abstand quer dazu = Lückenbreite
    if lateral > 0.25 * e1.length or not MIN_OPENING_MM <= width <= MAX_OPENING_MM:
        return False
    # Die Wände liegen jeweils auf der abgewandten Seite ihrer Stirnseite.
    return _wall_behind(e1, -delta, pieces) and _wall_behind(e2, delta, pieces)


def _gap_is_free(gap: Gap, pieces: list[Piece]) -> bool:
    """In der Lücke darf keine andere Wand stehen."""
    start, end = gap.left.mid, gap.right.mid
    for t in (0.2, 0.35, 0.5, 0.65, 0.8):
        x, y = start + (end - start) * t
        if any(_inside(p.points, x, y) for p in pieces):
            return False
    return True


def find_gaps(pieces: list[Piece]) -> list[Gap]:
    edges = _jamb_edges(pieces)
    candidates = [
        Gap(e1, e2) for i, e1 in enumerate(edges) for e2 in edges[i + 1 :] if _faces(e1, e2, pieces)
    ]
    candidates.sort(key=lambda g: g.width)
    used: set[int] = set()
    gaps: list[Gap] = []
    for gap in candidates:
        ids = {id(gap.left), id(gap.right)}
        if ids & used or not _gap_is_free(gap, pieces):
            continue
        used |= ids
        gaps.append(gap)
    return gaps


@dataclass(slots=True)
class Aperture:
    """Eine Öffnung über die ganze Wanddicke. Mehrschalige Wände (Mauerwerk, Dämmung, Putz
    als eigene Flächen) liefern je Schicht eine Lücke – die werden hier zusammengefasst."""

    start: np.ndarray  # Achse quer durch die Öffnung, in der Mitte der Wanddicke
    end: np.ndarray
    thickness: float
    corners: np.ndarray  # alle Laibungsecken – mögliche Drehpunkte eines Türblatts
    pieces: set[int]
    quads: list[np.ndarray]

    @property
    def width(self) -> float:
        return float(np.linalg.norm(self.end - self.start))


def _same_opening(g1: Gap, g2: Gap) -> bool:
    u = (g1.right.mid - g1.left.mid) / g1.width
    u2 = (g2.right.mid - g2.left.mid) / g2.width
    if abs(_cross(u, u2)) > math.sin(math.radians(3)) or abs(g1.width - g2.width) > 40.0:
        return False
    delta = (g2.left.mid + g2.right.mid) / 2 - (g1.left.mid + g1.right.mid) / 2
    along, across = abs(float(np.dot(delta, u))), abs(_cross(u, delta))
    return along <= 60.0 and across <= (g1.thickness + g2.thickness) / 2 + 40.0


def apertures(gaps: list[Gap]) -> list[Aperture]:
    groups: list[list[Gap]] = []
    for gap in gaps:
        for group in groups:
            if any(_same_opening(gap, other) for other in group):
                group.append(gap)
                break
        else:
            groups.append([gap])

    result: list[Aperture] = []
    for group in groups:
        first = group[0]
        u = (first.right.mid - first.left.mid) / first.width
        v = np.array([-u[1], u[0]])
        corners = np.array([p for g in group for p in (g.left.a, g.left.b, g.right.a, g.right.b)])
        centre = corners.mean(axis=0)
        along = (corners - centre) @ u
        across = (corners - centre) @ v
        mid_across = (across.min() + across.max()) / 2
        result.append(
            Aperture(
                start=centre + u * along.min() + v * mid_across,
                end=centre + u * along.max() + v * mid_across,
                thickness=float(across.max() - across.min()),
                corners=corners,
                pieces={p for g in group for p in (g.left.piece, g.right.piece)},
                quads=[g.quad() for g in group],
            )
        )
    return result


@dataclass(slots=True)
class DoorArc:
    swing: DoorSwing  # Anschlag: Band am Anfang (LEFT) oder Ende (RIGHT) der Öffnung
    opens_to: Literal["left", "right"]  # Aufschlagseite relativ zur Richtung Anfang → Ende
    curve: int = -1  # Index des Aufschlagbogens in ParsedPlan.curves


def _arc_error(aperture: Aperture, curve: Stroke) -> tuple[float, DoorArc] | None:
    """Türaufschlag als Viertelkreis um eine Laibungsecke: beide Bogenenden haben den
    gleichen Abstand r zum Drehpunkt und liegen r·√2 auseinander. Das Türblatt kann schmaler
    sein als die Öffnung (feststehendes Seitenteil) → r zwischen 55 % und 110 % der Breite.
    Viele PDFs speichern Bézierbögen nur mit ihren Endpunkten – Start und Ende genügen."""
    width = aperture.width
    ends = _array([curve.points[0], curve.points[-1]])
    chord = float(np.linalg.norm(ends[1] - ends[0]))
    axis = (aperture.end - aperture.start) / width
    side: Literal["left", "right"] = (
        "left" if _cross(axis, ends.mean(axis=0) - aperture.start) > 0 else "right"
    )
    best: tuple[float, DoorArc] | None = None
    for corner in aperture.corners:
        d1, d2 = (float(np.linalg.norm(end - corner)) for end in ends)
        radius = (d1 + d2) / 2
        if not 0.55 * width <= radius <= 1.1 * width or abs(d1 - d2) > 0.2 * radius:
            continue
        if not 1.1 * radius <= chord <= 1.7 * radius:
            continue
        error = (abs(d1 - d2) + abs(chord - radius * math.sqrt(2))) / radius
        error += 0.5 * abs(radius - width) / width  # volle Öffnungsbreite bevorzugen
        near_start = np.linalg.norm(corner - aperture.start) < np.linalg.norm(corner - aperture.end)
        swing = DoorSwing.LEFT if near_start else DoorSwing.RIGHT
        if best is None or error < best[0]:
            best = (error, DoorArc(swing, side))
    return best


def assign_door_arcs(found: list[Aperture], curves: list[Stroke]) -> dict[int, DoorArc]:
    """Jeder Bogen gehört zu höchstens einer Öffnung – der am besten passenden."""
    candidates = []
    for i, aperture in enumerate(found):
        centre = (aperture.start + aperture.end) / 2
        for j, curve in enumerate(curves):
            if np.linalg.norm(_array(curve.points).mean(axis=0) - centre) > 2.5 * aperture.width:
                continue
            match = _arc_error(aperture, curve)
            if match is not None:
                candidates.append((match[0], i, j, match[1]))
    doors: dict[int, DoorArc] = {}
    used: set[int] = set()
    for _, i, j, door in sorted(candidates, key=lambda c: c[0]):
        if i not in doors and j not in used:
            door.curve = j
            doors[i] = door
            used.add(j)
    return doors


# ------------------------------------------------------------------ Raster: außen/innen, Räume
@dataclass(slots=True)
class Raster:
    origin: np.ndarray
    blocked: np.ndarray  # Wände + geschlossene Öffnungen
    outside: np.ndarray

    def to_px(self, pts: np.ndarray) -> np.ndarray:
        return np.round((pts - self.origin) / RASTER_MM).astype(np.int32)

    def to_mm(self, px: np.ndarray) -> np.ndarray:
        return px.astype(np.float64) * RASTER_MM + self.origin

    def is_outside(self, x: float, y: float) -> bool:
        col, row = self.to_px(np.array([x, y]))
        h, w = self.outside.shape
        return not (0 <= row < h and 0 <= col < w) or bool(self.outside[row, col])


def _raster(pieces: list[Piece], quads: list[np.ndarray]) -> Raster:
    all_pts = np.vstack([p.points for p in pieces])
    origin = all_pts.min(axis=0) - 1_000.0
    size = np.ceil((all_pts.max(axis=0) + 1_000.0 - origin) / RASTER_MM).astype(int) + 1
    blocked = np.zeros((size[1], size[0]), dtype=np.uint8)
    raster = Raster(origin, blocked, blocked)
    for poly in [p.points for p in pieces] + quads:
        cv2.fillPoly(blocked, [raster.to_px(poly)], 1)
    # kleine Fugen zwischen Wandstücken schließen (Rundungsfehler, Dehnfugen)
    blocked = cv2.dilate(blocked, np.ones((3, 3), np.uint8))
    free = (blocked == 0).astype(np.uint8)
    mask = np.zeros((free.shape[0] + 2, free.shape[1] + 2), np.uint8)
    cv2.floodFill(free, mask, (0, 0), 2)
    return Raster(origin, blocked, free == 2)


def _side_samples(aperture: Aperture) -> list[list[np.ndarray]]:
    """Je Seite der Öffnung drei Punkte knapp hinter der Laibung."""
    axis = (aperture.end - aperture.start) / aperture.width
    normal = np.array([-axis[1], axis[0]])
    offset = aperture.thickness / 2 + 100.0
    return [
        [
            aperture.start + (aperture.end - aperture.start) * t + sign * normal * offset
            for t in (0.2, 0.5, 0.8)
        ]
        for sign in (1, -1)
    ]


def _opens_outside(aperture: Aperture, raster: Raster) -> bool:
    """Außenöffnung (Fenster) – je Öffnung geprüft, denn ein verzweigter Wandzug ist
    zugleich Außen- und Innenwand."""
    return any(raster.is_outside(x, y) for side in _side_samples(aperture) for x, y in side)


def _connects_rooms(
    aperture: Aperture, raster: Raster, labels: np.ndarray, sizes: np.ndarray
) -> bool:
    """Eine echte Öffnung führt auf beiden Seiten in einen Raum oder ins Freie. Zufällig
    gegenüberstehende Wandenden (Nische vor dem Eingang, Wandstummel quer durch die Diele)
    schließen dagegen nur einen Zwickel unter Mindestraumgröße ab."""
    h, w = labels.shape
    for side in _side_samples(aperture):
        reached = 0
        for x, y in side:
            col, row = raster.to_px(np.array([x, y]))
            if not (0 <= row < h and 0 <= col < w) or raster.outside[row, col]:
                reached += 1
            elif raster.blocked[row, col]:
                continue  # Wandecke direkt neben der Öffnung – kein Urteil
            elif sizes[labels[row, col]] * RASTER_MM**2 / 1e6 >= MIN_ROOM_M2:
                reached += 1
            else:
                return False  # Zwickel
        if not reached:
            return False
    return True


def prune_apertures(pieces: list[Piece], found: list[Aperture]) -> tuple[list[Aperture], Raster]:
    """Scheinöffnungen einzeln entfernen – breiteste zuerst, denn eine Scheinöffnung kann
    auch eine echte Tür daneben vom Raum abschneiden."""
    found = list(found)
    while True:
        raster = _raster(pieces, [q for a in found for q in a.quads])
        inner = ((raster.blocked == 0) & ~raster.outside).astype(np.uint8)
        _, labels, stats, _ = cv2.connectedComponentsWithStats(inner, connectivity=4)
        sizes = stats[:, cv2.CC_STAT_AREA]
        bad = [a for a in found if not _connects_rooms(a, raster, labels, sizes)]
        if not bad:
            return found, raster
        worst = max(bad, key=lambda a: a.width)
        found = [a for a in found if a is not worst]


def _outward_points(points: np.ndarray, offset: float) -> list[np.ndarray]:
    # Außennormale über den Umlaufsinn – gilt auch für verzweigte, nicht konvexe Wandzüge.
    x, y = points[:, 0], points[:, 1]
    ccw = float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) > 0
    samples = []
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        if np.linalg.norm(b - a) < 100.0:
            continue
        normal = np.array([(b - a)[1], -(b - a)[0]]) / np.linalg.norm(b - a)
        samples.append((a + b) / 2 + (normal if ccw else -normal) * offset)
    return samples


def _is_exterior(points: np.ndarray, raster: Raster) -> bool:
    return any(raster.is_outside(x, y) for x, y in _outward_points(points, 3 * RASTER_MM))


def _lies_outside(polygon: list[Point2D], raster: Raster) -> bool:
    """Liegt die Fläche außerhalb der Wände (Terrasse als farbige Fläche)?"""
    pts = _array(polygon)
    x, y = _centroid(pts)
    if not _inside(pts, x, y):  # L-Form, Schwerpunkt daneben → im Zweifel Innenraum
        return False
    return raster.is_outside(x, y)


def _rooms_from_raster(raster: Raster) -> list[list[Point2D]]:
    inner = ((raster.blocked == 0) & ~raster.outside).astype(np.uint8)
    contours, _ = cv2.findContours(inner, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons: list[list[Point2D]] = []
    for contour in contours:
        simplified = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2)
        if len(simplified) < 3:
            continue
        pts = raster.to_mm(simplified)
        polygon = [Point2D(x=float(x), y=float(y)) for x, y in pts]
        if polygon_area_mm2(polygon) / 1e6 >= MIN_ROOM_M2:
            polygons.append(polygon)
    return polygons


# ------------------------------------------------------------------ Beschriftung
def _label(polygon: list[Point2D], texts: list[TextItem]) -> str | None:
    """Raumname zuerst (größte Schrift), danach in Lesereihenfolge; ohne Maßzahlen und
    Einzelbuchstaben (senkrecht beschriftete Möbel zerfallen in solche)."""
    pts = _array(polygon)
    inside = [
        t
        for t in texts
        if len(t.text) >= 2
        and not _NUMBERS_ONLY.match(t.text)
        and _inside(pts, t.position.x, t.position.y)
    ]
    inside.sort(
        key=lambda t: (-round(t.height_mm or 0, -1), -round(t.position.y / 200), t.position.x)
    )
    return " ".join(t.text for t in inside) or None


# ------------------------------------------------------------------ Einrichtung
def _furniture(
    parsed: ParsedPlan, rooms: list[Room], door_arcs: set[int], stairs: list[Stair]
) -> list[Furniture]:
    """Gezeichnete Möbel: alle Linien und Kurven ohne Schraffuren (Fliesen, Dielen) und ohne
    Türaufschläge (Bogen + Türblatt schließen sonst mit der Wand eine Fläche ein)."""
    curves = [
        np.array([[p.x, p.y] for p in c.points])
        for j, c in enumerate(parsed.curves)
        if j not in door_arcs
    ]
    edges = [
        Segment(start=Point2D(x=a[0], y=a[1]), end=Point2D(x=b[0], y=b[1]))
        for c in curves
        if len(c) == 2
        for a, b in pairwise(c.tolist())
    ]
    lines = [*parsed.segments, *edges]
    hatch = hatch_lines(lines).lines
    kept = [
        np.array([[s.start.x, s.start.y], [s.end.x, s.end.y]])
        for i, s in enumerate(lines)
        if i not in hatch
    ]
    outlines = [np.array([[p.x, p.y] for p in s.outline]) for s in stairs]
    return find_furniture(rooms, kept, [c for c in curves if len(c) > 2], parsed.texts, outlines)


# ------------------------------------------------------------------ Gesamt
def recognize_cad(parsed: ParsedPlan) -> FloorPlan | None:
    """Liefert None, wenn der Plan keine gefüllten Wände hat (→ anderes Verfahren nutzen)."""
    color = wall_color(parsed.filled_areas)
    if color is None:
        return None
    pieces = _pieces(parsed.filled_areas, color)
    if len(pieces) < 4:
        return None

    found, raster = prune_apertures(pieces, apertures(find_gaps(pieces)))
    for piece in pieces:
        piece.wall.is_exterior = _is_exterior(piece.points, raster)

    walls = [p.wall for p in pieces]
    doors = assign_door_arcs(found, parsed.curves)
    openings: list[Opening] = []
    for n, aperture in enumerate(found):
        exterior = _opens_outside(aperture, raster)
        gap_wall = Wall(
            id=f"gap_{n:03d}",
            start=Point2D(x=float(aperture.start[0]), y=float(aperture.start[1])),
            end=Point2D(x=float(aperture.end[0]), y=float(aperture.end[1])),
            thickness_mm=aperture.thickness,
            is_exterior=exterior,
            confidence=0.8,
        )
        walls.append(gap_wall)
        door = doors.get(n)
        if door is not None:
            kind, sill, height = OpeningType.DOOR, 0.0, DOOR_HEIGHT_MM
        elif exterior:
            kind = OpeningType.WINDOW
            french = aperture.width >= FRENCH_WINDOW_MIN_MM
            sill = 0.0 if french else WINDOW_SILL_MM
            height = FRENCH_WINDOW_HEIGHT_MM if french else WINDOW_HEIGHT_MM
        else:
            kind, sill, height = OpeningType.PASSAGE, 0.0, DOOR_HEIGHT_MM
        openings.append(
            Opening(
                id=f"{kind}_{n:03d}",
                type=kind,
                wall_id=gap_wall.id,
                offset_mm=0.0,
                width_mm=aperture.width,
                height_mm=height,
                sill_height_mm=sill,
                swing=door.swing if door else None,
                opens_to=door.opens_to if door else None,
                confidence=0.8 if door is not None or exterior else 0.6,
            )
        )

    room_fills = [a.polygon for a in parsed.filled_areas if _is_room_color(a.color, color)]
    polygons = [p for p in room_fills if polygon_area_mm2(p) / 1e6 >= MIN_ROOM_M2]
    room_source = "farbige Raumflächen"
    if not polygons:
        polygons, room_source = _rooms_from_raster(raster), "von Wänden umschlossene Flächen"
    rooms = [
        Room(
            id=f"room_{i:03d}",
            polygon=polygon,
            label=_label(polygon, parsed.texts),
            outdoor=_lies_outside(polygon, raster),  # farbig hinterlegte Terrasse
            confidence=0.9,
        )
        for i, polygon in enumerate(polygons)
    ]

    # Terrassen, Balkone: meist nicht gefüllt, nur Umriss, Belag und Stützen
    house = [p.points for p in pieces] + [q for a in found for q in a.quads]
    known = [_array(r.polygon) for r in rooms]
    columns: list[Column] = []
    for area in find_outdoor(
        parsed.segments,
        parsed.curves,
        parsed.filled_areas,
        [t for t in parsed.texts if not any(_inside(k, t.position.x, t.position.y) for k in known)],
        house,
        raster.is_outside,
    ):
        rooms.append(
            Room(
                id=f"room_{len(rooms):03d}",
                polygon=area.polygon,
                label=_label(area.polygon, parsed.texts),
                outdoor=True,
                roofed=area.roofed,
                confidence=0.7,
            )
        )
        columns += [
            Column(
                id=f"column_{len(columns) + n:03d}",
                center=Point2D(x=float(c.center[0]), y=float(c.center[1])),
                size_mm=min(c.size, 1_000.0),
                angle_deg=c.angle_deg,
                confidence=0.7,
            )
            for n, c in enumerate(area.columns)
        ]
    outdoor = [r for r in rooms if r.outdoor]

    stairs = find_stairs(parsed.segments, parsed.curves, [p.points for p in pieces])
    furniture = _furniture(parsed, rooms, {d.curve for d in doors.values()}, stairs)

    counts = defaultdict(int)
    for opening in openings:
        counts[str(opening.type)] += 1
    return FloorPlan(
        project_id=parsed.project_id,
        source_key=parsed.source_key,
        source_format=parsed.source_format,
        page=parsed.page,
        walls=walls,
        openings=openings,
        rooms=rooms,
        stairs=stairs,
        columns=columns,
        furniture=furniture,
        metadata={
            "recognizer": "cad-fills",
            "wall_color": color,
            "rooms_from": room_source,
            "outdoor": ", ".join(
                f"{(r.label or 'Außenbereich').split()[0]} {r.area_m2:g} m²"
                + (" überdacht" if r.roofed else "")
                for r in outdoor
            )
            + (f", {len(columns)} Stützen" if columns else "")
            or "keine",
            "openings": ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "keine",
            "furniture": ", ".join(
                f"{k}: {v}" for k, v in sorted(Counter(f.kind for f in furniture).items())
            )
            or "keine",
            "stairs": ", ".join(f"{len(s.steps)} Stufen à {s.rise_mm:g} mm" for s in stairs)
            or "keine",
            "scale": f"1:{parsed.plan_scale:g}" if parsed.plan_scale else "unbekannt",
        },
    )
