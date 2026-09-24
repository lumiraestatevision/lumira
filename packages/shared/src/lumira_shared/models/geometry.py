"""Geometrie-Grundtypen.

Konvention im gesamten Projekt: Längen in Millimetern, Flächen in Quadratmetern,
Koordinatensystem rechtshändig in der Draufsicht (x nach rechts, y nach oben).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator, ConfigDict, Field

from lumira_shared.models.base import LumiraModel

Confidence = Annotated[float, Field(ge=0.0, le=1.0, description="Erkennungssicherheit 0..1")]


class Point2D(LumiraModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float

    def distance_to(self, other: Point2D) -> float:
        return math.hypot(other.x - self.x, other.y - self.y)


def _normalize_polygon(points: list[Point2D]) -> list[Point2D]:
    # Geschlossene Darstellung (erster == letzter Punkt) auf offene normalisieren.
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise ValueError("Ein Polygon braucht mindestens 3 unterschiedliche Punkte")
    return points


Polygon = Annotated[list[Point2D], AfterValidator(_normalize_polygon)]


def polygon_area_mm2(points: Sequence[Point2D]) -> float:
    """Fläche nach der Gaußschen Trapezformel (Shoelace), unabhängig vom Umlaufsinn."""
    twice_area = sum(
        p.x * q.y - q.x * p.y for p, q in zip(points, [*points[1:], points[0]], strict=True)
    )
    return abs(twice_area) / 2.0
