"""Einrichtung je Raum: feste Ausstattung laut LV (Küche, Sanitär) und lose Möbel.

Regelbasiert, ohne Lernverfahren – plausibel, keine Innenarchitektur:

1. Raumpolygon gegen den Uhrzeigersinn, je Wand die Innennormale.
2. Türen, Durchgänge und bodentiefe Fenster sperren Wandabschnitte (mit Abstand), normale
   Fenster nur für hohe Möbel (Schrank, Oberschränke).
3. Möbel an der Wand: Rückseite an die Wand, Position entlang der Wand in 50-mm-Schritten;
   gültig, wenn frei von Sperren, ganz im Raum und ohne Überschneidung.
4. Freistehend (Esstisch, Couchtisch): Rasterpunkte im Raum, gleiche Prüfungen.

Ausgabe je Stück: Mittelpunkt, Blickrichtung (Front zeigt in den Raum), Maße in mm,
``loose`` (lose Möbel – im Viewer ausblendbar) und ``kind`` für das Blender-Skript.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from lumira_shared.models import (
    BLVResult,
    FloorPlan,
    MaterialCategory,
    OpeningType,
    Room,
    RoomType,
)

WALL_GAP = 20.0  # Abstand Möbelrückseite – Wand
DOOR_CLEARANCE = 250.0  # links/rechts neben Türen frei
STEP = 50.0

# kind → (Breite, Tiefe, Höhe) in mm
SIZES: dict[str, tuple[float, float, float]] = {
    "sofa": (2200, 920, 820),
    "coffee_table": (1100, 600, 400),
    "armchair": (800, 820, 820),
    "sideboard": (1800, 450, 550),
    "dining_table": (1600, 900, 750),
    "chair": (460, 520, 860),
    "plant": (500, 500, 1100),
    "bed_double": (1800, 2080, 950),
    "bed_single": (900, 2000, 900),
    "nightstand": (450, 400, 480),
    "wardrobe": (2000, 620, 2200),
    "desk": (1400, 700, 750),
    "office_chair": (600, 600, 1000),
    "shelf": (900, 350, 1900),
    "coat_rack": (1100, 400, 1900),
    "washing_machine": (600, 620, 850),
    "kitchen": (2400, 620, 2200),  # Länge wird an die Wand angepasst
    "kitchen_counter": (2400, 900, 920),  # Theke: 600 Schränke + 300 Überstand
    "wc": (380, 560, 800),
    "washbasin": (600, 470, 850),
    "handbasin": (450, 360, 850),
    "shower": (900, 900, 2000),
    "bathtub": (1700, 750, 580),
}
TALL = {"wardrobe", "shelf", "coat_rack", "kitchen", "shower"}

_BATHTUB = re.compile(r"badewanne|wannenbad|acryl-?einbauwanne|(?<!dusch)wanne")
_SHOWER = re.compile(r"dusch")
_WC = re.compile(r"\bwc\b|klosett|tiefsp[üu]l|wc-anlage")
_BASIN = re.compile(r"waschtisch|waschbecken|handwasch")


# ------------------------------------------------------------------ Geometrie
@dataclass(slots=True)
class Rect:
    """Gedrehtes Rechteck: Mittelpunkt, Front-Richtung (Winkel), Breite quer, Tiefe längs."""

    cx: float
    cy: float
    angle: float  # Richtung, in die die Front zeigt
    w: float
    d: float

    def axes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        front = (math.cos(self.angle), math.sin(self.angle))
        side = (front[1], -front[0])
        return side, front

    def corners(self) -> list[tuple[float, float]]:
        (sx, sy), (fx, fy) = self.axes()
        hw, hd = self.w / 2, self.d / 2
        return [
            (self.cx + sx * a * hw + fx * b * hd, self.cy + sy * a * hw + fy * b * hd)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]

    def moved(self, forward: float = 0.0, sideways: float = 0.0) -> Rect:
        (sx, sy), (fx, fy) = self.axes()
        return Rect(
            self.cx + fx * forward + sx * sideways,
            self.cy + fy * forward + sy * sideways,
            self.angle,
            self.w,
            self.d,
        )


def _project(corners: list[tuple[float, float]], axis: tuple[float, float]) -> tuple[float, float]:
    values = [x * axis[0] + y * axis[1] for x, y in corners]
    return min(values), max(values)


def overlaps(a: Rect, b: Rect, margin: float = 10.0) -> bool:
    """Trennende-Achsen-Test für zwei gedrehte Rechtecke."""
    ca, cb = a.corners(), b.corners()
    for axis in (*a.axes(), *b.axes()):
        lo_a, hi_a = _project(ca, axis)
        lo_b, hi_b = _project(cb, axis)
        if hi_a - margin <= lo_b or hi_b - margin <= lo_a:
            return False
    return True


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def rect_in_polygon(rect: Rect, polygon: list[tuple[float, float]], tolerance: float = 5.0) -> bool:
    shrunk = Rect(
        rect.cx, rect.cy, rect.angle, max(rect.w - tolerance, 1), max(rect.d - tolerance, 1)
    )
    if not all(point_in_polygon(c, polygon) for c in shrunk.corners()):
        return False
    # Einspringende Ecken (L-förmige Räume) dürfen nicht im Rechteck liegen.
    box = shrunk.corners()
    return not any(point_in_polygon(v, box) for v in polygon)


def hull_rect(points: list[tuple[float, float]]) -> Rect:
    """Kleinstes umschließendes Rechteck eines konvexen Umrisses (Kanten als Kandidaten)."""
    best: Rect | None = None
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
        if math.hypot(x2 - x1, y2 - y1) < 1.0:
            continue
        angle = math.atan2(y2 - y1, x2 - x1)
        ux, uy = math.cos(angle), math.sin(angle)
        along = [x * ux + y * uy for x, y in points]
        across = [-x * uy + y * ux for x, y in points]
        a0, a1, c0, c1 = min(along), max(along), min(across), max(across)
        ma, mc = (a0 + a1) / 2, (c0 + c1) / 2
        rect = Rect(ma * ux - mc * uy, ma * uy + mc * ux, angle + math.pi / 2, a1 - a0, c1 - c0)
        if best is None or rect.w * rect.d < best.w * best.d:
            best = rect
    assert best is not None, "Umriss ohne Kanten"
    return best


STAIR_LANDING = 1000.0  # Bewegungsfläche vor dem Antritt


def stair_obstacles(plan: FloorPlan) -> list[Rect]:
    """Treppen und die Bewegungsfläche vor dem Antritt – dort stehen keine Möbel."""
    obstacles = []
    for stair in plan.stairs:
        obstacles.append(hull_rect([(p.x, p.y) for p in stair.outline]))
        if len(stair.walking_line) >= 2:
            (x0, y0), (x1, y1) = ((p.x, p.y) for p in stair.walking_line[:2])
            length = math.hypot(x0 - x1, y0 - y1) or 1.0
            ux, uy = (x0 - x1) / length, (y0 - y1) / length  # vom Lauf weg
            reach = length + STAIR_LANDING / 2
            obstacles.append(
                Rect(x0 + ux * reach, y0 + uy * reach, math.atan2(uy, ux), 1000.0, STAIR_LANDING)
            )
    return obstacles


@dataclass(slots=True)
class Edge:
    ax: float
    ay: float
    length: float
    ux: float
    uy: float
    blocked_all: list[tuple[float, float]] = field(default_factory=list)
    blocked_tall: list[tuple[float, float]] = field(default_factory=list)
    doorways: list[tuple[float, float]] = field(default_factory=list)  # Öffnungsbreite ohne Zugabe
    open: bool = False  # keine Wand dahinter (offene Küche, Raumübergang)

    @property
    def normal(self) -> tuple[float, float]:  # nach innen (Polygon gegen den Uhrzeigersinn)
        return -self.uy, self.ux

    def point(self, along: float) -> tuple[float, float]:
        return self.ax + self.ux * along, self.ay + self.uy * along

    def free(self, lo: float, hi: float, tall: bool) -> bool:
        blocks = self.blocked_all + (self.blocked_tall if tall else [])
        return all(hi <= b_lo or lo >= b_hi for b_lo, b_hi in blocks)

    def free_length(self, tall: bool) -> float:
        blocks = sorted(self.blocked_all + (self.blocked_tall if tall else []))
        best, cursor = 0.0, 0.0
        for lo, hi in blocks:
            best = max(best, lo - cursor)
            cursor = max(cursor, hi)
        return max(best, self.length - cursor)


def _ccw(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    area = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True)
    )
    return points if area > 0 else list(reversed(points))


@dataclass(slots=True)
class OpeningSpan:
    x0: float
    y0: float
    x1: float
    y1: float
    thickness: float
    blocks_all: bool


def opening_spans(plan: FloorPlan) -> list[OpeningSpan]:
    spans = []
    for opening in plan.openings:
        wall = plan.wall(opening.wall_id)
        length = wall.length_mm or 1.0
        ux, uy = (wall.end.x - wall.start.x) / length, (wall.end.y - wall.start.y) / length
        a, b = opening.offset_mm, opening.offset_mm + opening.width_mm
        full = opening.type is not OpeningType.WINDOW or opening.sill_height_mm < 300
        spans.append(
            OpeningSpan(
                wall.start.x + ux * a,
                wall.start.y + uy * a,
                wall.start.x + ux * b,
                wall.start.y + uy * b,
                wall.thickness_mm,
                full,
            )
        )
    return spans


Outline = list[tuple[float, float]]


def wall_outlines(plan: FloorPlan) -> list[Outline]:
    """Grundriss jeder Wand – exakte CAD-Fläche oder Rechteck aus Achse und Dicke."""
    outlines = []
    for wall in plan.walls:
        if wall.footprint:
            outlines.append([(p.x, p.y) for p in wall.footprint])
            continue
        length = wall.length_mm or 1.0
        ux, uy = (wall.end.x - wall.start.x) / length, (wall.end.y - wall.start.y) / length
        hx, hy = -uy * wall.thickness_mm / 2, ux * wall.thickness_mm / 2
        outlines.append(
            [
                (wall.start.x + hx, wall.start.y + hy),
                (wall.end.x + hx, wall.end.y + hy),
                (wall.end.x - hx, wall.end.y - hy),
                (wall.start.x - hx, wall.start.y - hy),
            ]
        )
    return outlines


OPEN_EDGE_PROBE = 60.0  # so weit hinter der Raumkante wird nach einer Wand gesucht


def _is_open(edge: Edge, walls: list[Outline]) -> bool:
    """Raumkante ohne Wand dahinter? Stichproben alle 200 mm knapp außerhalb des Raums."""
    nx, ny = edge.normal
    count = max(1, round(edge.length / 200))
    samples = [edge.length * (i + 0.5) / count for i in range(count)]
    walled = 0
    for along in samples:
        px, py = edge.point(along)
        probe = (px - nx * OPEN_EDGE_PROBE, py - ny * OPEN_EDGE_PROBE)
        if any(point_in_polygon(probe, outline) for outline in walls):
            walled += 1
    return walled < 0.5 * len(samples)


def room_edges(
    polygon: list[tuple[float, float]],
    spans: Iterable[OpeningSpan],
    walls: list[Outline] | None = None,
) -> list[Edge]:
    edges = []
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 300:
            continue
        edge = Edge(x1, y1, length, (x2 - x1) / length, (y2 - y1) / length)
        if walls is not None and _is_open(edge, walls):
            # offen zum Nachbarraum: keine Möbel mit dem Rücken dorthin
            edge.open = True
            edge.blocked_all.append((0.0, length))
            edges.append(edge)
            continue
        nx, ny = edge.normal
        for span in spans:
            t = []
            near = True
            for px, py in ((span.x0, span.y0), (span.x1, span.y1)):
                dx, dy = px - x1, py - y1
                distance = -(dx * nx + dy * ny)  # >0: hinter der Wandfläche (in der Wand)
                if not -50 <= distance <= span.thickness + 200:
                    near = False
                t.append(dx * edge.ux + dy * edge.uy)
            if not near:
                continue
            lo, hi = min(t) - DOOR_CLEARANCE, max(t) + DOOR_CLEARANCE
            if hi <= 0 or lo >= length:
                continue
            target = edge.blocked_all if span.blocks_all else edge.blocked_tall
            target.append((max(lo, 0.0), min(hi, length)))
            if span.blocks_all:
                edge.doorways.append((max(min(t), 0.0), min(max(t), length)))
        edges.append(edge)
    return edges


# ------------------------------------------------------------------ Platzieren
DOORWAY_DEPTH = 1000.0  # vor Türen und Terrassentüren bleibt ein Gehbereich frei


@dataclass
class Layout:
    polygon: list[tuple[float, float]]
    edges: list[Edge]
    placed: list[Rect] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Gehbereiche vor Türen als unsichtbare Hindernisse – auch für freistehende Möbel."""
        for edge in self.edges:
            nx, ny = edge.normal
            for lo, hi in edge.doorways:
                px, py = edge.point((lo + hi) / 2)
                self.placed.append(
                    Rect(
                        px + nx * DOORWAY_DEPTH / 2,
                        py + ny * DOORWAY_DEPTH / 2,
                        math.atan2(ny, nx),
                        hi - lo + 200,
                        DOORWAY_DEPTH,
                    )
                )

    def fits(self, rect: Rect, ignore: tuple[Rect, ...] = ()) -> bool:
        return rect_in_polygon(rect, self.polygon) and not any(
            overlaps(rect, other) for other in self.placed if other not in ignore
        )

    def add(
        self, kind: str, rect: Rect, *, loose: bool, extra: dict[str, Any] | None = None
    ) -> Rect:
        self.placed.append(rect)
        self.items.append(
            {
                "kind": kind,
                "x": round(rect.cx, 1),
                "y": round(rect.cy, 1),
                "angle": round(rect.angle, 5),
                "w": rect.w,
                "d": rect.d,
                "h": SIZES[kind][2],
                "loose": loose,
                **(extra or {}),
            }
        )
        return rect

    def against_wall(
        self,
        kind: str,
        *,
        width: float | None = None,
        corner: bool = False,
        edges: list[Edge] | None = None,
        clearance: float = 0.0,
        avoid_windows: bool = False,
        tall: bool | None = None,
    ) -> Rect | None:
        """Erste gültige Position an einer Wand. ``clearance``: freie Tiefe vor der Front;
        ``avoid_windows``: Wände ohne Fenster zuerst (Bett, Sofa); ``tall``: überschreibt,
        ob Fenster im Weg sind (Küche ohne Oberschränke passt unter ein Fenster)."""
        w = width or SIZES[kind][0]
        d = SIZES[kind][1]
        tall = kind in TALL if tall is None else tall
        order = sorted(
            self.edges,
            key=lambda e: (avoid_windows and bool(e.blocked_tall), -e.free_length(tall)),
        )
        for edge in edges or order:
            if edge.length < w:
                continue
            steps = int((edge.length - w) // STEP) + 1
            positions = [w / 2 + i * STEP for i in range(steps)]
            if not corner:  # mittig bevorzugen
                positions.sort(key=lambda a: abs(a - edge.length / 2))
            else:  # Ecken zuerst, beide Enden
                positions.sort(key=lambda a: min(a - w / 2, edge.length - a - w / 2))
            nx, ny = edge.normal
            angle = math.atan2(ny, nx)
            for along in positions:
                if not edge.free(along - w / 2, along + w / 2, tall):
                    continue
                px, py = edge.point(along)
                rect = Rect(px + nx * (d / 2 + WALL_GAP), py + ny * (d / 2 + WALL_GAP), angle, w, d)
                # Freiraum nur nach vorn (in den Raum), nicht hinter die Wand
                ahead = rect.moved(forward=clearance / 2)
                zone = Rect(ahead.cx, ahead.cy, angle, w, d + clearance) if clearance else rect
                if self.fits(rect) and rect_in_polygon(zone, self.polygon):
                    return rect
        return None

    def free_standing(self, w: float, d: float, *, avoid: Rect | None = None) -> Rect | None:
        """Rasterpunkt, an dem ein Rechteck (in Raumrichtung) passt – möglichst weit weg
        von ``avoid`` und mit Abstand zu den Wänden."""
        xs = [x for x, _ in self.polygon]
        ys = [y for _, y in self.polygon]
        main = max(self.edges, key=lambda e: e.length)
        angle = math.atan2(main.uy, main.ux)
        candidates = []
        for gx in range(int(min(xs)), int(max(xs)), 100):
            for gy in range(int(min(ys)), int(max(ys)), 100):
                rect = Rect(float(gx), float(gy), angle + math.pi / 2, w, d)
                if self.fits(rect):
                    score = math.hypot(gx - avoid.cx, gy - avoid.cy) if avoid else 0.0
                    candidates.append((score, rect))
        return max(candidates, key=lambda c: c[0])[1] if candidates else None


def _near_opposite_edge(layout: Layout, rect: Rect) -> list[Edge]:
    """Wände, deren Innennormale der Front von ``rect`` entgegenzeigt (gegenüber)."""
    fx, fy = math.cos(rect.angle), math.sin(rect.angle)
    return sorted(
        (e for e in layout.edges if e.normal[0] * fx + e.normal[1] * fy < -0.9),
        key=lambda e: -e.length,
    )


# ------------------------------------------------------------------ Räume
def _sanitary_from_lv(blv: BLVResult) -> set[str]:
    names = " ".join(
        m.name.lower() for m in blv.materials_for() if m.category is MaterialCategory.SANITARY
    )
    found = set()
    if _BATHTUB.search(names):
        found.add("bathtub")
    if _SHOWER.search(names):
        found.add("shower")
    if _WC.search(names):
        found.add("wc")
    if _BASIN.search(names):
        found.add("washbasin")
    return found


def _furnish_living(layout: Layout, dining: bool) -> None:
    sofa = layout.against_wall("sofa", clearance=900, avoid_windows=True)
    if sofa:
        layout.add("sofa", sofa, loose=True)
        table = sofa.moved(forward=SIZES["sofa"][1] / 2 + 450 + SIZES["coffee_table"][1] / 2)
        table = Rect(table.cx, table.cy, table.angle, *SIZES["coffee_table"][:2])
        if layout.fits(table):
            layout.add("coffee_table", table, loose=True)
            for side in (1, -1):  # Sessel seitlich, zum Tisch gedreht
                chair = table.moved(sideways=side * (SIZES["coffee_table"][0] / 2 + 550))
                chair = Rect(
                    chair.cx, chair.cy, table.angle + side * math.pi / 2, *SIZES["armchair"][:2]
                )
                if layout.fits(chair):
                    layout.add("armchair", chair, loose=True)
                    break
        opposite = _near_opposite_edge(layout, sofa)
        board = layout.against_wall("sideboard", edges=opposite)
        if board:
            layout.add("sideboard", board, loose=True)
    if dining:
        w, d = SIZES["dining_table"][:2]
        spot = layout.free_standing(w + 200, d + 2 * 650, avoid=sofa)
        if spot:
            table = Rect(spot.cx, spot.cy, spot.angle, w, d)
            layout.add("dining_table", table, loose=True)
            cw, cd = SIZES["chair"][:2]
            for side in (1, -1):
                for offset in (-w / 4, w / 4):
                    chair = table.moved(forward=side * (d / 2 + cd / 2 - 80), sideways=offset)
                    angle = table.angle + (math.pi if side > 0 else 0.0)
                    layout.add("chair", Rect(chair.cx, chair.cy, angle, cw, cd), loose=True)
    plant = layout.against_wall("plant", corner=True)
    if plant:
        layout.add("plant", plant, loose=True)


def _furnish_bedroom(layout: Layout, double: bool) -> None:
    kind = "bed_double" if double else "bed_single"
    bed = layout.against_wall(kind, clearance=500, avoid_windows=True)
    if bed:
        layout.add(kind, bed, loose=True)
        for side in (1, -1):
            stand = bed.moved(
                sideways=side * (SIZES[kind][0] / 2 + SIZES["nightstand"][0] / 2 + 30),
                forward=-(SIZES[kind][1] - SIZES["nightstand"][1]) / 2,
            )
            stand = Rect(stand.cx, stand.cy, stand.angle, *SIZES["nightstand"][:2])
            if layout.fits(stand):
                layout.add("nightstand", stand, loose=True)
    wardrobe_width = min(
        2400.0, max((e.free_length(tall=True) for e in layout.edges), default=0) - 100
    )
    if wardrobe_width >= 1000:
        wardrobe = layout.against_wall("wardrobe", width=wardrobe_width, corner=True, clearance=600)
        if wardrobe:
            layout.add("wardrobe", wardrobe, loose=True)
    if not double:
        desk = layout.against_wall("desk", clearance=700)
        if desk:
            layout.add("desk", desk, loose=True)


def _furnish_office(layout: Layout) -> None:
    desk = layout.against_wall("desk", clearance=800)
    if desk:
        layout.add("desk", desk, loose=True)
        chair = desk.moved(forward=SIZES["desk"][1] / 2 + 200)
        layout.add(
            "office_chair", Rect(chair.cx, chair.cy, desk.angle + math.pi, 600, 600), loose=True
        )
    shelf = layout.against_wall("shelf", corner=True, clearance=500)
    if shelf:
        layout.add("shelf", shelf, loose=True)


def _furnish_kitchen(layout: Layout, front_color: str) -> None:
    """Geschlossene Küche: Zeile an der längsten freien Wand. Offene Küche (Kante ohne Wand zum
    Nachbarraum): Theke mit Kochfeld an der offenen Kante und die Zeile an der Wand, die dort
    anschließt – eine L-Küche wie in offenen Grundrissen üblich; steht dort ein Fenster, nur
    Unterschränke (Spüle unter dem Fenster)."""
    walls = [e for e in layout.edges if not e.open]
    counter = _kitchen_counter(layout, front_color)
    if counter is not None:
        _l_kitchen_run(layout, walls, counter[1], front_color)
        return
    best = max(walls, key=lambda e: e.free_length(tall=True), default=None)
    if best is None:
        return
    upper = True
    length = min(3600.0, best.free_length(tall=True) - 50)
    if length < 1200:  # zu kurz für Oberschränke (Fenster) → nur Unterschränke
        upper = False
        best = max(walls, key=lambda e: e.free_length(tall=False))
        length = min(3600.0, best.free_length(tall=False) - 50)
    if length < 900:
        return
    run = layout.against_wall("kitchen", width=length, corner=True, edges=[best], clearance=900)
    if run:
        extra = {"color": front_color, "upper": upper, "hob": True}
        layout.add("kitchen", run, loose=False, extra=extra)


def _l_kitchen_run(
    layout: Layout, walls: list[Edge], corner: tuple[float, float], front_color: str
) -> None:
    """Zeile an der Wand, die an der Theke anstößt – so lang wie möglich, bis an die Theke."""

    def distance(edge: Edge) -> float:
        ends = (edge.point(0.0), edge.point(edge.length))
        return min(math.hypot(x - corner[0], y - corner[1]) for x, y in ends)

    adjacent = [e for e in walls if distance(e) <= 100.0]
    for edge in sorted(adjacent, key=lambda e: -e.length) + sorted(walls, key=lambda e: -e.length):
        windows = bool(edge.blocked_tall)
        length = min(3600.0, edge.free_length(tall=False) - 50)
        while length >= 900:
            run = layout.against_wall(
                "kitchen", width=length, corner=True, edges=[edge], clearance=900, tall=not windows
            )
            if run:
                extra = {
                    "color": front_color,
                    "upper": not windows,
                    "tall_unit": not windows,
                    "hob": False,  # Kochfeld sitzt in der Theke
                }
                layout.add("kitchen", run, loose=False, extra=extra)
                return
            length -= 50


COUNTER_MIN = 1200.0


def _kitchen_counter(layout: Layout, front_color: str) -> tuple[Rect, tuple[float, float]] | None:
    """Theke entlang der offenen Kante: Rücken zum Wohnbereich, an einem Wandende beginnend.
    Liefert die Theke und die Ecke, an der sie an die Wand stößt."""
    open_edges = sorted((e for e in layout.edges if e.open), key=lambda e: -e.length)
    w_max, d, _ = SIZES["kitchen_counter"]
    for edge in open_edges:
        nx, ny = edge.normal
        angle = math.atan2(ny, nx)
        width = min(w_max, edge.length - 900.0)  # Durchgang zum Wohnbereich bleibt frei
        if width < COUNTER_MIN:
            continue
        for along, corner in ((width / 2, 0.0), (edge.length - width / 2, edge.length)):
            px, py = edge.point(along)
            rect = Rect(px + nx * d / 2, py + ny * d / 2, angle, width, d)
            zone = rect.moved(forward=450)  # 900 mm Arbeitsfläche davor
            zone = Rect(zone.cx, zone.cy, angle, width, d + 900)
            if layout.fits(rect) and rect_in_polygon(zone, layout.polygon):
                placed = layout.add(
                    "kitchen_counter", rect, loose=False, extra={"color": front_color}
                )
                return placed, edge.point(corner)
    return None


def _furnish_wet(layout: Layout, room: Room, lv: set[str]) -> None:
    small = room.room_type is RoomType.WC
    wanted = ["wc", "handbasin"] if small else ["wc", "washbasin"]
    if not small:
        area = room.area_m2
        if "bathtub" in lv and area >= 6:
            wanted.insert(0, "bathtub")
        if "shower" in lv or "bathtub" not in lv:
            wanted.insert(0, "shower")
    for kind in wanted:
        corner = kind in ("shower", "bathtub")
        spot = layout.against_wall(kind, corner=corner, clearance=0 if corner else 550)
        if spot:
            layout.add(kind, spot, loose=False)


def furnish(plan: FloorPlan, blv: BLVResult) -> list[dict[str, Any]]:
    spans = opening_spans(plan)
    lv_sanitary = _sanitary_from_lv(blv)
    kitchens = [
        m for m in blv.materials_for() if m.category is MaterialCategory.KITCHEN and m.color_hex
    ]
    kitchen_color = kitchens[0].color_hex if kitchens and kitchens[0].color_hex else "#F2F1EC"
    stairs = stair_obstacles(plan)
    walls = wall_outlines(plan)
    items: list[dict[str, Any]] = []
    for room in plan.rooms:
        if room.outdoor:  # Terrasse/Balkon: (noch) keine Gartenmöbel
            continue
        polygon = _ccw([(p.x, p.y) for p in room.polygon])
        layout = Layout(polygon, room_edges(polygon, spans, walls))
        if not layout.edges:
            continue
        layout.placed.extend(stairs)
        label = (room.label or "").lower()
        match room.room_type:
            case RoomType.LIVING | RoomType.DINING:
                _furnish_living(
                    layout, dining="essen" in label or room.room_type is RoomType.DINING
                )
            case RoomType.BEDROOM:
                _furnish_bedroom(layout, double=True)
            case RoomType.CHILD:
                _furnish_bedroom(layout, double=False)
            case RoomType.OFFICE:
                _furnish_office(layout)
            case RoomType.KITCHEN:
                _furnish_kitchen(layout, kitchen_color)
            case RoomType.BATHROOM | RoomType.WC:
                _furnish_wet(layout, room, lv_sanitary)
            # Flure bleiben (noch) leer; Garderobe o. Ä. folgt mit den im Plan gezeichneten Möbeln.
            case RoomType.UTILITY:
                spot = layout.against_wall("washing_machine", corner=True, clearance=600)
                if spot:
                    layout.add("washing_machine", spot, loose=False)
            case _:
                pass
        for n, item in enumerate(layout.items):
            items.append({"id": f"{room.id}_{item['kind']}_{n}", "room_id": room.id, **item})
    return items
