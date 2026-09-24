"""Deterministische Nachbearbeitung der LLM-Ausgabe: RAL, Lage, Varianten."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from lumira_blv.logic.extraction import (
    BLVExtraction,
    ExtractedMaterial,
    ExtractedVariant,
    to_blv_result,
)
from lumira_blv.logic.ral import find_ral
from lumira_shared.models import BLVResult, MaterialCategory, MaterialLocation, RoomType

MaterialFactory = Callable[..., ExtractedMaterial]  # Fixture make_material aus conftest.py


def _variant(
    name: str, ids: list[str], *, default: bool = False, surcharge: float | None = None
) -> ExtractedVariant:
    return ExtractedVariant(
        name=name, description=None, material_ids=ids, surcharge_eur=surcharge, is_default=default
    )


def _result(materials: list[ExtractedMaterial], variants: list[ExtractedVariant]) -> BLVResult:
    return to_blv_result(
        BLVExtraction(materials=materials, variants=variants, notes=[]),
        project_id=uuid.uuid4(),
        source_key="lv.pdf",
        extracted_by="test",
    )


# ------------------------------------------------------------------ RAL
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Anthrazit RAL 7016", ("7016", "#383E42", "Anthrazitgrau")),
        ("ral-9010 reinweiß", ("9010", "#F1EDE1", "Reinweiß")),
        ("RAL9016", ("9016", "#F1F1EA", "Verkehrsweiß")),
        ("RAL 9999 gibt es nicht", None),
        ("weiß", None),
        (None, None),
    ],
)
def test_find_ral(text: str | None, expected: tuple[str, str, str] | None) -> None:
    assert find_ral(text) == expected


def test_ral_table_value_beats_llm_guess(make_material: MaterialFactory) -> None:
    result = _result(
        [
            make_material(
                "fenster_aussen",
                category=MaterialCategory.WINDOW,
                color="Anthrazit RAL 7016",
                color_hex="#708090",
                location=MaterialLocation.EXTERIOR,
            ),
            make_material(
                "fenster_innen",
                category=MaterialCategory.WINDOW,
                color="Weiß",
                color_hex="#FFFFFF",
                source_excerpt="innen Weiß, außen Anthrazit RAL 7016",
            ),
        ],
        [_variant("Standard", ["fenster_aussen", "fenster_innen"], default=True)],
    )
    colors = {m.id: m.color_hex for m in result.materials}
    # Das Zitat wird bewusst NICHT ausgewertet – es beschreibt oft innen und außen zugleich.
    assert colors == {"fenster_aussen": "#383E42", "fenster_innen": "#FFFFFF"}


# ------------------------------------------------------------------ Lage / Endoberfläche
def test_location_and_final_surface_are_kept(make_material: MaterialFactory) -> None:
    result = _result(
        [
            make_material(
                "putz",
                category=MaterialCategory.WALL_FINISH,
                name="Kratzputz",
                location=MaterialLocation.EXTERIOR,
            ),
            make_material("estrich", name="Trockenestrich", is_final_surface=False),
            make_material("parkett"),
        ],
        [_variant("Standard", [], default=True)],
    )
    assert [m.id for m in result.materials if m.is_visible_inside] == ["parkett"]


# ------------------------------------------------------------------ Varianten
def test_alternative_variant_inherits_everything_else(make_material: MaterialFactory) -> None:
    result = _result(
        [
            make_material(
                "teppich", name="Teppich", room_types=[RoomType.LIVING, RoomType.BEDROOM]
            ),
            make_material(
                "fliese", category=MaterialCategory.TILES, room_types=[RoomType.BATHROOM]
            ),
            make_material("farbe", category=MaterialCategory.WALL_FINISH),
            make_material("parkett", name="Eichenparkett", room_types=[RoomType.LIVING]),
        ],
        [
            _variant("Standard", ["teppich", "fliese", "farbe"], default=True),
            _variant("Parkett statt Teppich", ["parkett"], surcharge=2900),
        ],
    )
    standard, parkett = result.variants
    assert standard.material_ids == ["teppich", "fliese", "farbe"]
    assert parkett.material_ids == ["parkett", "fliese", "farbe"]  # Teppich ersetzt, Rest geerbt
    living = result.materials_for(variant="Parkett statt Teppich", room_type=RoomType.LIVING)
    assert [m.id for m in living] == ["farbe", "parkett"]


def test_exterior_alternative_does_not_replace_interior(make_material: MaterialFactory) -> None:
    wall = MaterialCategory.WALL_FINISH
    result = _result(
        [
            make_material("innenputz", category=wall),
            make_material("fassade_weiss", category=wall, location=MaterialLocation.EXTERIOR),
            make_material("fassade_grau", category=wall, location=MaterialLocation.EXTERIOR),
        ],
        [
            _variant("Standard", ["innenputz", "fassade_weiss"], default=True),
            _variant("Fassade grau", ["fassade_grau"]),
        ],
    )
    assert result.variants[1].material_ids == ["fassade_grau", "innenputz"]


def test_empty_standard_gets_all_non_exclusive_materials(make_material: MaterialFactory) -> None:
    result = _result(
        [
            make_material("a"),
            make_material("b", category=MaterialCategory.TILES),
            make_material("premium", name="Naturstein"),
        ],
        [_variant("Standard", [], default=True), _variant("Premium", ["premium"])],
    )
    assert result.variants[0].material_ids == ["a", "b"]


def test_default_is_chosen_by_name_if_not_flagged(make_material: MaterialFactory) -> None:
    result = _result(
        [make_material("a")],
        [_variant("Sonderwunsch Parkett", ["a"]), _variant("Grundausstattung", ["a"])],
    )
    assert result.default_variant is not None
    assert result.default_variant.name == "Grundausstattung"


def test_without_variants_a_standard_is_created(make_material: MaterialFactory) -> None:
    result = _result([make_material("a"), make_material("b", category=MaterialCategory.TILES)], [])
    assert [(v.name, v.is_default, v.material_ids) for v in result.variants] == [
        ("Standard", True, ["a", "b"])
    ]
