"""DXF einlesen: Linien, Polylinien und Texte aus dem Modelbereich.

DWG wird (noch) nicht direkt gelesen – siehe handler.py.
"""

from __future__ import annotations

import io
from itertools import pairwise

from ezdxf import recover, units
from ezdxf.document import Drawing
from ezdxf.entities.line import Line
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.mtext import MText
from ezdxf.entities.text import Text
from ezdxf.enums import InsertUnits

from lumira_parser.logic.types import RawPlan, dedupe_segments
from lumira_shared import NonRetryableError
from lumira_shared.models import Point2D, Segment, TextItem


def _mm_factor(doc: Drawing, notes: list[str]) -> float:
    source = InsertUnits(doc.units)
    if source is InsertUnits.Unitless:
        notes.append("DXF ohne Einheit ($INSUNITS=0) – Millimeter angenommen")
        return 1.0
    return units.conversion_factor(source, InsertUnits.Millimeters)


def parse_dxf(data: bytes) -> RawPlan:
    try:
        doc, auditor = recover.read(io.BytesIO(data))
    except Exception as exc:  # ezdxf wirft je nach Defekt unterschiedliche Fehler
        raise NonRetryableError(f"DXF nicht lesbar: {exc}") from exc

    notes: list[str] = []
    if auditor.has_errors:
        notes.append(f"DXF repariert: {len(auditor.errors)} Fehler")
    factor = _mm_factor(doc, notes)

    def point(x: float, y: float) -> Point2D:
        return Point2D(x=x * factor, y=y * factor)

    segments: list[Segment] = []
    texts: list[TextItem] = []

    for entity in doc.modelspace():
        if isinstance(entity, Line):
            start, end = entity.dxf.start, entity.dxf.end
            if not start.isclose(end):
                segments.append(
                    Segment(
                        start=point(start.x, start.y),
                        end=point(end.x, end.y),
                        layer=entity.dxf.layer,
                    )
                )
        elif isinstance(entity, LWPolyline):
            vertices = [point(x, y) for x, y in entity.get_points("xy")]
            if entity.closed and len(vertices) > 2:
                vertices.append(vertices[0])
            segments.extend(
                Segment(start=a, end=b, layer=entity.dxf.layer)
                for a, b in pairwise(vertices)
                if a != b
            )
        elif isinstance(entity, (Text, MText)):
            if isinstance(entity, MText):
                raw, text_height = entity.plain_text(), entity.dxf.get("char_height", 0)
            else:
                raw, text_height = entity.dxf.text, entity.dxf.get("height", 0)
            content = " ".join(str(raw).split())
            if content:
                insert = entity.dxf.insert
                height = float(text_height or 0) * factor
                texts.append(
                    TextItem(
                        text=content, position=point(insert.x, insert.y), height_mm=height or None
                    )
                )

    segments = dedupe_segments(segments)
    xs = [p.x for s in segments for p in (s.start, s.end)]
    ys = [p.y for s in segments for p in (s.start, s.end)]
    width = (max(xs) - min(xs)) if xs else 0.0
    height = (max(ys) - min(ys)) if ys else 0.0
    return RawPlan(width_mm=width, height_mm=height, segments=segments, texts=texts, notes=notes)
