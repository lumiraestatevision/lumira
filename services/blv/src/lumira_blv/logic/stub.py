"""STUB: Standardausstattung, wenn kein LV vorliegt oder kein API-Key gesetzt ist.

Damit läuft die Eventkette lokal ohne Kosten und ohne Netzwerk durch.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from lumira_shared.models import BLVResult, EquipmentVariant, Material, MaterialCategory, RoomType

_WET = [RoomType.BATHROOM, RoomType.WC]
_DRY = [
    RoomType.LIVING,
    RoomType.DINING,
    RoomType.BEDROOM,
    RoomType.CHILD,
    RoomType.OFFICE,
    RoomType.HALLWAY,
]


def default_result(project_id: uuid.UUID, *, source_key: str | None, note: str) -> BLVResult:
    materials = [
        Material(
            id="parkett_eiche",
            category=MaterialCategory.FLOORING,
            name="Eichenparkett, Landhausdiele, geölt",
            color="Eiche natur",
            color_hex="#B8894F",
            finish="geölt",
            room_types=_DRY,
        ),
        Material(
            id="parkett_fischgraet",
            category=MaterialCategory.FLOORING,
            name="Eichenparkett, Fischgrät, geölt",
            color="Eiche hell",
            color_hex="#C9A36B",
            finish="geölt",
            room_types=_DRY,
        ),
        Material(
            id="fliese_grau",
            category=MaterialCategory.TILES,
            name="Feinsteinzeug",
            color="Hellgrau",
            color_hex="#BDBDBA",
            finish="matt",
            format="60x60 cm",
            room_types=[*_WET, RoomType.KITCHEN, RoomType.UTILITY, RoomType.STORAGE],
        ),
        Material(
            id="naturstein",
            category=MaterialCategory.TILES,
            name="Naturstein, Kalkstein",
            color="Beige",
            color_hex="#D8CBB0",
            finish="geschliffen",
            format="60x120 cm",
            room_types=_WET,
        ),
        Material(
            id="wandfarbe_weiss",
            category=MaterialCategory.WALL_FINISH,
            name="Dispersionsfarbe",
            color="RAL 9010 Reinweiß",
            color_hex="#F1ECE1",
            finish="matt",
        ),
    ]
    variants = [
        EquipmentVariant(
            name="Standard",
            description="Grundausstattung",
            material_ids=["parkett_eiche", "fliese_grau", "wandfarbe_weiss"],
            is_default=True,
        ),
        EquipmentVariant(
            name="Premium",
            description="Gehobene Ausstattung",
            material_ids=["parkett_fischgraet", "naturstein", "wandfarbe_weiss"],
            surcharge_eur=Decimal("12500"),
        ),
    ]
    return BLVResult(
        project_id=project_id,
        source_key=source_key,
        materials=materials,
        variants=variants,
        notes=[note],
        extracted_by="stub",
    )
