"""Pipeline-Status: Das backend liest ALLE Events mit und führt den Projektstatus in der DB.

Außerdem schließt es die Kette ab: Nach vr.exported (oder nach model.generated, wenn
PIPELINE_VR_ENABLED=false) publiziert es project.completed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lumira_backend.config import BackendSettings
from lumira_backend.db import Database, Project, ProjectEvent, ProjectStatus
from lumira_shared import Event, EventType, ServiceContext
from lumira_shared.service import ContextHandler


def build_handlers(db: Database, settings: BackendSettings) -> dict[EventType, ContextHandler]:
    completion_trigger = (
        EventType.VR_EXPORTED if settings.pipeline_vr_enabled else EventType.MODEL_GENERATED
    )

    async def record(event: Event, ctx: ServiceContext) -> Event | None:
        should_complete = False
        try:
            async with db.sessionmaker() as session, session.begin():
                project = await session.get(Project, event.project_id)
                if project is None:
                    ctx.log.warning("orchestrator.unknown_project")
                    return None
                if project.run_id and event.run_id and event.run_id != project.run_id:
                    # Verspätetes Event eines früheren Durchlaufs (Projekt wurde neu berechnet)
                    ctx.log.info("orchestrator.stale_run", run_id=str(event.run_id))
                    return None

                already_seen = await session.scalar(
                    select(ProjectEvent.id).where(ProjectEvent.event_id == event.event_id)
                )
                if already_seen is not None:
                    ctx.log.info("orchestrator.duplicate_event")  # at-least-once → idempotent
                    return None

                session.add(
                    ProjectEvent(
                        event_id=event.event_id,
                        project_id=event.project_id,
                        type=str(event.type),
                        producer=event.producer,
                        occurred_at=event.occurred_at,
                        payload=event.model_dump(mode="json"),
                    )
                )
                project.artifacts = {**project.artifacts, **event.artifacts}
                if event.type is not EventType.STEP_FAILED:
                    project.current_step = str(event.type)

                if event.type is EventType.STEP_FAILED and event.error is not None:
                    project.status = ProjectStatus.FAILED
                    project.error = event.error.model_dump(mode="json")
                elif event.type is EventType.PROJECT_COMPLETED:
                    project.status = ProjectStatus.COMPLETED

                should_complete = (
                    event.type is completion_trigger and project.status is ProjectStatus.PROCESSING
                )
        except IntegrityError:
            # Paralleler Consumer hat dasselbe Event gerade gespeichert.
            ctx.log.info("orchestrator.duplicate_event_race")
            return None

        if should_complete:
            return event.follow_up(EventType.PROJECT_COMPLETED, producer=ctx.settings.service_name)
        return None

    return dict.fromkeys(EventType, record)
