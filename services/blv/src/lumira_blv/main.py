"""Lumira blv – Leistungsverzeichnis per LLM auswerten (Port 8004)."""

from lumira_blv.config import BLVSettings
from lumira_blv.handler import handle_rooms_classified
from lumira_shared import EventType, create_service_app

app = create_service_app(
    BLVSettings(),
    handlers={EventType.ROOMS_CLASSIFIED: handle_rooms_classified},
    description="Extrahiert Materialien und Ausstattungsvarianten aus dem Leistungsverzeichnis.",
)
