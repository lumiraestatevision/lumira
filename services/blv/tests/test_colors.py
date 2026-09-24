from __future__ import annotations

import pytest

from lumira_blv.logic.colors import CATEGORY_DEFAULTS, resolve_color
from lumira_shared.models import ColorSource, MaterialCategory

C = MaterialCategory


@pytest.mark.parametrize(
    ("category", "name", "color", "color_hex", "expected"),
    [
        # 1. RAL schlägt alles – auch einen abweichenden LLM-Hexwert
        (
            C.WINDOW,
            "Kunststofffenster außen",
            "Anthrazit RAL 7016",
            "#708090",
            ("#383E42", ColorSource.RAL),
        ),
        # 2. eindeutige Farbangabe aus dem Dokument
        (C.SANITARY, "Waschtisch", "Weiß Alpin", "#ffffff", ("#FFFFFF", ColorSource.DOCUMENT)),
        # 3. Farbwort – spezifisch vor allgemein
        (C.TILES, "Bodenfliesen", "dunkelgrau", None, ("#5E6163", ColorSource.ASSUMED)),
        (C.TILES, "Fliesen anthrazit 60x60", None, None, ("#383E42", ColorSource.ASSUMED)),
        (C.DOOR, "Innentür", "Cremeweiß", None, ("#E9E0D2", ColorSource.ASSUMED)),
        # 4. Materialwort (auch mit Umlauten und in zusammengesetzten Wörtern)
        (
            C.FLOORING,
            "Eichenparkett mit Sockelleiste",
            None,
            None,
            ("#B8894F", ColorSource.ASSUMED),
        ),
        (C.FLOORING, "Klickvinyl", None, None, ("#A88B66", ColorSource.ASSUMED)),
        (C.TILES, "Bodenfliesen Feinsteinzeug", None, None, ("#BDBAB3", ColorSource.ASSUMED)),
        (C.WALL_FINISH, "Nadelholzschalung Lärche", None, None, ("#C79A63", ColorSource.ASSUMED)),
        (C.WALL_FINISH, "Maschinengipsputz", None, None, ("#F2F1EC", ColorSource.ASSUMED)),
        # 5. nur die Kategorie ist bekannt
        (
            C.SANITARY,
            "Tiefspül-WC",
            None,
            None,
            (CATEGORY_DEFAULTS[C.SANITARY], ColorSource.ASSUMED),
        ),
    ],
)
def test_resolve_color(
    category: MaterialCategory,
    name: str,
    color: str | None,
    color_hex: str | None,
    expected: tuple[str, ColorSource],
) -> None:
    assert resolve_color(category=category, name=name, color=color, color_hex=color_hex) == expected


def test_invalid_llm_hex_falls_back_to_assumption() -> None:
    assert resolve_color(
        category=C.FLOORING, name="Parkett Eiche", color="natur", color_hex="braun"
    ) == (
        "#B8894F",
        ColorSource.ASSUMED,
    )


def test_every_category_has_a_default() -> None:
    assert set(CATEGORY_DEFAULTS) == set(MaterialCategory)
