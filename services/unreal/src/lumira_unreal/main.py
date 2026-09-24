"""Lumira unreal – Rendering und VR-Export (Port 8006). Lokal nur Stub."""

from lumira_shared import EventType, create_service_app
from lumira_unreal.config import UnrealSettings
from lumira_unreal.handler import handle_model_generated

app = create_service_app(
    UnrealSettings(),
    handlers={EventType.MODEL_GENERATED: handle_model_generated},
    description="STUB: VR-Export mit Unreal Engine 5 (echter Betrieb auf GPU-Server).",
)
