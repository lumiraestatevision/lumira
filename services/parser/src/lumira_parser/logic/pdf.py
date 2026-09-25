"""PDF-Grundriss einlesen: Vektorgeometrie mit Zeichenattributen und Texte (pdfplumber),
Seitenbild (pypdfium2).

Beide Bibliotheken sind permissiv lizenziert (MIT bzw. Apache-2.0/BSD-3). PyMuPDF wird
bewusst nicht verwendet: AGPL-3.0 wäre für eine kommerzielle SaaS-Plattform problematisch.

Gelesen wird, was CAD-Exporte mitliefern und der recognizer braucht:
- Linien, Rechtecke, Kurven als Segmente – mit Strichstärke
- gefüllte Flächen mit Füllfarbe (Wände sind oft grau gefüllt, Räume farbig hinterlegt)
- gezeichnete Kurven als Linienzüge (Türaufschläge)
- Texte als zusammenhängende Beschriftungen, aus Einzelzeichen gebildet – pdfplumbers
  Wortbildung zerlegt CAD-Texte mit eng gesetzten Zeichen sonst in Einzelbuchstaben
- Maßstab aus dem Schriftfeld („Maßstab 1:100“), sonst ``pdf_assumed_scale``

Nicht umgesetzt: nur Seite 1; gedrehte Texte (meist Maßzahlen) werden übersprungen;
gescannte PDFs (reine Rasterbilder) liefern keine Geometrie – dafür ist OCR im recognizer da.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from pydantic import ValidationError

from lumira_parser.logic.types import RawPlan, dedupe_segments
from lumira_shared import NonRetryableError
from lumira_shared.models import FilledArea, Point2D, Segment, Stroke, TextItem, polygon_area_mm2

PT_TO_MM = 25.4 / 72.0
# Übliche Planmaßstäbe – schützt vor Fehltreffern wie "1:3" in Beschreibungstexten.
PLAN_SCALES = (20, 25, 50, 75, 100, 125, 200, 250, 500)
_SCALE_WITH_WORD = re.compile(r"(?:ma(?:ß|ss)stab|\bm)\s*[:.]?\s*1\s*:\s*(\d{1,4})\b", re.I)
_SCALE_ALONE = re.compile(r"^\s*1\s*:\s*(\d{1,4})\s*$")


def open_pdf(data: bytes) -> pdfium.PdfDocument:
    """Öffnet das PDF und übersetzt typische Defekte in nicht wiederholbare Fehler."""
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise NonRetryableError("PDF ist passwortgeschützt") from exc
        raise NonRetryableError(f"PDF nicht lesbar: {exc}") from exc
    if len(doc) == 0:
        doc.close()
        raise NonRetryableError("PDF enthält keine Seiten")
    return doc


def _render_png(doc: pdfium.PdfDocument, dpi: int) -> bytes:
    # scale = Pixel pro PDF-Punkt; laut pypdfium2-Doku float, die Typangabe sagt fälschlich int.
    image = doc[0].render(scale=dpi / 72).to_pil()  # pyright: ignore[reportArgumentType]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ------------------------------------------------------------------ Texte
@dataclass(slots=True)
class Phrase:
    """Zusammenhängender Text in PDF-Punkten (y von oben, wie pdfplumber)."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y(self) -> float:
        return (self.top + self.bottom) / 2


def _phrase(chars: list[dict[str, Any]]) -> Phrase | None:
    text = " ".join("".join(str(c["text"]) for c in chars).split())
    if not text:
        return None
    return Phrase(
        text,
        min(float(c["x0"]) for c in chars),
        max(float(c["x1"]) for c in chars),
        min(float(c["top"]) for c in chars),
        max(float(c["bottom"]) for c in chars),
        max(float(c["size"]) for c in chars),
    )


def _line_to_phrases(chars: list[dict[str, Any]]) -> list[Phrase]:
    chars = sorted(chars, key=lambda c: float(c["x0"]))
    groups: list[list[dict[str, Any]]] = []
    for char in chars:
        if groups:
            last = groups[-1][-1]
            gap = float(char["x0"]) - float(last["x1"])
            if gap <= 0.8 * max(float(char["size"]), float(last["size"])):
                groups[-1].append(char)
                continue
        groups.append([char])  # deutlich mehr als ein Leerzeichen → neue Beschriftung
    return [p for group in groups if (p := _phrase(group)) is not None]


def _attach_superscripts(phrases: list[Phrase]) -> list[Phrase]:
    """Hochgestellte Zeichen (m², 36⁵) liegen auf eigener Grundlinie → an den Text davor hängen."""
    result = sorted(phrases, key=lambda p: -p.size)
    merged: set[int] = set()
    for i, small in enumerate(result):
        for big in result:
            if (
                big is not small
                and id(big) not in merged
                and small.size < 0.8 * big.size
                and -0.3 * big.size <= small.x0 - big.x1 <= 0.3 * big.size
                and small.bottom > big.top
                and small.top < big.bottom
            ):
                big.text += small.text
                big.x1 = max(big.x1, small.x1)
                merged.add(id(result[i]))
                break
    return [p for p in result if id(p) not in merged]


def extract_phrases(page: Any) -> list[Phrase]:
    """Zeichen → Zeilen (gleiche Grundlinie) → Beschriftungen, Hochstellungen angehängt."""
    chars = [c for c in page.chars if c.get("upright", True) and str(c.get("text", ""))]
    chars.sort(key=lambda c: float(c["bottom"]))
    lines: list[list[dict[str, Any]]] = []
    baseline = 0.0
    for char in chars:
        bottom, size = float(char["bottom"]), float(char["size"])
        if lines and abs(bottom - baseline) <= 0.2 * size:
            lines[-1].append(char)
        else:
            lines.append([char])
            baseline = bottom
    return _attach_superscripts([p for line in lines for p in _line_to_phrases(line)])


