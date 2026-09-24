"""S3-Zugriff für Zwischenergebnisse (JSON, Bilder, FBX, glTF).

Lokal zeigt ``AWS_ENDPOINT_URL`` auf MinIO (z. B. ``http://minio:9000``). Ist die Variable
gesetzt, nutzt der Client automatisch Path-Style-Adressierung, die MinIO erwartet.
Ohne die Variable spricht derselbe Code mit echtem AWS S3 – kein Codewechsel nötig.

boto3 ist synchron; alle Aufrufe laufen per ``asyncio.to_thread`` außerhalb des Event-Loops.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}
_EXTRA_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",
}


class ObjectNotFoundError(FileNotFoundError):
    """Das angefragte Objekt existiert nicht im Bucket."""


def artifact_key(project_id: UUID | str, step: str, filename: str) -> str:
    """Deterministischer Key, z. B. ``projects/<id>/parser/plan.json``.

    Deterministische Keys machen Handler idempotent: Eine Wiederholung überschreibt
    dasselbe Objekt, statt Dubletten zu erzeugen.
    """
    return f"projects/{project_id}/{step}/{filename.lstrip('/')}"


def guess_content_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _EXTRA_TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"


class S3Storage:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        client: S3Client | None = None,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = (
            endpoint_url
            or os.environ.get("AWS_ENDPOINT_URL_S3")
            or os.environ.get("AWS_ENDPOINT_URL")
        )
        self.region = (
            region
            or os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or "eu-central-1"
        )
        self._client: S3Client = client or self._create_client()

    def _create_client(self) -> S3Client:
        config_kwargs: dict[str, Any] = {
            "retries": {"max_attempts": 5, "mode": "standard"},
        }
        if self.endpoint_url:
            # S3-kompatible Speicher (MinIO): Path-Style statt bucket.host-Subdomains und
            # Prüfsummen nur dort, wo S3 sie verlangt.
            config_kwargs |= {
                "s3": {"addressing_style": "path"},
                "request_checksum_calculation": "when_required",
                "response_checksum_validation": "when_required",
            }
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            config=Config(**config_kwargs),
        )

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    # ------------------------------------------------------------------ Bucket
    async def ensure_bucket(self) -> None:
        """Legt den Bucket an, falls er fehlt (für Tests; lokal übernimmt das der Init-Container)."""

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if _error_code(exc) not in _NOT_FOUND_CODES:
                    raise
                if self.region == "us-east-1":
                    self._client.create_bucket(Bucket=self.bucket)
                else:
                    self._client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={"LocationConstraint": self.region},  # pyright: ignore[reportArgumentType]
                    )

        await asyncio.to_thread(_ensure)

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------ Bytes
    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type or guess_content_type(key),
            Metadata=dict(metadata or {}),
        )
        return key

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                response = self._client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in _NOT_FOUND_CODES:
                    raise ObjectNotFoundError(self.uri(key)) from exc
                raise
            return response["Body"].read()

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    # ------------------------------------------------------------------ JSON / Modelle
    async def put_json(self, key: str, obj: BaseModel | Mapping[str, Any] | list[Any]) -> str:
        if isinstance(obj, BaseModel):
            body = obj.model_dump_json(indent=2).encode()
        else:
            body = json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode()
        return await self.put_bytes(key, body, content_type="application/json")

    async def get_json(self, key: str) -> Any:
        return json.loads(await self.get_bytes(key))

    async def get_model[M: BaseModel](self, key: str, model: type[M]) -> M:
        return model.model_validate_json(await self.get_bytes(key))

    # ------------------------------------------------------------------ Dateien (große Artefakte)
    async def upload_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        """Multipart-Upload, geeignet für große FBX-/glTF-Dateien."""
        await asyncio.to_thread(
            self._client.upload_file,
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type or guess_content_type(path.name)},
        )
        return key

    async def download_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(self._client.download_file, self.bucket, key, str(path))
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(self.uri(key)) from exc
            raise
        return path


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
