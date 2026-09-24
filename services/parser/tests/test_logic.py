from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from lumira_parser.logic import parse_dxf, parse_pdf
from lumira_parser.samples import LABELS_MM, WALLS_MM, sample_dxf, sample_pdf
from lumira_shared import NonRetryableError
from lumira_shared.testing import PdfPage, simple_pdf


def test_dxf_segments_and_labels() -> None:
    plan = parse_dxf(sample_dxf())
    assert len(plan.segments) == len(WALLS_MM)
    assert {s.layer for s in plan.segments} == {"WAENDE"}
    assert [t.text for t in plan.texts] == [label for label, _, _ in LABELS_MM]
    assert plan.width_mm == pytest.approx(10_000)
    assert plan.height_mm == pytest.approx(8_000)
    assert plan.page_png is None


def test_dxf_in_meters_is_converted_to_mm() -> None:
    plan = parse_dxf(sample_dxf(unit="m"))
    assert plan.width_mm == pytest.approx(10_000)
    bad = next(t for t in plan.texts if t.text.startswith("Bad"))
    assert bad.position.x == pytest.approx(8_000)
    assert bad.height_mm == pytest.approx(250)


def test_invalid_dxf_is_not_retryable() -> None:
    with pytest.raises(NonRetryableError, match="DXF"):
        parse_dxf(b"das ist kein dxf")


def test_pdf_vectors_text_and_image() -> None:
    plan = parse_pdf(sample_pdf(), assumed_scale=100)
    assert len(plan.segments) == len(WALLS_MM)
    # pdfplumber liefert Zeilen von oben nach unten → Reihenfolge egal; inkl. Umlaute und „m²“
    assert sorted(t.text for t in plan.texts) == sorted(label for label, _, _ in LABELS_MM)
    assert plan.page_png is not None
    assert plan.page_png.startswith(b"\x89PNG")
    # Außenwand 10 m bei 1:100 → im PDF 283,5 pt → zurückgerechnet wieder 10 m
    longest = max(s.length_mm for s in plan.segments)
    assert longest == pytest.approx(10_000, rel=0.001)
    assert any("1:100" in note for note in plan.notes)


def test_pdf_keeps_orientation_and_origin() -> None:
    # Diagonale von unten links nach oben rechts; PDF-Ursprung liegt unten links.
    data = simple_pdf([PdfPage(lines=[(0, 0, 72, 72)], width=200, height=100)])
    [segment] = parse_pdf(data, assumed_scale=1).segments
    ends = sorted([(round(p.x, 1), round(p.y, 1)) for p in (segment.start, segment.end)])
    assert ends == [(0.0, 0.0), (25.4, 25.4)]


def test_multi_page_pdf_uses_first_page() -> None:
    data = simple_pdf([PdfPage(lines=[(10, 10, 100, 10)]), PdfPage(lines=[(0, 0, 1, 1)] * 3)])
    plan = parse_pdf(data)
    assert plan.page_count == 2
    assert len(plan.segments) == 1
    assert any("Seite 1 von 2" in n for n in plan.notes)


def test_encrypted_pdf_is_not_retryable() -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(sample_pdf())))
    writer.encrypt(user_password="geheim", owner_password="owner", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(NonRetryableError, match="passwortgeschützt"):
        parse_pdf(buffer.getvalue())


def test_garbage_pdf_is_not_retryable() -> None:
    with pytest.raises(NonRetryableError, match="PDF"):
        parse_pdf(b"%PDF-kaputt")
