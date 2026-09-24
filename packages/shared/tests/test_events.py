from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from lumira_shared.events import (
    PIPELINE,
    Artifact,
    Event,
    EventType,
    project_created,
    stream_name,
)


def test_pipeline_order_matches_spec() -> None:
    assert [str(t) for t in PIPELINE] == [
        "project.created",
        "plan.parsed",
        "plan.recognized",
        "rooms.classified",
        "blv.processed",
        "model.generated",
        "vr.exported",
        "project.completed",
    ]


def test_stream_name() -> None:
    assert stream_name(EventType.PLAN_PARSED) == "lumira:events:plan.parsed"


def test_follow_up_carries_artifacts_and_causation(project_id: UUID) -> None:
    created = project_created(
        project_id, floor_plan_key="projects/x/upload/plan.pdf", blv_key="projects/x/upload/blv.pdf"
    )
    parsed = created.follow_up(
        EventType.PLAN_PARSED,
        producer="parser",
        artifacts={Artifact.PARSED_PLAN: "projects/x/parser/parsed.json"},
        data={"pages": 1},
    )
    assert parsed.project_id == project_id
    assert parsed.causation_id == created.event_id
    assert parsed.artifacts == {
        "floor_plan_source": "projects/x/upload/plan.pdf",
        "blv_source": "projects/x/upload/blv.pdf",
        "parsed_plan": "projects/x/parser/parsed.json",
    }
    assert parsed.data == {"pages": 1}


def test_required_artifacts_are_enforced(project_id: UUID) -> None:
    created = project_created(project_id, floor_plan_key="k")
    with pytest.raises(ValidationError, match="parsed_plan"):
        created.follow_up(EventType.PLAN_PARSED, producer="parser")


def test_step_failed_requires_error(project_id: UUID) -> None:
    with pytest.raises(ValidationError, match="error"):
        Event(type=EventType.STEP_FAILED, project_id=project_id, producer="parser")


def test_failed_event(project_id: UUID) -> None:
    created = project_created(project_id, floor_plan_key="k")
    failed = created.failed(
        producer="parser", error_type="ValueError", message="kaputt", attempts=3, retryable=True
    )
    assert failed.type is EventType.STEP_FAILED
    assert failed.error is not None
    assert failed.error.failed_event_id == created.event_id
    assert failed.error.failed_event_type is EventType.PROJECT_CREATED


def test_stream_fields_roundtrip(project_id: UUID) -> None:
    event = project_created(project_id, floor_plan_key="k")
    fields = event.to_stream_fields()
    assert fields["type"] == "project.created"
    assert Event.from_stream_fields(fields) == event
