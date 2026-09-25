"""Maßstab prüfen: Flächenangaben im Plan („F: 14,58 m²“) gegen die erkannte Raumgeometrie.

Stimmen die Verhältnisse mehrerer Räume überein, aber nicht mit 1, war der angenommene
Maßstab falsch (z. B. 1:50 statt 1:100 → Flächen um Faktor 4 daneben). Dann wird auf den
nächsten üblichen Planmaßstab korrigiert.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

from lumira_shared.models import FloorPlan, ParsedPlan, Point2D

PLAN_SCALES = (20, 25, 50, 75, 100, 125, 200, 250, 500)
_AREA = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:m²|m2|qm)", re.IGNORECASE)
_TOLERANCE = 0.07  # bis 7 % Längenabweichung gilt der Maßstab als bestätigt
_MAX_SPREAD = 0.15  # Räume müssen sich einig sein, sonst keine Korrektur


@dataclass(slots=True)
class ScaleCheck:
    factor: float | None  # Längenfaktor, mit dem die Geometrie zu korrigieren ist
    note: str


def label_area(label: str | None) -> float | None:
    match = _AREA.search(label or "")
    return float(match.group(1).replace(",", ".")) if match else None


def check_scale(plan: FloorPlan, current_scale: float | None) -> ScaleCheck:
    ratios = [
        area / room.area_m2
        for room in plan.rooms
        if room.area_m2 >= 1.0 and (area := label_area(room.label))
    ]
    if len(ratios) < 2:
        return ScaleCheck(None, "Maßstab nicht prüfbar (zu wenige Flächenangaben im Plan)")
    ratio = statistics.median(ratios)
    factor = math.sqrt(ratio)
    if abs(factor - 1) <= _TOLERANCE:
        return ScaleCheck(
            None,
            f"Maßstab bestätigt: {len(ratios)} Raumflächen weichen im Mittel "
            f"{abs(ratio - 1) * 100:.1f} % von den Planangaben ab",
        )
    if max(abs(r / ratio - 1) for r in ratios) > _MAX_SPREAD or current_scale is None:
        return ScaleCheck(None, "Flächenangaben widersprechen sich – Maßstab nicht korrigiert")
    target = current_scale * factor
    snapped = min(PLAN_SCALES, key=lambda s: abs(s - target))
    if abs(snapped / target - 1) > 0.05:
        return ScaleCheck(None, f"Maßstab unklar (rechnerisch 1:{target:.0f}) – nicht korrigiert")
    return ScaleCheck(
        snapped / current_scale,
        f"Maßstab korrigiert: 1:{current_scale:g} → 1:{snapped} (laut Raumflächen im Plan)",
    )


def rescale(parsed: ParsedPlan, factor: float) -> ParsedPlan:
    """Alle Koordinaten mit ``factor`` strecken (Ursprung bleibt)."""

    def p(point: Point2D) -> Point2D:
        return Point2D(x=point.x * factor, y=point.y * factor)

    return parsed.model_copy(
        update={
            "width_mm": parsed.width_mm * factor,
            "height_mm": parsed.height_mm * factor,
            "plan_scale": parsed.plan_scale * factor if parsed.plan_scale else None,
            "segments": [
                s.model_copy(update={"start": p(s.start), "end": p(s.end)}) for s in parsed.segments
            ],
            "filled_areas": [
                a.model_copy(update={"polygon": [p(q) for q in a.polygon]})
                for a in parsed.filled_areas
            ],
            "curves": [
                c.model_copy(update={"points": [p(q) for q in c.points]}) for c in parsed.curves
            ],
            "texts": [t.model_copy(update={"position": p(t.position)}) for t in parsed.texts],
        }
    )
