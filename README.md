# Lumira

Vom Grundriss zum begehbaren 3D-Modell: Nutzer laden einen Grundriss (PDF/DXF) und ein
Bauleistungsverzeichnis (PDF) hoch. Eine Pipeline aus Microservices erkennt Wände, Türen,
Fenster und Räume, klassifiziert die Raumtypen, liest Materialien und Ausstattungsvarianten
per LLM aus dem Leistungsverzeichnis, erzeugt mit Blender ein maßstabsgetreues 3D-Modell
(FBX, glTF) und übergibt es an Unreal Engine 5 für VR.

## Schnellstart – vom frischen Klon zum laufenden System

Voraussetzung: Linux oder **WSL2 (Ubuntu 24.04)** mit NVIDIA-Treiber auf Windows-Seite (optional).
Das Repo muss im Linux-Dateisystem liegen (`~/code/…`), nicht unter `/mnt/c` – sonst
funktioniert Hot Reload nicht.

```bash
git clone <repo-url> ~/code/lumira && cd ~/code/lumira
bash scripts/bootstrap-wsl.sh     # einmal pro Rechner: Docker, NVIDIA Toolkit, uv, Node, pnpm
make setup                        # Python-Umgebung, pre-commit, Frontend, Docker-Images
make up                           # Stack starten, wartet bis alles gesund ist
make test-integration             # Upload → komplette Eventkette → project.completed
```

Nach `bootstrap-wsl.sh` einmal das Terminal schließen und neu öffnen (Docker-Gruppe),
dann mit `cd ~/code/lumira && make setup` weiter. Wer die Tools schon hat, überspringt Schritt 2.

Danach: **Web-UI** <http://localhost:3000> · **API-Doku** <http://localhost:8000/docs> ·
**MinIO-Konsole** <http://localhost:9001> (Zugang: `MINIO_ROOT_USER`/`…PASSWORD` aus `.env`).

> Der erste `make setup` dauert (Downloads: PyTorch, Blender ≈ 3 GB; mit GPU-Profil ≈ 8 GB).

## Architektur

```
                      ┌──────────── Redis Streams (Consumer Groups) ─────────────┐
 Upload ─► backend ─► project.created ─► parser ─► plan.parsed ─► recognizer ─► plan.recognized
  (API)                                                                              │
            ┌──────────────── classifier ◄───────────────────────────────────────────┘
            ▼
     rooms.classified ─► blv ─► blv.processed ─► generator ─► model.generated ─► unreal ─► vr.exported
                        (LLM)                    (Blender)                      (Stub)        │
 backend: protokolliert alle Events, schließt mit project.completed ab ◄──────────────────────┘
          Fehler in jedem Schritt → step.failed (nach Wiederholungen) + Dead-Letter-Stream
```

- **Ein Monorepo**, jeder Service mit eigenem `pyproject.toml`, `Dockerfile` und Port.
  Gemeinsame Modelle, Events, Redis- und S3-Helfer liegen in `packages/shared` (`lumira-shared`).
- **uv-Workspace** mit einem `uv.lock`, Python 3.12. Das Frontend (Next.js, pnpm) ist nicht Teil davon.
- **Events** transportieren nur S3-Keys (`artifacts`); Daten liegen in S3 bzw. lokal MinIO.
- Zustellung *at-least-once*: Handler sind idempotent (deterministische S3-Keys), fehlgeschlagene
  Events werden wiederholt und nach 3 Versuchen als `step.failed` gemeldet.

| Service | Port | Aufgabe |
|---|---|---|
| backend | 8000 | API, Uploads, Projektstatus (PostgreSQL, Alembic) |
| parser | 8001 | DXF/PDF → Rohgeometrie und Texte |
| recognizer | 8002 | Wände, Öffnungen, Räume (GPU optional, CPU-Fallback) |
| classifier | 8003 | Raumtypen aus deutscher Beschriftung, Geometrie-Fallback |
| blv | 8004 | Leistungsverzeichnis per LLM → Materialien, Varianten |
| generator | 8005 | 3D-Modell mit Blender 4.5 LTS (headless) → FBX, glTF |
| unreal | 8006 | VR-Export – **lokal nur Stub** |
| frontend | 3000 | Web-UI (Next.js) mit 3D-Vorschau |

## Täglicher Umgang

