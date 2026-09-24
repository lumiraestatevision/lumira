"""Fallback für Räume ohne verwertbare Beschriftung: Klassifikation nach Geometrie.

STUB: Das Modell wird beim Import auf einer kleinen, SYNTHETISCHEN Tabelle typischer
Raumgrößen im deutschen Wohnungsbau trainiert (Fläche, Seitenverhältnis). Ersetzen durch
ein Modell, das auf echten, gelabelten Grundrissen trainiert ist (Features u. a. Anzahl
Türen, Nachbarräume, Fenster, Sanitärsymbole).
"""

from __future__ import annotations

import functools

import numpy as np
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from lumira_shared.models import Room, RoomType

# (Fläche m², Seitenverhältnis lang/kurz, Raumtyp)
_TRAINING: tuple[tuple[float, float, RoomType], ...] = (
    (1.5, 1.5, RoomType.WC),
    (2.0, 1.8, RoomType.WC),
    (2.5, 2.0, RoomType.WC),
    (1.0, 1.2, RoomType.STORAGE),
    (2.0, 1.1, RoomType.STORAGE),
    (3.0, 1.3, RoomType.STORAGE),
    (5.0, 1.3, RoomType.BATHROOM),
    (6.5, 1.5, RoomType.BATHROOM),
    (8.0, 1.2, RoomType.BATHROOM),
    (6.0, 3.5, RoomType.HALLWAY),
    (8.0, 4.0, RoomType.HALLWAY),
    (10.0, 3.0, RoomType.HALLWAY),
    (12.0, 5.0, RoomType.HALLWAY),
    (9.0, 1.4, RoomType.KITCHEN),
    (11.0, 1.6, RoomType.KITCHEN),
    (13.0, 1.2, RoomType.BEDROOM),
    (15.0, 1.3, RoomType.BEDROOM),
    (18.0, 1.2, RoomType.BEDROOM),
    (25.0, 1.3, RoomType.LIVING),
    (32.0, 1.4, RoomType.LIVING),
    (40.0, 1.2, RoomType.LIVING),
)

GEOMETRY_CONFIDENCE = 0.3


def features(room: Room) -> tuple[float, float]:
    xs = [p.x for p in room.polygon]
    ys = [p.y for p in room.polygon]
    short, long = sorted((max(xs) - min(xs), max(ys) - min(ys)))
    return room.area_m2, (long / short) if short > 0 else 1.0


@functools.cache
def _model() -> Pipeline:
    # Nächster typischer Raum nach skalierten Merkmalen – bei so wenigen Referenzpunkten
    # robuster und nachvollziehbarer als ein Entscheidungsbaum.
    x = np.array([(area, aspect) for area, aspect, _ in _TRAINING])
    y = np.array([str(t) for *_, t in _TRAINING])
    return make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=1)).fit(x, y)


def classify_geometry(room: Room) -> RoomType:
    prediction = _model().predict(np.array([features(room)]))[0]
    return RoomType(str(prediction))
