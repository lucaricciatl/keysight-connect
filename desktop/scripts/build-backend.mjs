#!/usr/bin/env node
/**
 * Freeze server/entrypoint.py into a standalone executable for the Electron
 * bundle.  Cross-platform replacement for the old bash script.
 *
 * Output (electron-builder copies it to Resources/backend/keysight-backend):
 *   backend-dist/keysight-backend/keysight-backend(.exe)
 *
 * A dedicated venv at the repo root keeps the freeze reproducible and the
 * system Python untouched.  --system-site-packages lets it reuse an existing
 * fastapi/uvicorn/pyvisa; anything missing is installed into the venv.
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const root = path.resolve(desktop, '..');
const isWin = process.platform === 'win32';

const outDir = path.join(desktop, 'backend-dist');
const venv = process.env.KEYSIGHT_VENV || path.join(root, '.venv-desktop');
const venvPython = isWin
  ? path.join(venv, 'Scripts', 'python.exe')
  : path.join(venv, 'bin', 'python3');

const basePython = process.env.KEYSIGHT_PYTHON || (isWin ? 'python' : 'python3');

function run(cmd, args, opts = {}) {
  console.log(`\n$ ${cmd} ${args.join(' ')}`);
  const res = spawnSync(cmd, args, { stdio: 'inherit', cwd: root, ...opts });
  if (res.error) {
    console.error(`failed to start ${cmd}: ${res.error.message}`);
    process.exit(1);
  }
  if (res.status !== 0) process.exit(res.status ?? 1);
  return res;
}

if (!fs.existsSync(venvPython)) {
  console.log(`== creating build venv: ${venv}`);
  run(basePython, ['-m', 'venv', '--system-site-packages', venv]);
}

const check = spawnSync(
  venvPython,
  ['-c', 'import importlib.util, sys; need = ("fastapi", "uvicorn", "pyvisa", "pyvisa_py", "PyInstaller"); '
    + 'missing = [m for m in need if importlib.util.find_spec(m) is None]; '
    + 'print(" ".join(missing)); sys.exit(1 if missing else 0)'],
  { encoding: 'utf8' },
);

if (check.error) {
  console.error(`cannot run ${venvPython}: ${check.error.message}`);
  process.exit(1);
}

if (check.status !== 0) {
  const missing = (check.stdout || '').trim();
  console.log(`== installing into the venv: ${missing || check.stderr.trim() || 'unknown'}`);
  run(venvPython, ['-m', 'pip', 'install', '--quiet', '--upgrade', 'pip']);
  run(venvPython, ['-m', 'pip', 'install', '--quiet',
    'pyinstaller', 'fastapi', 'uvicorn[standard]', 'pyvisa', 'pyvisa-py']);
}

fs.rmSync(outDir, { recursive: true, force: true });

run(venvPython, [
  '-m', 'PyInstaller',
  '--noconfirm',
  '--clean',
  '--onedir',
  '--name', 'keysight-backend',
  '--paths', root,
  '--distpath', outDir,
  '--workpath', path.join(desktop, '.pyinstaller'),
  '--specpath', path.join(desktop, '.pyinstaller'),
  '--hidden-import', 'uvicorn.logging',
  '--hidden-import', 'uvicorn.loops.auto',
  '--hidden-import', 'uvicorn.protocols.http.auto',
  '--hidden-import', 'uvicorn.protocols.websockets.auto',
  '--hidden-import', 'uvicorn.lifespan.on',
  '--collect-submodules', 'pyvisa_py',
  '--collect-submodules', 'server',
  path.join(root, 'server', 'entrypoint.py'),
]);

const exe = path.join(outDir, 'keysight-backend', `keysight-backend${isWin ? '.exe' : ''}`);
if (!fs.existsSync(exe)) {
  console.error(`\nPyInstaller reported success but ${exe} is missing`);
  process.exit(1);
}
console.log(`\nBuilt: ${exe}`);
