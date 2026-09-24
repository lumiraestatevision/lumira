from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from lumira_backend.db import ProjectStatus


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    type: str
    producer: str
    occurred_at: datetime


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: ProjectStatus
    current_step: str | None
    artifacts: dict[str, str]
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectRead):
    events: list[EventRead]
