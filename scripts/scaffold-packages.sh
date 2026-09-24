#!/usr/bin/env bash
# Legt die minimalen Paketordner (src-Layout) an, damit uv alle Workspace-Member bauen kann.
# Idempotent: vorhandene Dateien werden nicht überschrieben.
set -euo pipefail
cd "$(dirname "$0")/.."

make_pkg() {
  local dir="$1" module="$2" doc="$3"
  mkdir -p "${dir}/src/${module}" "${dir}/tests"
  local init="${dir}/src/${module}/__init__.py"
  [[ -f "${init}" ]] || printf '"""%s"""\n\n__version__ = "0.1.0"\n' "${doc}" > "${init}"
}

make_pkg packages/shared      lumira_shared     "Lumira Shared – gemeinsame Modelle, Events, Streams, Storage."
make_pkg services/backend     lumira_backend    "Lumira Backend – API Gateway (Port 8000)."
make_pkg services/parser      lumira_parser     "Lumira Parser – DWG/DXF und PDF einlesen (Port 8001)."
make_pkg services/recognizer  lumira_recognizer "Lumira Recognizer – KI-Planerkennung (Port 8002)."
make_pkg services/classifier  lumira_classifier "Lumira Classifier – Raumklassifikation (Port 8003)."
make_pkg services/blv         lumira_blv        "Lumira BLV – Leistungsverzeichnis per LLM (Port 8004)."
make_pkg services/generator   lumira_generator  "Lumira Generator – 3D-Modell mit Blender (Port 8005)."
make_pkg services/unreal      lumira_unreal     "Lumira Unreal – Rendering und VR-Export, lokal Stub (Port 8006)."

: > packages/shared/src/lumira_shared/py.typed
mkdir -p services/generator/blender_scripts tests/integration infra/minio frontend

echo "Paketstruktur angelegt."
