"""Leistungsverzeichnis mit einem lokalen Modell über Ollama auswerten – kostenlos, offline.

Gedacht für Entwicklung und Tests. Für Kunden-LVs später Claude (BLV_PROVIDER=anthropic):
kleine lokale Modelle erkennen Varianten und Innen/Außen deutlich unzuverlässiger.

- Kleine Modelle lesen keine PDFs: Der Text wird mit pypdfium2 extrahiert und mit
  Seitenmarkierungen übergeben. Gescannte PDFs ohne Textebene werden abgelehnt.
- Strukturierte Ausgabe: Ollama erzwingt über ``format`` das JSON-Schema ``BLVExtraction``.
- Passt das Dokument nicht ins Kontextfenster (``num_ctx``), wird es seitenweise in Abschnitte
  geteilt; wird eine Antwort trotzdem abgeschnitten, wird der Abschnitt halbiert. Die
  Teilergebnisse werden zusammengeführt (siehe ``merge``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import httpx
import pypdfium2 as pdfium
from pydantic import ValidationError

from lumira_blv.logic.extraction import BLVExtraction, ExtractedMaterial, ExtractedVariant
from lumira_blv.logic.prompts import SYSTEM_PROMPT, USER_INSTRUCTION
from lumira_shared import NonRetryableError, get_logger

log = get_logger(__name__)

_CHARS_PER_TOKEN = 3  # konservativ für deutschen Text
_PROMPT_TOKENS = 3_000  # System-Prompt inkl. Schema (grob)
# Ausführliche LVs ergeben leicht 50+ Materialien → großzügig, sonst wird oft halbiert.
_MAX_OUTPUT_TOKENS = 16_384
_MIN_CHUNK_CHARS = 2_000  # kleiner wird beim Halbieren nicht geteilt
_MIN_TEXT_CHARS_PER_PAGE = 50
_KEEP_ALIVE = "15m"  # Modell so lange im Grafikspeicher halten


class OllamaUnavailableError(RuntimeError):
    """Ollama nicht erreichbar oder überlastet – später erneut versuchen."""


class _TruncatedError(Exception):
    """Antwort hat das Ausgabelimit erreicht."""


# ------------------------------------------------------------------ PDF → Text-Abschnitte
def pdf_pages_text(pdf: bytes) -> list[str]:
    doc = pdfium.PdfDocument(pdf)
    try:
        pages: list[str] = []
        for index in range(len(doc)):
            page = doc[index]
            textpage = page.get_textpage()
            pages.append(_clean(textpage.get_text_range()))
            textpage.close()
            page.close()
        return pages
    finally:
        doc.close()


def _clean(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.replace("\r", "\n").split("\n"))
    return "\n".join(line for line in lines if line)


def output_tokens(num_ctx: int) -> int:
    return min(_MAX_OUTPUT_TOKENS, num_ctx // 2)


def chunk_char_budget(num_ctx: int) -> int:
    input_tokens = num_ctx - _PROMPT_TOKENS - output_tokens(num_ctx)
    return max(_MIN_CHUNK_CHARS, input_tokens * _CHARS_PER_TOKEN)


def _split_text(text: str, max_chars: int) -> list[str]:
    """Teilt an Zeilengrenzen in Stücke von höchstens ``max_chars`` (einzelne Überlänge bleibt)."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if current and size + len(line) + 1 > max_chars:
            pieces.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


