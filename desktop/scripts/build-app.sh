#!/usr/bin/env bash
# Full desktop pipeline: web bundle -> frozen backend -> .app + .dmg
#
#   cd desktop && npm run dist
#
# Produces (arm64 macOS):
#   release/mac-arm64/Keysight Connect.app
#   release/Keysight Connect-<version>-arm64.dmg
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop="$(dirname "$here")"
root="$(dirname "$desktop")"

cd "$root"

echo "== 1/3  front-end bundle"
if [ ! -d "$root/web/node_modules" ]; then
  npm --prefix web install
fi
npm --prefix web run build

echo
echo "== 2/3  frozen Python backend"
bash "$desktop/scripts/build-backend.sh"

echo
echo "== 3/3  Electron package"
cd "$desktop"
./node_modules/.bin/electron-builder --mac --arm64

echo
echo "Done."
for p in "$desktop/release/mac-arm64/Keysight Connect.app" "$desktop"/release/*.dmg; do
  [ -e "$p" ] && echo "  $p"
done
