"""FloorPlan + BLVResult → Szenenbeschreibung (JSON) für das Blender-Skript.

Das Blender-Skript kennt keine Lumira-Modelle (es läuft in Blenders eigenem Python), daher
diese schlanke, stabile Zwischenform. Einheiten: Millimeter. Oberflächen siehe surfaces.py.
"""

from __future__ import annotations

import re
from typing import Any

from lumira_generator.logic.furnish import furnish, point_in_polygon, wall_outlines
from lumira_generator.logic.railing import stair_railing
from lumira_generator.logic.surfaces import TEXTURES_MM, describe
from lumira_shared.models import (
    BLVResult,
    FloorPlan,
    Material,
    MaterialCategory,
    MaterialLocation,
    Opening,
    OpeningType,
    Room,
    RoomType,
    Stair,
    Wall,
)

FALLBACK_FLOOR = {"name": "Estrich", "color": "#9E9A93"}
FALLBACK_WALL = {"name": "Wandfarbe weiß", "color": "#F1ECE1"}
FALLBACK_DOOR = {"name": "Innentür weiß", "color": "#F2F1EC"}
FALLBACK_WINDOW = {"name": "Fensterrahmen weiß", "color": "#F4F4F2"}
_FLOOR_CATEGORIES = (MaterialCategory.FLOORING, MaterialCategory.TILES)
_RAILING = re.compile(r"geländer|handlauf|brüstung|stab|pfosten")


def _floor_score(material: Material, room_type: RoomType) -> tuple[int, ...]:
    """Kleiner = besser. Nassräume bevorzugen Fliesen; Bodenfliesen vor Wandfliesen."""
    name = material.name.lower()
    wet = room_type in (RoomType.BATHROOM, RoomType.WC)
    return (
        0 if (material.category is MaterialCategory.TILES) == wet else 1,
        0 if "boden" in name else (2 if "wand" in name else 1),
        0 if material.room_types else 1,  # raumgenaue Angabe vor „gilt überall“
    )


def _visible_floors(blv: BLVResult, room_type: RoomType, variant: str | None) -> list[Material]:
    return [
        m
        for m in blv.materials_for(variant=variant, room_type=room_type)
        if m.category in _FLOOR_CATEGORIES and m.is_visible_inside
    ]


def _floor_for(blv: BLVResult, room_type: RoomType, variant: str | None) -> dict[str, Any]:
    """Bodenbelag des Raums in der Variante. Nennt die Variante für den Raum nur Untergrund
    (z. B. Standard = Estrich, Parkett als Sonderwunsch), zeigt ein Verkaufsmodell trotzdem
    einen fertigen Boden: den ersten passenden aus den übrigen Varianten – gekennzeichnet."""
    candidates = _visible_floors(blv, room_type, variant)
    borrowed_from = None
    if not candidates:
        for other in blv.variants:
            candidates = _visible_floors(blv, room_type, other.name)
            if candidates:
                borrowed_from = other.name
                break
    material = min(candidates, key=lambda m: _floor_score(m, room_type)) if candidates else None
    surface = describe(material, fallback=FALLBACK_FLOOR)
    if borrowed_from is not None:
        surface["from_variant"] = borrowed_from
    return surface


FALLBACK_OUTDOOR_FLOOR = {
    "name": "Terrassendielen",
    "color": "#8B6B4E",
    "kind": "texture",
    "texture": "plank_flooring_04",
    "size_mm": TEXTURES_MM["plank_flooring_04"],
}
_OUTDOOR_FLOOR = re.compile(r"terrass|balkon|loggia|au[ßs]enbelag")


def _outdoor_floor(blv: BLVResult, variant: str | None) -> dict[str, Any]:
    """Belag von Terrasse/Balkon laut LV („Terrassenbelag …“, meist Lage außen) – sonst
    Holzdielen, die übliche Ausführung überdachter Terrassen."""
    found = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category in (*_FLOOR_CATEGORIES, MaterialCategory.OTHER)
        and m.is_final_surface
        and _OUTDOOR_FLOOR.search(m.name.lower())
    ]
    if not found:
        return dict(FALLBACK_OUTDOOR_FLOOR)
    return describe(found[0], fallback=FALLBACK_OUTDOOR_FLOOR, outdoor=True)


