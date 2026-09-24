"""Lumira generator – 3D-Modell mit Blender headless (Port 8005)."""

from __future__ import annotations

import shutil

from lumira_generator.config import GeneratorSettings
from lumira_generator.handler import handle_blv_processed
from lumira_shared import EventType, create_service_app

settings = GeneratorSettings()


async def blender_available() -> bool:
    return shutil.which(settings.blender_bin) is not None and settings.blender_script.is_file()


app = create_service_app(
    settings,
    handlers={EventType.BLV_PROCESSED: handle_blv_processed},
    readiness_checks={"blender": blender_available},
    description="Erzeugt ein maßstabsgetreues 3D-Modell (FBX, glTF) mit Blender.",
)
