"""Kunden-Link: öffentlich lesbar über einen geheimen Schlüssel (/share/<token>).

Nur Name, Status und das 3D-Modell – keine Projekt-ID, keine Uploads, kein Verlauf, keine
Aktionen. In der Demo liegt nur dieser Pfad außerhalb des Passwortschutzes (Caddyfile).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from lumira_backend.api.projects import Ctx, Db, artifact_response
from lumira_backend.api.schemas import SharedProject
from lumira_backend.db import Project, ProjectStatus
from lumira_shared import Artifact

router = APIRouter(prefix="/share", tags=["share"])


async def _shared(db: Db, token: str) -> Project:
    async with db.sessionmaker() as session:
        project = await session.scalar(select(Project).where(Project.share_token == token))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link ungültig oder widerrufen")
    return project


@router.get("/{token}")
async def get_shared(token: str, db: Db) -> SharedProject:
    project = await _shared(db, token)
    return SharedProject(
        name=project.name,
        status=project.status,
        has_model=project.status is ProjectStatus.COMPLETED
        and Artifact.MODEL_GLTF in project.artifacts,
        updated_at=project.updated_at,
    )


@router.get("/{token}/model")
async def get_shared_model(token: str, ctx: Ctx, db: Db) -> Response:
    project = await _shared(db, token)
    key = project.artifacts.get(Artifact.MODEL_GLTF)
    if project.status is not ProjectStatus.COMPLETED or key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Noch kein 3D-Modell")
    return await artifact_response(ctx, key)
