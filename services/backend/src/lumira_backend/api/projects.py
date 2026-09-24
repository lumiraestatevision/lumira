"""Projekt-API: Upload, Liste, Status, Artefakt-Download."""

from __future__ import annotations

import uuid
from pathlib import PurePath
from typing import Annotated, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from lumira_backend.api.schemas import ProjectDetail, ProjectRead
from lumira_backend.config import BackendSettings
from lumira_backend.db import Database, Project, ProjectStatus
from lumira_shared import (
    ObjectNotFoundError,
    ServiceContext,
    artifact_key,
    get_context,
    project_created,
)
from lumira_shared.storage import guess_content_type

router = APIRouter(prefix="/projects", tags=["projects"])

PLAN_EXTENSIONS = {".pdf", ".dxf", ".dwg"}
BLV_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

Ctx = Annotated[ServiceContext, Depends(get_context)]


def _db(request: Request) -> Database:
    return request.app.state.db


Db = Annotated[Database, Depends(_db)]


def _suffix(upload: UploadFile, allowed: set[str], field: str) -> str:
    suffix = PurePath(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{field}: Dateityp '{suffix or '?'}' nicht erlaubt, erlaubt sind {sorted(allowed)}",
        )
    return suffix


async def _read_limited(upload: UploadFile, limit: int, field: str) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"{field}: größer als {limit // (1024 * 1024)} MB"
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: Datei ist leer")
    return data


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    ctx: Ctx,
    db: Db,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    floor_plan: Annotated[UploadFile, File(description="Grundriss (PDF, DXF oder DWG)")],
    blv: Annotated[UploadFile | None, File(description="Leistungsverzeichnis (PDF)")] = None,
    images: Annotated[list[UploadFile] | None, File(description="Beispiel-/Bestandsfotos")] = None,
) -> ProjectRead:
    settings = cast(BackendSettings, ctx.settings)
    limit = settings.max_upload_bytes
    project_id = uuid.uuid4()

    plan_suffix = _suffix(floor_plan, PLAN_EXTENSIONS, "floor_plan")
    plan_key = artifact_key(project_id, "upload", f"floor_plan{plan_suffix}")
    await ctx.storage.put_bytes(plan_key, await _read_limited(floor_plan, limit, "floor_plan"))

    blv_key: str | None = None
    if blv is not None and blv.filename:
        _suffix(blv, BLV_EXTENSIONS, "blv")
        blv_key = artifact_key(project_id, "upload", "blv.pdf")
        await ctx.storage.put_bytes(blv_key, await _read_limited(blv, limit, "blv"))

    images_prefix: str | None = None
    for index, image in enumerate(images or []):
        suffix = _suffix(image, IMAGE_EXTENSIONS, "images")
        images_prefix = artifact_key(project_id, "upload", "images/")
        await ctx.storage.put_bytes(
            f"{images_prefix}{index:03d}{suffix}", await _read_limited(image, limit, "images")
        )

    event = project_created(
        project_id,
        floor_plan_key=plan_key,
        blv_key=blv_key,
        reference_images_prefix=images_prefix,
        producer=settings.service_name,
    )
    async with db.sessionmaker() as session, session.begin():
        project = Project(
            id=project_id, name=name, status=ProjectStatus.PROCESSING, artifacts=event.artifacts
        )
        session.add(project)

    try:
        await ctx.publisher.publish(event)
    except Exception as exc:
        async with db.sessionmaker() as session, session.begin():
            failed = await session.get_one(Project, project_id)
            failed.status = ProjectStatus.FAILED
            failed.error = {"step": "backend", "message": "Event-Bus nicht erreichbar"}
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Event-Bus nicht erreichbar"
        ) from exc

    async with db.sessionmaker() as session:
        return ProjectRead.model_validate(await session.get_one(Project, project_id))


@router.get("")
async def list_projects(db: Db, limit: int = 50) -> list[ProjectRead]:
    async with db.sessionmaker() as session:
        rows = await session.scalars(
            select(Project).order_by(Project.created_at.desc()).limit(min(limit, 200))
        )
        return [ProjectRead.model_validate(p) for p in rows]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, db: Db) -> ProjectDetail:
    async with db.sessionmaker() as session:
        project = await session.scalar(
            select(Project).where(Project.id == project_id).options(selectinload(Project.events))
        )
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
        return ProjectDetail.model_validate(project)


@router.get("/{project_id}/artifacts/{name}")
async def get_artifact(project_id: uuid.UUID, name: str, ctx: Ctx, db: Db) -> Response:
    async with db.sessionmaker() as session:
        project = await session.get(Project, project_id)
    if project is None or name not in project.artifacts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht gefunden")
    key = project.artifacts[name]
    try:
        data = await ctx.storage.get_bytes(key)
    except ObjectNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht im Speicher") from exc
    filename = PurePath(key).name
    return Response(
        content=data,
        media_type=guess_content_type(key),
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
