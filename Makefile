# Lumira – Entwickler-Befehle. `make help` zeigt alle Ziele.
#
# Lint, Typecheck und Tests nutzen dieselben Befehle wie die CI.
# Docker-Profile kommen aus COMPOSE_PROFILES in .env (cpu | gpu, optional vr).

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

COMPOSE   := docker compose
ALL_PROFILES := --profile cpu --profile gpu --profile vr --profile llm
PACKAGES  := packages/shared $(wildcard services/*)
SERVICE   ?=
TORCH     ?= cpu
PYTEST_ARGS ?= -v

# SERVICE=parser → services/parser, Paket lumira-parser; SERVICE=shared → packages/shared
SERVICE_DIR    = $(if $(filter shared,$(SERVICE)),packages/shared,services/$(SERVICE))
SERVICE_PKG    = lumira-$(SERVICE)
SERVICE_EXTRAS = $(if $(filter recognizer,$(SERVICE)),--extra $(TORCH),)
require-service = @test -n "$(SERVICE)" -a -d "$(SERVICE_DIR)" \
  || { echo "✘ Aufruf mit SERVICE=<name> (shared, backend, parser, …); unbekannt: '$(SERVICE)'"; exit 1; }

# Images einzeln bauen: parallele Exporte großer Images lassen BuildKit gelegentlich scheitern.
BUILD_ORDER := backend parser classifier blv unreal generator frontend

.PHONY: help
help: ## Diese Übersicht
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ------------------------------------------------------------------ Einrichtung
.PHONY: setup
setup: check-tools .env ## Einmalig: Python-Umgebung, pre-commit, Frontend, Docker-Images
	$(MAKE) --no-print-directory sync
	uv run --no-sync pre-commit install
	pnpm --dir frontend install --frozen-lockfile
	$(MAKE) --no-print-directory build
	@echo -e "\n✔ Setup fertig. Weiter mit: make up"

.PHONY: sync
sync: ## Python-Umgebung mit ALLEN Paketen aktualisieren (nach git pull)
	uv sync --locked --all-packages --extra $(TORCH)

.PHONY: check-tools
check-tools:
	@for tool in uv docker pnpm git; do \
	  command -v $$tool >/dev/null || { echo "✘ $$tool fehlt – siehe scripts/bootstrap-wsl.sh"; exit 1; }; \
	done
	@docker compose version >/dev/null || { echo "✘ docker compose fehlt"; exit 1; }

.env:
	cp .env.example .env
	chmod 600 .env
	@echo "→ .env aus .env.example angelegt (API-Keys bei Bedarf eintragen)"

.PHONY: build
build: .env ## Docker-Images bauen (nacheinander, aktives Profil)
	@for service in $(BUILD_ORDER) $$(bash scripts/check-profiles.sh); do \
	  echo "→ build $$service"; $(COMPOSE) build $$service; \
	done

# ------------------------------------------------------------------ Stack
.PHONY: up
up: .env ## Stack starten (Profile aus .env) und warten, bis alles gesund ist
	@# "up" stoppt Services inaktiver Profile nicht → z. B. die andere recognizer-Variante beenden
	@inactive="$$(bash scripts/check-profiles.sh --inactive)" || exit 1; \
	  $(COMPOSE) $(ALL_PROFILES) stop $$inactive >/dev/null 2>&1 || true; \
	  if ! grep -qw ollama <<<"$$inactive"; then \
	    echo "→ lokales LLM: Modell laden (beim ersten Mal einige GB, mit Fortschrittsanzeige)"; \
	    $(COMPOSE) run --rm ollama-pull || exit 1; \
	  fi
	$(COMPOSE) up -d --wait --wait-timeout 600 --remove-orphans
	@$(MAKE) --no-print-directory ps
	@echo -e "\n  Web-UI      http://localhost:3000\n  API-Doku    http://localhost:8000/docs\n  MinIO       http://localhost:9001"

.PHONY: down
down: ## Stack stoppen (Daten bleiben erhalten)
	$(COMPOSE) $(ALL_PROFILES) down --remove-orphans

.PHONY: restart
restart: ## Einzelnen Service neu starten: make restart SERVICE=blv
	@test -n "$(SERVICE)" || { echo "Aufruf: make restart SERVICE=<name>"; exit 1; }
	$(COMPOSE) up -d --force-recreate --wait $(SERVICE)

.PHONY: ps
ps: ## Status aller Container
	@$(COMPOSE) ps -a --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

.PHONY: logs
logs: ## Logs verfolgen (alle oder: make logs SERVICE=parser)
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

.PHONY: reset
reset: ## ALLE lokalen Daten löschen (DB, Redis, MinIO, Modelle) und neu starten
	@if [ "$${FORCE:-}" != "1" ]; then \
	  read -r -p "Wirklich alle lokalen Daten löschen? [j/N] " answer; \
	  [[ "$$answer" =~ ^[jJyY]$$ ]] || { echo "Abgebrochen."; exit 1; }; \
	fi
	$(COMPOSE) $(ALL_PROFILES) down --volumes --remove-orphans
	$(MAKE) --no-print-directory up

# ------------------------------------------------------------------ Qualität (wie in der CI)
.PHONY: fmt
fmt: ## Code formatieren und automatisch behebbare Lint-Fehler korrigieren
	uv run --no-sync ruff format .
	uv run --no-sync ruff check --fix .

.PHONY: lint
lint: ## Lint + Formatprüfung (ändert nichts)
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .
	uv lock --check

.PHONY: typecheck
typecheck: ## Typprüfung mit pyright
	uv run --no-sync pyright

.PHONY: test
test: ## Unit-Tests aller Pakete (ohne laufenden Stack)
	@failed=""; for package in $(PACKAGES); do \
	  echo -e "\n\033[1m=== $$package\033[0m"; \
	  (cd $$package && uv run --no-sync pytest -q) || failed="$$failed $$package"; \
	done; \
	if [ -n "$$failed" ]; then echo -e "\n✘ Fehlgeschlagen:$$failed"; exit 1; fi; \
	echo -e "\n✔ Alle Pakete grün"

# ------------------------------------------------------------------ Einzelner Service (CI-Jobs je Service)
.PHONY: sync-service
sync-service: ## NUR dieses Paket installieren (CI; lokal danach wieder `make sync`)
	$(require-service)
	uv sync --locked --package $(SERVICE_PKG) $(SERVICE_EXTRAS)

.PHONY: lint-service
lint-service: ## Lint + Format eines Pakets: make lint-service SERVICE=parser
	$(require-service)
	uv run --no-sync ruff check $(SERVICE_DIR)
	uv run --no-sync ruff format --check $(SERVICE_DIR)

.PHONY: typecheck-service
typecheck-service: ## Typprüfung eines Pakets: make typecheck-service SERVICE=parser
	$(require-service)
	uv run --no-sync pyright $(SERVICE_DIR)

.PHONY: test-service
test-service: ## Tests eines Pakets: make test-service SERVICE=parser (oder shared)
	$(require-service)
	cd $(SERVICE_DIR) && uv run --no-sync pytest $(PYTEST_ARGS)

.PHONY: check-service
check-service: lint-service typecheck-service test-service ## lint + typecheck + test eines Pakets

.PHONY: migration-check
migration-check: ## Alembic-Prüfung wie in der CI – LÖSCHT die Tabellen (lokal nur mit FORCE=1)
	@[ "$${CI:-}" = "true" ] || [ "$${FORCE:-}" = "1" ] \
	  || { echo "✘ downgrade base löscht alle Projektdaten – lokal nur mit FORCE=1"; exit 1; }
	cd services/backend && uv run --no-sync alembic upgrade head
	cd services/backend && uv run --no-sync alembic check
	cd services/backend && uv run --no-sync alembic downgrade base
	cd services/backend && uv run --no-sync alembic upgrade head

.PHONY: test-integration
test-integration: ## Ende-zu-Ende-Test gegen den laufenden Stack (vorher: make up)
	uv run --no-sync pytest tests/integration -m integration -v

.PHONY: check
check: lint typecheck test ## Alles, was die CI prüft (lint, typecheck, test)
