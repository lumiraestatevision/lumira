"""Texterkennung (EasyOCR) für gescannte Pläne ohne Textebene.

Die Modelle (~100 MB) lädt EasyOCR beim ersten Aufruf nach EASYOCR_MODULE_PATH
(Volume ``ml-models``), danach bleiben sie im Cache.
"""

from __future__ import annotations

import functools
from typing import Any

import cv2
import numpy as np

from lumira_recognizer.logic.device import Device
from lumira_shared.models import Point2D, TextItem


@functools.cache
def _reader(languages: tuple[str, ...], device: Device) -> Any:
    import easyocr

    return easyocr.Reader(list(languages), gpu=device == "cuda", verbose=False)


def read_texts(
    png: bytes,
    *,
    page_width_mm: float,
    page_height_mm: float,
    languages: tuple[str, ...],
    device: Device,
    min_confidence: float = 0.4,
) -> list[TextItem]:
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    height_px, width_px = image.shape[:2]
    mm_per_px_x = page_width_mm / width_px
    mm_per_px_y = page_height_mm / height_px

    items: list[TextItem] = []
    for bbox, text, confidence in _reader(languages, device).readtext(image):
        if confidence < min_confidence or not str(text).strip():
            continue
        xs = [float(p[0]) for p in bbox]
        ys = [float(p[1]) for p in bbox]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        items.append(
            TextItem(
                text=str(text).strip(),
                position=Point2D(x=cx * mm_per_px_x, y=(height_px - cy) * mm_per_px_y),
                height_mm=(max(ys) - min(ys)) * mm_per_px_y or None,
            )
        )
    return items