def split_into_chunks(pages: Sequence[str], max_chars: int) -> list[str]:
    """Fasst ganze Seiten (mit Markierung) zu Abschnitten von höchstens ``max_chars`` zusammen.
    Nur eine Seite, die allein zu lang ist, wird an Zeilengrenzen geteilt."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for number, text in enumerate(pages, start=1):
        if not text:
            continue
        for piece in _split_text(f"=== Seite {number} ===\n{text}", max_chars):
            if current and size + len(piece) + 2 > max_chars:
                chunks.append("\n\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _halve(text: str) -> list[str]:
    """Teilt einen Abschnitt möglichst an einer Seitengrenze, sonst an einer Zeile, in zwei."""
    middle = len(text) // 2
    for marker in ("\n\n=== Seite ", "\n"):
        cuts = [match.start() for match in re.finditer(re.escape(marker), text) if match.start()]
        if cuts:
            cut = min(cuts, key=lambda i: abs(i - middle))
            return [text[:cut].strip(), text[cut:].strip()]
    return [text[:middle], text[middle:]]


# ------------------------------------------------------------------ Teilergebnisse zusammenführen
def _material_key(m: ExtractedMaterial) -> tuple[object, ...]:
    return (m.category, m.location, m.name.casefold().strip(), tuple(sorted(m.room_types)))


def _standard_ids(part: BLVExtraction) -> list[str]:
    """Material-IDs der Grundausstattung eines Abschnitts."""
    flagged = [v for v in part.variants if v.is_default]
    if flagged:
        return [i for v in flagged for i in v.material_ids]
    in_variants = {i for v in part.variants for i in v.material_ids}
    return [m.id for m in part.materials if m.id not in in_variants]


def merge(parts: Sequence[BLVExtraction]) -> BLVExtraction:
    """Vereinigt die Ergebnisse mehrerer Abschnitte.

    - Material-IDs werden je Abschnitt eindeutig gemacht; gleiche Materialien (Kategorie, Lage,
      Name, Räume) aus verschiedenen Abschnitten werden zusammengelegt.
    - Die Grundausstattung aller Abschnitte wird eine Standardvariante; weitere Varianten
      werden über den Namen zusammengeführt.
    """
    if len(parts) == 1:
        return parts[0]

    materials: list[ExtractedMaterial] = []
    by_key: dict[tuple[object, ...], str] = {}
    default: ExtractedVariant | None = None
    default_ids: list[str] = []
    others: dict[str, ExtractedVariant] = {}
    notes: list[str] = []

    for n, part in enumerate(parts, start=1):
        mapping: dict[str, str] = {}
        for m in part.materials:
            if m.id in mapping:
                continue
            key = _material_key(m)
            if key not in by_key:
                by_key[key] = f"a{n}-{m.id}"
                materials.append(m.model_copy(update={"id": by_key[key]}))
            mapping[m.id] = by_key[key]

        def remap(ids: Sequence[str], n: int = n, mapping: dict[str, str] = mapping) -> list[str]:
            return [mapping.get(i, f"a{n}-{i}") for i in ids]

        default_ids += remap(_standard_ids(part))
        for v in part.variants:
            if v.is_default:
                default = default or v
                continue
            name = v.name.casefold().strip()
            known = others.get(name)
            ids = remap(v.material_ids)
            if known is None:
                others[name] = v.model_copy(update={"material_ids": ids})
            else:
                others[name] = known.model_copy(
                    update={
                        "material_ids": [*known.material_ids, *ids],
                        "description": known.description or v.description,
                        "surcharge_eur": known.surcharge_eur or v.surcharge_eur,
                    }
                )
        notes += part.notes

    standard = ExtractedVariant(
        name=default.name if default else "Standard",
        description=default.description if default else None,
        material_ids=list(dict.fromkeys(default_ids)),
        surcharge_eur=None,
        is_default=True,
    )
    variants = [standard] + [
        v.model_copy(update={"material_ids": list(dict.fromkeys(v.material_ids))})
        for v in others.values()
    ]
    notes.append(f"Lokales Modell: Dokument in {len(parts)} Abschnitten ausgewertet")
    return BLVExtraction(materials=materials, variants=variants, notes=list(dict.fromkeys(notes)))


# ------------------------------------------------------------------ Ollama-Aufruf
def _system_prompt() -> str:
    schema = json.dumps(BLVExtraction.model_json_schema(), ensure_ascii=False)
    return f"{SYSTEM_PROMPT}\n\nAntworte ausschließlich mit JSON nach diesem Schema:\n{schema}"


def _user_prompt(chunk: str, index: int, total: int) -> str:
    part = (
        f"\nDies ist Abschnitt {index} von {total} des Dokuments. Erfasse alles, was in diesem "
        "Abschnitt steht; die Abschnitte werden anschließend zusammengeführt."
        if total > 1
        else ""
    )
    return f"{USER_INSTRUCTION}{part}\n\n<dokument>\n{chunk}\n</dokument>"


async def _chat(
    client: httpx.AsyncClient, *, model: str, system: str, user: str, num_ctx: int, think: bool
) -> BLVExtraction:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": BLVExtraction.model_json_schema(),
        "stream": False,
        "think": think,
        "keep_alive": _KEEP_ALIVE,
        "options": {"num_ctx": num_ctx, "num_predict": output_tokens(num_ctx), "temperature": 0},
    }
    try:
        response = await client.post("/api/chat", json=payload)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise OllamaUnavailableError(
            f"Ollama unter {client.base_url} nicht erreichbar ({type(exc).__name__}) – "
            "läuft das Compose-Profil 'llm'?"
        ) from exc

    if response.status_code != httpx.codes.OK:
        error = _error_text(response)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise NonRetryableError(
                f"Ollama kennt das Modell '{model}' nicht ({error}) – BLV_OLLAMA_MODEL prüfen "
                "oder `docker compose run --rm ollama-pull` ausführen"
            )
        if response.status_code >= 500:
            raise OllamaUnavailableError(f"Ollama-Fehler {response.status_code}: {error}")
        raise NonRetryableError(f"Ollama lehnt die Anfrage ab ({response.status_code}): {error}")

    body = response.json()
    log.info(
        "blv.llm_usage",
        provider="ollama",
        model=body.get("model", model),
        done_reason=body.get("done_reason"),
        input_tokens=body.get("prompt_eval_count"),
        output_tokens=body.get("eval_count"),
        duration_s=round(body.get("total_duration", 0) / 1e9, 1),
    )
    if body.get("done_reason") == "length":
        raise _TruncatedError
    content = (body.get("message") or {}).get("content") or ""
    try:
        return BLVExtraction.model_validate_json(content)
    except ValidationError as exc:
        raise NonRetryableError(
            f"Antwort des lokalen Modells passt nicht zum Schema: {exc.error_count()} Fehler"
        ) from exc


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", response.text))
    except ValueError:
        return response.text[:500]


async def extract_with_ollama(
    pdf: bytes,
    *,
    base_url: str,
    model: str,
    num_ctx: int,
    think: bool = False,
    timeout_s: float = 1800,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[BLVExtraction, str]:
    """Liefert (Ergebnis, "ollama/<modell>")."""
    pages = pdf_pages_text(pdf)
    if sum(len(p) for p in pages) < _MIN_TEXT_CHARS_PER_PAGE * max(len(pages), 1):
        raise NonRetryableError(
            "LV-PDF enthält (fast) keinen Text – vermutlich gescannt. Das lokale Modell braucht "
            "eine Textebene; gescannte LVs nur mit BLV_PROVIDER=anthropic oder gemini."
        )

    queue = split_into_chunks(pages, chunk_char_budget(num_ctx))
    system = _system_prompt()
    parts: list[BLVExtraction] = []
    timeout = httpx.Timeout(timeout_s, connect=10)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport) as client:
        while queue:
            chunk = queue.pop(0)
            index = len(parts) + 1
            total = len(parts) + len(queue) + 1
            log.info("blv.ollama_chunk", chunk=index, of=total, chars=len(chunk))
            try:
                parts.append(
                    await _chat(
                        client,
                        model=model,
                        system=system,
                        user=_user_prompt(chunk, index, total),
                        num_ctx=num_ctx,
                        think=think,
                    )
                )
            except _TruncatedError:
                if len(chunk) < 2 * _MIN_CHUNK_CHARS:
                    raise NonRetryableError(
                        "Antwort des lokalen Modells abgeschnitten, obwohl der Abschnitt schon "
                        "klein ist – BLV_OLLAMA_NUM_CTX erhöhen"
                    ) from None
                log.warning("blv.ollama_truncated_split", chunk=index, chars=len(chunk))
                queue[:0] = _halve(chunk)

    return merge(parts), f"ollama/{model}"
