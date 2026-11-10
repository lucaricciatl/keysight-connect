#!/usr/bin/env bash
# Publish the built bundle as a GitHub release.
#
#   npm run dist          # first: builds release/mac-arm64/Keysight Connect.app + .dmg
#   bash scripts/release.sh            # tags v<package.json version>
#   bash scripts/release.sh v0.2.0     # or an explicit tag
#
# Requires `gh` to be authenticated with contents:write on the origin repo.
# The .app is zipped with ditto so symlinks (and the code signature) survive;
# a plain zip of the folder produces an app that will not launch.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop="$(dirname "$here")"
cd "$desktop"

version="$(node -p "require('./package.json').version")"
tag="${1:-v$version}"

app="release/mac-arm64/Keysight Connect.app"
dmg="release/Keysight Connect-$version-arm64.dmg"
zip="release/Keysight Connect-$version-arm64.app.zip"

[ -d "$app" ] || { echo "missing $app — run 'npm run dist' first" >&2; exit 1; }
[ -f "$dmg" ] || { echo "missing $dmg — run 'npm run dist' first" >&2; exit 1; }

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated. Run: gh auth login" >&2
  exit 1
fi

echo "== zipping the .app (ditto, signature-preserving)"
ditto -c -k --sequesterRsrc --keepParent "$app" "$zip"
echo "   $(du -h "$zip" | cut -f1)  $zip"

notes="$(mktemp)"
cat >"$notes" <<NOTES
Keysight Connect $version — desktop app for Keysight B2901B SMUs.

The app starts its own instrument service (frozen Python backend) on launch:
no Python, no terminal, no browser tab.

Download
- \`Keysight Connect-$version-arm64.dmg\` — open, drag to /Applications
- \`Keysight Connect-$version-arm64.app.zip\` — unzip, drag to /Applications

Requirements
- Apple Silicon (arm64) macOS

Notes
- Ad-hoc signed, not notarised: the first launch needs right-click → Open,
  or \`xattr -dr com.apple.quarantine "/Applications/Keysight Connect.app"\`.
- Sessions are written to ~/Library/Application Support/Keysight Connect/data.
- Without hardware, run \`python3 mock_b2901b.py --port 5025\` and connect to
  127.0.0.1:5025 in the sidebar.
NOTES

if gh release view "$tag" >/dev/null 2>&1; then
  echo "== release $tag exists, uploading assets"
  gh release upload "$tag" "$zip" "$dmg" --clobber
else
  echo "== creating release $tag"
  gh release create "$tag" "$zip" "$dmg" \
    --title "Keysight Connect $version" \
    --notes-file "$notes" \
    --target "$(git rev-parse --abbrev-ref HEAD)"
fi

rm -f "$notes"
echo
gh release view "$tag" --json url,assets \
  --template '{{.url}}{{"\n"}}{{range .assets}}  {{.name}}  {{.size}} bytes{{"\n"}}{{end}}'
