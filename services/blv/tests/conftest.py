from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lumira_blv.logic.extraction import ExtractedMaterial
from lumira_shared.models import MaterialCategory, MaterialLocation

MaterialFactory = Callable[..., ExtractedMaterial]


@pytest.fixture
def make_material() -> MaterialFactory:
    """ExtractedMaterial mit sinnvollen Standardwerten; einzelne Felder per Keyword ändern."""

    def build(id_: str, **overrides: Any) -> ExtractedMaterial:
        values: dict[str, Any] = {
            "id": id_,
            "category": MaterialCategory.FLOORING,
            "name": "Parkett",
            "manufacturer": None,
            "product": None,
            "color": None,
            "color_hex": None,
            "finish": None,
            "format": None,
            "room_types": [],
            "location": MaterialLocation.INTERIOR,
            "is_final_surface": True,
            "blv_position": None,
            "source_excerpt": None,
        }
        return ExtractedMaterial(**(values | overrides))

    return build
