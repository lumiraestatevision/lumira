"""Basisklasse und ID-Helfer für alle Lumira-Modelle."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class LumiraModel(BaseModel):
    """Basis aller Lumira-Modelle.

    ``extra="ignore"`` folgt dem Tolerant-Reader-Prinzip: Ein älterer Service darf ein
    JSON-Dokument lesen, das ein neuerer Service um zusätzliche Felder erweitert hat.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def new_id(prefix: str) -> str:
    """Kurze, lesbare, eindeutige ID wie ``wall_3f9a0c1b2d4e``."""
    return f"{prefix}_{uuid4().hex[:12]}"
