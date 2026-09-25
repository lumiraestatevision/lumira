"""Ausgabeschema für das LLM und Überführung in das gemeinsame BLVResult.

Das Schema ist bewusst flach und ohne Validierungs-Constraints: Die strukturierte Ausgabe
der API garantiert gültiges JSON in dieser Form. Fachliche Nachbearbeitung passiert
deterministisch in ``to_blv_result``:

- Bereinigung: Haustechnik, Rohbau, Lage, Kategorie, Dubletten (siehe cleanup.py)
- Farben: RAL-Tabelle > Farbangabe im LV > gekennzeichnete Annahme (siehe colors.py)
- Varianten: Das LLM nennt für Nicht-Standard-Varianten nur die ABWEICHENDEN Materialien;
  alles Übrige wird aus der Standardvariante übernommen. Optionen ohne eigenes Material
  (z. B. "Schornstein") sehen aus wie der Standard und werden nur als Hinweis geführt.
- Referenzen und genau eine Standardvariante werden geprüft
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from lumira_blv.logic.cleanup import clean_extraction
from lumira_blv.logic.colors import resolve_color
from lumira_shared.models import (
    BLVResult,
    ColorSource,
    EquipmentVariant,
    Material,
    MaterialCategory,
    MaterialLocation,
    RoomType,
)

_DEFAULT_NAME = re.compile(r"standard|basis|grund", re.IGNORECASE)


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
    location: MaterialLocation = Field(
        description="interior: in der Wohnung sichtbar. exterior: Fassade, Sockel, Dach, "
        "Balkon-/Terrassenbelag, Außenfensterbank, Außenseite von Fenstern und Türen"
    )
    is_final_surface: bool = Field(
        description="true: sichtbare Endoberfläche. false: Untergrund, der später belegt wird "
        "(z. B. Estrich, Unterboden, Trockenbauplatte vor dem Belag)"
    )
    blv_position: str | None = Field(description="Positionsnummer im LV, z. B. '02.03.0010'")
    source_excerpt: str | None = Field(description="Kurzes Zitat der Textstelle")


class ExtractedVariant(BaseModel):
    name: str = Field(description="z. B. 'Standard', 'Parkett statt Teppich', 'Premium'")
    description: str | None
    material_ids: list[str] = Field(
        description="Standardvariante: alle Standardmaterialien. Andere Varianten: NUR die "
        "Materialien, die vom Standard abweichen – der Rest wird übernommen."
    )
    surcharge_eur: float | None = Field(description="Aufpreis in Euro, falls genannt")
    is_default: bool


class BLVExtraction(BaseModel):
    materials: list[ExtractedMaterial]
    variants: list[ExtractedVariant]
    notes: list[str] = Field(description="Unklarheiten und Hinweise zum Dokument")


def _to_material(m: ExtractedMaterial) -> Material:
    color_hex, color_source = resolve_color(
        category=m.category,
        name=m.name,
        color=m.color,
        color_hex=m.color_hex,
        product=m.product,
        finish=m.finish,
    )
    return Material(
        id=m.id,
        category=m.category,
        name=m.name,
        manufacturer=m.manufacturer,
        product=m.product,
        color=m.color,
        color_hex=color_hex,
        color_source=color_source,
        finish=m.finish,
        format=m.format,
        room_types=m.room_types,
        location=m.location,
        is_final_surface=m.is_final_surface,
        blv_position=m.blv_position,
        source_excerpt=m.source_excerpt[:2000] if m.source_excerpt else None,
    )


def _replaces(alternative: Material, standard: Material) -> bool:
    """Ersetzt ein Variantenmaterial ein Standardmaterial? Gleiche Kategorie und Lage,
    und die Räume überschneiden sich (leere Liste = alle Räume)."""
    if (alternative.category, alternative.location) != (standard.category, standard.location):
        return False
    if not alternative.room_types or not standard.room_types:
        return True
    return bool(set(alternative.room_types) & set(standard.room_types))


def _choose_default(variants: list[ExtractedVariant]) -> int | None:
    flagged = [i for i, v in enumerate(variants) if v.is_default]
    if flagged:
        return flagged[0]
    named = [i for i, v in enumerate(variants) if _DEFAULT_NAME.search(v.name)]
    return named[0] if named else (0 if variants else None)


def to_blv_result(
    extraction: BLVExtraction, *, project_id: uuid.UUID, source_key: str, extracted_by: str
) -> BLVResult:
    extraction, cleanup_notes = clean_extraction(extraction)
    notes = [*extraction.notes, *cleanup_notes]
    materials = {m.id: _to_material(m) for m in extraction.materials}

    def known(variant: ExtractedVariant) -> list[str]:
        unknown = [i for i in variant.material_ids if i not in materials]
        if unknown:
            notes.append(f"Variante '{variant.name}': unbekannte Material-IDs {unknown} entfernt")
        return list(dict.fromkeys(i for i in variant.material_ids if i in materials))

    raw = list(extraction.variants)
    default_index = _choose_default(raw)
    if default_index is None:
        raw = [
            ExtractedVariant(
                name="Standard",
                description=None,
                material_ids=[],
                surcharge_eur=None,
                is_default=True,
            )
        ]
        default_index = 0

    ids = [known(v) for v in raw]
    others = {i for n, v_ids in enumerate(ids) if n != default_index for i in v_ids}
    if not ids[default_index]:
        # Standard ohne Materialliste: alles, was nicht exklusiv zu anderen Varianten gehört.
        ids[default_index] = [i for i in materials if i not in others]
    standard = [materials[i] for i in ids[default_index]]

    variants: list[EquipmentVariant] = []
    without_material: list[str] = []
    for n, v in enumerate(raw):
        variant_ids = ids[n]
        if n != default_index and not variant_ids:
            without_material.append(v.name)
            continue
        if n != default_index:
            own = [materials[i] for i in variant_ids]
            inherited = [s.id for s in standard if not any(_replaces(o, s) for o in own)]
            variant_ids = [*variant_ids, *inherited]
        variants.append(
            EquipmentVariant(
                name=v.name,
                description=v.description,
                material_ids=variant_ids,
                surcharge_eur=Decimal(str(v.surcharge_eur))
                if v.surcharge_eur and v.surcharge_eur > 0
                else None,
                is_default=n == default_index,
            )
        )

    if without_material:
        notes.append(
            "Optionen ohne eigenes Material (im 3D-Modell wie Standard): "
            + ", ".join(without_material)
        )
    assumed = [m.name for m in materials.values() if m.color_source is ColorSource.ASSUMED]
    if assumed:
        notes.append(
            f"Farbe angenommen (keine Farbangabe im LV) für {len(assumed)} von "
            f"{len(materials)} Materialien: {', '.join(assumed[:8])}{' …' if len(assumed) > 8 else ''}"
        )

    return BLVResult(
        project_id=project_id,
        source_key=source_key,
        materials=list(materials.values()),
        variants=variants,
        notes=notes,
        extracted_by=extracted_by,
    )
