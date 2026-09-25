"""LV-Material → Oberflächenbeschreibung (Textur, Fliesenformat, Putz …)."""

from __future__ import annotations

import pytest

from lumira_generator.logic.surfaces import TEXTURES_MM, describe, tile_format_mm
from lumira_shared.models import Material, MaterialCategory

FALLBACK = {"name": "Estrich", "color": "#9E9A93"}


def _material(
    name: str, category: MaterialCategory = MaterialCategory.FLOORING, **extra: object
) -> Material:
    return Material(id="m", category=category, name=name, **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "texture"),
    [
        ("Eichenparkett mit Sockelleiste", "oak_wood_planks"),
        ("Fischgrätparkett Eiche", "herringbone_parquet"),
        ("Laminatboden", "laminate_floor_02"),
        ("Landhausdiele Eiche", "laminate_floor_03"),
        ("Bodenbelag Klickvinyl", "laminate_floor_03"),
        ("Parkett Nussbaum", "plank_flooring_04"),
    ],
)
def test_wood_floors_get_photo_texture_in_real_size(name: str, texture: str) -> None:
    surface = describe(_material(name), fallback=FALLBACK)
    assert surface["kind"] == "texture"
    assert surface["texture"] == texture
    assert surface["size_mm"] == TEXTURES_MM[texture]


def test_tiles_use_format_and_color_from_the_lv() -> None:
    surface = describe(
        _material(
            "Bodenfliesen Feinsteinzeug",
            MaterialCategory.TILES,
            format="60 x 60 cm",
            color_hex="#8A8A86",
        ),
        fallback=FALLBACK,
    )
    assert surface == {
        "name": "Bodenfliesen Feinsteinzeug",
        "color": "#8A8A86",
        "kind": "tiles",
        "tile_mm": [600.0, 600.0],
        "grout_mm": 2.0,  # Großformat → schmale Fuge
    }


@pytest.mark.parametrize(
    ("text", "wall", "expected"),
    [
        ("60 x 60 cm", False, (600.0, 600.0)),
        ("30x60", True, (300.0, 600.0)),
        ("600 x 1200 mm", False, (600.0, 1200.0)),
        (f"30 {chr(0xD7)} 60 cm", True, (300.0, 600.0)),  # Malzeichen
        ("Format 20/10 cm", False, (200.0, 100.0)),
        ("ohne Angabe", False, (300.0, 300.0)),
        ("ohne Angabe", True, (300.0, 600.0)),
        ("1 x 1 cm", False, (300.0, 300.0)),  # unplausibel → Standard
    ],
)
def test_tile_format(text: str, wall: bool, expected: tuple[float, float]) -> None:
    assert tile_format_mm(text, wall=wall) == expected


def test_other_surfaces() -> None:
    carpet = describe(
        _material("Textiler Bodenbelag (Teppich)", color_hex="#6B6259"), fallback=FALLBACK
    )
    assert carpet["kind"] == "carpet"
    screed = describe(_material("Zementestrich"), fallback=FALLBACK)
    assert (screed["kind"], screed["color"]) == ("plain", "#9E9A93")
    paint = describe(
        _material("Dispersionsanstrich", MaterialCategory.WALL_FINISH, color_hex="#FFFFFF"),
        fallback=FALLBACK,
        wall=True,
    )
    assert paint["kind"] == "plaster"
    assert describe(None, fallback=FALLBACK)["kind"] == "plain"
    assert describe(None, fallback=FALLBACK, wall=True)["kind"] == "plaster"
