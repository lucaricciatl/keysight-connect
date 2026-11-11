#!/usr/bin/env node
/**
 * Full desktop pipeline: web bundle -> frozen backend -> installer + app.
 *
 *   npm run dist              # host platform (mac arm64, or win x64)
 *   npm run dist -- --win     # force a Windows build (needs a Windows host or wine)
 *   npm run dist -- --mac     # force a macOS build (needs macOS)
 *
 * macOS output:  release/mac-arm64/Keysight Connect.app  +  release/*.dmg
 * Windows output: release/win-unpacked/ + release/Keysight Connect Setup <ver>.exe
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const root = path.resolve(desktop, '..');
const isWin = process.platform === 'win32';
const npm = isWin ? 'npm.cmd' : 'npm';

const argv = process.argv.slice(2);
const target = argv.includes('--win') ? 'win'
  : argv.includes('--mac') ? 'mac'
  : (isWin ? 'win' : 'mac');

function run(cmd, args, opts = {}) {
  console.log(`\n$ ${cmd} ${args.join(' ')}`);
  const res = spawnSync(cmd, args, {
    stdio: 'inherit',
    shell: isWin && cmd.endsWith('.cmd'),
    ...opts,
  });
  if (res.error) {
    console.error(`failed to start ${cmd}: ${res.error.message}`);
    process.exit(1);
  }
  if (res.status !== 0) process.exit(res.status ?? 1);
}

// 1. front-end bundle
console.log('== 1/3  front-end bundle');
if (!fs.existsSync(path.join(root, 'web', 'node_modules'))) {
  run(npm, ['install'], { cwd: path.join(root, 'web') });
}
run(npm, ['run', 'build'], { cwd: path.join(root, 'web') });

// 2. frozen Python backend
console.log('\n== 2/3  frozen Python backend');
run(process.execPath, [path.join(here, 'build-backend.mjs')], { cwd: root });

// 3. Electron package
console.log(`\n== 3/3  Electron package (${target})`);
const builderCli = path.join(desktop, 'node_modules', 'electron-builder', 'cli.js');
const builderArgs = [builderCli, `--${target}`];
if (target === 'mac') builderArgs.push(process.arch === 'arm64' ? '--arm64' : '--x64');
if (target === 'win') builderArgs.push('--x64');
run(process.execPath, builderArgs, { cwd: desktop });

// summary
const release = path.join(desktop, 'release');
const found = fs.existsSync(release)
  ? fs.readdirSync(release).filter((f) => /\.(dmg|exe|app|zip)$/.test(f) || f.endsWith('.app'))
  : [];
console.log('\nDone.');
for (const f of found) console.log(`  ${path.join(release, f)}`);
