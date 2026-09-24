from typing import Literal

from lumira_shared import BaseServiceSettings


class RecognizerSettings(BaseServiceSettings):
    service_name: str = "recognizer"
    port: int = 8002

    # auto: CUDA, wenn verfügbar, sonst CPU. cuda: CUDA erzwingen (mit Warnung + CPU-Fallback).
    recognizer_device: Literal["auto", "cpu", "cuda"] = "auto"
    min_wall_length_mm: float = 300.0
    default_wall_thickness_mm: float = 175.0
    ocr_languages: str = "de,en"
