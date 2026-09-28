"""Im Plan gezeichnete Möbel und Ausstattung.

CAD-Pläne zeichnen die Einrichtung als geschlossene Linienfiguren: Bett mit Kissen, Schrank
mit Kreuz, Stuhl als Achteck, Kochfeld mit vier Kochstellen, Wanne mit Maßangabe. Ablauf je
Innenraum (alles in Millimetern, 10-mm-Raster):

  1. Sperrbild: alles außerhalb des Raums + alle Linien und Kurven im Raum, ohne Schraffuren
     (Fliesenraster, Dielen) und ohne Türaufschläge.
  2. Freie Flächen: die größte ist der begehbare Raum; alle anderen sind Innenflächen von
     Figuren (Bettumriss, Kissen, Schrankdreiecke …).
  3. Innenflächen, die nur eine Linie trennt, gehören zu einer Figur (Bett + Kissen, Schrank
     + Kreuz, Küchenzeile aus Einzelschränken).
  4. Einordnen nach Raumtyp, Maßen, Kreuz (Schrank), Kurven (Sanitärobjekte, Kochstellen)
     und Beschriftung („1,70 x 75“ Wanne, „90 x 90“ Dusche, „WM“ Waschmaschine).
  5. Ausrichtung: Rückseite an der Wand; Stühle schauen zum Tisch, Bett mit dem Kopfende
     an der Wand.

Unbekannte Figuren werden verworfen – lieber ein Möbel weniger als ein falsches.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import ArrayLike

from lumira_shared.models import Furniture, Point2D, Room, RoomType, TextItem

RASTER_MM = 10.0
MIN_PART_MM2 = 50.0 * 50.0  # kleinere Zwickel: Linienkreuzungen, Schraffurreste
MERGE_PX = 2  # Innenflächen im Abstand einer Linie gehören zusammen
MAX_ROOM_SHARE = 0.45  # eine Figur über fast den ganzen Raum ist kein Möbel (Fliesenraster)
WALL_PROBE_MM = 150.0
SOFA_DEPTH_MM = 950.0

_TIMES = chr(0xD7)  # Malzeichen statt „x“
_BATH_DIMENSION = re.compile(rf"(\d[.,]\d{{2}})\s*[x{_TIMES}]\s*(\d{{2,3}})")  # „1,70 x 75“
_SQUARE_DIMENSION = re.compile(rf"(\d{{2,3}})\s*[x{_TIMES}]\s*(\d{{2,3}})")  # „90 x 90“
_WASHER = re.compile(r"^(wm|tr|wt|waschmaschine|trockner)$", re.IGNORECASE)
_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"kommode|komode|sideboard|lowboard"), "sideboard"),
    (re.compile(r"kleiderschrank|garderobe"), "wardrobe"),
    (re.compile(r"regal"), "shelf"),
]

WHOLE = {"bed_double", "bed_single", "sofa", "bathtub", "shower", "kitchen", "wardrobe"}
FIXED = {
    "kitchen",
    "kitchen_counter",
    "wc",
    "washbasin",
    "handbasin",
    "shower",
    "bathtub",
    "washing_machine",
}


@dataclass(slots=True)
class Symbol:
    """Eine geschlossene Figur im Raum."""

    center: np.ndarray
    long: float  # Länge der langen Seite (mm)
    short: float
    axis: float  # Richtung der langen Seite (rad)
    area: float  # mm²
    parts: int  # Innenflächen
    triangles: int  # dreieckige Innenflächen (Kreuz im Rechteck → Schrank)
    small_parts: int  # kleine Innenflächen (Kochstellen, Abfluss)
    curves: int  # Kurven in der Figur (Becken, WC, Wanne)
    vertices: int = 4  # Ecken des Umrisses
    texts: list[str] = field(default_factory=list)
    kind: str | None = None

    def corners(self) -> np.ndarray:
        u = np.array([math.cos(self.axis), math.sin(self.axis)])
        v = np.array([-u[1], u[0]])
        return np.array(
            [
                self.center + u * a * self.long / 2 + v * b * self.short / 2
                for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
            ]
        )


@dataclass(slots=True)
class _Grid:
    origin: np.ndarray
    shape: tuple[int, int]

    def px(self, pts: ArrayLike) -> np.ndarray:
        return np.round((np.asarray(pts, float) - self.origin) / RASTER_MM).astype(np.int32)

    def mm(self, px: ArrayLike) -> np.ndarray:
        return np.asarray(px, float) * RASTER_MM + self.origin


def _symbols(
    room: np.ndarray,
    lines: list[np.ndarray],
    curves: list[np.ndarray],
    texts: list[TextItem],
    keep_whole: Callable[[Symbol], bool] = lambda _: False,
) -> list[Symbol]:
    """``keep_whole``: Figur nicht in Stühle zerlegen (Bett mit Kissen, Sofa mit Polstern)."""
    lo, hi = room.min(axis=0) - 50.0, room.max(axis=0) + 50.0
    cols, rows = (np.ceil((hi - lo) / RASTER_MM).astype(int) + 1).tolist()
    grid = _Grid(lo, (rows, cols))
    barrier = np.ones(grid.shape, np.uint8)
    cv2.fillPoly(barrier, [grid.px(room)], 0)
    outside = barrier > 0
    drawn = np.zeros(grid.shape, np.uint8)
    for line in lines:
        cv2.polylines(drawn, [grid.px(line)], False, 1, 1)
    for curve in curves:
        cv2.polylines(drawn, [grid.px(curve)], False, 1, 1)
    barrier = np.maximum(barrier, np.asarray(cv2.dilate(drawn, np.ones((2, 2), np.uint8))))

    free = (barrier == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    if count <= 1:
        return []
    areas = stats[:, cv2.CC_STAT_AREA].astype(float)
    areas[0] = 0  # Hintergrund = Sperre
    room_area = float((barrier == 0).sum())
    min_px = MIN_PART_MM2 / RASTER_MM**2
    candidates = [i for i in range(1, count) if areas[i] >= min_px]
    floor = _floor_parts(candidates, labels, stats)
    # Begehbare Fläche: die größte übrige – außer der Boden ist ganz gefliest (Bad), dann ist
    # die größte übrige Fläche ein Möbel (Wannenrand)
    rest = [i for i in candidates if i not in floor]
    if rest:
        main = max(rest, key=lambda i: areas[i])
        if areas[main] > 0.2 * room_area:
            floor.add(main)
    parts = [i for i in candidates if i not in floor]
    if not parts:
        return []
    masks = _group(parts, floor, labels, stats, outside)

    curve_mids = [grid.px(c.mean(axis=0)) for c in curves]

    def symbol(mask: np.ndarray, members: list[int]) -> Symbol:
        (cx, cy), (w, h), angle = _rect(mask)
        long, short = (w, h) if w >= h else (h, w)
        triangles = small = 0
        for i in members:
            part = (labels == i).astype(np.uint8)
            cs, _ = cv2.findContours(part, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if len(cv2.approxPolyDP(max(cs, key=cv2.contourArea), 2.0, True)) == 3:
                triangles += 1
            if areas[i] * RASTER_MM**2 < 150.0 * 150.0:
                small += 1
        return Symbol(
            center=grid.mm([cx, cy]),
            long=long * RASTER_MM,
            short=short * RASTER_MM,
            axis=math.radians(angle if w >= h else angle + 90),
            area=int(mask.sum()) * RASTER_MM**2,
            parts=len(members),
            triangles=triangles,
            small_parts=small,
            curves=sum(1 for m in curve_mids if _in_mask(mask, m)),
            vertices=_vertices(mask),
            texts=[t.text for t in texts if _in_mask(mask, grid.px([t.position.x, t.position.y]))],
        )

    symbols = []
    for mask in masks:
        if int(mask.sum()) > MAX_ROOM_SHARE * room_area:
            continue
        members = [i for i in parts if mask[labels == i].any()]
        whole = symbol(mask, members)
        seats = [i for i in members if _seat_like(labels == i, curve_mids)]
        if len(seats) >= 2 and not keep_whole(whole):
            # Tisch, Stühle und Theke hängen über angeschnittene Fliesen zusammen: Stühle
            # einzeln, der Rest ohne sie neu gruppiert
            for i in seats:
                inner = [
                    j for j in members if j != i and (_filled(labels == i) & (labels == j)).any()
                ]
                symbols.append(symbol(np.isin(labels, [i, *inner]), [i, *inner]))
            nested = {
                j for i in seats for j in members if (_filled(labels == i) & (labels == j)).any()
            }

            def scrap(i: int) -> bool:  # angeschnittene Fliesen zwischen den Stühlen
                w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
                area = stats[i, cv2.CC_STAT_AREA]
                return area / max(w * h, 1) < 0.7 and area * RASTER_MM**2 < 0.12e6

            others = [i for i in members if i not in nested and not scrap(i)]
            for i in [i for i in others if areas[i] * RASTER_MM**2 >= 0.25e6]:
                symbols.append(symbol(labels == i, [i]))  # Tisch für sich
                others.remove(i)
            size = 2 * MERGE_PX + 1
            grown = np.asarray(
                cv2.dilate(
                    np.isin(labels, others).astype(np.uint8), np.ones((size, size), np.uint8)
                )
            )
            grown[~mask] = 0
            n, sub = cv2.connectedComponents(grown, connectivity=8)
            for g in range(1, n):
                part_mask = sub == g
                subset = [i for i in others if part_mask[labels == i].any()]
                if subset:
                    symbols.append(symbol(part_mask, subset))
            continue
        symbols.append(whole)
    return symbols


def _rect(mask: np.ndarray) -> tuple[tuple[float, float], tuple[float, float], float]:
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    contour = max(contours, key=cv2.contourArea)
    (cx, cy), (w, h), angle = cv2.minAreaRect(contour.astype(np.float32))
    return (cx, cy), (w, h), angle


def _seat_like(mask: np.ndarray, curve_mids: list[np.ndarray]) -> bool:
    """Stuhl/Hocker: 35–65 cm, fast quadratisch, füllt sein Rechteck gut – und als Kurve (Achteck,
    Rundung) oder gedreht gezeichnet. Achsparallele Rechtecke (Kissen im Bett) zählen nicht."""
    _, (w, h), angle = _rect(mask)
    if min(w, h) == 0:
        return False
    size_ok = min(w, h) * RASTER_MM >= 300 and max(w, h) * RASTER_MM <= 650
    if not (size_ok and max(w, h) / min(w, h) < 1.4 and int(mask.sum()) / (w * h) > 0.7):
        return False
    rotated = min(angle % 90, 90 - angle % 90) > 5
    filled = _filled(mask)
    return rotated or _vertices(mask) >= 6 or any(_in_mask(filled, m) for m in curve_mids)


def _vertices(mask: np.ndarray) -> int:
    """Ecken des Umrisses (Rechteck 4, Achteck/Oval mehr)."""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    return len(cv2.approxPolyDP(max(contours, key=cv2.contourArea), 2.0, True))


TILE_MIN_CELLS = 12
TILE_MAX_MM = 650.0
LOOSE_PATCH_M2 = 1.0  # Restboden ist groß; eine Theke mit Aufsatz (unregelmäßig) kleiner


def _filled(mask: np.ndarray) -> np.ndarray:
    """Maske mit geschlossenen Löchern (Umriss gefüllt)."""
    out = np.zeros(mask.shape, np.uint8)
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    cv2.drawContours(out, contours, -1, 1, thickness=cv2.FILLED)
    return out > 0


STRIP_MAX_MM = 300.0


def _wall_strip(i: int, labels: np.ndarray, stats: np.ndarray, outside: np.ndarray) -> bool:
    """Schmaler, langer Streifen, der mit einer Längsseite an der Wand liegt."""
    x, y, w, h = (int(stats[i, k]) for k in range(4))
    short, long = sorted((w, h))
    if short * RASTER_MM > STRIP_MAX_MM or long < 3 * short:
        return False
    pad = 4
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    own = (labels[y0 : y + h + pad, x0 : x + w + pad] == i).astype(np.uint8)
    ring = (np.asarray(cv2.dilate(own, np.ones((2 * pad + 1,) * 2, np.uint8))) > 0) & (own == 0)
    wall = outside[y0 : y + h + pad, x0 : x + w + pad]
    return int((ring & wall).sum()) >= 0.25 * int(ring.sum())


def _group(
    parts: list[int],
    floor: set[int],
    labels: np.ndarray,
    stats: np.ndarray,
    outside: np.ndarray,
) -> list[np.ndarray]:
    """Innenflächen zu Figuren zusammenfassen:
    - Nachbarn, die nur eine Linie trennt (Schrankdreiecke, Küchenschränke, Bett + Kissen),
    - außer schmalen Streifen längs der Wand (Vorwand, Schacht): Sie grenzen an WC, Waschtisch
      und Wanne zugleich und würden das ganze Bad zu einem Block verbinden,
    - Figuren innerhalb einer anderen gehören zu ihr (Maßkasten in der Wanne)."""
    strips = [i for i in parts if _wall_strip(i, labels, stats, outside)]
    joined = [i for i in parts if i not in strips]
    size = 2 * MERGE_PX + 1
    grouped = np.asarray(
        cv2.dilate(np.isin(labels, joined).astype(np.uint8), np.ones((size, size), np.uint8))
    )
    grouped[np.isin(labels, [*floor, *strips])] = 0  # nie in Boden oder Wandstreifen wachsen
    n, group_labels = cv2.connectedComponents(grouped, connectivity=8)
    masks = [group_labels == g for g in range(1, n)] + [labels == i for i in strips]

    # verschachtelte Figuren in die umgebende übernehmen (größte zuerst)
    masks.sort(key=lambda m: -int(_filled(m).sum()))
    result: list[np.ndarray] = []
    for mask in masks:
        pixels = int(mask.sum())
        host = next((r for r in result if int((mask & _filled(r)).sum()) > 0.8 * pixels), None)
        if host is not None:
            host |= mask
        else:
            result.append(mask.copy())
    return result


def _floor_parts(candidates: list[int], labels: np.ndarray, stats: np.ndarray) -> set[int]:
    """Innenflächen, die Boden sind – nur in Räumen mit Fliesenraster:
    - Fliesen: viele gleich große, achsenparallele Rechtecke,
    - angeschnittene Fliesen (an Möbeln, Wänden): passen in eine Fliese und grenzen an Boden,
    - große unregelmäßige Restflächen ohne Inneres am Fliesenbereich (Boden neben der Theke) –
      der Wannenrand umschließt dagegen die Wanne und bleibt Möbel.
    Ohne Fliesenraster ist jede Innenfläche Teil eines Möbels."""
    box = {
        i: (int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])) for i in candidates
    }
    fill = {i: stats[i, cv2.CC_STAT_AREA] / max(w * h, 1) for i, (w, h) in box.items()}
    rect = {i: wh for i, wh in box.items() if fill[i] >= 0.88}
    # häufigste Zellgröße (auf 2 Rasterpunkte gerundet) = Fliesenformat
    sizes: dict[tuple[int, int], int] = {}
    for w, h in rect.values():
        if max(w, h) * RASTER_MM <= TILE_MAX_MM:
            key = (round(w / 2), round(h / 2))
            sizes[key] = sizes.get(key, 0) + 1
    if not sizes:
        return set()
    (tw, th), n = max(sizes.items(), key=lambda kv: kv[1])
    if n < TILE_MIN_CELLS:
        return set()
    # Randfliesen an der Wand sind etwas größer (dort fehlt die Fugenlinie)
    limit_w, limit_h = tw * 2 * 1.35 + 1, th * 2 * 1.35 + 1
    floor = {i for i, (w, h) in rect.items() if w <= limit_w and h <= limit_h}

    def fits_tile(i: int) -> bool:
        w, h = box[i]
        return (w <= limit_w and h <= limit_h) or (w <= limit_h and h <= limit_w)

    def patch(i: int) -> bool:
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area * RASTER_MM**2 / 1e6 < LOOSE_PATCH_M2 or fill[i] >= 0.7:
            return False
        return int(_filled(labels == i).sum()) <= area * 1.05  # umschließt nichts

    kernel = np.ones((7, 7), np.uint8)  # über eine Fuge (2 Rasterpunkte) hinweg
    for _ in range(5):  # Randfliesen grenzen oft nur an andere Randfliesen
        grown = np.asarray(cv2.dilate(np.isin(labels, list(floor)).astype(np.uint8), kernel))
        touching = set(np.unique(labels[grown > 0]).tolist())
        added = {
            i for i in candidates if i not in floor and i in touching and (fits_tile(i) or patch(i))
        }
        if not added:
            break
        floor |= added
    return floor


def _in_mask(mask: np.ndarray, px: np.ndarray) -> bool:
    col, row = int(px[0]), int(px[1])
    return 0 <= row < mask.shape[0] and 0 <= col < mask.shape[1] and bool(mask[row, col])


# ------------------------------------------------------------------ Einordnen
def _between(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def classify(symbol: Symbol, room_type: RoomType, kitchen: bool) -> str | None:
    """Möbelart aus Maßen und Merkmalen – None, wenn nichts sicher passt."""
    long, short = symbol.long, symbol.short
    text = " ".join(symbol.texts)
    if any(_WASHER.match(t.strip()) for t in symbol.texts):
        return "washing_machine"
    if symbol.parts == 1 and symbol.triangles == 1:
        return None  # Türblatt mit Aufschlag-Sehne
    for pattern, kind in _HINTS:  # Beschriftung im Möbel („Kommode 2,70/60“)
        if pattern.search(text.lower()) and short <= 800:
            return kind
    dimension = _BATH_DIMENSION.search(text) or _SQUARE_DIMENSION.search(text)
    if symbol.parts == 1 and symbol.texts and not dimension:
        return None  # Rahmen einer Raumbeschriftung
    if symbol.curves >= 6 and long <= 1400:
        return "plant"  # Blätter als viele Kurven
    wet = room_type in (RoomType.BATHROOM, RoomType.WC)
    if wet or _BATH_DIMENSION.search(text) or _SQUARE_DIMENSION.search(text):
        if _BATH_DIMENSION.search(text) or (
            _between(long, 1400, 2000) and _between(short, 650, 950)
        ):
            return "bathtub"
        if _SQUARE_DIMENSION.search(text) or (
            _between(long, 750, 1250) and _between(short, 750, 1250)
        ):
            return "shower"
        # WC-Schüssel: rund/oval statt Rechteckbecken
        oval = symbol.vertices >= 6 or symbol.area / max(long * short, 1.0) < 0.85
        if _between(long, 400, 750) and _between(short, 300, 480) and (symbol.curves or oval):
            return "wc"
        if _between(long, 400, 1300) and _between(short, 300, 650):
            return "washbasin"
        return None  # im Bad nur Sanitärobjekte (Schächte, Vorwände bleiben leer)
    has_cross = symbol.triangles >= 2
    if kitchen and _between(short, 550, 750) and long >= 1200 and symbol.parts >= 3:
        return "kitchen"  # Zeile aus Einzelschränken (Kochfeld, Spüle, Hochschrank)
    living = room_type in (RoomType.LIVING, RoomType.DINING)
    if (
        living
        and symbol.parts >= 3
        and symbol.curves <= 1  # Polster sind gerade Linien, Stühle Kurven
        and not has_cross
        and _between(long, 1600, 3600)
        and _between(short, 700, 2400)
    ):
        return "sofa"
    if kitchen and _between(short, 400, 1400) and _between(long, 1200, 3000) and not has_cross:
        return "counter?"  # Theke – oder Tisch/Sofa, entscheidet die Nähe zur Küchenzeile
    if has_cross and room_type is RoomType.HALLWAY and short >= 380 and long >= 500:
        return "wardrobe"  # Garderobenschrank
    if has_cross:
        if _between(short, 500, 750) and long >= 500:
            return "wardrobe"
        if _between(short, 280, 500) and long >= 600:
            return "sideboard"
        if _between(short, 500, 700) and _between(long, 500, 700) and room_type is RoomType.UTILITY:
            return "washing_machine"
    if not living and _between(short, 1300, 2100) and _between(long, 1900, 2300):
        return "bed_double"
    if not living and _between(short, 800, 1299) and _between(long, 1900, 2300):
        return "bed_single"
    if _between(short, 300, 620) and _between(long, 300, 620):
        return "chair" if symbol.curves or symbol.parts <= 2 else None
    if (
        room_type in (RoomType.CHILD, RoomType.OFFICE, RoomType.BEDROOM)
        and _between(short, 500, 850)
        and _between(long, 900, 1800)
    ):
        return "desk"
    if _between(short, 750, 1150) and _between(long, 1200, 2800):
        return "table?"  # Esstisch, Couchtisch oder Sofa – entscheidet die Umgebung
    if _between(short, 700, 1000) and _between(long, 700, 1000):
        return "armchair"
    if _between(short, 300, 500) and _between(long, 1000, 2600):
        return "sideboard"
    if _between(short, 450, 900) and _between(long, 800, 1400):
        return "coffee_table"
    return None


COUNTER_REACH_MM = 2_500.0


def _resolve_tables(symbols: list[Symbol]) -> None:
    """Theke nur nahe der Küchenzeile. Tisch mit Stühlen ringsum → Esstisch; mit Polstern →
    Sofa; sonst Couchtisch."""
    kitchens = [s for s in symbols if s.kind == "kitchen"]
    for s in symbols:
        if s.kind == "counter?":
            close = any(np.linalg.norm(k.center - s.center) < COUNTER_REACH_MM for k in kitchens)
            s.kind = "kitchen_counter" if close else "table?"
    chairs = [s for s in symbols if s.kind == "chair"]
    for s in symbols:
        if s.kind != "table?":
            continue
        near = [c for c in chairs if np.linalg.norm(c.center - s.center) < s.long / 2 + 700]
        if len(near) >= 2:
            s.kind = "dining_table"
        elif s.parts >= 3:
            s.kind = "sofa"
        else:
            s.kind = "coffee_table"


# ------------------------------------------------------------------ Ausrichtung
def _front(symbol: Symbol, room: np.ndarray, others: list[Symbol]) -> tuple[float, float, float]:
    """(Front-Richtung, Breite, Tiefe): Rückseite an der Wand. Betten mit dem Kopfende (kurze
    Seite), Schränke/Küchen/Sofas mit der langen Seite; Stühle schauen zum nächsten Tisch."""
    u = np.array([math.cos(symbol.axis), math.sin(symbol.axis)])
    v = np.array([-u[1], u[0]])
    # Seiten: (Normale nach außen, Länge der Seite = Breite, Abstand zur Mitte = halbe Tiefe)
    ends = [(u, symbol.short, symbol.long / 2), (-u, symbol.short, symbol.long / 2)]
    flanks = [(v, symbol.long, symbol.short / 2), (-v, symbol.long, symbol.short / 2)]
    sides = ends + flanks
    polygon = room.astype(np.float32)

    def at_wall(side: tuple[np.ndarray, float, float]) -> bool:
        normal, _, half = side
        x, y = symbol.center + normal * (half + WALL_PROBE_MM)
        return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) < 0

    def facing(normal: np.ndarray, width: float) -> tuple[float, float, float]:
        return math.atan2(normal[1], normal[0]), width, symbol.long + symbol.short - width

    kind = symbol.kind or ""
    targets = {
        "chair": ("dining_table", "desk", "coffee_table", "kitchen_counter"),
        "office_chair": ("desk",),
        "armchair": ("coffee_table",),
        "kitchen_counter": ("kitchen",),  # Schrankseite zur Küchenzeile
    }
    if kind in targets:
        tables = [o for o in others if o.kind in targets[kind] and o is not symbol]
        if tables:
            target = min(tables, key=lambda o: float(np.linalg.norm(o.center - symbol.center)))
            direction = target.center - symbol.center
            normal, width, _ = max(sides, key=lambda s: float(s[0] @ direction))
            return facing(normal, width)
    if kind in ("bed_double", "bed_single", "wc"):
        candidates = ends  # Kopfende / Spülkasten an der kurzen Seite
    elif kind in ("chair", "washing_machine", "shower", "nightstand", "armchair"):
        candidates = sides
    else:
        candidates = flanks  # Schrank, Küche, Sofa, Wanne: lange Seite an der Wand
    backs = [s for s in candidates if at_wall(s)] or [s for s in sides if at_wall(s)] or candidates
    normal, width, _ = backs[0]
    return facing(-normal, width)


def find_furniture(
    rooms: list[Room],
    lines: list[np.ndarray],
    curves: list[np.ndarray],
    texts: list[TextItem],
    stairs: list[np.ndarray] | None = None,
) -> list[Furniture]:
    """``lines``/``curves``: Linienzüge in mm ohne Schraffuren und Türaufschläge; ``stairs``:
    Treppenumrisse – Stufen sind auch geschlossene Figuren, aber kein Möbel."""
    stair_outlines = [s.astype(np.float32) for s in stairs or []]

    def on_stair(point: np.ndarray) -> bool:
        x, y = float(point[0]), float(point[1])
        return any(cv2.pointPolygonTest(s, (x, y), True) > -300.0 for s in stair_outlines)

    found: list[Furniture] = []
    for room in rooms:
        if room.outdoor:
            continue
        polygon = np.array([[p.x, p.y] for p in room.polygon])
        lo, hi = polygon.min(axis=0), polygon.max(axis=0)

        def near(pts: np.ndarray, lo: np.ndarray = lo, hi: np.ndarray = hi) -> bool:
            return bool(np.all(pts.max(axis=0) >= lo) and np.all(pts.min(axis=0) <= hi))

        label = (room.label or "").lower()
        kitchen = bool(re.search(r"koch|küche|kueche", label))
        room_type = room.room_type if room.room_type is not RoomType.UNKNOWN else _room_type(label)
        symbols = _symbols(
            polygon,
            [ln for ln in lines if near(ln)],
            [c for c in curves if near(c)],
            [t for t in texts if lo[0] <= t.position.x <= hi[0] and lo[1] <= t.position.y <= hi[1]],
            keep_whole=lambda s, t=room_type, k=kitchen: classify(s, t, k) in WHOLE,
        )
        symbols = [s for s in symbols if not on_stair(s.center)]
        for s in symbols:
            s.kind = classify(s, room_type, kitchen)
        _resolve_tables(symbols)
        _nightstands(symbols)
        symbols += _missing_tables(symbols)
        for s in symbols:
            if s.kind is None or s.kind.endswith("?"):
                continue
            angle, width, depth = _front(s, polygon, symbols)
            center = s.center
            if s.kind == "sofa" and depth > SOFA_DEPTH_MM * 1.2:
                # Ecksofa: gerades Sofa entlang der Rückseite (Recamiere entfällt)
                back = -np.array([math.cos(angle), math.sin(angle)])
                center = center + back * (depth - SOFA_DEPTH_MM) / 2
                depth = SOFA_DEPTH_MM
            found.append(
                Furniture(
                    id=f"furniture_{len(found):03d}",
                    kind=s.kind,
                    center=Point2D(x=float(center[0]), y=float(center[1])),
                    width_mm=round(width, 1),
                    depth_mm=round(depth, 1),
                    angle_deg=round(math.degrees(angle), 2),
                    room_id=room.id,
                    confidence=0.7,
                )
            )
    return found


CHAIR_CLUSTER_MM = 1_500.0
CHAIR_HALF_DEPTH_MM = 250.0


def _missing_tables(symbols: list[Symbol]) -> list[Symbol]:
    """Stuhlreihen ohne erkannten Tisch (Tisch mit der Umgebung verschmolzen): Tisch zwischen
    den Reihen ergänzen – Stühle ohne Tisch sehen im Modell falsch aus."""
    chairs = [s for s in symbols if s.kind == "chair"]
    tables = [s for s in symbols if s.kind in ("dining_table", "kitchen_counter", "desk")]
    clusters: list[list[Symbol]] = []
    for chair in chairs:
        near = [
            c
            for c in clusters
            if any(np.linalg.norm(chair.center - o.center) < CHAIR_CLUSTER_MM for o in c)
        ]
        merged = [chair] + [o for c in near for o in c]
        clusters = [c for c in clusters if c not in near] + [merged]
    added = []
    for cluster in clusters:
        if len(cluster) < 4:
            continue
        pts = np.array([c.center for c in cluster])
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        if any(np.all(t.center >= lo) and np.all(t.center <= hi) for t in tables):
            continue
        span = hi - lo
        # Stühle in zwei Spalten (wenige x-Werte) → Tisch längs y, sonst längs x
        columns = len({round(float(x) / 300) for x in pts[:, 0]})
        rows = len({round(float(y) / 300) for y in pts[:, 1]})
        along = 1 if columns < rows else 0
        inset = np.full(2, CHAIR_HALF_DEPTH_MM)
        inset[
            along
        ] = -CHAIR_HALF_DEPTH_MM  # längs über die Stühle hinaus, quer zwischen den Reihen
        size = span - 2 * inset
        if size.min() < 500:
            continue
        axis = 0.0 if size[0] >= size[1] else math.pi / 2
        added.append(
            Symbol(
                center=(lo + hi) / 2,
                long=float(size.max()),
                short=float(size.min()),
                axis=axis,
                area=float(size[0] * size[1]),
                parts=1,
                triangles=0,
                small_parts=0,
                curves=0,
                kind="dining_table",
            )
        )
    return added


# Die Erkennung läuft vor der Raumklassifizierung – grobe Raumart aus der Beschriftung genügt
_ROOM_WORDS: list[tuple[re.Pattern[str], RoomType]] = [
    (re.compile(r"\bbad\b|dusche|\bwc\b|toilette"), RoomType.BATHROOM),
    (re.compile(r"schlaf|eltern"), RoomType.BEDROOM),
    (re.compile(r"kind|gast"), RoomType.CHILD),
    (re.compile(r"büro|arbeit|office"), RoomType.OFFICE),
    (re.compile(r"\bh[aw]r\b|hauswirtschaft|abstell|technik|wasch"), RoomType.UTILITY),
    (re.compile(r"wohn|essen"), RoomType.LIVING),
    (re.compile(r"küche|koch"), RoomType.KITCHEN),
    (re.compile(r"diele|flur|eingang|garderobe"), RoomType.HALLWAY),
]


def _room_type(label: str) -> RoomType:
    return next((t for pattern, t in _ROOM_WORDS if pattern.search(label)), RoomType.UNKNOWN)


def _nightstands(symbols: list[Symbol]) -> None:
    """Kleine Quadrate neben einem Bett sind Nachttische, keine Stühle; eine Pflanze zählt einmal
    (Blätter und Topf sind oft getrennte Figuren)."""
    beds = [s for s in symbols if s.kind in ("bed_double", "bed_single")]
    for s in symbols:
        if s.kind == "chair" and any(
            np.linalg.norm(s.center - b.center) < b.long / 2 + b.short / 2 + 300 for b in beds
        ):
            s.kind = "nightstand"
    plants = sorted((s for s in symbols if s.kind == "plant"), key=lambda s: -s.area)
    for i, s in enumerate(plants):
        if any(np.linalg.norm(s.center - p.center) < 600 for p in plants[:i] if p.kind == "plant"):
            s.kind = None
