#!/usr/bin/env python3
"""Lädt die Möbelmodelle (Poly Haven, CC0, glTF mit 1K-Texturen) nach assets/models/<id>/.

Nicht im Git-Repo. Aufruf über ``make assets``; idempotent, prüft SHA-256 aus
scripts/models.sha256. Neue Modelle: ID unten ergänzen, einmal mit UPDATE_CHECKSUMS=1 laufen
lassen, Prüfsummen committen. Nur Python-Standardbibliothek.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

# Poly Haven hat kaum moderne Wohnmöbel (Sofa/Stühle/Tische dort sind antik bzw. gotisch) –
# Möbel baut das Blender-Skript deshalb selbst; von hier kommt nur, was sich nicht gut
# erzeugen lässt.
MODELS = [
    "calathea_orbifolia_01",  # Zimmerpflanze (5 Varianten in einer Datei)
]
API = "https://api.polyhaven.com/files/{}"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "models"
SUMS = ROOT / "scripts" / "models.sha256"
HEADERS = {"User-Agent": "lumira-asset-fetch/1.0"}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:  # feste https-Quelle
        return response.read()


def _files(model_id: str) -> dict[str, str]:
    """Relativer Pfad → URL für glTF (1K) samt .bin und Texturen."""
    info = json.loads(_get(API.format(model_id)))["gltf"]["1k"]["gltf"]
    files = {Path(info["url"]).name: info["url"]}
    files |= {path: entry["url"] for path, entry in info.get("include", {}).items()}
    return files


def fetch() -> None:
    for model_id in MODELS:
        folder = TARGET / model_id
        if (folder / f"{model_id}_1k.gltf").is_file():
            continue
        for relative, url in _files(model_id).items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            print(f"→ {path.relative_to(ROOT)}")
            data = _get(url)
            path.with_suffix(path.suffix + ".part").write_bytes(data)
            path.with_suffix(path.suffix + ".part").replace(path)


def _files_on_disk() -> list[Path]:
    return [
        p
        for model_id in MODELS
        for p in sorted((TARGET / model_id).rglob("*"))
        if p.is_file() and not p.name.endswith(".part")
    ]


def _digests() -> list[str]:
    return [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(TARGET).as_posix()}"
        for p in _files_on_disk()
    ]


def main() -> int:
    fetch()
    if os.environ.get("UPDATE_CHECKSUMS") == "1":
        SUMS.write_text("\n".join(_digests()) + "\n", encoding="utf-8")
        print(f"Prüfsummen geschrieben: {SUMS.relative_to(ROOT)}")
        return 0
    expected = set(SUMS.read_text(encoding="utf-8").split("\n")) - {""}
    actual = set(_digests())
    if expected - actual:
        print("✘ Möbelmodelle fehlen oder weichen ab:", *sorted(expected - actual), sep="\n  ")
        return 1
    size = sum(p.stat().st_size for p in _files_on_disk())
    print(f"✔ Möbelmodelle vollständig und unverändert ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
