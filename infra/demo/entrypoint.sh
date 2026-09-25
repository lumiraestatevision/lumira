#!/bin/sh
# Hasht DEMO_PASSWORD beim Start (bcrypt) – in .env steht so nur das Klartext-Passwort,
# und Caddy selbst sieht es nie.
set -eu

if [ "${#DEMO_PASSWORD}" -lt 12 ]; then
  echo "DEMO_PASSWORD fehlt oder ist kürzer als 12 Zeichen (.env)" >&2
  exit 1
fi
DEMO_PASSWORD_HASH="$(caddy hash-password --plaintext "$DEMO_PASSWORD")"
export DEMO_PASSWORD_HASH
unset DEMO_PASSWORD

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
