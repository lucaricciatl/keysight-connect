#!/usr/bin/env node
/**
 * Publish the built bundle as a GitHub release.
 *
 *   npm run dist                    # first
 *   npm run release                 # tags v<package.json version>
 *   npm run release -- v0.2.0       # explicit tag
 *
 * Needs `gh` authenticated (or GH_TOKEN set) with contents:write.
 *
 * macOS: zips the .app with ditto so symlinks and the code signature survive —
 * a plain folder zip produces an app that will not launch.  Windows: attaches
 * the NSIS installer and the portable .exe as they come out of electron-builder.
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const release = path.join(desktop, 'release');
const isWin = process.platform === 'win32';

const pkg = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8'));
const version = pkg.version;

const argv = process.argv.slice(2);
const tag = argv.find((a) => !a.startsWith('-')) || `v${version}`;

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', cwd: desktop, ...opts });
  if (res.error) {
    console.error(`failed to start ${cmd}: ${res.error.message}`);
    process.exit(1);
  }
  if (res.status !== 0) process.exit(res.status ?? 1);
}

function list(dir) {
  return fs.existsSync(dir) ? fs.readdirSync(dir) : [];
}

const assets = [];

// Collect whatever this platform produced — and anything else already sitting
// in release/ (e.g. Windows artifacts built on a Windows machine or downloaded
// from CI), so one `npm run release` can publish both platforms.
const exes = list(release).filter((f) => f.toLowerCase().endsWith('.exe'));
for (const f of exes) assets.push(path.join(release, f));

const dmgs = list(release).filter((f) => f.toLowerCase().endsWith('.dmg'));
for (const f of dmgs) assets.push(path.join(release, f));

if (!isWin) {
  const app = path.join(release, 'mac-arm64', 'Keysight Connect.app');
  if (fs.existsSync(app)) {
    const zip = path.join(release, `Keysight Connect-${version}-arm64.app.zip`);
    console.log('== zipping the .app (ditto, signature-preserving)');
    run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', app, zip]);
    assets.unshift(zip);
  }
}

if (!assets.length) {
  console.error(`nothing to publish in ${release} — run 'npm run dist' first`);
  process.exit(1);
}

// `gh auth status` insists on read:org, which a plain repo-scoped PAT lacks and
// release publishing does not need.  Ask the API instead.
const auth = spawnSync('gh', ['api', 'user'], { encoding: 'utf8' });
if (auth.status !== 0) {
  console.error("gh cannot authenticate. Run 'gh auth login', or export GH_TOKEN.");
  process.exit(1);
}

const notesPath = path.join(os.tmpdir(), `keysight-notes-${Date.now()}.md`);
fs.writeFileSync(notesPath, `Keysight Connect ${version} — desktop app for Keysight B2901B SMUs.

The app starts its own instrument service (frozen Python backend) on launch:
no Python, no terminal, no browser tab.

Download
- macOS (Apple Silicon) — \`Keysight Connect-${version}-arm64.dmg\`: open, drag to /Applications
- Windows (x64) — \`Keysight Connect Setup ${version}.exe\` (installer) or the portable .exe

Notes
- macOS build is ad-hoc signed, not notarised: first launch needs
  right-click → Open, or
  \`xattr -dr com.apple.quarantine "/Applications/Keysight Connect.app"\`.
- Windows build is unsigned: SmartScreen will ask once ("More info" → "Run anyway").
- Sessions go to the per-user app-data folder (File → Open Data Folder).
- Without hardware, run \`python3 mock_b2901b.py --port 5025\` and connect to
  127.0.0.1:5025 in the sidebar.
`);

const exists = spawnSync('gh', ['release', 'view', tag], { encoding: 'utf8' }).status === 0;

if (exists) {
  console.log(`== release ${tag} exists, uploading assets`);
  run('gh', ['release', 'upload', tag, ...assets, '--clobber']);
} else {
  console.log(`== creating release ${tag}`);
  run('gh', ['release', 'create', tag, ...assets,
    '--title', `Keysight Connect ${version}`,
    '--notes-file', notesPath,
    '--target', 'main']);
}

fs.rmSync(notesPath, { force: true });

console.log();
run('gh', ['release', 'view', tag, '--json', 'url,assets',
  '--template', '{{.url}}{{"\\n"}}{{range .assets}}  {{.name}}  {{.size}} bytes{{"\\n"}}{{end}}']);
