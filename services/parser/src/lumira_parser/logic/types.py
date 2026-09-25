from __future__ import annotations

from dataclasses import dataclass, field

from lumira_shared.models import FilledArea, Segment, Stroke, TextItem


def dedupe_segments(segments: list[Segment], tolerance_mm: float = 1.0) -> list[Segment]:
    """Entfernt doppelte Segmente (auch in Gegenrichtung) – häufig in CAD-/PDF-Exporten."""
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    unique: list[Segment] = []
    for segment in segments:
        a = (round(segment.start.x / tolerance_mm), round(segment.start.y / tolerance_mm))
        b = (round(segment.end.x / tolerance_mm), round(segment.end.y / tolerance_mm))
        key = (a, b) if a <= b else (b, a)
        if key not in seen:
            seen.add(key)
            unique.append(segment)
    return unique


@dataclass(slots=True)
class RawPlan:
    """Ergebnis eines Parsers – noch ohne Projektbezug, alle Werte in mm."""

    width_mm: float
    height_mm: float
    plan_scale: float | None = None
    segments: list[Segment] = field(default_factory=list)
    filled_areas: list[FilledArea] = field(default_factory=list)
    curves: list[Stroke] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    page_count: int = 1
    page_png: bytes | None = None
    notes: list[str] = field(default_factory=list)
