"""Rohgeometrie, wie der parser sie aus DXF/PDF liest – Eingabe des recognizers.

Alle Koordinaten sind bereits in Millimeter umgerechnet, der Ursprung liegt unten links.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from lumira_shared.models.base import LumiraModel
from lumira_shared.models.floorplan import SourceFormat
from lumira_shared.models.geometry import Point2D


class Segment(LumiraModel):
    start: Point2D
    end: Point2D
    layer: str | None = None

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


class TextItem(LumiraModel):
    text: str = Field(min_length=1)
    position: Point2D
    height_mm: float | None = Field(default=None, gt=0)


class ParsedPlan(LumiraModel):
    project_id: UUID
    source_key: str
    source_format: SourceFormat
    page: int | None = Field(default=None, ge=0)
    page_count: int = Field(default=1, ge=1)
    width_mm: float = Field(ge=0)
    height_mm: float = Field(ge=0)
    segments: list[Segment] = Field(default_factory=list)
    texts: list[TextItem] = Field(default_factory=list)
    page_image_key: str | None = Field(default=None, description="Gerendertes Seitenbild (PNG)")
    notes: list[str] = Field(default_factory=list)
