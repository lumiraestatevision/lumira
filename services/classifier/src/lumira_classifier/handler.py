"""plan.recognized → rooms.classified"""

from __future__ import annotations

from collections import Counter

from lumira_classifier.logic.geometry import GEOMETRY_CONFIDENCE, classify_geometry
from lumira_classifier.logic.labels_de import classify_label, parse_area_m2
from lumira_shared import Artifact, Event, EventType, ServiceContext, artifact_key
from lumira_shared.models import FloorPlan, RoomType

STEP = "classifier"
LABEL_CONFIDENCE = 0.9


def classify(plan: FloorPlan) -> FloorPlan:
    for room in plan.rooms:
        by_label = classify_label(room.label)
        if by_label is not None:
            room.room_type, room.confidence = by_label, LABEL_CONFIDENCE
        else:
            room.room_type, room.confidence = classify_geometry(room), GEOMETRY_CONFIDENCE
        room.label_area_m2 = parse_area_m2(room.label) or room.label_area_m2
    return plan


async def handle_plan_recognized(event: Event, ctx: ServiceContext) -> Event:
    plan = classify(
        await ctx.storage.get_model(event.artifacts[Artifact.RECOGNIZED_PLAN], FloorPlan)
    )
    key = artifact_key(event.project_id, STEP, "floor_plan.json")
    await ctx.storage.put_json(key, plan)

    counts = Counter(str(r.room_type) for r in plan.rooms)
    unknown = counts.get(str(RoomType.UNKNOWN), 0)
    ctx.log.info("rooms.classified", room_types=dict(counts), unknown=unknown)
    return event.follow_up(
        EventType.ROOMS_CLASSIFIED,
        producer=ctx.settings.service_name,
        artifacts={Artifact.CLASSIFIED_PLAN: key},
        data={"room_types": dict(counts)},
    )
