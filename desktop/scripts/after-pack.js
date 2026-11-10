'use strict';

/**
 * electron-builder afterPack hook.
 *
 * There is no Developer ID here, so electron-builder skips signing — but on
 * Apple Silicon an unsigned bundle can be killed on launch ("damaged"), and a
 * quarantined .dmg makes that certain.  An ad-hoc signature is enough for a
 * local, double-clickable app.
 *
 * Runs before the DMG target, so the disk image contains the signed bundle.
 */

const { execFileSync } = require('node:child_process');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const name = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${name}.app`);

  console.log(`  • ad-hoc signing ${name}.app`);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], {
    stdio: 'inherit',
  });

  try {
    execFileSync('codesign', ['--verify', '--deep', '--strict', appPath], {
      stdio: 'inherit',
    });
    console.log('  • signature verified');
  } catch (err) {
    // Fail the build: shipping an app that will not launch is worse than no app.
    throw new Error(`ad-hoc signing failed: ${err.message}`);
  }
};
