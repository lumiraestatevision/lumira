"""Lumira classifier – Raumklassifikation (Port 8003)."""

from lumira_classifier.config import ClassifierSettings
from lumira_classifier.handler import handle_plan_recognized
from lumira_shared import EventType, create_service_app

app = create_service_app(
    ClassifierSettings(),
    handlers={EventType.PLAN_RECOGNIZED: handle_plan_recognized},
    description="Ordnet Räumen Typen zu (deutsche Planbeschriftung, Geometrie-Fallback).",
)
