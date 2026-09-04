#!/usr/bin/env bash
# Vrati izgled (template, CSS, JS, static img) na snimljeno "početno".
# Ne dira viewove, modele, URL-ove ni ostale funkcije.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SNAP="$ROOT/scripts/snapshots/pocetno"

if [[ ! -d "$SNAP/EcommerceApp/template" ]]; then
  echo "Snapshot pocetno nije pronađen: $SNAP" >&2
  exit 1
fi

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$SNAP/EcommerceApp/template/" "$ROOT/EcommerceApp/template/"

rsync -a --delete \
  --exclude '*.gz' \
  "$SNAP/EcommerceApp/static/css/" "$ROOT/EcommerceApp/static/css/"

rsync -a --delete \
  --exclude '*.gz' \
  "$SNAP/EcommerceApp/static/js/" "$ROOT/EcommerceApp/static/js/"

if [[ -d "$SNAP/EcommerceApp/static/img" ]]; then
  rsync -a "$SNAP/EcommerceApp/static/img/" "$ROOT/EcommerceApp/static/img/"
fi

echo "Vraćen početni izgled (template + CSS + JS + img). Funkcije nisu dirane."
echo "Ako koristiš minifikovani CSS, pokreni: python3 scripts/minify_assets.py"
