#!/usr/bin/env bash
# Vrati projekat na stanje 01.07.2026. 08:40
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SNAP="$ROOT/scripts/snapshots/0840"

if [[ ! -d "$SNAP" ]]; then
  echo "Snapshot 0840 nije pronađen: $SNAP" >&2
  exit 1
fi

cp "$SNAP/EcommerceApp/sitemaps.py" "$ROOT/EcommerceApp/sitemaps.py"
cp "$SNAP/EcommerceApp/admin.py" "$ROOT/EcommerceApp/admin.py"
cp "$SNAP/EcommerceApp/models.py" "$ROOT/EcommerceApp/models.py"
cp "$SNAP/EcommerceApp/utils/images.py" "$ROOT/EcommerceApp/utils/images.py"
cp "$SNAP/EcommerceApp/views_media.py" "$ROOT/EcommerceApp/views_media.py"
cp "$SNAP/EcommerceApp/template/home.html" "$ROOT/EcommerceApp/template/home.html"
cp "$SNAP/EcommerceApp/template/product_detail.html" "$ROOT/EcommerceApp/template/product_detail.html"
cp "$SNAP/EcommerceApp/template/partials/product_card_info.html" "$ROOT/EcommerceApp/template/partials/product_card_info.html"
cp "$SNAP/EcommerceApp/static/css/style.css" "$ROOT/EcommerceApp/static/css/style.css"
cp "$SNAP/EcommerceApp/static/css/style.min.css" "$ROOT/EcommerceApp/static/css/style.min.css"
cp "$SNAP/EcommerceApp/migrations/0047_product_proizvedeno_u_japanu.py" "$ROOT/EcommerceApp/migrations/0047_product_proizvedeno_u_japanu.py"
cp "$SNAP/EcommerceProject/urls.py" "$ROOT/EcommerceProject/urls.py"

echo "Vraćeno na snapshot 08:40. Pokreni: python3 manage.py migrate"