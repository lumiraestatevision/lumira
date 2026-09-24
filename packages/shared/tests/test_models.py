from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from lumira_shared.models import (
    BLVResult,
    EquipmentVariant,
    FloorPlan,
    Material,
    MaterialCategory,
    Opening,
    OpeningType,
    Point2D,
    Room,
    RoomType,
    Wall,
)


def test_wall_length_is_computed() -> None:
    wall = Wall(start=Point2D(x=0, y=0), end=Point2D(x=3000, y=4000), thickness_mm=175)
    assert wall.length_mm == pytest.approx(5000)
    assert wall.id.startswith("wall_")


def test_degenerate_wall_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Länge 0"):
        Wall(start=Point2D(x=1, y=1), end=Point2D(x=1, y=1), thickness_mm=175)


def test_room_area_and_closed_polygon_normalisation() -> None:
    square = [
        Point2D(x=0, y=0),
        Point2D(x=2000, y=0),
        Point2D(x=2000, y=2000),
        Point2D(x=0, y=2000),
    ]
    room = Room(polygon=[*square, square[0]])
    assert len(room.polygon) == 4
    assert room.area_m2 == 4.0


def test_room_needs_three_points() -> None:
    with pytest.raises(ValidationError, match="mindestens 3"):
        Room(polygon=[Point2D(x=0, y=0), Point2D(x=1, y=1)])


def test_floor_plan_roundtrip(floor_plan: FloorPlan) -> None:
    restored = FloorPlan.model_validate_json(floor_plan.model_dump_json())
    assert restored == floor_plan
    assert restored.total_area_m2 == 12.0
    assert restored.openings_in("wall_0")[0].type is OpeningType.DOOR


def test_floor_plan_rejects_dangling_wall_reference(floor_plan: FloorPlan) -> None:
    data = floor_plan.model_dump()
    data["openings"].append(
        Opening(
            type=OpeningType.WINDOW,
            wall_id="wall_missing",
            offset_mm=0,
            width_mm=1000,
            height_mm=1200,
        ).model_dump()
    )
    with pytest.raises(ValidationError, match="wall_missing"):
        FloorPlan.model_validate(data)


def test_floor_plan_rejects_duplicate_ids(floor_plan: FloorPlan) -> None:
    data = floor_plan.model_dump()
    data["walls"].append(data["walls"][0])
    with pytest.raises(ValidationError, match="Doppelte IDs"):
        FloorPlan.model_validate(data)


def test_unknown_fields_are_ignored(floor_plan: FloorPlan) -> None:
    data = floor_plan.model_dump(mode="json")
    data["added_by_newer_service"] = True
    assert FloorPlan.model_validate(data) == floor_plan


def _blv(project_id: UUID, variants: list[EquipmentVariant]) -> BLVResult:
    return BLVResult(
        project_id=project_id,
        materials=[
            Material(id="parkett", category=MaterialCategory.FLOORING, name="Eiche Parkett"),
            Material(
                id="fliese",
                category=MaterialCategory.TILES,
                name="Feinsteinzeug 60x60",
                room_types=[RoomType.BATHROOM],
            ),
            Material(
                id="naturstein",
                category=MaterialCategory.TILES,
                name="Naturstein",
                room_types=[RoomType.BATHROOM],
            ),
        ],
        variants=variants,
    )


def test_blv_first_variant_becomes_default(project_id: UUID) -> None:
    result = _blv(
        project_id,
        [
            EquipmentVariant(name="Standard", material_ids=["parkett", "fliese"]),
            EquipmentVariant(name="Premium", material_ids=["parkett", "naturstein"]),
        ],
    )
    assert result.default_variant is not None
    assert result.default_variant.name == "Standard"
    # Parkett hat keine Raumeinschränkung und gilt daher überall.
    assert [m.id for m in result.materials_for(room_type=RoomType.BATHROOM)] == [
        "parkett",
        "fliese",
    ]
    assert [m.id for m in result.materials_for(variant="Premium", room_type=RoomType.BATHROOM)] == [
        "parkett",
        "naturstein",
    ]
    assert [m.id for m in result.materials_for(variant="Premium", room_type=RoomType.LIVING)] == [
        "parkett"
    ]


def test_blv_rejects_unknown_material_reference(project_id: UUID) -> None:
    with pytest.raises(ValidationError, match="unbekannte Materialien"):
        _blv(project_id, [EquipmentVariant(name="Standard", material_ids=["gibt_es_nicht"])])


def test_blv_rejects_two_defaults(project_id: UUID) -> None:
    with pytest.raises(ValidationError, match="Mehr als eine Standardvariante"):
        _blv(
            project_id,
            [
                EquipmentVariant(name="A", is_default=True),
                EquipmentVariant(name="B", is_default=True),
            ],
        )


def test_material_color_hex_is_validated() -> None:
    with pytest.raises(ValidationError):
        Material(category=MaterialCategory.WALL_FINISH, name="Weiß", color_hex="white")
