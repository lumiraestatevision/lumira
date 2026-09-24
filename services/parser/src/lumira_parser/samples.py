"""Beispiel-Grundriss (kleine Wohnung) als DXF und PDF – für Tests, Demos und den
End-to-End-Integrationstest. Wird zur Laufzeit erzeugt, damit keine Binärdateien im Repo liegen.

Grundriss 10 m x 8 m, Innenwände teilen in: Wohnen/Essen, Schlafen, Bad, Flur.
"""

from __future__ import annotations

import io

from ezdxf import filemanagement, units

from lumira_shared.testing import PdfPage, simple_pdf

# (x1, y1, x2, y2) in mm
WALLS_MM: list[tuple[float, float, float, float]] = [
    (0, 0, 10_000, 0),
    (10_000, 0, 10_000, 8_000),
    (10_000, 8_000, 0, 8_000),
    (0, 8_000, 0, 0),
    (6_000, 0, 6_000, 8_000),  # trennt Wohnen/Essen von Schlafen/Bad
    (6_000, 4_500, 10_000, 4_500),  # trennt Schlafen und Bad
    (0, 2_000, 6_000, 2_000),  # trennt Flur ab
]

# (Text, x, y) in mm
LABELS_MM: list[tuple[str, float, float]] = [
    ("Wohnen/Essen 36,00 m²", 3_000, 5_000),
    ("Schlafen 14,00 m²", 8_000, 6_300),
    ("Bad 18,00 m²", 8_000, 2_200),
    ("Flur 12,00 m²", 3_000, 1_000),
]


def sample_dxf(*, unit: str = "mm") -> bytes:
    """DXF in Millimetern (Standard) oder Metern (``unit="m"``) zum Testen der Umrechnung."""
    factor = {"mm": 1.0, "m": 0.001}[unit]
    doc = filemanagement.new("R2018", setup=True)
    doc.units = units.MM if unit == "mm" else units.M
    msp = doc.modelspace()
    for x1, y1, x2, y2 in WALLS_MM:
        msp.add_line(
            (x1 * factor, y1 * factor), (x2 * factor, y2 * factor), dxfattribs={"layer": "WAENDE"}
        )
    for text, x, y in LABELS_MM:
        msp.add_text(text, height=250 * factor, dxfattribs={"layer": "RAUMSTEMPEL"}).set_placement(
            (x * factor, y * factor)
        )
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def sample_pdf(*, scale: float = 100.0) -> bytes:
    """Vektor-PDF (A4 quer) im Maßstab 1:``scale``."""
    pt_per_mm = 72.0 / 25.4 / scale
    margin = 60.0

    def to_pdf(x: float, y: float) -> tuple[float, float]:
        return margin + x * pt_per_mm, margin + y * pt_per_mm

    page = PdfPage(
        lines=[(*to_pdf(x1, y1), *to_pdf(x2, y2)) for x1, y1, x2, y2 in WALLS_MM],
        texts=[(*to_pdf(x - 1_200, y), text, 8) for text, x, y in LABELS_MM],
    )
    return simple_pdf([page])
