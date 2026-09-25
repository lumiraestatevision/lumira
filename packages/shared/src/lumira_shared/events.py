"""Event-Definitionen der Lumira-Pipeline.

Eventkette:
    project.created → plan.parsed → plan.recognized → rooms.classified → blv.processed
    → model.generated → vr.exported → project.completed      (+ step.failed bei Fehlern)

Events transportieren keine großen Daten, sondern S3-Keys (Claim-Check-Pattern) im
Feld ``artifacts``. Jedes Folgeevent übernimmt die Artefakte seines Vorgängers, sodass
z. B. der generator sowohl den klassifizierten Grundriss als auch das BLV-Ergebnis findet.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from lumira_shared.models.base import LumiraModel

STREAM_PREFIX = "lumira:events"
DEAD_LETTER_STREAM = "lumira:dead-letter"


class EventType(StrEnum):
    PROJECT_CREATED = "project.created"
    PLAN_PARSED = "plan.parsed"
    PLAN_RECOGNIZED = "plan.recognized"
    ROOMS_CLASSIFIED = "rooms.classified"
    BLV_PROCESSED = "blv.processed"
    MODEL_GENERATED = "model.generated"
    VR_EXPORTED = "vr.exported"
    PROJECT_COMPLETED = "project.completed"
    STEP_FAILED = "step.failed"


PIPELINE: tuple[EventType, ...] = (
    EventType.PROJECT_CREATED,
    EventType.PLAN_PARSED,
    EventType.PLAN_RECOGNIZED,
    EventType.ROOMS_CLASSIFIED,
    EventType.BLV_PROCESSED,
    EventType.MODEL_GENERATED,
    EventType.VR_EXPORTED,
    EventType.PROJECT_COMPLETED,
)


class Artifact(StrEnum):
    """Wohlbekannte Namen für S3-Artefakte im Feld ``Event.artifacts``."""

    FLOOR_PLAN_SOURCE = "floor_plan_source"  # hochgeladener Grundriss (PDF/DWG/DXF)
    BLV_SOURCE = "blv_source"  # hochgeladenes Leistungsverzeichnis (PDF), optional
    REFERENCE_IMAGES = "reference_images"  # S3-Präfix mit Beispiel-/Bestandsfotos, optional
    PARSED_PLAN = "parsed_plan"  # ParsedPlan: Rohgeometrie + Texte (JSON)
    PLAN_PAGE_IMAGE = "plan_page_image"  # gerenderte Planseite (PNG), nur bei PDF
    RECOGNIZED_PLAN = "recognized_plan"  # FloorPlan mit Wänden/Öffnungen/Räumen
    CLASSIFIED_PLAN = "classified_plan"  # FloorPlan mit Raumtypen
    BLV_RESULT = "blv_result"  # BLVResult (JSON)
    MODEL_FBX = "model_fbx"
    MODEL_GLTF = "model_gltf"
    VR_PACKAGE = "vr_package"  # Manifest des VR-Exports


REQUIRED_ARTIFACTS: Mapping[EventType, frozenset[Artifact]] = {
    EventType.PROJECT_CREATED: frozenset({Artifact.FLOOR_PLAN_SOURCE}),
    EventType.PLAN_PARSED: frozenset({Artifact.FLOOR_PLAN_SOURCE, Artifact.PARSED_PLAN}),
    EventType.PLAN_RECOGNIZED: frozenset({Artifact.RECOGNIZED_PLAN}),
    EventType.ROOMS_CLASSIFIED: frozenset({Artifact.CLASSIFIED_PLAN}),
    EventType.BLV_PROCESSED: frozenset({Artifact.CLASSIFIED_PLAN, Artifact.BLV_RESULT}),
    EventType.MODEL_GENERATED: frozenset({Artifact.MODEL_FBX, Artifact.MODEL_GLTF}),
    EventType.VR_EXPORTED: frozenset({Artifact.VR_PACKAGE}),
    EventType.PROJECT_COMPLETED: frozenset(),
    EventType.STEP_FAILED: frozenset(),
}


def stream_name(event_type: EventType, prefix: str = STREAM_PREFIX) -> str:
    """Ein Redis Stream pro Eventtyp, z. B. ``lumira:events:plan.parsed``."""
    return f"{prefix}:{event_type}"


class ErrorInfo(LumiraModel):
    step: str = Field(description="Service, in dem der Fehler auftrat")
    failed_event_type: EventType
    failed_event_id: UUID
    error_type: str
    message: str
    attempts: int = Field(ge=1)
    retryable: bool


class Event(LumiraModel):
    event_id: UUID = Field(default_factory=uuid4)
    type: EventType
    project_id: UUID
    producer: str
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    causation_id: UUID | None = Field(default=None, description="event_id des auslösenden Events")
    run_id: UUID | None = Field(
        default=None,
        description="Pipeline-Durchlauf = event_id seines project.created; wird an alle Folgeevents "
        "weitergegeben. Das backend verwirft Events früherer Durchläufe (Neu berechnen).",
    )
    schema_version: Literal[1] = 1
    artifacts: dict[str, str] = Field(default_factory=dict)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def _check_contract(self) -> Self:
        missing = sorted(REQUIRED_ARTIFACTS[self.type] - self.artifacts.keys())
        if missing:
            raise ValueError(f"{self.type} benötigt die Artefakte {missing}")
        if (self.type is EventType.STEP_FAILED) != (self.error is not None):
            raise ValueError("Das Feld 'error' ist genau bei step.failed Pflicht")
        return self

    @property
    def stream(self) -> str:
        return stream_name(self.type)

    def follow_up(
        self,
        event_type: EventType,
        *,
        producer: str,
        artifacts: Mapping[str, str] | None = None,
        data: Mapping[str, JsonValue] | None = None,
    ) -> Event:
        """Folgeevent: übernimmt Projekt und Artefakte, verweist per causation_id auf dieses Event."""
        return Event(
            type=event_type,
            project_id=self.project_id,
            producer=producer,
            causation_id=self.event_id,
            run_id=self.run_id,
            artifacts={**self.artifacts, **(artifacts or {})},
            data=dict(data or {}),
        )

    def failed(
        self,
        *,
        producer: str,
        error_type: str,
        message: str,
        attempts: int,
        retryable: bool,
    ) -> Event:
        return Event(
            type=EventType.STEP_FAILED,
            project_id=self.project_id,
            producer=producer,
            causation_id=self.event_id,
            run_id=self.run_id,
            artifacts=dict(self.artifacts),
            error=ErrorInfo(
                step=producer,
                failed_event_type=self.type,
                failed_event_id=self.event_id,
                error_type=error_type,
                message=message,
                attempts=attempts,
                retryable=retryable,
            ),
        )

    def to_stream_fields(self) -> dict[str, str]:
        return {"type": str(self.type), "event": self.model_dump_json()}

    @classmethod
    def from_stream_fields(cls, fields: Mapping[str, str]) -> Event:
        return cls.model_validate_json(fields["event"])


def project_created(
    project_id: UUID,
    *,
    floor_plan_key: str,
    blv_key: str | None = None,
    reference_images_prefix: str | None = None,
    producer: str = "backend",
) -> Event:
    """Startevent der Pipeline, publiziert vom backend nach dem Upload."""
    artifacts = {Artifact.FLOOR_PLAN_SOURCE: floor_plan_key}
    if blv_key:
        artifacts[Artifact.BLV_SOURCE] = blv_key
    if reference_images_prefix:
        artifacts[Artifact.REFERENCE_IMAGES] = reference_images_prefix
    event_id = uuid4()
    return Event(
        event_id=event_id,
        run_id=event_id,  # dieses Event eröffnet einen neuen Durchlauf
        type=EventType.PROJECT_CREATED,
        project_id=project_id,
        producer=producer,
        artifacts={str(k): v for k, v in artifacts.items()},
    )
