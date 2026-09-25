"""Ende-zu-Ende: Upload → komplette Eventkette → project.completed (gegen den laufenden Stack).

    make up && make test-integration

Die Standardtests laden KEIN Leistungsverzeichnis hoch → der blv-Service nutzt die
Standardausstattung (Stub). So sind sie deterministisch und brauchen keinen LLM-Zugang.
Der LLM-Test läuft nur mit LUMIRA_TEST_LLM=1 (verbraucht Kontingent beim Anbieter).
"""

from __future__ import annotations

import os
import struct
from typing import Any

import pytest

from lumira_parser.samples import sample_cad_pdf, sample_dxf, sample_pdf
from lumira_shared.testing import PdfPage, simple_pdf

pytestmark = pytest.mark.integration

# Die Fixture "pipeline" (conftest.py) ist ein Client für die Projekt-API.
Pipeline = Any

CHAIN_WITHOUT_VR = [
    "project.created",
    "plan.parsed",
    "plan.recognized",
    "rooms.classified",
    "blv.processed",
    "model.generated",
    "project.completed",
]


def _expected_chain(types: list[str]) -> list[str]:
    """Mit Profil 'vr' kommt vr.exported vor project.completed hinzu."""
    if "vr.exported" in types:
        return [*CHAIN_WITHOUT_VR[:-1], "vr.exported", "project.completed"]
    return CHAIN_WITHOUT_VR


def _assert_valid_glb(data: bytes) -> None:
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF"
    assert version == 2
    assert length == len(data)


def test_dxf_project_runs_through_complete_chain(pipeline: Pipeline) -> None:
    project_id = pipeline.create(
        "E2E DXF", {"floor_plan": ("grundriss.dxf", sample_dxf(), "application/dxf")}
    )

    detail = pipeline.wait_until_finished(project_id)

    assert detail["status"] == "completed", detail["error"]
    types = pipeline.event_types(detail)
    assert types == _expected_chain(types)
    assert detail["error"] is None

    plan = pipeline.artifact(project_id, "classified_plan").json()
    rooms = {room["room_type"]: room["area_m2"] for room in plan["rooms"]}
    assert rooms == {"living": 36.0, "bedroom": 14.0, "bathroom": 18.0, "hallway": 12.0}
    assert len(plan["walls"]) == 7

    blv = pipeline.artifact(project_id, "blv_result").json()
    assert blv["extracted_by"] == "stub"  # kein LV hochgeladen

    _assert_valid_glb(pipeline.artifact(project_id, "model_gltf").content)
    fbx = pipeline.artifact(project_id, "model_fbx").content
    assert fbx.startswith(b"Kaydara FBX Binary")


def test_finished_project_can_be_deleted_with_all_files(pipeline: Pipeline) -> None:
    project_id = pipeline.create(
        "E2E Löschen", {"floor_plan": ("grundriss.dxf", sample_dxf(), "application/dxf")}
    )
    detail = pipeline.wait_until_finished(project_id)
    assert detail["status"] == "completed", detail["error"]
    assert pipeline.artifact(project_id, "model_gltf").content

    assert pipeline.client.delete(f"/projects/{project_id}").status_code == 204

    assert pipeline.client.get(f"/projects/{project_id}").status_code == 404
    assert pipeline.client.get(f"/projects/{project_id}/artifacts/model_gltf").status_code == 404
    listed = [p["id"] for p in pipeline.client.get("/projects").json()]
    assert project_id not in listed


