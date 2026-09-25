"""Grundriss-Modelle: FloorPlan, Wall, Opening, Room.

Ein FloorPlan wird entlang der Pipeline schrittweise angereichert:
parser (Rohgeometrie) → recognizer (Wände, Öffnungen, Räume) → classifier (Raumtypen).
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, computed_field, model_validator

from lumira_shared.models.base import LumiraModel, new_id
from lumira_shared.models.geometry import Confidence, Point2D, Polygon, polygon_area_mm2

DEFAULT_WALL_HEIGHT_MM = 2500.0  # übliche lichte Raumhöhe im Wohnungsbau


class SourceFormat(StrEnum):
    PDF = "pdf"
    DWG = "dwg"
    DXF = "dxf"


class OpeningType(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    PASSAGE = "passage"  # Durchgang ohne Türblatt


class DoorSwing(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    SLIDING = "sliding"
    NONE = "none"


class RoomType(StrEnum):
    """Raumtypen. Das Mapping deutscher Planbeschriftungen liegt im classifier."""

    LIVING = "living"  # Wohnen
    DINING = "dining"  # Essen
    KITCHEN = "kitchen"  # Küche
    BEDROOM = "bedroom"  # Schlafen
    CHILD = "child"  # Kind
    OFFICE = "office"  # Arbeiten
    BATHROOM = "bathroom"  # Bad
    WC = "wc"  # WC / Gäste-WC
    HALLWAY = "hallway"  # Flur / Diele
    STORAGE = "storage"  # Abstellraum
    UTILITY = "utility"  # Hauswirtschaft / Technik
    BALCONY = "balcony"  # Balkon / Loggia / Terrasse
    STAIRCASE = "staircase"  # Treppenhaus
    UNKNOWN = "unknown"


class Wall(LumiraModel):
    id: str = Field(default_factory=lambda: new_id("wall"))
    start: Point2D
    end: Point2D
    thickness_mm: float = Field(gt=0, le=2000)
    height_mm: float = Field(default=DEFAULT_WALL_HEIGHT_MM, gt=0)
    footprint: Polygon | None = Field(
        default=None,
        description="Exakter Wandgrundriss (z. B. gefüllte CAD-Fläche mit Gehrung oder L-Form); "
        "ohne: Quader aus Achse und Dicke",
    )
    is_exterior: bool | None = None
    is_load_bearing: bool | None = None
    confidence: Confidence = 1.0

    @computed_field
    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)

    @model_validator(mode="after")
    def _non_degenerate(self) -> Self:
        if self.length_mm < 1.0:
            raise ValueError(f"Wand {self.id} hat (fast) Länge 0")
        return self


class Opening(LumiraModel):
    """Tür, Fenster oder Durchgang – immer einer Wand zugeordnet."""

    id: str = Field(default_factory=lambda: new_id("opening"))
    type: OpeningType
    wall_id: str
    offset_mm: float = Field(ge=0, description="Abstand Wandanfang → Öffnungsbeginn")
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    sill_height_mm: float = Field(default=0.0, ge=0, description="Brüstungshöhe, Türen: 0")
    swing: DoorSwing | None = Field(
        default=None, description="Anschlag: LEFT = Band am Wandanfang, RIGHT = am Wandende"
    )
    opens_to: Literal["left", "right"] | None = Field(
        default=None,
        description="Seite, in die das Türblatt aufschlägt – links/rechts der Wandrichtung "
        "(Anfang → Ende)",
    )
    confidence: Confidence = 1.0


class Room(LumiraModel):
    id: str = Field(default_factory=lambda: new_id("room"))
    polygon: Polygon
    label: str | None = Field(
        default=None, description="Rohtext aus dem Plan, z. B. 'Wohnen/Essen'"
    )
    room_type: RoomType = RoomType.UNKNOWN
    label_area_m2: float | None = Field(default=None, gt=0, description="Im Plan angegebene Fläche")
    floor_level: int = 0
    wall_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = 1.0

    @computed_field
    @property
    def area_m2(self) -> float:
        return round(polygon_area_mm2(self.polygon) / 1_000_000, 2)


class FloorPlan(LumiraModel):
    project_id: UUID
    source_key: str = Field(description="S3-Key der Originaldatei")
    source_format: SourceFormat
    page: int | None = Field(default=None, ge=0, description="PDF-Seite, 0-basiert")
    scale_mm_per_unit: float = Field(
        default=1.0, gt=0, description="Umrechnung Quelleinheit (pt, px, DXF-Einheit) → mm"
    )
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        all_ids = (
            [w.id for w in self.walls] + [o.id for o in self.openings] + [r.id for r in self.rooms]
        )
        duplicates = sorted(i for i, n in Counter(all_ids).items() if n > 1)
        if duplicates:
            raise ValueError(f"Doppelte IDs im Grundriss: {duplicates}")

        wall_ids = {w.id for w in self.walls}
        dangling = sorted(
            {o.wall_id for o in self.openings} - wall_ids
            | {wid for r in self.rooms for wid in r.wall_ids} - wall_ids
        )
        if dangling:
            raise ValueError(f"Verweise auf unbekannte Wände: {dangling}")
        return self

    def wall(self, wall_id: str) -> Wall:
        for wall in self.walls:
            if wall.id == wall_id:
                return wall
        raise KeyError(wall_id)

    def openings_in(self, wall_id: str) -> list[Opening]:
        return [o for o in self.openings if o.wall_id == wall_id]

    @property
    def total_area_m2(self) -> float:
        return round(sum(r.area_m2 for r in self.rooms), 2)
