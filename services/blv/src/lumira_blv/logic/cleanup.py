"""Regelbasierte Bereinigung der LLM-Ausgabe – gilt für alle Anbieter.

LLMs (besonders kleine lokale Modelle) übernehmen aus Baubeschreibungen auch Haustechnik,
Rohbau-Schichten und Optionen ohne Materialbezug. Für das 3D-Modell zählen aber nur sichtbare
Oberflächen. Die Regeln prüfen nur den Materialnamen (nicht das Zitat, das oft mehrere Bauteile
beschreibt) und sind bewusst eng gefasst; jede Korrektur landet als Hinweis im Ergebnis.

1. Haustechnik ohne sichtbare Oberfläche verwerfen (Leerrohre, Anschlüsse, Stromkreise …)
2. Lage korrigieren, wenn der Name eindeutig innen bzw. außen sagt
3. Rohbau- und Dämmschichten als Untergrund markieren (nie umgekehrt)
4. Kategorie "other" anhand eindeutiger Wörter zuordnen
5. Gleiche Materialien zusammenführen und Variantenreferenzen umschreiben
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from lumira_shared.models import MaterialCategory, MaterialLocation

if TYPE_CHECKING:  # zur Laufzeit zirkulär: extraction ruft clean_extraction auf
    from lumira_blv.logic.extraction import BLVExtraction, ExtractedMaterial

_NOT_A_SURFACE = re.compile(
    r"stromkreis|steckdose|leerrohr|leerdose|erdung|potentialausgleich|anschl(uss|üsse)"
    r"|verbundrohr|grundrohr|kg-rohr|abwasserrohr|wasserleitung|kabel|brennstelle"
    r"|wasserhahn|eckh(ahn|ähne)|rauch(warn)?melder|klingel|sprechanlage|kellenschnitt"
    r"|trennband|rinneneinlauf|dachdurchg|vorbereitung|pv-anlage|photovoltaik|solaranlage"
    r"|\bheizung\b|fu(ß|ss)bodenheizung|speicher\b"
    # Fugen und Profilschienen sind keine Flächen – sonst hielte der Generator sie für Fliesen
    r"|\bfugen?\b|schiene"
)

_EXTERIOR = re.compile(
    r"au(ß|ss)en(?!schale)|fassade|dach(deckung|eindeckung|ziegel|stein|pfanne|rinne|überstand)"
    r"|fallrohr|balkon|terrass|pflaster|vordach|sockelputz|garage"
)
_INTERIOR = re.compile(
    r"wohnungseingang|innent(ü|ue)r|innenw(a|ä)nd|innenputz|innenfensterbank"
    r"|fensterb(a|ä)n\w* innen|\binnen\b"
)

_SUBSTRATE = re.compile(
    r"estrich|(?<!ge)d(ä|ae)mm|trittschall|dampfbremse|abdichtung|feuchtigkeitssperre|bewehr"
    r"|bodenplatte|stahlbeton|betondecke|elementdecke|filigran|kalksandstein|porenbeton"
    r"|putzträger|ausgleichschicht|unterkonstruktion|rohdecke"
)
# Endbeschichtung im Namen → trotz Rohbauwort sichtbar (z. B. "Sockelputz Abdichtung")
_FINISH = re.compile(
    r"putz|anstrich|farbe|fliese|parkett|belag|tapete|laminat|vinyl|sichtestrich"
    r"|geschliffen|versiegelt|beschichtet|klinker|verblend"
)

_CATEGORY_WORDS: list[tuple[re.Pattern[str], MaterialCategory]] = [
    (re.compile(r"fensterb(a|ä)nk"), MaterialCategory.WINDOW),
    (re.compile(r"treppe"), MaterialCategory.STAIRS),
    (re.compile(r"fliese"), MaterialCategory.TILES),
    # Leisten bewusst nicht: als "flooring" würde der Generator sie für den Boden halten.
    (
        re.compile(r"^(?!.*leiste).*(parkett|laminat|vinyl|teppich|dielen)"),
        MaterialCategory.FLOORING,
    ),
    (re.compile(r"tapete|putz|anstrich|wandfarbe"), MaterialCategory.WALL_FINISH),
    (re.compile(r"waschtisch|waschbecken|\bwc\b|dusche|wanne|armatur"), MaterialCategory.SANITARY),
    (re.compile(r"leuchte|lampe"), MaterialCategory.LIGHTING),
    (re.compile(r"t(ü|ue)r\b|t(ü|ue)ren\b"), MaterialCategory.DOOR),
]

_OPTIONAL_FIELDS = (
    "manufacturer",
    "product",
    "color",
    "color_hex",
    "finish",
    "format",
    "blv_position",
    "source_excerpt",
)
_MAX_LISTED = 8


def _norm(text: str) -> str:
    return " ".join(re.sub(r"\W+", " ", text.casefold()).split())


def material_key(m: ExtractedMaterial) -> tuple[object, ...]:
    """Zwei Einträge beschreiben dasselbe Material: gleiche Kategorie, Lage, Räume, gleicher
    Name, Farbe und Format (Hersteller u. Ä. dürfen fehlen – sie werden ergänzt)."""
    return (
        m.category,
        m.location,
        tuple(sorted(m.room_types)),
        _norm(m.name),
        _norm(m.color or ""),
        _norm(m.format or ""),
    )


def _listing(names: Iterable[str]) -> str:
    names = list(names)
    more = f" … (+{len(names) - _MAX_LISTED})" if len(names) > _MAX_LISTED else ""
    return ", ".join(names[:_MAX_LISTED]) + more


def _location(m: ExtractedMaterial, name: str) -> MaterialLocation:
    outside, inside = bool(_EXTERIOR.search(name)), bool(_INTERIOR.search(name))
    if outside and not inside:
        return MaterialLocation.EXTERIOR
    if inside and not outside:
        return MaterialLocation.INTERIOR
    return m.location  # beides oder nichts → Einschätzung des LLM behalten


def _category(m: ExtractedMaterial, name: str) -> MaterialCategory:
    if m.category is not MaterialCategory.OTHER:
        return m.category
    for pattern, category in _CATEGORY_WORDS:
        if pattern.search(name):
            return category
    return m.category


def _fill_missing(kept: ExtractedMaterial, dup: ExtractedMaterial) -> ExtractedMaterial:
    """Angaben, die nur die Dublette hat, übernehmen."""
    missing = {
        field: getattr(dup, field)
        for field in _OPTIONAL_FIELDS
        if getattr(kept, field) is None and getattr(dup, field) is not None
    }
    return kept.model_copy(update=missing) if missing else kept


def _corrected(
    m: ExtractedMaterial, relocated: list[str], substrate: list[str], recategorized: list[str]
) -> ExtractedMaterial:
    name = m.name.casefold()
    updates: dict[str, object] = {}
    location = _location(m, name)
    if location is not m.location:
        updates["location"] = location
        side = "innen" if location is MaterialLocation.INTERIOR else "außen"
        relocated.append(f"{m.name} → {side}")
    if m.is_final_surface and _SUBSTRATE.search(name) and not _FINISH.search(name):
        updates["is_final_surface"] = False
        substrate.append(m.name)
    category = _category(m, name)
    if category is not m.category:
        updates["category"] = category
        recategorized.append(f"{m.name} → {category}")
    return m.model_copy(update=updates) if updates else m


def clean_extraction(extraction: BLVExtraction) -> tuple[BLVExtraction, list[str]]:
    """Liefert (bereinigte Extraktion, Hinweise zu allen Korrekturen)."""
    notes: list[str] = []
    dropped: list[str] = []
    relocated: list[str] = []
    substrate: list[str] = []
    recategorized: list[str] = []
    merged: list[str] = []

    kept: dict[str, ExtractedMaterial] = {}  # verbleibende ID → Material
    by_key: dict[tuple[object, ...], str] = {}
    id_map: dict[str, str | None] = {}  # ID laut LLM → verbleibende ID (None = verworfen)
    # Gleich benannte Materialien verschiedener Varianten sind Alternativen, keine Dubletten.
    in_variants = {
        m.id: frozenset(n for n, v in enumerate(extraction.variants) if m.id in v.material_ids)
        for m in extraction.materials
    }

    for m in extraction.materials:
        if m.id in id_map:
            notes.append(f"Doppelte Material-ID '{m.id}' verworfen")
            continue
        if _NOT_A_SURFACE.search(m.name.casefold()):
            dropped.append(m.name)
            id_map[m.id] = None
            continue
        material = _corrected(m, relocated, substrate, recategorized)
        key = (*material_key(material), in_variants[m.id])
        if key in by_key:
            target = by_key[key]
            kept[target] = _fill_missing(kept[target], material)
            id_map[m.id] = target
            merged.append(m.name)
            continue
        by_key[key] = m.id
        kept[m.id] = material
        id_map[m.id] = m.id

    def remap(ids: list[str]) -> list[str]:
        # Unbekannte IDs bleiben stehen – to_blv_result meldet sie.
        mapped = (id_map.get(i, i) for i in ids)
        return list(dict.fromkeys(i for i in mapped if i is not None))

    variants = [
        v.model_copy(update={"material_ids": remap(v.material_ids)}) for v in extraction.variants
    ]

    if dropped:
        notes.append(f"Ohne sichtbare Oberfläche verworfen ({len(dropped)}): {_listing(dropped)}")
    if substrate:
        notes.append(f"Als Untergrund markiert ({len(substrate)}): {_listing(substrate)}")
    if relocated:
        notes.append(f"Lage korrigiert: {_listing(relocated)}")
    if recategorized:
        notes.append(f"Kategorie zugeordnet: {_listing(recategorized)}")
    if merged:
        notes.append(f"Doppelt genannt, zusammengeführt: {_listing(merged)}")

    cleaned = extraction.model_copy(update={"materials": list(kept.values()), "variants": variants})
    return cleaned, notes