def _room_floor(blv: BLVResult, room: Room, variant: str | None) -> dict[str, Any]:
    if room.outdoor:
        return _outdoor_floor(blv, variant)
    return _floor_for(blv, room.room_type, variant)


ROOF_OVERHANG_MM = 100.0  # Dach steht über die Stützen hinaus
_COLUMN_REACH_MM = 1_000.0


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in seq:
            while (
                len(out) >= 2
                and (
                    (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                    - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])
                )
                <= 0
            ):
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(pts[::-1])


def _roof(plan: FloorPlan, room: Room) -> list[list[float]] | None:
    """Dach über einem überdachten Außenbereich: Hülle aus Belag und den Stützen daneben."""
    if not (room.outdoor and room.roofed):
        return None
    polygon = [(p.x, p.y) for p in room.polygon]
    xs, ys = [x for x, _ in polygon], [y for _, y in polygon]
    points = list(polygon)
    for column in plan.columns:
        cx, cy = column.center.x, column.center.y
        if not (
            min(xs) - _COLUMN_REACH_MM <= cx <= max(xs) + _COLUMN_REACH_MM
            and min(ys) - _COLUMN_REACH_MM <= cy <= max(ys) + _COLUMN_REACH_MM
        ):
            continue
        half = column.size_mm / 2 + ROOF_OVERHANG_MM
        points += [(cx + dx, cy + dy) for dx in (-half, half) for dy in (-half, half)]
    return [[x, y] for x, y in _convex_hull(points)]


def _first(blv: BLVResult, variant: str | None, category: MaterialCategory) -> Material | None:
    """Erstes sichtbares Innenmaterial der Kategorie – wohnungsweite Angaben zuerst."""
    found = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is category and m.is_visible_inside
    ]
    return min(found, key=lambda m: 0 if not m.room_types else 1) if found else None


def _wall_finish(blv: BLVResult, variant: str | None) -> dict[str, Any]:
    material = _first(blv, variant, MaterialCategory.WALL_FINISH)
    return describe(material, fallback=FALLBACK_WALL, wall=True)


def _color(material: Material | None, fallback: dict[str, str]) -> dict[str, str]:
    if material is None or not material.color_hex:
        return fallback
    return {"name": material.name, "color": material.color_hex}


_ENTRANCE = re.compile(r"haust[üu]r|hauseingang|wohnungseingang|eingangst[üu]r")
_WINDOW_SILL = re.compile(r"fensterbank|fensterb[äa]nke")
# „Weiß innen / Anthrazit RAL 7016 außen“ → der Teil mit „innen“ bestimmt die Farbe im Raum
_INSIDE_PART = re.compile(r"([^/;,]*\binnen\b[^/;,]*)", re.IGNORECASE)
_WHITE = re.compile(r"wei(ß|ss)", re.IGNORECASE)


def _door_finish(blv: BLVResult, variant: str | None) -> dict[str, str]:
    """Innentüren bestimmen die Türfarbe – Haus-/Wohnungseingangstüren nur als Rückfall."""
    doors = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is MaterialCategory.DOOR and m.is_visible_inside
    ]
    inner = [m for m in doors if not _ENTRANCE.search(m.name.lower())]
    return _color((inner or doors or [None])[0], FALLBACK_DOOR)


def _window_frame(blv: BLVResult, variant: str | None) -> dict[str, str]:
    """Fensterrahmen innen: bei „weiß innen / anthrazit außen“ zählt die Innenseite."""
    frames = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is MaterialCategory.WINDOW
        and m.location is MaterialLocation.INTERIOR
        and not _WINDOW_SILL.search(m.name.lower())
    ]
    if not frames:
        return FALLBACK_WINDOW
    frame = frames[0]
    inside = _INSIDE_PART.search(frame.color or "")
    if inside and _WHITE.search(inside.group(1)):
        return {"name": frame.name, "color": FALLBACK_WINDOW["color"]}
    return _color(frame, FALLBACK_WINDOW)


