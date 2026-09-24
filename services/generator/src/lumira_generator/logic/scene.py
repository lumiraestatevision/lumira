"""FloorPlan + BLVResult → Szenenbeschreibung (JSON) für das Blender-Skript.

Das Blender-Skript kennt keine Lumira-Modelle (es läuft in Blenders eigenem Python), daher
diese schlanke, stabile Zwischenform. Einheiten: Millimeter.
"""

from __future__ import annotations

from typing import Any

from lumira_shared.models import BLVResult, FloorPlan, MaterialCategory, RoomType

FALLBACK_FLOOR = {"name": "Estrich", "color": "#9E9A93"}
FALLBACK_WALL = {"name": "Wandfarbe weiß", "color": "#F1ECE1"}
_FLOOR_CATEGORIES = (MaterialCategory.FLOORING, MaterialCategory.TILES)


def _floor_for(blv: BLVResult, room_type: RoomType, variant: str | None) -> dict[str, str]:
    candidates = [
        m
        for m in blv.materials_for(variant=variant, room_type=room_type)
        if m.category in _FLOOR_CATEGORIES
    ]
    if room_type in (RoomType.BATHROOM, RoomType.WC):
        candidates.sort(key=lambda m: m.category is not MaterialCategory.TILES)  # Fliesen zuerst
    if not candidates:
        return FALLBACK_FLOOR
    material = candidates[0]
    return {"name": material.name, "color": material.color_hex or FALLBACK_FLOOR["color"]}


def _wall_finish(blv: BLVResult, variant: str | None) -> dict[str, str]:
    for material in blv.materials_for(variant=variant):
        if material.category is MaterialCategory.WALL_FINISH:
            return {"name": material.name, "color": material.color_hex or FALLBACK_WALL["color"]}
    return FALLBACK_WALL


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