def test_cad_pdf_with_filled_walls_is_read_exactly(pipeline: Pipeline) -> None:
    """CAD-Export (Wände grau gefüllt, Räume farbig): Maße, Öffnungen und Räume 1:1."""
    project_id = pipeline.create(
        "E2E CAD-PDF", {"floor_plan": ("grundriss.pdf", sample_cad_pdf(), "application/pdf")}
    )

    detail = pipeline.wait_until_finished(project_id)

    assert detail["status"] == "completed", detail["error"]
    plan = pipeline.artifact(project_id, "classified_plan").json()
    assert plan["metadata"]["recognizer"] == "cad-fills"
    assert plan["metadata"]["scale"] == "1:100"
    assert plan["metadata"]["scale_check"].startswith("Maßstab bestätigt")
    rooms = {room["room_type"]: room["area_m2"] for room in plan["rooms"]}
    assert rooms == {"living": 30.0, "bathroom": 18.0}
    openings = sorted((o["type"], round(o["width_mm"])) for o in plan["openings"])
    assert openings == [("door", 900), ("window", 1000), ("window", 1500)]
    assert sum(1 for w in plan["walls"] if w["footprint"]) == 8

    _assert_valid_glb(pipeline.artifact(project_id, "model_gltf").content)


def test_pdf_project_also_renders_page_image(pipeline: Pipeline) -> None:
    project_id = pipeline.create(
        "E2E PDF", {"floor_plan": ("grundriss.pdf", sample_pdf(), "application/pdf")}
    )

    detail = pipeline.wait_until_finished(project_id)

    assert detail["status"] == "completed", detail["error"]
    assert pipeline.artifact(project_id, "plan_page_image").content.startswith(b"\x89PNG")
    plan = pipeline.artifact(project_id, "classified_plan").json()
    assert sorted(room["room_type"] for room in plan["rooms"]) == [
        "bathroom",
        "bedroom",
        "hallway",
        "living",
    ]


def test_unsupported_input_fails_cleanly(pipeline: Pipeline) -> None:
    project_id = pipeline.create(
        "E2E DWG",
        {"floor_plan": ("grundriss.dwg", b"AC1032 kein echtes DWG", "application/acad")},
    )

    detail = pipeline.wait_until_finished(project_id)

    assert detail["status"] == "failed"
    assert pipeline.event_types(detail) == ["project.created", "step.failed"]
    assert detail["error"]["step"] == "parser"
    assert detail["error"]["retryable"] is False
    assert "DWG" in detail["error"]["message"]


def test_upload_is_validated(pipeline: Pipeline) -> None:
    response = pipeline.client.post(
        "/projects",
        data={"name": "falsch"},
        files={"floor_plan": ("grundriss.docx", b"x", "application/octet-stream")},
    )
    assert response.status_code == 422


@pytest.mark.llm
@pytest.mark.skipif(os.environ.get("LUMIRA_TEST_LLM") != "1", reason="nur mit LUMIRA_TEST_LLM=1")
def test_blv_is_evaluated_by_llm(pipeline: Pipeline) -> None:
    lines = [
        "Bau- und Leistungsbeschreibung – Musterwohnung",
        "Bodenbeläge: Wohn- und Schlafräume Eichenparkett, Landhausdiele, geölt.",
        "Bad und WC: Bodenfliesen Feinsteinzeug 60 x 60 cm, grau; Wandfliesen weiß 30 x 60 cm.",
        "Wände: Maschinengipsputz Q2, Anstrich weiß.",
        "Fenster: Kunststoff, innen weiß, außen Anthrazit RAL 7016.",
        "Fassade: mineralischer Scheibenputz, weiß.",
    ]
    page = PdfPage(texts=[(60, 540 - 24 * i, text, 11) for i, text in enumerate(lines)])
    project_id = pipeline.create(
        "E2E mit LV",
        {
            "floor_plan": ("grundriss.dxf", sample_dxf(), "application/dxf"),
            "blv": ("baubeschreibung.pdf", simple_pdf([page]), "application/pdf"),
        },
    )

    detail = pipeline.wait_until_finished(project_id)

    assert detail["status"] == "completed", detail["error"]
    blv = pipeline.artifact(project_id, "blv_result").json()
    assert blv["extracted_by"] != "stub", "Kein API-Key im blv-Container gesetzt?"
    materials = blv["materials"]
    assert any(m["location"] == "exterior" for m in materials)  # Fassade
    assert any(m["color_hex"] == "#383E42" and m["color_source"] == "ral" for m in materials)