def _step_surface(surface: dict[str, Any]) -> dict[str, Any]:
    """Stufen sind aus einem Stück (Stufenplatte): Fliesen ohne Fugenraster, leicht glänzend."""
    if surface.get("kind") != "tiles":
        return surface
    # eigener Name: dasselbe LV-Material als Fliese (Boden) und als Stufenplatte sind zwei
    # verschiedene Materialien im Modell
    lower = surface["name"].lower()
    name = (
        surface["name"] if "treppe" in lower or "stufe" in lower else f"{surface['name']} (Stufen)"
    )
    return {"name": name, "color": surface["color"], "kind": "plain", "roughness": 0.35}


def _stair_tread(
    plan: FloorPlan, stair: Stair, blv: BLVResult, variant: str | None
) -> dict[str, Any]:
    """Stufenbelag laut LV (Kategorie Treppe, ohne Geländer) – sonst der Boden des Raums am
    Antritt."""
    treads = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is MaterialCategory.STAIRS
        and m.is_visible_inside
        and not _RAILING.search(m.name.lower())
    ]
    if treads:
        return _step_surface(describe(treads[0], fallback=FALLBACK_FLOOR))
    start = stair.walking_line[0] if stair.walking_line else stair.steps[0][0]
    room_type = next(
        (
            room.room_type
            for room in plan.rooms
            if point_in_polygon((start.x, start.y), [(p.x, p.y) for p in room.polygon])
        ),
        RoomType.HALLWAY,
    )
    return _step_surface(_floor_for(blv, room_type, variant))


FALLBACK_RAILING = {"name": "Geländer Stahl", "color": "#3A3A3A"}


def _railing_finish(blv: BLVResult, variant: str | None) -> dict[str, str]:
    """Farbe des Treppengeländers laut LV (z. B. „pulverbeschichtet anthrazit“)."""
    railings = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is MaterialCategory.STAIRS and _RAILING.search(m.name.lower())
    ]
    return _color(railings[0] if railings else None, FALLBACK_RAILING)


def _stairs(plan: FloorPlan, blv: BLVResult, variant: str | None) -> list[dict[str, Any]]:
    walls = wall_outlines(plan)
    finish = _railing_finish(blv, variant)
    return [
        {
            "id": stair.id,
            # Stufe k: massiver Block bis zur Oberkante (k + 1) · Steigung; letzte = oberes Geschoss
            "steps": [
                {"polygon": [[p.x, p.y] for p in polygon], "top": (k + 1) * stair.rise_mm}
                for k, polygon in enumerate(stair.steps)
            ],
            "outline": [[p.x, p.y] for p in stair.outline],
            "floor_to_floor": stair.floor_to_floor_mm,
            "tread": _stair_tread(plan, stair, blv, variant),
            # Geländer an den offenen Seiten (Treppenauge, freie Wange)
            "railing": stair_railing(stair, walls),
            "railing_finish": finish,
        }
        for stair in plan.stairs
    ]


def _threshold_room(plan: FloorPlan, wall: Wall, opening: Opening) -> str | None:
    """Boden in Türen und Durchgängen: Die Raumböden enden an der Wandfläche – ohne Schwelle
    klafft in der Wanddicke eine Lücke (sichtbar und im Rundgang eine Absturzkante). Belag des
    Raums, in den die Tür aufschlägt, sonst des ersten angrenzenden Raums (liefert dessen ID)."""
    if opening.type is OpeningType.WINDOW:
        return None
    length = wall.length_mm or 1.0
    ux, uy = (wall.end.x - wall.start.x) / length, (wall.end.y - wall.start.y) / length
    along = opening.offset_mm + opening.width_mm / 2
    cx, cy = wall.start.x + ux * along, wall.start.y + uy * along
    reach = wall.thickness_mm / 2 + 150.0
    sides = {"left": (-uy, ux), "right": (uy, -ux)}
    order = [opening.opens_to] if opening.opens_to else []
    order += [side for side in ("left", "right") if side not in order]
    for side in order:
        nx, ny = sides[side]
        point = (cx + nx * reach, cy + ny * reach)
        for room in plan.rooms:
            if point_in_polygon(point, [(p.x, p.y) for p in room.polygon]):
                return room.id
    return None


