#!/usr/bin/env bash
# Prüft COMPOSE_PROFILES (+ PIPELINE_VR_ENABLED, BLV_PROVIDER) und gibt den recognizer-Service des aktiven
# Profils aus: "recognizer" (cpu) oder "recognizer-gpu" (gpu). Genutzt vom Makefile.
#   --inactive  gibt stattdessen die Services INAKTIVER Profile aus (zum Stoppen vor "up")
set -euo pipefail
mode="${1:-}"
cd "$(dirname "$0")/.."

from_env_file() {
  grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | xargs || true
}
profiles="${COMPOSE_PROFILES:-$(from_env_file COMPOSE_PROFILES)}"
vr_enabled="${PIPELINE_VR_ENABLED:-$(from_env_file PIPELINE_VR_ENABLED)}"
blv_provider="${BLV_PROVIDER:-$(from_env_file BLV_PROVIDER)}"

IFS=',' read -ra list <<<"$profiles"
has() {
  local wanted="$1" item
  for item in "${list[@]}"; do [ "$(echo "$item" | xargs)" = "$wanted" ] && return 0; done
  return 1
}

if has cpu && has gpu; then
  echo "✘ COMPOSE_PROFILES='$profiles' enthält cpu UND gpu – bitte genau eines wählen" >&2
  exit 1
fi
if [ "${vr_enabled,,}" = "true" ] && ! has vr; then
  echo "✘ PIPELINE_VR_ENABLED=true, aber Profil 'vr' fehlt – Projekte würden nie fertig." >&2
  echo "  Entweder COMPOSE_PROFILES um ',vr' ergänzen oder PIPELINE_VR_ENABLED=false setzen." >&2
  exit 1
fi
if has vr && [ "${vr_enabled,,}" != "true" ]; then
  echo "⚠ Profil 'vr' aktiv, aber PIPELINE_VR_ENABLED=false – Projekte enden schon nach model.generated." >&2
fi
if [ "${blv_provider,,}" = "ollama" ] && ! has llm; then
  echo "✘ BLV_PROVIDER=ollama, aber Profil 'llm' fehlt – blv erreicht kein lokales Modell." >&2
  echo "  COMPOSE_PROFILES um ',llm' ergänzen (z. B. cpu,llm) oder einen anderen BLV_PROVIDER wählen." >&2
  exit 1
fi
if has llm && [ "${blv_provider,,}" != "ollama" ]; then
  echo "⚠ Profil 'llm' aktiv, aber BLV_PROVIDER=${blv_provider:-anthropic} – Ollama läuft ungenutzt." >&2
fi

if has gpu; then
  active=recognizer-gpu inactive=recognizer
elif has cpu; then
  active=recognizer inactive=recognizer-gpu
else
  echo "✘ COMPOSE_PROFILES muss 'cpu' oder 'gpu' enthalten (aktuell: '$profiles')" >&2
  exit 1
fi

if [ "$mode" = "--inactive" ]; then
  has vr || inactive="$inactive unreal"
  has llm || inactive="$inactive ollama"
  echo "$inactive"
else
  echo "$active"
fi
