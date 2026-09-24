"""blv.processed → model.generated"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

from lumira_generator.config import GeneratorSettings
from lumira_generator.logic.blender_runner import run_blender
from lumira_generator.logic.scene import build_scene
from lumira_shared import Artifact, Event, EventType, ServiceContext, artifact_key
from lumira_shared.models import BLVResult, FloorPlan

STEP = "generator"


async def handle_blv_processed(event: Event, ctx: ServiceContext) -> Event:
    settings = cast(GeneratorSettings, ctx.settings)
    plan = await ctx.storage.get_model(event.artifacts[Artifact.CLASSIFIED_PLAN], FloorPlan)
    blv = await ctx.storage.get_model(event.artifacts[Artifact.BLV_RESULT], BLVResult)
    scene = build_scene(plan, blv, variant=settings.equipment_variant)

    await ctx.storage.put_json(artifact_key(event.project_id, STEP, "scene.json"), scene)
    with tempfile.TemporaryDirectory(prefix="lumira-gen-") as tmp:
        result = await run_blender(
            scene,
            blender_bin=settings.blender_bin,
            script=settings.blender_script,
            workdir=Path(tmp),
            timeout_s=settings.blender_timeout_s,
        )
        fbx_key = await ctx.storage.upload_file(
            artifact_key(event.project_id, STEP, "model.fbx"), result.fbx
        )
        glb_key = await ctx.storage.upload_file(
            artifact_key(event.project_id, STEP, "model.glb"), result.glb
        )

    ctx.log.info("model.generated", variant=scene["variant"], **result.stats)
    return event.follow_up(
        EventType.MODEL_GENERATED,
        producer=settings.service_name,
        artifacts={Artifact.MODEL_FBX: fbx_key, Artifact.MODEL_GLTF: glb_key},
        data={"variant": scene["variant"], **result.stats},
    )
