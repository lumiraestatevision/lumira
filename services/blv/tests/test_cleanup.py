"""Regelbasierte Bereinigung – Beispiele stammen aus echten Auswertungen von Test-LVs."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from lumira_blv.logic.cleanup import clean_extraction
from lumira_blv.logic.extraction import (
    BLVExtraction,
    ExtractedMaterial,
    ExtractedVariant,
    to_blv_result,
)
from lumira_shared.models import MaterialCategory, MaterialLocation

MaterialFactory = Callable[..., ExtractedMaterial]  # Fixture make_material aus conftest.py

INSIDE, OUTSIDE = MaterialLocation.INTERIOR, MaterialLocation.EXTERIOR
OTHER = MaterialCategory.OTHER


def _variant(name: str, ids: list[str], *, default: bool = False) -> ExtractedVariant:
    return ExtractedVariant(
        name=name, description=None, material_ids=ids, surcharge_eur=None, is_default=default
    )


def _clean(*materials: ExtractedMaterial, variants: list[ExtractedVariant] | None = None):
    return clean_extraction(
        BLVExtraction(materials=list(materials), variants=variants or [], notes=["vom LLM"])
    )


# ------------------------------------------------------------------ Haustechnik
@pytest.mark.parametrize(
    "name",
    [
        "Stromkreis Steckdosen (pro Raum)",
        "Außensteckdose",
        "Leerrohre (Antenne/Telefon)",
        "Einbauleuchten (Leerdosen)",
        "Erdungsband",
        "Küchenanschluss Spülmaschine",
        "Waschmaschinenanschluss",
        "Fünf-Schicht-Verbundrohr (Wasser)",
        "Kanalgrundrohr (KG-Rohr)",
        "Rauchwarnmelder",
        "Klingelanlage",
        "Eckhähne Bad/WC",
        "Frostsicherer Außenwasserhahn",
        "Heizung BRÖTJE WGB E",
        "Fußbodenheizung (Optional)",
        "Vorbereitung für PV-Anlage (Dachtraglast)",
        "Trennband (Wandanschlüsse)",
        "Dauerelastische Fugen (Fliesen)",
        "Kunststoff Schienen (Wandfliesen)",
        "Aluminiumschienen (Materialgrenzen Boden)",
    ],
)
def test_building_services_are_dropped(make_material: MaterialFactory, name: str) -> None:
    cleaned, notes = _clean(make_material("x", name=name, category=OTHER))
    assert cleaned.materials == []
    assert notes == [f"Ohne sichtbare Oberfläche verworfen (1): {name}"]


@pytest.mark.parametrize(
    "name",
    [
        "Handtuchheizkörper Astor",
        "Kunststoff Rollladenpanzer",
        "Schalter Gira System 55",
        "Dachrinnen und Fallrohre Kupfer",
        "Waschtisch Eurovit Plus",
    ],
)
def test_visible_objects_are_kept(make_material: MaterialFactory, name: str) -> None:
    cleaned, _ = _clean(make_material("x", name=name, category=OTHER))
    assert [m.name for m in cleaned.materials] == [name]


# ------------------------------------------------------------------ Lage
@pytest.mark.parametrize(
    ("name", "llm", "expected"),
    [
        ("Wohnungseingangstür CPL weiß", OUTSIDE, INSIDE),
        ("Naturstein Fensterbank (Innen)", OUTSIDE, INSIDE),
        ("Grundputz, Gewebespachtelung, Edelputz (Außen)", INSIDE, OUTSIDE),
        ("Dachdeckung (Beton-Dachpfannen)", INSIDE, OUTSIDE),
        ("Holzdielen Balkonbelag", INSIDE, OUTSIDE),
        ("Außenmauerwerk (Innenschale)", INSIDE, OUTSIDE),
        # widersprüchlich oder ohne Hinweis → LLM behält recht
        ("Kunststofffenster weiß innen / anthrazit außen", OUTSIDE, OUTSIDE),
        ("Gipsplatten (Dachgeschoss)", INSIDE, INSIDE),
        ("Haustüre Alutürelement", INSIDE, INSIDE),
    ],
)
def test_location_follows_unambiguous_name(
    make_material: MaterialFactory,
    name: str,
    llm: MaterialLocation,
    expected: MaterialLocation,
) -> None:
    cleaned, notes = _clean(make_material("x", name=name, location=llm))
    assert cleaned.materials[0].location is expected
    assert any("Lage korrigiert" in n for n in notes) is (llm is not expected)


# ------------------------------------------------------------------ Untergrund
@pytest.mark.parametrize(
    ("name", "final"),
    [
        ("Innenwände Erdgeschoss (Kalksandstein)", False),
        ("Innenwand Porenbeton-Plansteinen", False),
        ("Betondecke Erdgeschoss (Filigran)", False),
        ("Anhydritfliessestrich CAF", False),
        ("Wärmedämmverbundsysteme (Außenwand)", False),
        ("Sockelputz Abdichtung", True),  # Putz ist die sichtbare Schicht
        ("Sichtestrich geschliffen", True),
        ("Bodenfliesen Feinsteinzeug", True),
        ("Verblendmauerwerk Klinker", True),
        ("Gedämmte Einschubtreppe", True),  # "gedämmt" beschreibt, ist keine Dämmschicht
        ("Schwimmender Estrich mit Randdämmung", False),
    ],
)
def test_structural_layers_become_substrate(
    make_material: MaterialFactory, name: str, final: bool
) -> None:
    cleaned, _ = _clean(make_material("x", name=name))
    assert cleaned.materials[0].is_final_surface is final


def test_substrate_is_never_turned_into_surface(make_material: MaterialFactory) -> None:
    cleaned, notes = _clean(make_material("x", name="Eichenparkett", is_final_surface=False))
    assert cleaned.materials[0].is_final_surface is False
    assert notes == []


# ------------------------------------------------------------------ Kategorie
@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        ("Raufasertapete (Wohnräume)", OTHER, MaterialCategory.WALL_FINISH),
        ("Fensterbank Innen (Kunststein)", OTHER, MaterialCategory.WINDOW),
        ("Fußleisten", OTHER, OTHER),  # sonst hielte der Generator sie für den Boden
        ("Teppich-Sockelleiste", OTHER, OTHER),
        ("Laminatboden", OTHER, MaterialCategory.FLOORING),
        ("Treppenverkleidung", OTHER, MaterialCategory.STAIRS),
        ("Kunststoff Armaturen (Bad)", OTHER, MaterialCategory.SANITARY),
        ("Türbeschläge Edelstahl", OTHER, OTHER),  # kein eindeutiges Wort
        ("Balkonbelag Fliesen", MaterialCategory.FLOORING, MaterialCategory.FLOORING),
    ],
)
def test_other_category_is_resolved_from_name(
    make_material: MaterialFactory,
    name: str,
    category: MaterialCategory,
    expected: MaterialCategory,
) -> None:
    cleaned, _ = _clean(make_material("x", name=name, category=category))
    assert cleaned.materials[0].category is expected


# ------------------------------------------------------------------ Dubletten und Varianten
def test_duplicates_are_merged_and_references_rewritten(make_material: MaterialFactory) -> None:
    sanitary = MaterialCategory.SANITARY
    cleaned, notes = _clean(
        make_material("m1", category=sanitary, name="Bodengleiche Duschwanne Acryl"),
        make_material(
            "m2", category=sanitary, name="bodengleiche  Duschwanne, Acryl", manufacturer="Kaldewei"
        ),
        make_material("m3", name="Leerrohre"),
        variants=[
            _variant("Standard", ["m1", "m2", "m3"], default=True),
            _variant("Komfort", ["m9"]),
        ],
    )
    assert [(m.id, m.manufacturer) for m in cleaned.materials] == [("m1", "Kaldewei")]
    standard, comfort = cleaned.variants
    assert standard.material_ids == ["m1"]  # Dublette umgeschrieben, verworfene ID entfernt
    assert comfort.material_ids == ["m9"]  # unbekannte ID bleibt für die Meldung
    assert cleaned.notes == ["vom LLM"]
    assert any("zusammengeführt: bodengleiche  Duschwanne, Acryl" in n for n in notes)


def test_same_name_in_other_variant_or_color_is_no_duplicate(
    make_material: MaterialFactory,
) -> None:
    cleaned, notes = _clean(
        make_material("std", name="Fassadenputz", location=OUTSIDE),
        make_material("alt", name="Fassadenputz", location=OUTSIDE),
        make_material("weiss", name="Wandfliese", color="weiß"),
        make_material("grau", name="Wandfliese", color="grau"),
        variants=[
            _variant("Standard", ["std", "weiss", "grau"], default=True),
            _variant("Putz grau", ["alt"]),
        ],
    )
    assert [m.id for m in cleaned.materials] == ["std", "alt", "weiss", "grau"]
    assert notes == []


def test_duplicate_ids_are_reported(make_material: MaterialFactory) -> None:
    cleaned, notes = _clean(make_material("m1"), make_material("m1", name="Fliese"))
    assert [m.name for m in cleaned.materials] == ["Parkett"]
    assert notes == ["Doppelte Material-ID 'm1' verworfen"]


def test_options_without_own_material_are_not_variants(make_material: MaterialFactory) -> None:
    result = to_blv_result(
        BLVExtraction(
            materials=[
                make_material("parkett"),
                make_material("fbh", name="Fußbodenheizung Bad"),
                make_material("laminat", name="Laminat"),
            ],
            variants=[
                _variant("Standard", ["parkett"], default=True),
                _variant("Fußbodenheizung Bad", ["fbh"]),  # Material wird verworfen
                _variant("Schornstein", []),
                _variant("Laminat statt Parkett", ["laminat"]),
            ],
            notes=[],
        ),
        project_id=uuid.uuid4(),
        source_key="lv.pdf",
        extracted_by="test",
    )
    assert [v.name for v in result.variants] == ["Standard", "Laminat statt Parkett"]
    assert (
        "Optionen ohne eigenes Material (im 3D-Modell wie Standard): "
        "Fußbodenheizung Bad, Schornstein"
    ) in result.notes
