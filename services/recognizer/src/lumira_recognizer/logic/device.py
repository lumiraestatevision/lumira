"""Rechengerät wählen: CUDA, wenn verfügbar – sonst CPU-Fallback."""

from __future__ import annotations

from typing import Literal

from lumira_shared import get_logger

log = get_logger(__name__)

Device = Literal["cpu", "cuda"]


_resolved: dict[str, Device] = {}


def resolve_device(preference: Literal["auto", "cpu", "cuda"]) -> Device:
    """Ergebnis wird gecacht – die CUDA-Prüfung dauert beim ersten Mal mehrere Sekunden."""
    if preference not in _resolved:
        _resolved[preference] = _resolve(preference)
    return _resolved[preference]


def _resolve(preference: Literal["auto", "cpu", "cuda"]) -> Device:
    if preference == "cpu":
        return "cpu"
    import torch  # spät importieren: torch lädt mehrere Sekunden

    if torch.cuda.is_available():
        return "cuda"
    if preference == "cuda":
        log.warning("recognizer.cuda_unavailable", fallback="cpu")
    return "cpu"


def device_info(device: Device) -> dict[str, str | bool | None]:
    import torch

    info: dict[str, str | bool | None] = {
        "device": device,
        "torch": torch.__version__,
        "cuda_build": torch.version.cuda,  # pyright: ignore[reportAttributeAccessIssue]
        "cuda_available": torch.cuda.is_available(),
        "gpu": None,
    }
    if device == "cuda":
        info["gpu"] = torch.cuda.get_device_name(0)
    return info
