#!/usr/bin/env bash
# Deploy the PUBLIC instance (https://charts.fibtrader.dev): crypto only, Supabase sign-in, no private plugins.
#   ops/deploy_public.sh          # code + deps + restart
#   ops/deploy_public.sh --quick  # skip pip install
# Ships the committed HEAD (git archive) with the private plugin folders removed, builds the frontend
# locally (the 1 GB VM is too small for a Vite build) and refuses to deploy if private code shows up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.secrets/deploy.env" ] && . "$ROOT/.secrets/deploy.env"  # LV_PUBLIC_HOST=<ssh alias> (private)
HOST="${LV_PUBLIC_HOST:?set LV_PUBLIC_HOST to the ssh host of the server}"
DIR="fibtrader-view"
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then echo "Uncommitted changes — commit first."; exit 1; fi

# backend tests first: a red suite stops the deploy (tests/test_routes.py guards the history protection of new routes)
(cd "$ROOT/backend" && "$ROOT/venv/bin/python" -m pytest -q -x -p no:cacheprovider) || { echo "backend tests failed — not deploying"; exit 1; }

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
git -C "$ROOT" archive HEAD | tar -x -C "$BUILD"
# private paths and leak patterns live in files that are themselves never published
EXCLUDE="$ROOT/ops/public-exclude.txt"; PATTERNS="$ROOT/ops/leak-patterns.txt"
if [ -f "$EXCLUDE" ]; then grep -v '^\s*#' "$EXCLUDE" | grep -v '^\s*$' | while read -r p; do rm -rf "${BUILD:?}/$p"; done; fi

ln -s "$ROOT/frontend/node_modules" "$BUILD/frontend/node_modules"
(cd "$BUILD/frontend" && node scripts/patch-klinecharts.mjs && node scripts/check-i18n.mjs && node scripts/test-indicators.mjs && npx tsc -b && npx vite build --logLevel warn)
rm "$BUILD/frontend/node_modules"

if [ -f "$PATTERNS" ] && grep -rIliF -f <(grep -v '^\s*#' "$PATTERNS" | grep -v '^\s*$') "$BUILD/backend" "$BUILD/frontend/dist" "$BUILD/frontend/src" 2>/dev/null; then
  echo "private code or data found in the public build — aborting"; exit 1
fi

rsync -az --delete --exclude /data/ --exclude /venv/ --exclude /.env --exclude /frontend/node_modules/ "$BUILD/" "$HOST:$DIR/"

ssh "$HOST" bash -s <<EOF
set -euo pipefail
cd ~/$DIR
test -d venv || python3 -m venv venv
if [ $QUICK = 0 ]; then ./venv/bin/pip install -q --upgrade pip && ./venv/bin/pip install -q -r backend/requirements.txt; fi
sudo cp ops/fibtrader-view.service /etc/systemd/system/fibtrader-view.service
sudo systemctl daemon-reload
sudo systemctl enable -q fibtrader-view
sudo systemctl restart fibtrader-view
# the 1 GB VM sometimes needs more than a few seconds to start: wait up to 30 s
for i in \$(seq 1 30); do curl -fsS http://127.0.0.1:8100/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8100/api/config; echo
EOF
echo "deployed: https://charts.fibtrader.dev"
