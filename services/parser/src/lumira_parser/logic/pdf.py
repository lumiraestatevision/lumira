"""PDF-Grundriss einlesen: Vektorlinien und Textzeilen (pdfplumber), Seitenbild (pypdfium2).

Beide Bibliotheken sind permissiv lizenziert (MIT bzw. Apache-2.0/BSD-3). PyMuPDF wird
bewusst nicht verwendet: AGPL-3.0 wäre für eine kommerzielle SaaS-Plattform problematisch.

STUB-Anteile:
- Maßstab: fest 1:N (``pdf_assumed_scale``) statt Erkennung aus Maßketten/Maßstabsleiste.
- Nur die erste Seite wird ausgewertet.
- Gescannte PDFs (reine Rasterbilder) liefern keine Segmente – dafür ist der
  recognizer mit Bildverarbeitung zuständig.
"""

from __future__ import annotations

import io
from itertools import pairwise
from typing import Any

import pdfplumber
import pypdfium2 as pdfium

from lumira_parser.logic.types import RawPlan, dedupe_segments
from lumira_shared import NonRetryableError
from lumira_shared.models import Point2D, Segment, TextItem

PT_TO_MM = 25.4 / 72.0


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


def _segments(page: Any, mm_per_pt: float) -> list[Segment]:
    height = float(page.height)

    def point(x: float, top: float) -> Point2D:
        # pdfplumber: y wird von oben gemessen → Lumira: Ursprung unten links.
        return Point2D(x=float(x) * mm_per_pt, y=(height - float(top)) * mm_per_pt)

    segments: list[Segment] = []
    for obj in [*page.lines, *page.curves]:
        points = [point(x, top) for x, top in obj.get("pts") or []]
        segments.extend(Segment(start=a, end=b) for a, b in pairwise(points) if a != b)
    for rect in page.rects:
        x0, x1, top, bottom = rect["x0"], rect["x1"], rect["top"], rect["bottom"]
        corners = [point(x0, top), point(x1, top), point(x1, bottom), point(x0, bottom)]
        segments.extend(
            Segment(start=a, end=b) for a, b in pairwise([*corners, corners[0]]) if a != b
        )
    return dedupe_segments(segments)


def _texts(page: Any, mm_per_pt: float) -> list[TextItem]:
    height = float(page.height)
    texts: list[TextItem] = []
    for line in page.extract_text_lines(return_chars=False):
        content = " ".join(str(line["text"]).split())
        if not content:
            continue
        x = (float(line["x0"]) + float(line["x1"])) / 2
        y = (float(line["top"]) + float(line["bottom"])) / 2
        texts.append(
            TextItem(
                text=content,
                position=Point2D(x=x * mm_per_pt, y=(height - y) * mm_per_pt),
                height_mm=(float(line["bottom"]) - float(line["top"])) * mm_per_pt or None,
            )
        )
    return texts


def parse_pdf(data: bytes, *, assumed_scale: float = 100.0, render_dpi: int = 150) -> RawPlan:
    mm_per_pt = PT_TO_MM * assumed_scale
    doc = open_pdf(data)
    try:
        page_count = len(doc)
        width_pt, height_pt = doc[0].get_size()
        png = _render_png(doc, render_dpi)
    finally:
        doc.close()

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first = pdf.pages[0]
        segments = _segments(first, mm_per_pt)
        texts = _texts(first, mm_per_pt)

    notes = [f"Maßstab 1:{assumed_scale:g} angenommen (STUB)"]
    if page_count > 1:
        notes.append(f"Nur Seite 1 von {page_count} ausgewertet")
    if not segments:
        notes.append("Keine Vektorlinien gefunden – vermutlich gescannter Plan")

    return RawPlan(
        width_mm=width_pt * mm_per_pt,
        height_mm=height_pt * mm_per_pt,
        segments=segments,
        texts=texts,
        page_count=page_count,
        page_png=png,
        notes=notes,
    )
