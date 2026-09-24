"""model.generated → vr.exported

STUB: Lokal wird kein Unreal Engine gestartet. Der Stub schreibt nur ein Manifest, das
beschreibt, was der echte Export liefern wird. Geplanter Ablauf auf dem GPU-Server:

  1. FBX per Unreal Python API (Interchange) in ein Template-Projekt importieren
  2. Materialien aus dem BLV auf Unreal-Master-Materials abbilden, Beleuchtung (Lumen) bauen
  3. Paketieren für Pixel Streaming bzw. als VR-Build (OpenXR)
  4. Paket + Vorschaubilder nach S3, Manifest mit Download-/Streaming-URL
"""

from __future__ import annotations

from datetime import UTC, datetime

from lumira_shared import Artifact, Event, EventType, ServiceContext, artifact_key

STEP = "unreal"


async def handle_model_generated(event: Event, ctx: ServiceContext) -> Event:
    manifest = {
        "status": "stub",
        "project_id": str(event.project_id),
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "fbx": event.artifacts[Artifact.MODEL_FBX],
            "gltf": event.artifacts[Artifact.MODEL_GLTF],
        },
        "vr": {"engine": "Unreal Engine 5", "package": None, "streaming_url": None},
        "note": "Lokaler Stub – echter Export läuft später auf dem GPU-Server.",
    }
    key = artifact_key(event.project_id, STEP, "vr_manifest.json")
    await ctx.storage.put_json(key, manifest)
    ctx.log.info("vr.exported", stub=True)
    return event.follow_up(
        EventType.VR_EXPORTED,
        producer=ctx.settings.service_name,
        artifacts={Artifact.VR_PACKAGE: key},
        data={"stub": True},
    )
