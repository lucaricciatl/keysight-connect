#!/usr/bin/env bash
# Freeze server/entrypoint.py into a standalone executable for the Electron bundle.
#
# Output: desktop/backend-dist/keysight-backend/  (PyInstaller --onedir)
# main.js looks for desktop/backend-dist/keysight-backend/keysight-backend and,
# when present, runs that instead of `python3 -m server.app`.
#
# Uses a dedicated venv at the repo root so the freeze is reproducible and the
# system Python is left alone.  --system-site-packages lets it reuse the
# fastapi/uvicorn/pyvisa already installed there; anything missing is installed
# into the venv.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop="$(dirname "$here")"
root="$(dirname "$desktop")"
out="$desktop/backend-dist"
venv="${KEYSIGHT_VENV:-$root/.venv-desktop}"

base_python="${KEYSIGHT_PYTHON:-python3}"

if [ ! -x "$venv/bin/python" ]; then
  echo "== creating build venv: $venv"
  "$base_python" -m venv --system-site-packages "$venv"
fi
python="$venv/bin/python"

echo "== checking build dependencies"
if ! "$python" -c '
import importlib
need = ("fastapi", "uvicorn", "pyvisa", "pyvisa_py", "PyInstaller")
missing = [m for m in need if importlib.util.find_spec(m) is None]
print(" ".join(missing))
raise SystemExit(1 if missing else 0)
'; then
  echo "== installing missing packages into the venv"
  "$python" -m pip install --quiet --upgrade pip
  "$python" -m pip install --quiet pyinstaller fastapi "uvicorn[standard]" pyvisa pyvisa-py
fi

cd "$root"
rm -rf "$out"
"$python" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name keysight-backend \
  --paths "$root" \
  --distpath "$out" \
  --workpath "$desktop/.pyinstaller" \
  --specpath "$desktop/.pyinstaller" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --collect-submodules pyvisa_py \
  --collect-submodules server \
  server/entrypoint.py

echo
echo "Built: $out/keysight-backend/keysight-backend"
