"""Beispiel-Grundriss (kleine Wohnung) als DXF und PDF – für Tests, Demos und den
End-to-End-Integrationstest. Wird zur Laufzeit erzeugt, damit keine Binärdateien im Repo liegen.

Grundriss 10 m x 8 m, Innenwände teilen in: Wohnen/Essen, Schlafen, Bad, Flur.
Dazu ``sample_cad_pdf``: ein kleiner Plan im Stil echter CAD-Exporte (gefüllte Wände).
"""

from __future__ import annotations

import io
import math

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


# ------------------------------------------------------------------ CAD-Beispiel
# Wie ein Archicad-/Allplan-Export: Wände grau gefüllt, Räume farbig hinterlegt, Türbogen.
# Innenmaße: Wohnen 5,00 x 6,00 m = 30,00 m², Bad 3,00 x 6,00 m = 18,00 m².
# Außenwand 300 mm, Innenwand 115 mm. Öffnungen: Fenster 1,50 m unten (Wohnen),
# Fenster 1,00 m rechts (Bad), Tür 0,90 m in der Innenwand mit Aufschlag ins Wohnen.
CAD_WALL_RECTS_MM: list[tuple[float, float, float, float]] = [  # x0, y0, x1, y1
    (0, 0, 1_500, 300),  # unten, links vom Fenster
    (3_000, 0, 8_715, 300),  # unten, rechts vom Fenster
    (0, 6_300, 8_715, 6_600),  # oben
    (0, 300, 300, 6_300),  # links
    (8_415, 300, 8_715, 2_500),  # rechts, unter dem Fenster
    (8_415, 3_500, 8_715, 6_300),  # rechts, über dem Fenster
    (5_300, 300, 5_415, 1_000),  # Innenwand, unter der Tür
    (5_300, 1_900, 5_415, 6_300),  # Innenwand, über der Tür
]
CAD_ROOMS_MM: list[tuple[str, str, tuple[float, float, float, float]]] = [
    ("Wohnen", "F: 30,00 m²", (300, 300, 5_300, 6_300)),
    ("Bad", "F: 18,00 m²", (5_415, 300, 8_415, 6_300)),
]
CAD_DOOR_HINGE_MM = (5_300.0, 1_000.0)  # Tür 900 mm, schlägt ins Wohnen auf
WALL_GREY = (0.5, 0.5, 0.5)
ROOM_COLORS = [(1.0, 1.0, 0.66), (0.84, 1.0, 0.66)]


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def sample_cad_pdf(*, scale: int = 100, scale_text: bool = True) -> bytes:
    """CAD-typischer Grundriss (A4 quer) im Maßstab 1:``scale``, optional mit „Maßstab 1:N“."""
    pt_per_mm = 72.0 / 25.4 / scale
    origin = (100.0, 120.0)

    def pt(x: float, y: float) -> tuple[float, float]:
        return origin[0] + x * pt_per_mm, origin[1] + y * pt_per_mm

    def poly(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [pt(x, y) for x, y in points]

    fills = [
        (poly(_rect(*r)), color) for (_, _, r), color in zip(CAD_ROOMS_MM, ROOM_COLORS, strict=True)
    ]
    fills += [(poly(_rect(*r)), WALL_GREY) for r in CAD_WALL_RECTS_MM]
    hx, hy = CAD_DOOR_HINGE_MM
    arc = [
        (hx - 900 * math.sin(a), hy + 900 * math.cos(a))
        for a in (i * math.pi / 2 / 8 for i in range(9))
    ]  # von (hx, hy+900) = geschlossen bis (hx-900, hy) = offen
    texts = []
    for name, area, (x0, y0, x1, y1) in CAD_ROOMS_MM:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        texts += [(*pt(cx - 400, cy + 200), name, 10), (*pt(cx - 600, cy - 300), area, 8)]
    texts.append((*pt(0, -1_500), "8,72", 7))  # Maßkette außerhalb des Gebäudes
    if scale_text:
        texts.append((*pt(11_000, -1_500), f"Maßstab 1:{scale}", 8))
    page = PdfPage(
        lines=[(*pt(0, -1_000), *pt(8_715, -1_000))],  # Maßlinie
        texts=texts,
        fills=fills,
        polylines=[(poly(arc), 0.25), (poly([(hx, hy), (hx - 900, hy)]), 0.25)],
        line_width=0.25,
    )
    return simple_pdf([page])


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
