"""Ergebnis der Leistungsverzeichnis-Auswertung: Materialien und Ausstattungsvarianten."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from lumira_shared.models.base import LumiraModel, new_id
from lumira_shared.models.floorplan import RoomType


class MaterialCategory(StrEnum):
    FLOORING = "flooring"  # Bodenbelag
    WALL_FINISH = "wall_finish"  # Wandoberfläche (Putz, Farbe, Tapete)
    TILES = "tiles"  # Fliesen
    CEILING = "ceiling"  # Decke
    SANITARY = "sanitary"  # Sanitärobjekte
    DOOR = "door"
    WINDOW = "window"
    KITCHEN = "kitchen"
    LIGHTING = "lighting"
    STAIRS = "stairs"  # Treppen (Stufen, Geländer)
    OTHER = "other"


class ColorSource(StrEnum):
    RAL = "ral"  # RAL-Code im Dokument → exakter Tabellenwert
    DOCUMENT = "document"  # eindeutige Farbangabe im Dokument
    ASSUMED = "assumed"  # Annahme aus Farb-/Materialwort oder Kategorie (keine Farbangabe)


class MaterialLocation(StrEnum):
    INTERIOR = "interior"  # in der Wohnung sichtbar
    EXTERIOR = "exterior"  # Fassade, Sockel, Dach, Balkon-/Terrassenbelag, Außenfensterbank


class Material(LumiraModel):
    id: str = Field(default_factory=lambda: new_id("mat"))
    category: MaterialCategory
    name: str = Field(min_length=1, description="z. B. 'Eichenparkett, geölt'")
    manufacturer: str | None = None
    product: str | None = None
    color: str | None = Field(default=None, description="Farbangabe aus dem BLV, z. B. 'Anthrazit'")
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    color_source: ColorSource | None = Field(
        default=None, description="Herkunft von color_hex – 'assumed' = Annahme, nicht aus dem LV"
    )
    finish: str | None = Field(default=None, description="z. B. matt, geölt, poliert")
    format: str | None = Field(default=None, description="z. B. '60x60 cm'")
    room_types: list[RoomType] = Field(
        default_factory=list, description="Leer = gilt für alle Räume"
    )
    location: MaterialLocation = MaterialLocation.INTERIOR
    is_final_surface: bool = Field(
        default=True,
        description="Sichtbare Endoberfläche; False z. B. für Estrich/Unterboden unter späterem Belag",
    )
    blv_position: str | None = Field(default=None, description="Pos.-Nr., z. B. '02.03.0010'")
    source_excerpt: str | None = Field(
        default=None,
        max_length=2000,
        description="Originaltext aus dem BLV zur Nachvollziehbarkeit",
    )

    def applies_to(self, room_type: RoomType) -> bool:
        return not self.room_types or room_type in self.room_types

    @property
    def is_visible_inside(self) -> bool:
        """Relevant für das Innenraum-Modell: innen und sichtbare Endoberfläche."""
        return self.location is MaterialLocation.INTERIOR and self.is_final_surface


class EquipmentVariant(LumiraModel):
    """Ausstattungsvariante, z. B. 'Standard', 'Komfort' oder 'Premium'."""

    name: str = Field(min_length=1)
    description: str | None = None
    material_ids: list[str] = Field(default_factory=list)
    surcharge_eur: Decimal | None = Field(default=None, ge=0)
    is_default: bool = False


class BLVResult(LumiraModel):
    project_id: UUID
    source_key: str | None = Field(default=None, description="S3-Key des BLV-PDFs, None = kein BLV")
    materials: list[Material] = Field(default_factory=list)
    variants: list[EquipmentVariant] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    extracted_by: str | None = Field(default=None, description="LLM-Modell-ID oder 'stub'")

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        material_ids = [m.id for m in self.materials]
        duplicates = sorted(i for i, n in Counter(material_ids).items() if n > 1)
        if duplicates:
            raise ValueError(f"Doppelte Material-IDs: {duplicates}")

        known = set(material_ids)
        for variant in self.variants:
            unknown = sorted(set(variant.material_ids) - known)
            if unknown:
                raise ValueError(
                    f"Variante '{variant.name}' verweist auf unbekannte Materialien: {unknown}"
                )

        defaults = [v.name for v in self.variants if v.is_default]
        if len(defaults) > 1:
            raise ValueError(f"Mehr als eine Standardvariante: {defaults}")
        if self.variants and not defaults:
            self.variants[0].is_default = True
        return self

    @property
    def default_variant(self) -> EquipmentVariant | None:
        return next((v for v in self.variants if v.is_default), None)

    def materials_for(
        self, *, variant: str | None = None, room_type: RoomType | None = None
    ) -> list[Material]:
        """Materialien einer Variante (Standard: Default-Variante), optional gefiltert nach Raumtyp."""
        chosen = (
            next((v for v in self.variants if v.name == variant), None)
            if variant
            else self.default_variant
        )
        if variant and chosen is None:
            raise KeyError(variant)
        candidates = (
            [m for m in self.materials if m.id in set(chosen.material_ids)]
            if chosen
            else list(self.materials)
        )
        if room_type is not None:
            candidates = [m for m in candidates if m.applies_to(room_type)]
        return candidates