```bash
make help                         # alle Befehle
make up / make down               # Stack starten / stoppen (Daten bleiben)
make logs SERVICE=blv             # Logs eines Services verfolgen
make restart SERVICE=blv          # Service neu starten (z. B. nach .env-Änderung)
make check                        # lint + typecheck + Unit-Tests – dasselbe wie die CI
make test-service SERVICE=parser  # Tests eines Pakets (SERVICE=shared für packages/shared)
make fmt                          # formatieren + Auto-Fixes
make reset                        # ALLE lokalen Daten löschen (fragt nach)
```

Code-Änderungen unter `services/*/src` und `packages/shared/src` lädt der jeweilige Container
automatisch neu (`uvicorn --reload`). Neue Abhängigkeiten erfordern `make build`.

pre-commit prüft bei jedem Commit ruff (Lint + Format), pyright, `uv.lock` und verhindert,
dass `.env` oder private Schlüssel committet werden – mit denselben Versionen wie `make check`.

## Konfiguration (`.env`)

`make setup` legt `.env` aus `.env.example` an; dort ist jede Variable erklärt. Wichtig:

| Variable | Bedeutung |
|---|---|
| `COMPOSE_PROFILES` | `cpu` **oder** `gpu`, optional `,vr` – z. B. `gpu,vr` |
| `PIPELINE_VR_ENABLED` | `true` nur zusammen mit Profil `vr` (sonst wird kein Projekt fertig – `make up` prüft das) |
| `BLV_PROVIDER` | `anthropic` (Claude, für den Betrieb) oder `gemini` (Gratistarif, nur lokal) |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Ohne Key für den gewählten Anbieter: Standardausstattung (Stub) |

**Gemini-Gratistarif:** nur für lokale Entwicklung. Google darf Ein- und Ausgaben lesen
(keine vertraulichen oder personenbezogenen Daten!), und laut Nutzungsbedingungen dürfen
Anwendungen für Nutzer in EWR/Schweiz/UK nur den Bezahltarif nutzen. Bei Überlastung weicht
der Service automatisch auf die Modelle in `BLV_GEMINI_FALLBACK_MODELS` aus.

## Tests

| Ebene | Befehl | Braucht |
|---|---|---|
| Unit (alle Pakete) | `make test` | nichts (fakeredis, moto, SQLite) |
| Ende-zu-Ende | `make test-integration` | laufenden Stack (`make up`) |
| Ende-zu-Ende mit echtem LLM | `LUMIRA_TEST_LLM=1 make test-integration` | API-Key in `.env` |

Beispiel-Grundrisse (DXF, PDF) erzeugt der Code zur Laufzeit (`lumira_parser.samples`).
Echte Testdokumente gehören nach `testdata/private/` – der Ordner wird nie committet.

## Was lokal (noch) nicht echt ist

- **Unreal Engine**: nur Stub (schreibt ein Manifest). Echter Betrieb später auf einem GPU-Server.
- **DWG**: wird mit klarer Fehlermeldung abgelehnt – bitte als DXF exportieren.
- **PDF-Maßstab**: fest 1:100 angenommen; Erkennung aus Maßketten folgt.
- **Erkennung**: Wände und Räume für achsparallele Vektorpläne; Türen/Fenster sind Platzhalter;
  KI-Erkennung gescannter Pläne ist ein Stub (die GPU wird dafür vorbereitet, aber noch nicht genutzt).
- **Raumklassifikation ohne Beschriftung**: Modell mit synthetischen Referenzdaten.
- **Farben**: Ohne Farbangabe im LV werden Farben aus Material/Kategorie angenommen und als
  `color_source: assumed` gekennzeichnet.

## Projektstruktur

```
packages/shared/        lumira-shared: Modelle, Events, Streams, Storage, Service-Grundgerüst
services/<name>/        je Service: src/, tests/, pyproject.toml, Dockerfile
services/backend/       zusätzlich alembic.ini + migrations/
services/generator/     zusätzlich blender_scripts/ (läuft in Blenders eigenem Python)
frontend/               Next.js-UI (pnpm)
infra/                  Init-Skripte (MinIO-Bucket)
tests/integration/      Ende-zu-Ende-Tests gegen den Stack
scripts/                bootstrap-wsl.sh, check-profiles.sh
testdata/private/       echte Testdokumente (git-ignoriert)
```

## Lizenzen

Alle eingebundenen Bibliotheken sind permissiv lizenziert; PyMuPDF (AGPL) wird bewusst nicht
verwendet. Blender (GPL) läuft als eigenständiges Programm. Direkt enthaltene Drittinhalte
stehen in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
