"""Minimaler PDF-Writer für Testdaten: Linien und Text, beliebig viele Seiten.

Bewusst ohne Bibliothek (z. B. PyMuPDF steht unter AGPL) – PDF ist für diesen Zweck ein
einfaches Textformat. Koordinaten in PDF-Punkten (1 pt = 1/72 Zoll), Ursprung unten links.
Text nutzt die Standardschrift Helvetica mit WinAnsi-Kodierung (Umlaute, ß, ² funktionieren).
"""

from __future__ import annotations

from dataclasses import dataclass, field

Line = tuple[float, float, float, float]  # x1, y1, x2, y2
Text = tuple[float, float, str, float]  # x, y, text, Schriftgröße


@dataclass
class PdfPage:
    lines: list[Line] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    width: float = 842.0  # A4 quer
    height: float = 595.0
    line_width: float = 2.0


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content(page: PdfPage) -> bytes:
    parts = [f"q {page.line_width:g} w\n".encode()]
    parts += [
        f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n".encode() for x1, y1, x2, y2 in page.lines
    ]
    parts.append(b"Q\n")
    for x, y, text, size in page.texts:
        parts.append(
            f"BT /F1 {size:g} Tf {x:.2f} {y:.2f} Td (".encode() + _escape(text) + b") Tj ET\n"
        )
    return b"".join(parts)


def simple_pdf(pages: list[PdfPage]) -> bytes:
    if not pages:
        raise ValueError("Mindestens eine Seite")
    font_id = 3
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    kids: list[str] = []
    next_id = 4
    for page in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        stream = _content(page)
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width:g} {page.height:g}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        kids.append(f"{page_id} 0 R")
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += b"%d 0 obj\n" % obj_id + objects[obj_id] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    out += b"".join(b"%010d 00000 n \n" % offsets[i] for i in range(1, size))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)
