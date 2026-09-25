"""Rohgeometrie, wie der parser sie aus DXF/PDF liest – Eingabe des recognizers.

Alle Koordinaten sind bereits in Millimeter umgerechnet, der Ursprung liegt unten links.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from lumira_shared.models.base import LumiraModel
from lumira_shared.models.floorplan import SourceFormat
from lumira_shared.models.geometry import Point2D, Polygon


def _line_width() -> Any:
    return Field(
        default=None, ge=0, description="Strichstärke auf dem Papier in mm (nicht maßstabsbezogen)"
    )


class Segment(LumiraModel):
    start: Point2D
    end: Point2D
    layer: str | None = None
    line_width_mm: float | None = _line_width()

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


class FilledArea(LumiraModel):
    """Gefüllte Fläche aus CAD-Exporten – typisch: Wände grau, Räume farbig hinterlegt."""

    polygon: Polygon
    color: str | None = Field(default=None, description="Füllfarbe #RRGGBB")


class Stroke(LumiraModel):
    """Gezeichneter Linienzug aus einem Stück, z. B. ein Türaufschlag-Bogen."""

    points: list[Point2D] = Field(min_length=2)
    line_width_mm: float | None = _line_width()


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
    plan_scale: float | None = Field(
        default=None, gt=0, description="Maßstab 1:N, mit dem die Koordinaten umgerechnet wurden"
    )
    segments: list[Segment] = Field(default_factory=list)
    filled_areas: list[FilledArea] = Field(default_factory=list)
    curves: list[Stroke] = Field(default_factory=list)
    texts: list[TextItem] = Field(default_factory=list)
    page_image_key: str | None = Field(default=None, description="Gerendertes Seitenbild (PNG)")
    notes: list[str] = Field(default_factory=list)
