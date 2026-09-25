"""CAD-typische PDFs: gefüllte Flächen, Kurven, Strichstärken, Beschriftungen, Maßstab."""

from __future__ import annotations

from collections import Counter

import pytest

from lumira_parser.logic import parse_pdf
from lumira_parser.logic.pdf import Phrase, _color_hex, detect_scale
from lumira_parser.samples import CAD_WALL_RECTS_MM, sample_cad_pdf
from lumira_shared.models import polygon_area_mm2
from lumira_shared.testing import PdfPage, simple_pdf


def test_filled_areas_keep_color_and_exact_geometry() -> None:
    raw = parse_pdf(sample_cad_pdf())

    colors = Counter(a.color for a in raw.filled_areas)
    assert colors == {"#808080": len(CAD_WALL_RECTS_MM), "#FFFFA8": 1, "#D6FFA8": 1}
    walls = sorted(polygon_area_mm2(a.polygon) for a in raw.filled_areas if a.color == "#808080")
    expected = sorted((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in CAD_WALL_RECTS_MM)
    assert walls == pytest.approx(expected, rel=1e-3)


def test_curves_and_line_widths_are_kept() -> None:
    raw = parse_pdf(sample_cad_pdf())

    # Türbogen + Türblatt (Linienzug aus einem Stück) – als Kurven mit Strichstärke 0,25 pt
    assert len(raw.curves) >= 1
    assert raw.curves[0].line_width_mm == pytest.approx(0.25 * 25.4 / 72, abs=0.001)
    widths = {s.line_width_mm for s in raw.segments}
    assert any(w is not None and w == pytest.approx(0.088, abs=0.001) for w in widths)


def test_labels_and_scale_come_from_the_plan() -> None:
    raw = parse_pdf(sample_cad_pdf())

    texts = [t.text for t in raw.texts]
    assert {"Wohnen", "F: 30,00 m²", "Bad", "F: 18,00 m²", "Maßstab 1:100"} <= set(texts)
    assert raw.plan_scale == 100
    assert raw.notes[0] == "Maßstab 1:100 aus der Planbeschriftung"


def test_scale_text_1_to_50_changes_geometry() -> None:
    at_100 = parse_pdf(sample_cad_pdf(scale=100))
    at_50 = parse_pdf(sample_cad_pdf(scale=50))

    assert at_50.plan_scale == 50
    area = lambda raw: max(polygon_area_mm2(a.polygon) for a in raw.filled_areas)  # noqa: E731
    assert area(at_50) == pytest.approx(area(at_100), rel=1e-3)  # gleiche Wirklichkeit


def test_without_scale_text_the_assumed_scale_is_used() -> None:
    raw = parse_pdf(sample_cad_pdf(scale_text=False), assumed_scale=100)
    assert raw.plan_scale == 100
    assert raw.notes[0] == "Maßstab 1:100 angenommen (keine Angabe im Plan gefunden)"


def _phrases(*texts: str) -> list[Phrase]:
    return [Phrase(t, 0, 10, 0, 10, 8) for t in texts]


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["Maßstab 1:100"], 100),
        (["M 1:50"], 50),
        (["Massstab", "1:200"], 200),  # Wort und Wert in eigenen Zellen des Schriftfelds
        (["Dachneigung 1:3"], None),  # kein üblicher Planmaßstab
        (["1:100", "1:50"], None),  # mehrdeutig
        (["Maßstab 1:100", "Detail 1:20"], 100),  # mit Wort hat Vorrang
    ],
)
def test_detect_scale(texts: list[str], expected: int | None) -> None:
    assert detect_scale(_phrases(*texts)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ((0.5,), "#808080"),
        ((1.0, 1.0, 0.66), "#FFFFA8"),
        ((0.0, 0.0, 0.0, 1.0), "#000000"),  # CMYK
        ("P0", None),  # Muster
        (None, None),
    ],
)
def test_color_hex(value: object, expected: str | None) -> None:
    assert _color_hex(value) == expected


def test_superscript_is_attached_and_distant_texts_stay_apart() -> None:
    page = PdfPage(
        texts=[
            (100, 300, "F: 14,58 m", 8),
            (138.5, 303, "2", 5.3),  # hochgestellt direkt hinter „m“ (Helvetica: m endet bei 138,2)
            (400, 300, "Gast", 8),  # gleiche Zeile, aber weit entfernt
        ]
    )
    raw = parse_pdf(simple_pdf([page]))
    assert sorted(t.text for t in raw.texts) == ["F: 14,58 m2", "Gast"]