def _variants(plan: FloorPlan, blv: BLVResult, chosen: str | None) -> list[dict[str, Any]]:
    """Ausstattungsvarianten aus dem LV: Bodenbelag je Raum und Stufenbelag je Treppe. Im Modell
    als umschaltbare Materialsätze (glTF KHR_materials_variants). Nur Varianten, die in diesem
    Grundriss etwas ändern (z. B. kein „Balkon“ ohne Balkon); leer, wenn keine übrig bleibt."""

    def plain(surface: dict[str, Any]) -> dict[str, Any]:
        # Herkunft („ausgeliehen aus Variante …“) ist für den Vergleich/Wechsel ohne Belang
        return {k: v for k, v in surface.items() if k != "from_variant"}

    def surfaces(name: str | None) -> dict[str, Any]:
        return {
            "floors": {r.id: plain(_room_floor(blv, r, name)) for r in plan.rooms},
            "treads": {s.id: plain(_stair_tread(plan, s, blv, name)) for s in plan.stairs},
        }

    # LVs führen Varianten oft als Einzeloptionen („Parkett statt Estrich“, „Fliesen im WC“):
    # Räume ohne eigenen Belag in einer Variante zeigen den Belag der Grundansicht. Leiht die
    # Grundansicht selbst aus anderen Varianten, ist sie eine Musterausstattung, kein „Standard“.
    borrowed = any("from_variant" in _room_floor(blv, r, chosen) for r in plan.rooms)
    seen = [surfaces(chosen)]
    variants = [{"name": "Musterausstattung" if borrowed else chosen or "Standard", **seen[0]}]
    for variant in blv.variants:
        other = surfaces(variant.name)
        if variant.name != chosen and other not in seen:  # gleiche Beläge nur einmal
            seen.append(other)
            variants.append({"name": variant.name, **other})
    return variants if len(variants) > 1 else []


def build_scene(plan: FloorPlan, blv: BLVResult, *, variant: str | None = None) -> dict[str, Any]:
    chosen = variant or (blv.default_variant.name if blv.default_variant else None)
    floors = {room.id: _room_floor(blv, room, chosen) for room in plan.rooms}
    thresholds = {
        o.id: _threshold_room(plan, plan.wall(o.wall_id), o)
        for o in plan.openings
        if o.type is not OpeningType.WINDOW
    }
    walls = [
        {
            "id": wall.id,
            "start": [wall.start.x, wall.start.y],
            "end": [wall.end.x, wall.end.y],
            "thickness": wall.thickness_mm,
            "height": wall.height_mm,
            "exterior": bool(wall.is_exterior),
            # Exakter Grundriss (CAD-Fläche) – sonst baut Blender einen Quader aus der Achse.
            "footprint": [[p.x, p.y] for p in wall.footprint] if wall.footprint else None,
            "openings": [
                {
                    "id": o.id,
                    "type": str(o.type),
                    "offset": o.offset_mm,
                    "width": o.width_mm,
                    "height": o.height_mm,
                    "sill": o.sill_height_mm,
                    "swing": str(o.swing) if o.swing else None,
                    "opens_to": o.opens_to,
                    "threshold": floors[room] if (room := thresholds.get(o.id)) else None,
                    "threshold_room": thresholds.get(o.id),  # Belag folgt der Variante
                }
                for o in plan.openings_in(wall.id)
            ],
        }
        for wall in plan.walls
    ]
    rooms = [
        {
            "id": room.id,
            "type": str(room.room_type),
            "label": room.label,
            "polygon": [[p.x, p.y] for p in room.polygon],
            "floor": floors[room.id],
            # Außenbereich: Belag auf einer Platte, keine Decke/Leuchte; überdacht → Dach
            "outdoor": room.outdoor,
            "roof": _roof(plan, room),
        }
        for room in plan.rooms
    ]
    columns = [
        {"center": [c.center.x, c.center.y], "size": c.size_mm, "angle": c.angle_deg}
        for c in plan.columns
    ]
    return {
        "units": "mm",
        "project_id": str(plan.project_id),
        "variant": chosen,
        "wall_finish": _wall_finish(blv, chosen),
        "door_finish": _door_finish(blv, chosen),
        "window_frame": _window_frame(blv, chosen),
        "walls": walls,
        "rooms": rooms,
        "stairs": _stairs(plan, blv, chosen),
        "columns": columns,
        "variants": _variants(plan, blv, chosen),
        # Einrichtung: feste Ausstattung (Küche, Sanitär) + lose Möbel (im Viewer ausblendbar)
        "fixtures": furnish(plan, blv),
    }
