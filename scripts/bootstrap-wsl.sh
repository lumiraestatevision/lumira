#!/usr/bin/env bash
# Lumira – Bootstrap für WSL2 / Ubuntu 24.04 (Setup-Schritte 1c–1f).
# Idempotent: darf beliebig oft ausgeführt werden.
# Aufruf als normaler Benutzer (NICHT mit sudo):  bash bootstrap-wsl.sh
set -euo pipefail

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[WARN] %s\033[0m\n' "$*"; }

if [[ ${EUID} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer ausführen, nicht mit sudo."
  exit 1
fi

log "sudo-Passwort (einmalig, Eingabe bleibt unsichtbar)"
sudo -v
# sudo-Zeitstempel während der Laufzeit frisch halten
( while kill -0 "$$" 2>/dev/null; do sudo -n true; sleep 50; done ) 2>/dev/null &
SUDO_KEEPALIVE=$!
trap 'kill "${SUDO_KEEPALIVE}" 2>/dev/null || true' EXIT

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- 1c
log "1c: Grundpakete"
sudo apt-get update
sudo -E apt-get upgrade -y
sudo -E apt-get install -y build-essential git curl ca-certificates gnupg unzip

if ! grep -q "systemd=true" /etc/wsl.conf 2>/dev/null; then
  printf "[boot]\nsystemd=true\n" | sudo tee -a /etc/wsl.conf >/dev/null
  warn "systemd wurde gerade aktiviert: in PowerShell 'wsl --shutdown' ausführen und dieses Skript erneut starten."
  exit 0
fi

# ---------------------------------------------------------------- 1d
log "1d: Docker-Paketquelle"
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

# ---------------------------------------------------------------- 1e
log "1e: NVIDIA-Container-Toolkit-Paketquelle"
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

log "1d/1e: Docker Engine, Compose, Buildx, NVIDIA Toolkit installieren"
sudo apt-get update
sudo -E apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit

sudo usermod -aG docker "${USER}"
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now containerd docker
sudo systemctl restart docker

# ---------------------------------------------------------------- 1f
log "1f: uv + Python 3.12"
export PATH="${HOME}/.local/bin:${PATH}"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12

log "1f: Node 24 LTS (fnm)"
# 'pnpm env' ist ab pnpm 12 deprecated -> Node-Versionen verwaltet fnm.
export PATH="${HOME}/.local/share/fnm:${PATH}"
if ! command -v fnm >/dev/null 2>&1; then
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
  grep -q 'fnm env' "${HOME}/.bashrc" || cat >> "${HOME}/.bashrc" <<'EOF'

# fnm (Node-Versionen)
export PATH="$HOME/.local/share/fnm:$PATH"
eval "$(fnm env --use-on-cd --shell bash)"
EOF
fi
eval "$(fnm env --shell bash)"
fnm install 24
fnm default 24

log "1f: pnpm"
export PNPM_HOME="${HOME}/.local/share/pnpm"
export PATH="${PNPM_HOME}/bin:${PNPM_HOME}:${PATH}"
if ! command -v pnpm >/dev/null 2>&1; then
  curl -fsSL https://get.pnpm.io/install.sh | ENV="${HOME}/.bashrc" SHELL="$(command -v bash)" sh -
fi

mkdir -p "${HOME}/code"

# ---------------------------------------------------------------- Prüfung
log "Prüfung"
printf 'uv:       %s\n' "$(uv --version)"
printf 'python:   %s\n' "$(uv python find 3.12)"
printf 'node:     %s\n' "$(node --version 2>/dev/null || echo FEHLT)"
printf 'pnpm:     %s\n' "$(pnpm --version 2>/dev/null || echo FEHLT)"
printf 'make:     %s\n' "$(make --version | head -1)"
printf 'docker:   %s\n' "$(sudo docker version --format '{{.Server.Version}}')"
printf 'compose:  %s\n' "$(sudo docker compose version --short)"

log "GPU-Test im Container (sollte die RTX 3060 Ti zeigen)"
sudo docker run --rm --gpus all ubuntu:24.04 nvidia-smi \
  || warn "GPU-Test fehlgeschlagen – Ausgabe bitte an Claude schicken."

log "Fertig. Jetzt 'exit' und in PowerShell erneut 'wsl' starten, damit die docker-Gruppe greift."
