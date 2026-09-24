#!/bin/sh
# Init-Container: legt den Bucket für Zwischenergebnisse an. Idempotent.
set -eu

: "${MINIO_ROOT_USER:?fehlt}"
: "${MINIO_ROOT_PASSWORD:?fehlt}"
: "${S3_BUCKET:=lumira}"

# Das Image läuft als Non-Root ohne beschreibbares Home – mc-Konfiguration nach /tmp.
export MC_CONFIG_DIR=/tmp/.mc

mc alias set lumira http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing "lumira/${S3_BUCKET}"
mc anonymous set none "lumira/${S3_BUCKET}" >/dev/null

echo "Bucket '${S3_BUCKET}' ist bereit."
