"""Deutsche Raumbeschriftungen → RoomType.

Die Beschriftung wird normalisiert (Kleinschreibung, Umlaute → ae/oe/ue, ß → ss) und in
Wörter zerlegt. Das erste Wort, das eine Regel trifft, bestimmt den Raumtyp:
„Wohnen/Essen“ → LIVING, „Küche/Essen“ → KITCHEN.
"""

from __future__ import annotations

import re

from lumira_shared.models import RoomType

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Kurze Abkürzungen nur als ganzes Wort (sonst träfe "ar" auch "arbeiten").
_EXACT: dict[str, RoomType] = {
    "wc": RoomType.WC,
    "bad": RoomType.BATHROOM,
    "ar": RoomType.STORAGE,
    "abst": RoomType.STORAGE,
    "hwr": RoomType.UTILITY,
    "hw": RoomType.UTILITY,
    "th": RoomType.STAIRCASE,
    "gang": RoomType.HALLWAY,
    "kind": RoomType.CHILD,
    "kiz": RoomType.CHILD,
    "sz": RoomType.BEDROOM,
    "wz": RoomType.LIVING,
}

# Präfixe in Prüfreihenfolge – spezifische vor allgemeinen ("wohnkueche" vor "wohn").
_PREFIXES: tuple[tuple[str, RoomType], ...] = (
    ("wohnkueche", RoomType.KITCHEN),
    ("toilette", RoomType.WC),
    ("badezimmer", RoomType.BATHROOM),
    ("duschbad", RoomType.BATHROOM),
    ("dusche", RoomType.BATHROOM),
    ("wannenbad", RoomType.BATHROOM),
    ("kueche", RoomType.KITCHEN),
    ("koch", RoomType.KITCHEN),
    ("pantry", RoomType.KITCHEN),
    ("esszimmer", RoomType.DINING),
    ("essplatz", RoomType.DINING),
    ("essen", RoomType.DINING),
    ("speisekammer", RoomType.STORAGE),
    ("speise", RoomType.DINING),
    ("wohn", RoomType.LIVING),
    ("schlaf", RoomType.BEDROOM),
    ("eltern", RoomType.BEDROOM),
    ("gaeste", RoomType.BEDROOM),
    ("gast", RoomType.BEDROOM),  # „Gast“ = Gästezimmer (Gäste-WC ist oben schon WC)
    ("kinder", RoomType.CHILD),
    ("arbeit", RoomType.OFFICE),
    ("buero", RoomType.OFFICE),
    ("office", RoomType.OFFICE),
    ("studio", RoomType.OFFICE),
    ("flur", RoomType.HALLWAY),
    ("diele", RoomType.HALLWAY),
    ("eingang", RoomType.HALLWAY),
    ("windfang", RoomType.HALLWAY),
    ("korridor", RoomType.HALLWAY),
    ("abstell", RoomType.STORAGE),
    ("lager", RoomType.STORAGE),
    ("vorrat", RoomType.STORAGE),
    ("keller", RoomType.STORAGE),
    ("hauswirtschaft", RoomType.UTILITY),
    ("technik", RoomType.UTILITY),
    ("heizung", RoomType.UTILITY),
    ("hausanschluss", RoomType.UTILITY),
    ("wasch", RoomType.UTILITY),
    ("balkon", RoomType.BALCONY),
    ("loggia", RoomType.BALCONY),
    ("terrasse", RoomType.BALCONY),
    ("dachterrasse", RoomType.BALCONY),
    ("treppe", RoomType.STAIRCASE),
)

_AREA = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:m²|m2|qm)", re.IGNORECASE)


def normalize(label: str) -> str:
    text = label.lower().translate(_UMLAUTS)
    return re.sub(r"gaeste[\s-]*wc", "wc", text)  # "Gäste-WC" ist ein WC, kein Gästezimmer


def classify_label(label: str | None) -> RoomType | None:
    if not label:
        return None
    for token in re.split(r"[^a-z]+", normalize(label)):
        if not token:
            continue
        if token in _EXACT:
            return _EXACT[token]
        for prefix, room_type in _PREFIXES:
            if token.startswith(prefix):
                return room_type
    return None


def parse_area_m2(label: str | None) -> float | None:
    """„Bad 6,85 m²“ → 6.85"""
    if not label:
        return None
    match = _AREA.search(label)
    return float(match.group(1).replace(",", ".")) if match else None
