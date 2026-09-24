"""Farbe eines Materials bestimmen – exakt, wenn möglich, sonst als gekennzeichnete Annahme.

Reihenfolge:
  1. RAL-Code in Farbe/Name        → Tabellenwert           (ColorSource.RAL)
  2. Hex-Wert aus dem LLM          → eindeutige Farbangabe  (ColorSource.DOCUMENT)
  3. Farbwort ("anthrazit", "weiß") → typischer Farbton     (ColorSource.ASSUMED)
  4. Materialwort ("Eiche", "Feinsteinzeug") → typischer Ton (ColorSource.ASSUMED)
  5. Kategorie (Boden, Wand, Sanitär …)      → neutraler Ton (ColorSource.ASSUMED)

Die Annahmen sind sRGB-Näherungen typischer Ausführungen im Wohnungsbau. Sie sind bewusst
an einer Stelle gesammelt, damit sie leicht zu prüfen und anzupassen sind.
"""

from __future__ import annotations

import re

from lumira_blv.logic.ral import find_ral
from lumira_shared.models import ColorSource, MaterialCategory

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Spezifische Begriffe vor allgemeinen ("dunkelgrau" vor "grau", "cremeweiss" vor "weiss").
COLOR_WORDS: tuple[tuple[str, str], ...] = (
    ("anthrazit", "#383E42"),
    ("dunkelgrau", "#5E6163"),
    ("hellgrau", "#C5C7C4"),
    ("lichtgrau", "#C5C7C4"),
    ("silbergrau", "#A5A9AC"),
    ("grau", "#9A9B97"),
    ("cremeweiss", "#E9E0D2"),
    ("creme", "#E9E0D2"),
    ("elfenbein", "#E6D2B5"),
    ("weiss", "#F2F1EC"),
    ("schwarz", "#1F1F1F"),
    ("taupe", "#8F8374"),
    ("sandfarben", "#CBB994"),
    ("sand", "#CBB994"),
    ("beige", "#D6C7A8"),
    ("ziegelrot", "#A5543A"),
    ("terrakotta", "#B0613F"),
    ("dunkelbraun", "#4E3527"),
    ("braun", "#6F4E37"),
)

MATERIAL_WORDS: tuple[tuple[str, str], ...] = (
    ("nussbaum", "#6B4A2F"),
    ("eiche", "#B8894F"),
    ("buche", "#C89B6D"),
    ("ahorn", "#D9BF93"),
    ("esche", "#CDB38A"),
    ("laerche", "#C79A63"),
    ("kiefer", "#C79A63"),
    ("fichte", "#D3AE7C"),
    ("nadelholz", "#C79A63"),
    ("parkett", "#B8894F"),
    ("dielen", "#B8894F"),
    ("holz", "#B8894F"),
    ("laminat", "#B08A5F"),
    ("vinyl", "#A88B66"),
    ("designbelag", "#A88B66"),
    ("teppich", "#9E968C"),
    ("textil", "#9E968C"),
    ("linoleum", "#A9A392"),
    ("feinsteinzeug", "#BDBAB3"),
    ("marmor", "#E6E2DA"),
    ("kalkstein", "#D8CBB0"),
    ("travertin", "#D8CBB0"),
    ("naturstein", "#D8CBB0"),
    ("granit", "#7D7B78"),
    ("schiefer", "#4A4F52"),
    ("steingut", "#EDEBE6"),
    ("fliese", "#CFCBC3"),
    ("keramik", "#EDEBE6"),
    ("sichtbeton", "#9E9A93"),
    ("beton", "#9E9A93"),
    ("estrich", "#9E9A93"),
    ("raufaser", "#F2F1EC"),
    ("tapete", "#F2F1EC"),
    ("putz", "#F2F1EC"),
    ("gips", "#F2F1EC"),
    ("anstrich", "#F2F1EC"),
    ("dispersion", "#F2F1EC"),
    ("edelstahl", "#C0C4C8"),
    ("chrom", "#C9CDD1"),
    ("aluminium", "#A9AEB3"),
    ("glas", "#D6E4E8"),
    ("ziegel", "#A5543A"),
)

CATEGORY_DEFAULTS: dict[MaterialCategory, str] = {
    MaterialCategory.FLOORING: "#A8A29A",
    MaterialCategory.TILES: "#CFCBC3",
    MaterialCategory.WALL_FINISH: "#F2F1EC",
    MaterialCategory.CEILING: "#F7F6F2",
    MaterialCategory.SANITARY: "#F4F4F2",
    MaterialCategory.DOOR: "#F2F1EC",
    MaterialCategory.WINDOW: "#F2F1EC",
    MaterialCategory.KITCHEN: "#E5E2DC",
    MaterialCategory.LIGHTING: "#F2F1EC",
    MaterialCategory.STAIRS: "#A8A29A",
    MaterialCategory.OTHER: "#B5B0A8",
}


def _normalize(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower().translate(_UMLAUTS)


def _first_match(text: str, table: tuple[tuple[str, str], ...]) -> str | None:
    return next((hex_value for word, hex_value in table if word in text), None)


def resolve_color(
    *,
    category: MaterialCategory,
    name: str,
    color: str | None,
    color_hex: str | None,
    product: str | None = None,
    finish: str | None = None,
) -> tuple[str, ColorSource]:
    ral = find_ral(color) or find_ral(name)
    if ral is not None:
        return ral[1], ColorSource.RAL
    if color_hex and _HEX.match(color_hex):
        return color_hex.upper(), ColorSource.DOCUMENT

    # Farbwörter zuerst in der Farbangabe, dann im Namen ("Fliesen anthrazit").
    by_color = _first_match(_normalize(color), COLOR_WORDS) or _first_match(
        _normalize(name), COLOR_WORDS
    )
    if by_color:
        return by_color, ColorSource.ASSUMED
    by_material = _first_match(_normalize(name, product, finish), MATERIAL_WORDS)
    if by_material:
        return by_material, ColorSource.ASSUMED
    return CATEGORY_DEFAULTS[category], ColorSource.ASSUMED
