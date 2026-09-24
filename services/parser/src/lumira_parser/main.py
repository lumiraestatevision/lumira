"""Lumira parser – DWG/DXF und PDF einlesen (Port 8001)."""

from lumira_parser.config import ParserSettings
from lumira_parser.handler import handle_project_created
from lumira_shared import EventType, create_service_app

app = create_service_app(
    ParserSettings(),
    handlers={EventType.PROJECT_CREATED: handle_project_created},
    description="Liest Grundrisse (DXF, PDF) und liefert Rohgeometrie als ParsedPlan.",
)
