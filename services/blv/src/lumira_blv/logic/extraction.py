"""Ausgabeschema für das LLM und Überführung in das gemeinsame BLVResult.

Das Schema ist bewusst flach und ohne Validierungs-Constraints: Die strukturierte Ausgabe
der API garantiert gültiges JSON in dieser Form, fachliche Prüfungen (Farbformat,
Referenzen, genau eine Standardvariante) passieren danach in ``to_blv_result``.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from lumira_shared.models import BLVResult, EquipmentVariant, Material, MaterialCategory, RoomType

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ExtractedMaterial(BaseModel):
    id: str = Field(description="Kurze, eindeutige ID, z. B. 'm1'")
    category: MaterialCategory
    name: str = Field(description="Bezeichnung, z. B. 'Eichenparkett, geölt'")
    manufacturer: str | None
    product: str | None
    color: str | None = Field(description="Farbangabe wie im Dokument, z. B. 'RAL 9010'")
    color_hex: str | None = Field(description="#RRGGBB, nur bei eindeutiger Farbangabe")
    finish: str | None
    format: str | None = Field(description="z. B. '60x60 cm'")
    room_types: list[RoomType] = Field(description="Leer = gilt für alle Räume")
    blv_position: str | None = Field(description="Positionsnummer im LV, z. B. '02.03.0010'")
    source_excerpt: str | None = Field(description="Kurzes Zitat der Textstelle")


class ExtractedVariant(BaseModel):
    name: str = Field(description="z. B. 'Standard', 'Komfort', 'Premium'")
    description: str | None
    material_ids: list[str]
    surcharge_eur: float | None = Field(description="Aufpreis in Euro, falls genannt")
    is_default: bool


class BLVExtraction(BaseModel):
    materials: list[ExtractedMaterial]
    variants: list[ExtractedVariant]
    notes: list[str] = Field(description="Unklarheiten und Hinweise zum Dokument")


def to_blv_result(
    extraction: BLVExtraction, *, project_id: uuid.UUID, source_key: str, extracted_by: str
) -> BLVResult:
    notes = list(extraction.notes)
    materials: list[Material] = []
    seen: set[str] = set()
    for m in extraction.materials:
        if m.id in seen:
            notes.append(f"Doppelte Material-ID '{m.id}' verworfen")
            continue
        seen.add(m.id)
        materials.append(
            Material(
                id=m.id,
                category=m.category,
                name=m.name,
                manufacturer=m.manufacturer,
                product=m.product,
                color=m.color,
                color_hex=m.color_hex if m.color_hex and _HEX.match(m.color_hex) else None,
                finish=m.finish,
                format=m.format,
                room_types=m.room_types,
                blv_position=m.blv_position,
                source_excerpt=m.source_excerpt[:2000] if m.source_excerpt else None,
            )
        )

    variants: list[EquipmentVariant] = []
    default_taken = False
    for v in extraction.variants:
        unknown = [i for i in v.material_ids if i not in seen]
        if unknown:
            notes.append(f"Variante '{v.name}': unbekannte Material-IDs {unknown} entfernt")
        is_default = v.is_default and not default_taken
        default_taken = default_taken or is_default
        variants.append(
            EquipmentVariant(
                name=v.name,
                description=v.description,
                material_ids=[i for i in v.material_ids if i in seen],
                surcharge_eur=Decimal(str(v.surcharge_eur))
                if v.surcharge_eur and v.surcharge_eur > 0
                else None,
                is_default=is_default,
            )
        )

    return BLVResult(
        project_id=project_id,
        source_key=source_key,
        materials=materials,
        variants=variants,
        notes=notes,
        extracted_by=extracted_by,
    )