def detect_scale(phrases: list[Phrase]) -> int | None:
    """Maßstab aus dem Schriftfeld: „Maßstab 1:100“, „M 1:50“ oder allein stehend „1:100“."""
    with_word = {int(m.group(1)) for p in phrases if (m := _SCALE_WITH_WORD.search(p.text))}
    alone = {int(m.group(1)) for p in phrases if (m := _SCALE_ALONE.match(p.text))}
    for found in (with_word, alone):
        candidates = found & set(PLAN_SCALES)
        if len(candidates) == 1:
            return candidates.pop()
    return None


# ------------------------------------------------------------------ Geometrie
def _color_hex(value: Any) -> str | None:
    """pdfplumber-Farbe (Grau, RGB oder CMYK, Werte 0..1) → #RRGGBB."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    try:
        channels = [float(v) for v in value]
    except (TypeError, ValueError):
        return None  # Muster/Pattern
    match channels:
        case [g]:
            rgb = (g, g, g)
        case [r, g, b]:
            rgb = (r, g, b)
        case [c, m, y, k]:
            rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
        case _:
            return None
    return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in rgb)


class _Converter:
    """PDF-Punkte (y von oben) → Millimeter im Planmaßstab (y von unten)."""

    def __init__(self, page_height: float, mm_per_pt: float) -> None:
        self.height = page_height
        self.mm_per_pt = mm_per_pt

    def point(self, x: float, top: float) -> Point2D:
        return Point2D(x=float(x) * self.mm_per_pt, y=(self.height - float(top)) * self.mm_per_pt)

    def points(self, obj: dict[str, Any]) -> list[Point2D]:
        if obj.get("object_type") == "rect" or not obj.get("pts"):
            x0, x1, top, bottom = obj["x0"], obj["x1"], obj["top"], obj["bottom"]
            raw = [(x0, top), (x1, top), (x1, bottom), (x0, bottom)]
        else:
            raw = obj["pts"]
        return [self.point(x, top) for x, top in raw]


def _line_width(obj: dict[str, Any]) -> float | None:
    width = obj.get("linewidth")
    return round(float(width) * PT_TO_MM, 3) if width is not None else None


def _segments(page: Any, conv: _Converter) -> list[Segment]:
    segments: list[Segment] = []
    for obj in [*page.lines, *page.curves]:
        points = [conv.point(x, top) for x, top in obj.get("pts") or []]
        width = _line_width(obj)
        segments.extend(
            Segment(start=a, end=b, line_width_mm=width) for a, b in pairwise(points) if a != b
        )
    for rect in page.rects:
        corners = conv.points(rect)
        width = _line_width(rect)
        segments.extend(
            Segment(start=a, end=b, line_width_mm=width)
            for a, b in pairwise([*corners, corners[0]])
            if a != b
        )
    return dedupe_segments(segments)


def _filled_areas(page: Any, conv: _Converter) -> list[FilledArea]:
    areas: list[FilledArea] = []
    for obj in [*page.rects, *page.curves]:
        if not obj.get("fill"):
            continue
        try:
            area = FilledArea(
                polygon=conv.points(obj), color=_color_hex(obj.get("non_stroking_color"))
            )
        except ValidationError:
            continue  # entartet (weniger als 3 verschiedene Punkte)
        if polygon_area_mm2(area.polygon) >= 1.0:
            areas.append(area)
    return areas


def _curves(page: Any, conv: _Converter) -> list[Stroke]:
    return [
        Stroke(points=conv.points(obj), line_width_mm=_line_width(obj))
        for obj in page.curves
        if obj.get("stroke") and len(obj.get("pts") or []) >= 2
    ]


def _texts(phrases: list[Phrase], conv: _Converter) -> list[TextItem]:
    return [
        TextItem(
            text=p.text,
            position=conv.point(p.x, p.y),
            height_mm=p.size * conv.mm_per_pt or None,
        )
        for p in phrases
    ]


def parse_pdf(data: bytes, *, assumed_scale: float = 100.0, render_dpi: int = 150) -> RawPlan:
    doc = open_pdf(data)
    try:
        page_count = len(doc)
        width_pt, height_pt = doc[0].get_size()
        png = _render_png(doc, render_dpi)
    finally:
        doc.close()

    notes: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first = pdf.pages[0]
        phrases = extract_phrases(first)
        scale = detect_scale(phrases)
        if scale is not None:
            notes.append(f"Maßstab 1:{scale} aus der Planbeschriftung")
        else:
            scale = assumed_scale
            notes.append(f"Maßstab 1:{assumed_scale:g} angenommen (keine Angabe im Plan gefunden)")
        conv = _Converter(float(first.height), PT_TO_MM * scale)
        segments = _segments(first, conv)
        filled = _filled_areas(first, conv)
        curves = _curves(first, conv)
        texts = _texts(phrases, conv)

    if page_count > 1:
        notes.append(f"Nur Seite 1 von {page_count} ausgewertet")
    if not segments and not filled:
        notes.append("Keine Vektorgeometrie gefunden – vermutlich gescannter Plan")

    return RawPlan(
        width_mm=width_pt * conv.mm_per_pt,
        height_mm=height_pt * conv.mm_per_pt,
        plan_scale=float(scale),
        segments=segments,
        filled_areas=filled,
        curves=curves,
        texts=texts,
        page_count=page_count,
        page_png=png,
        notes=notes,
    )
