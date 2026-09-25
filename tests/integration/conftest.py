"""Gemeinsame Hilfen für die Ende-zu-Ende-Tests gegen den laufenden Stack (`make up`)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest

API_URL = os.environ.get("LUMIRA_API_URL", "http://localhost:8000")
FINAL_TIMEOUT_S = float(os.environ.get("LUMIRA_E2E_TIMEOUT", "300"))
# Für die passwortgeschützte Demo: LUMIRA_API_URL=https://….trycloudflare.com/api
_USER, _PASSWORD = os.environ.get("LUMIRA_API_USER"), os.environ.get("LUMIRA_API_PASSWORD")
AUTH = (_USER, _PASSWORD) if _USER and _PASSWORD else None


class Pipeline:
    """Dünner Client für die Projekt-API des backends."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def create(self, name: str, files: dict[str, tuple[str, bytes, str]]) -> str:
        response = self.client.post("/projects", data={"name": name}, files=files)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def wait_until_finished(self, project_id: str) -> dict[str, Any]:
        """Pollt, bis das Projekt nicht mehr 'processing' ist."""
        deadline = time.monotonic() + FINAL_TIMEOUT_S
        detail: dict[str, Any] = {}
        while time.monotonic() < deadline:
            detail = self.client.get(f"/projects/{project_id}").json()
            if detail["status"] != "processing":
                return detail
            time.sleep(0.5)
        pytest.fail(f"Projekt {project_id} nach {FINAL_TIMEOUT_S:.0f} s nicht fertig: {detail}")

    def artifact(self, project_id: str, name: str) -> httpx.Response:
        response = self.client.get(f"/projects/{project_id}/artifacts/{name}")
        assert response.status_code == 200, f"{name}: {response.status_code} {response.text}"
        return response

    @staticmethod
    def event_types(detail: dict[str, Any]) -> list[str]:
        return [event["type"] for event in detail["events"]]


@pytest.fixture(scope="session")
def pipeline() -> Iterator[Pipeline]:
    client = httpx.Client(base_url=API_URL, timeout=60, auth=AUTH)
    try:
        ready = client.get("/health/ready")
    except httpx.ConnectError:
        client.close()
        pytest.skip(f"Stack nicht erreichbar unter {API_URL} – vorher `make up`")
    if ready.status_code != 200:
        client.close()
        pytest.fail(f"backend nicht bereit: {ready.text}")
    yield Pipeline(client)
    client.close()
