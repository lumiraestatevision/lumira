from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from lumira_shared.models import FloorPlan
from lumira_shared.storage import ObjectNotFoundError, S3Storage, artifact_key, guess_content_type


def test_artifact_key_is_deterministic(project_id: UUID) -> None:
    assert (
        artifact_key(project_id, "parser", "/parsed.json")
        == f"projects/{project_id}/parser/parsed.json"
    )


def test_content_types() -> None:
    assert guess_content_type("model.glb") == "model/gltf-binary"
    assert guess_content_type("plan.pdf") == "application/pdf"
    assert guess_content_type("x.unknownext") == "application/octet-stream"


async def test_model_roundtrip(storage: S3Storage, floor_plan: FloorPlan) -> None:
    await storage.ensure_bucket()
    await storage.ensure_bucket()  # idempotent
    key = artifact_key(floor_plan.project_id, "recognizer", "floor_plan.json")

    assert not await storage.exists(key)
    await storage.put_json(key, floor_plan)
    assert await storage.exists(key)
    assert await storage.get_model(key, FloorPlan) == floor_plan


async def test_json_and_bytes(storage: S3Storage) -> None:
    await storage.ensure_bucket()
    await storage.put_json("a.json", {"räume": 3})
    assert await storage.get_json("a.json") == {"räume": 3}
    await storage.put_bytes("b.bin", b"\x00\x01")
    assert await storage.get_bytes("b.bin") == b"\x00\x01"


async def test_delete_prefix_removes_only_that_project(storage: S3Storage) -> None:
    await storage.ensure_bucket()
    for key in ("projects/a/upload/plan.pdf", "projects/a/parser/x.json", "projects/ab/keep.json"):
        await storage.put_bytes(key, b"x")

    assert await storage.delete_prefix("projects/a/") == 2

    assert not await storage.exists("projects/a/upload/plan.pdf")
    assert await storage.exists("projects/ab/keep.json")  # gleicher Anfang, anderes Projekt
    assert await storage.delete_prefix("projects/a/") == 0
    with pytest.raises(ValueError, match="Präfix"):
        await storage.delete_prefix("projects/a")  # ohne "/" träfe es auch projects/ab


async def test_missing_object_raises(storage: S3Storage) -> None:
    await storage.ensure_bucket()
    with pytest.raises(ObjectNotFoundError):
        await storage.get_bytes("gibt/es/nicht.json")


async def test_file_upload_download(storage: S3Storage, tmp_path: Path) -> None:
    await storage.ensure_bucket()
    source = tmp_path / "model.glb"
    source.write_bytes(b"glTF" + bytes(1024))
    await storage.upload_file("projects/p/generator/model.glb", source)

    target = await storage.download_file(
        "projects/p/generator/model.glb", tmp_path / "out" / "model.glb"
    )
    assert target.read_bytes() == source.read_bytes()


def test_endpoint_env_switches_to_minio_mode(
    aws_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    client = S3Storage("lumira")._client
    assert client.meta.endpoint_url == "http://minio:9000"
    assert client.meta.config.s3 == {"addressing_style": "path"}  # pyright: ignore[reportAttributeAccessIssue]


def test_without_endpoint_uses_aws(aws_env: None) -> None:
    storage = S3Storage("lumira")
    assert storage.endpoint_url is None
    assert "amazonaws.com" in storage._client.meta.endpoint_url
