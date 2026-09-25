from __future__ import annotations

import pytest

from lumira_classifier.logic.labels_de import classify_label, parse_area_m2
from lumira_shared.models import RoomType as R


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Wohnen/Essen 36,00 m²", R.LIVING),
        ("Wohnzimmer", R.LIVING),
        ("WZ", R.LIVING),
        ("Küche", R.KITCHEN),
        ("KÜCHE 9,20 m2", R.KITCHEN),
        ("Wohnküche", R.KITCHEN),
        ("Küche/Essen", R.KITCHEN),
        ("Kochen", R.KITCHEN),
        ("Essen", R.DINING),
        ("Esszimmer", R.DINING),
        ("Schlafen", R.BEDROOM),
        ("Schlafzimmer", R.BEDROOM),
        ("Eltern", R.BEDROOM),
        ("Gästezimmer", R.BEDROOM),
        ("Kind 1", R.CHILD),
        ("Kinderzimmer", R.CHILD),
        ("Arbeiten", R.OFFICE),
        ("Büro", R.OFFICE),
        ("Bad", R.BATHROOM),
        ("Badezimmer", R.BATHROOM),
        ("Duschbad", R.BATHROOM),
        ("WC", R.WC),
        ("Gäste-WC", R.WC),
        ("G-WC F:4,46 m2", R.WC),  # Beschriftungen aus einem echten CAD-Plan
        ("Gast F:14,58 m2", R.BEDROOM),
        ("Wohnen /Essen F: 38,37 m2", R.LIVING),
        ("Gäste WC 2,1 m²", R.WC),
        ("Flur", R.HALLWAY),
        ("Diele", R.HALLWAY),
        ("Windfang", R.HALLWAY),
        ("AR", R.STORAGE),
        ("Abstellraum", R.STORAGE),
        ("Speisekammer", R.STORAGE),
        ("HWR", R.UTILITY),
        ("Hauswirtschaftsraum", R.UTILITY),
        ("Technik", R.UTILITY),
        ("Balkon", R.BALCONY),
        ("Loggia", R.BALCONY),
        ("Terrasse", R.BALCONY),
        ("Treppenhaus", R.STAIRCASE),
        ("TH", R.STAIRCASE),
    ],
)
def test_german_labels(label: str, expected: R) -> None:
    assert classify_label(label) is expected


@pytest.mark.parametrize("label", [None, "", "Raum 1", "12,5 m²", "Ansicht Nord"])
def test_unknown_labels(label: str | None) -> None:
    assert classify_label(label) is None


@pytest.mark.parametrize(
    ("label", "area"),
    [
        ("Bad 6,85 m²", 6.85),
        ("Wohnen 32.5 m2", 32.5),
        ("Küche 9 qm", 9.0),
        ("Flur", None),
        (None, None),
    ],
)
def test_area_from_label(label: str | None, area: float | None) -> None:
    assert parse_area_m2(label) == area
