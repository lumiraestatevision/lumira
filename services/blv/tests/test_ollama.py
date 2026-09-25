"""Ollama-Anbindung gegen einen Fake-Server (httpx.MockTransport) – ohne Modell und GPU."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from lumira_blv.logic import ollama
from lumira_blv.logic.extraction import BLVExtraction, ExtractedMaterial, ExtractedVariant
from lumira_shared import NonRetryableError
from lumira_shared.models import MaterialCategory, RoomType
from lumira_shared.testing import PdfPage, simple_pdf

MaterialFactory = Callable[..., ExtractedMaterial]  # Fixture make_material aus conftest.py

EMPTY = BLVExtraction(materials=[], variants=[], notes=["leer"])


def _lv(*pages: list[str]) -> bytes:
    return simple_pdf(
        [
            PdfPage(texts=[(40, 560 - 14 * i, line, 9) for i, line in enumerate(lines)])
            for lines in pages
        ]
    )


def _long_page(label: str) -> list[str]:
    """Etwa 1.200 Zeichen Text."""
    return [
        f"{label} Zeile {i:02d}: Bodenbelag Eichenparkett Landhausdiele geölt" for i in range(20)
    ]


def _variant(name: str, ids: list[str], *, default: bool = False) -> ExtractedVariant:
    return ExtractedVariant(
        name=name, description=None, material_ids=ids, surcharge_eur=None, is_default=default
    )


class FakeOllama:
    """Nimmt /api/chat-Anfragen an; jede bekommt die nächste Antwort aus ``replies``
    (BLVExtraction, fertige httpx.Response oder Exception)."""

    def __init__(self, *replies: BLVExtraction | httpx.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return _answer(reply.model_dump_json())

    @property
    def documents(self) -> list[str]:
        return [r["messages"][1]["content"] for r in self.requests]


def _answer(content: str, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3.5:4b",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": 1500,
            "eval_count": 400,
            "total_duration": 12_000_000_000,
        },
    )


async def _run(
    server: FakeOllama, pdf: bytes | None = None, *, num_ctx: int = 32_768
) -> tuple[BLVExtraction, str]:
    return await ollama.extract_with_ollama(
        pdf or _lv(["Pos. 01 Bodenbelag: Eichenparkett, Landhausdiele, geölt"]),
        base_url="http://ollama:11434",
        model="qwen3.5:4b",
        num_ctx=num_ctx,
        transport=httpx.MockTransport(server),
    )


# ------------------------------------------------------------------ Anfrage und Antwort
async def test_request_contains_text_schema_and_options() -> None:
    server = FakeOllama(EMPTY)

    result, used = await _run(server)

    assert (result, used) == (EMPTY, "ollama/qwen3.5:4b")
    [request] = server.requests
    assert request["model"] == "qwen3.5:4b"
    assert request["format"] == BLVExtraction.model_json_schema()
    assert request["stream"] is False
    assert request["think"] is False
    assert request["options"] == {"num_ctx": 32_768, "num_predict": 16_384, "temperature": 0}
    system, user = request["messages"]
    assert "Leistungsverzeichnisse" in system["content"]
    assert '"materials"' in system["content"]  # Schema zusätzlich im Prompt
    assert "=== Seite 1 ===\nPos. 01 Bodenbelag: Eichenparkett" in user["content"]
    assert "Abschnitt" not in user["content"]  # passt in einen Aufruf


async def test_scanned_pdf_without_text_is_rejected() -> None:
    server = FakeOllama()
    with pytest.raises(NonRetryableError, match="gescannt"):
        await _run(server, simple_pdf([PdfPage(lines=[(0, 0, 100, 100)])]))
    assert server.requests == []


@pytest.mark.parametrize(
    ("reply", "error", "match"),
    [
        (httpx.ConnectError("refused"), ollama.OllamaUnavailableError, "Profil 'llm'"),
        (httpx.ReadTimeout("slow"), ollama.OllamaUnavailableError, "ReadTimeout"),
        (
            httpx.Response(500, json={"error": "out of memory"}),
            ollama.OllamaUnavailableError,
            "memory",
        ),
        (
            httpx.Response(404, json={"error": "model not found"}),
            NonRetryableError,
            "BLV_OLLAMA_MODEL",
        ),
        (
            httpx.Response(400, json={"error": "invalid format"}),
            NonRetryableError,
            "invalid format",
        ),
        (_answer('{"materials": "kaputt"}'), NonRetryableError, "Schema"),
    ],
    ids=["nicht-erreichbar", "timeout", "serverfehler", "modell-fehlt", "ungueltig", "kein-schema"],
)
async def test_errors(reply: Any, error: type[Exception], match: str) -> None:
    with pytest.raises(error, match=match):
        await _run(FakeOllama(reply))


# ------------------------------------------------------------------ Abschnitte
def test_chunks_respect_budget_and_keep_page_markers() -> None:
    pages = ["a" * 900, "b" * 900, "", "c" * 900]
    chunks = ollama.split_into_chunks(pages, max_chars=2_000)
    assert [c.count("=== Seite") for c in chunks] == [2, 1]
    assert "=== Seite 3 ===" not in "".join(chunks)  # leere Seite fällt weg
    assert chunks[1].startswith("=== Seite 4 ===")
    assert all(len(c) <= 2_000 for c in chunks)


def test_budget_grows_with_context() -> None:
    assert ollama.chunk_char_budget(32_768) == (32_768 - 3_000 - 16_384) * 3
    assert ollama.chunk_char_budget(4_096) == 2_000  # Untergrenze


async def test_long_document_is_split_and_merged() -> None:
    server = FakeOllama(EMPTY, EMPTY, EMPTY)
    pdf = _lv(_long_page("A"), _long_page("B"), _long_page("C"))

    result, _ = await _run(server, pdf, num_ctx=4_096)  # Budget 2.000 Zeichen → 3 Abschnitte

    assert len(server.requests) == 3
    assert "Abschnitt 1 von 3" in server.documents[0]
    assert "Abschnitt 3 von 3" in server.documents[2]
    assert "A Zeile" in server.documents[0]
    assert "C Zeile" in server.documents[2]
    assert any("3 Abschnitten" in n for n in result.notes)


async def test_truncated_answer_splits_chunk_in_half() -> None:
    server = FakeOllama(_answer('{"materials": [', done_reason="length"), EMPTY, EMPTY)
    pdf = _lv(_long_page("A"), _long_page("B"), _long_page("C"), _long_page("D"))

    await _run(server, pdf)  # passt eigentlich in einen Abschnitt

    first, left, right = server.documents
    assert "A Zeile" in first
    assert "D Zeile" in first
    assert "=== Seite 1 ===" in left
    assert "=== Seite 3 ===" not in left
    assert "=== Seite 3 ===" in right
    assert "A Zeile" not in right
    assert "Abschnitt 2 von 2" in right


async def test_truncated_small_chunk_gives_up() -> None:
    server = FakeOllama(_answer("{", done_reason="length"))
    with pytest.raises(NonRetryableError, match="BLV_OLLAMA_NUM_CTX"):
        await _run(server)


# ------------------------------------------------------------------ Zusammenführen
def test_merge_single_part_is_unchanged(make_material: MaterialFactory) -> None:
    part = BLVExtraction(materials=[make_material("m1")], variants=[], notes=[])
    assert ollama.merge([part]) is part


def test_merge_combines_materials_and_variants(make_material: MaterialFactory) -> None:
    tiles = {"category": MaterialCategory.TILES, "name": "Feinsteinzeug 60x60"}
    part1 = BLVExtraction(
        materials=[
            make_material("m1"),  # Parkett, überall
            make_material("m2", **tiles, room_types=[RoomType.BATHROOM]),
            make_material("m3", name="Fischgrätparkett"),
        ],
        variants=[
            _variant("Standard", ["m1", "m2"], default=True),
            _variant("Premium", ["m3"]),
        ],
        notes=["Hinweis A"],
    )
    part2 = BLVExtraction(
        materials=[
            make_material("m1", **tiles, room_types=[RoomType.BATHROOM]),  # Dublette von Teil 1
            make_material("m2", category=MaterialCategory.DOOR, name="Innentür weiß"),
            make_material("m3", name="Designparkett"),
        ],
        variants=[_variant("premium ", ["m3"], default=False)],  # ohne Standardvariante
        notes=["Hinweis A", "Hinweis B"],
    )

    merged = ollama.merge([part1, part2])

    assert [m.id for m in merged.materials] == ["a1-m1", "a1-m2", "a1-m3", "a2-m2", "a2-m3"]
    standard, premium = merged.variants
    assert (standard.name, standard.is_default) == ("Standard", True)
    # Teil 2 ohne Standardvariante: alles, was keiner anderen Variante gehört (Dublette → a1-m2)
    assert standard.material_ids == ["a1-m1", "a1-m2", "a2-m2"]
    assert (premium.name, premium.is_default) == ("Premium", False)
    assert premium.material_ids == ["a1-m3", "a2-m3"]
    assert merged.notes[:2] == ["Hinweis A", "Hinweis B"]
