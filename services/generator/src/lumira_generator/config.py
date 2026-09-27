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
    # 3D-Modelle (Pflanze …). Fehlt der Ordner, baut Blender einfache Ersatzformen.
    model_dir: Path | None = None
    # Licht einbrennen (Lightmap). 0 = aus. Die GPU schafft in gleicher Zeit ~20x mehr Samples;
    # Blender nimmt sie, wenn der Container eine NVIDIA-GPU sieht (docker-compose.gpu.yml).
    bake_samples: int = 64  # CPU: ~3–4 min für ein Doppelhaus
    bake_samples_gpu: int = 512  # RTX 3060 Ti: ~1 min
    lightmap_px: int = 2048  # ≈ 3 cm je Pixel für ein Doppelhaus
