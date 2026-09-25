"""Blender headless als Subprozess starten.

Warum Subprozess statt ``import bpy``? Die bpy-Wheels für Blender 4.x gibt es nur für
Python 3.11 – der Service läuft mit 3.12. Außerdem isoliert der Subprozess Abstürze und
Speicherlecks von Blender vom Service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lumira_shared import NonRetryableError

RESULT_PREFIX = "LUMIRA_RESULT "


class BlenderError(RuntimeError):
    """Blender ist fehlgeschlagen (wird vom Consumer erneut versucht)."""


@dataclass(frozen=True, slots=True)
class BlenderOutput:
    fbx: Path
    glb: Path
    stats: dict[str, Any]


def _prepare(script: Path, spec: Path, scene: dict[str, Any]) -> None:
    if not script.is_file():
        raise NonRetryableError(f"Blender-Skript fehlt: {script}")
    spec.write_text(json.dumps(scene), encoding="utf-8")


async def run_blender(
    scene: dict[str, Any],
    *,
    blender_bin: str,
    script: Path,
    workdir: Path,
    timeout_s: float,
    texture_dir: Path | None = None,
    model_dir: Path | None = None,
) -> BlenderOutput:
    spec = workdir / "scene.json"
    fbx, glb = workdir / "model.fbx", workdir / "model.glb"
    await asyncio.to_thread(_prepare, script, spec, scene)

    cmd = [
        blender_bin,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(script),
        "--",
        "--spec",
        str(spec),
        "--fbx",
        str(fbx),
        "--glb",
        str(glb),
    ]
    if texture_dir is not None:  # fehlender Ordner → Blender nutzt die Materialfarben
        cmd += ["--textures", str(texture_dir)]
    if model_dir is not None:  # fehlender Ordner → einfache Ersatzformen
        cmd += ["--models", str(model_dir)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # eigene Prozessgruppe → bei Timeout komplett beendbar
        )
    except FileNotFoundError as exc:
        raise NonRetryableError(f"Blender nicht gefunden: {blender_bin}") from exc

    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise BlenderError(f"Blender nach {timeout_s:g} s abgebrochen") from exc

    log = output.decode(errors="replace")
    tail = "\n".join(log.strip().splitlines()[-15:])
    if proc.returncode != 0:
        raise BlenderError(f"Blender beendet mit Code {proc.returncode}:\n{tail}")
    if not await asyncio.to_thread(lambda: fbx.is_file() and glb.is_file()):
        raise BlenderError(f"Blender hat keine Exportdateien erzeugt:\n{tail}")

    stats: dict[str, Any] = {}
    for line in log.splitlines():
        if line.startswith(RESULT_PREFIX):
            stats = json.loads(line.removeprefix(RESULT_PREFIX))
    return BlenderOutput(fbx=fbx, glb=glb, stats=stats)
