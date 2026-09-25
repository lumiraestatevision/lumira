from pathlib import Path

from lumira_shared import BaseServiceSettings

# services/generator/blender_scripts/build_scene.py – im Container unter /app/services/generator/
_DEFAULT_SCRIPT = Path(__file__).resolve().parents[2] / "blender_scripts" / "build_scene.py"


class GeneratorSettings(BaseServiceSettings):
    service_name: str = "generator"
    port: int = 8005

    blender_bin: str = "blender"
    blender_script: Path = _DEFAULT_SCRIPT
    blender_timeout_s: int = 600
    equipment_variant: str | None = None  # None = Standardvariante aus dem BLV
    # Fototexturen (make textures). Fehlt der Ordner, nutzt Blender die Materialfarben.
    texture_dir: Path | None = None
