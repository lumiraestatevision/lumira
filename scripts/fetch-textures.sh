#!/usr/bin/env bash
# Lädt die Bodentexturen (Poly Haven, CC0) nach assets/textures/ – nicht im Git-Repo.
# Aufruf: make textures   (idempotent; prüft SHA-256 aus scripts/textures.sha256)
#
# Neue Textur: ID unten ergänzen, einmal mit UPDATE_CHECKSUMS=1 ausführen, Prüfsummen committen.
set -euo pipefail
cd "$(dirname "$0")/.."

TEXTURES=(
  oak_wood_planks      # Eichenparkett, Eichendiele
  laminate_floor_02    # Laminat
  laminate_floor_03    # Landhausdiele, Vinyl in Holzoptik
  herringbone_parquet  # Fischgrät
  plank_flooring_04    # dunkles Holz (Nussbaum, Räuchereiche)
)
MAPS=(diff nor_gl rough)
BASE=https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k
TARGET=assets/textures
SUMS=scripts/textures.sha256

mkdir -p "$TARGET"
for id in "${TEXTURES[@]}"; do
  mkdir -p "$TARGET/$id"
  for map in "${MAPS[@]}"; do
    file="$TARGET/$id/${id}_${map}_2k.jpg"
    [ -s "$file" ] && continue
    echo "→ $file"
    curl -fsSL --retry 3 -o "$file.part" "$BASE/$id/${id}_${map}_2k.jpg"
    mv "$file.part" "$file"
  done
done

if [ "${UPDATE_CHECKSUMS:-0}" = 1 ]; then
  (cd "$TARGET" && find . -name '*.jpg' | sort | xargs sha256sum) > "$SUMS"
  echo "Prüfsummen geschrieben: $SUMS"
else
  (cd "$TARGET" && sha256sum --quiet -c "../../$SUMS")
  echo "✔ Texturen vollständig und unverändert ($(du -sh "$TARGET" | cut -f1))"
fi
