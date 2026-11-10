'use strict';

/**
 * Minimal, read-only bridge.  The renderer is the existing web app and does
 * not need Electron APIs; this only lets the UI (or the console) tell that it
 * is running inside the desktop shell rather than a browser tab.
 */

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('keysight', {
  isDesktop: true,
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
});
