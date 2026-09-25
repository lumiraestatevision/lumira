"""FloorPlan + BLVResult → Szenenbeschreibung (JSON) für das Blender-Skript.

Das Blender-Skript kennt keine Lumira-Modelle (es läuft in Blenders eigenem Python), daher
diese schlanke, stabile Zwischenform. Einheiten: Millimeter.
"""

from __future__ import annotations

from typing import Any

from lumira_shared.models import BLVResult, FloorPlan, Material, MaterialCategory, RoomType

FALLBACK_FLOOR = {"name": "Estrich", "color": "#9E9A93"}
FALLBACK_WALL = {"name": "Wandfarbe weiß", "color": "#F1ECE1"}
_FLOOR_CATEGORIES = (MaterialCategory.FLOORING, MaterialCategory.TILES)


def _floor_score(material: Material, room_type: RoomType) -> tuple[int, ...]:
    """Kleiner = besser. Nassräume bevorzugen Fliesen; Bodenfliesen vor Wandfliesen."""
    name = material.name.lower()
    wet = room_type in (RoomType.BATHROOM, RoomType.WC)
    return (
        0 if (material.category is MaterialCategory.TILES) == wet else 1,
        0 if "boden" in name else (2 if "wand" in name else 1),
        0 if material.room_types else 1,  # raumgenaue Angabe vor „gilt überall“
    )


def _floor_for(blv: BLVResult, room_type: RoomType, variant: str | None) -> dict[str, str]:
    candidates = [
        m
        for m in blv.materials_for(variant=variant, room_type=room_type)
        if m.category in _FLOOR_CATEGORIES and m.is_visible_inside
    ]
    if not candidates:
        return FALLBACK_FLOOR
    material = min(candidates, key=lambda m: _floor_score(m, room_type))
    return {"name": material.name, "color": material.color_hex or FALLBACK_FLOOR["color"]}


def _wall_finish(blv: BLVResult, variant: str | None) -> dict[str, str]:
    finishes = [
        m
        for m in blv.materials_for(variant=variant)
        if m.category is MaterialCategory.WALL_FINISH and m.is_visible_inside
    ]
    if not finishes:
        return FALLBACK_WALL
    material = min(finishes, key=lambda m: 0 if not m.room_types else 1)  # Wohnräume-weit zuerst
    return {"name": material.name, "color": material.color_hex or FALLBACK_WALL["color"]}


def build_scene(plan: FloorPlan, blv: BLVResult, *, variant: str | None = None) -> dict[str, Any]:
    chosen = variant or (blv.default_variant.name if blv.default_variant else None)
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
            "floor": _floor_for(blv, room.room_type, chosen),
        }
        for room in plan.rooms
    ]
    return {
        "units": "mm",
        "project_id": str(plan.project_id),
        "variant": chosen,
        "wall_finish": _wall_finish(blv, chosen),
        "walls": walls,
        "rooms": rooms,
    }
