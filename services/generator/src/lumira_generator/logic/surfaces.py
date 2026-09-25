"""LV-Material → Oberflächenbeschreibung für das Blender-Skript.

Blender bekommt keine Lumira-Modelle, sondern eine schlanke Beschreibung je Oberfläche:

- ``texture``: fotografierte Textur (Holzböden, Poly Haven CC0, siehe scripts/fetch-textures.sh)
  mit realer Kantenlänge – eine Diele ist im Modell so lang wie in echt
- ``tiles``: Fliesen im Format aus dem LV (z. B. 60 x 60 cm), Fuge und Farbe werden in Blender
  erzeugt
- ``plaster``: Putz/Anstrich in der LV-Farbe mit feiner Putzstruktur
- ``carpet``: Teppich (erzeugte Faserstruktur)
- ``plain``: nur Farbe (Estrich, Unbekanntes)

Fehlt eine Texturdatei, fällt Blender auf die Farbe zurück – die Beschreibung bleibt gültig.
"""

from __future__ import annotations

import re
from typing import Any

from lumira_shared.models import Material, MaterialCategory

# Textur-ID → reale Kantenlänge des Bildes in mm (Angaben von Poly Haven)
TEXTURES_MM: dict[str, float] = {
    "oak_wood_planks": 1_200,
    "laminate_floor_02": 1_700,
    "laminate_floor_03": 2_080,
    "herringbone_parquet": 3_400,
    "plank_flooring_04": 2_000,
}

# Reihenfolge = Priorität (spezifisch vor allgemein); geprüft gegen Name, Produkt, Oberfläche
_WOOD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"fischgr[äa]t|herringbone"), "herringbone_parquet"),
    (
        re.compile(r"nussbaum|walnuss|r[äa]uchereiche|wenge|dunkel|terrasse|balkon"),
        "plank_flooring_04",
    ),
    (re.compile(r"landhaus|vinyl|designbelag|klick"), "laminate_floor_03"),
    (re.compile(r"laminat"), "laminate_floor_02"),
    (re.compile(r"parkett|eiche|diele|holz|buche|esche"), "oak_wood_planks"),
]
_TILES = re.compile(r"fliese|feinsteinzeug|steinzeug|keramik|naturstein|marmor|granit|terrazzo")
_CARPET = re.compile(r"teppich|velours|schlingen")
_TIMES = chr(0xD7)  # Malzeichen – steht in LVs oft statt „x“ („60 mal 60 cm“)
_FORMAT = re.compile(
    rf"(\d{{1,4}}(?:[.,]\d)?)\s*[x{_TIMES}/]\s*(\d{{1,4}}(?:[.,]\d)?)\s*(mm|cm)?", re.IGNORECASE
)
DEFAULT_TILE_MM = (300.0, 300.0)
DEFAULT_WALL_TILE_MM = (300.0, 600.0)


def _text(material: Material) -> str:
    parts = (material.name, material.product, material.finish, material.color)
    return " ".join(p for p in parts if p).lower()


def tile_format_mm(format_text: str | None, *, wall: bool = False) -> tuple[float, float]:
    """„60 x 60 cm“, „30x60“, „600 x 1200 mm“ → (Breite, Höhe) in mm. Ohne Einheit: cm."""
    default = DEFAULT_WALL_TILE_MM if wall else DEFAULT_TILE_MM
    match = _FORMAT.search(format_text or "")
    if not match:
        return default
    a, b = (float(match.group(i).replace(",", ".")) for i in (1, 2))
    factor = 1.0 if (match.group(3) or "").lower() == "mm" else 10.0
    width, height = a * factor, b * factor
    if not (50 <= width <= 3_000 and 50 <= height <= 3_000):  # Unsinn, z. B. Maße ohne Bezug
        return default
    return width, height


def _grout(tile_mm: tuple[float, float]) -> float:
    return 2.0 if max(tile_mm) >= 600 else 3.0


def describe(
    material: Material | None, *, fallback: dict[str, str], wall: bool = False
) -> dict[str, Any]:
    """Oberfläche für Blender. ``fallback`` = {"name", "color"} ohne passendes LV-Material."""
    if material is None:
        return {"kind": "plaster" if wall else "plain", **fallback}
    text = _text(material)
    color = material.color_hex or fallback["color"]
    base = {"name": material.name, "color": color}

    if material.category is MaterialCategory.TILES or _TILES.search(text):
        tile = tile_format_mm(material.format or text, wall=wall)
        return {**base, "kind": "tiles", "tile_mm": list(tile), "grout_mm": _grout(tile)}
    if _CARPET.search(text):
        return {**base, "kind": "carpet"}
    if material.category is MaterialCategory.FLOORING:
        for pattern, texture in _WOOD_RULES:
            if pattern.search(text):
                return {
                    **base,
                    "kind": "texture",
                    "texture": texture,
                    "size_mm": TEXTURES_MM[texture],
                }
        return {**base, "kind": "plain"}
    if material.category is MaterialCategory.WALL_FINISH:
        return {**base, "kind": "plaster"}
    return {**base, "kind": "plain"}
