'use strict';

/**
 * Verification harness for the Electron shell.
 *
 *   node scripts/probe.js            -> DOM text + screenshot of the live app
 *
 * Talks to the running app over the Chrome DevTools Protocol, so what it
 * reports is what the renderer actually has in its DOM — not a guess from a
 * screenshot.  Start the app with a debugging port first:
 *
 *   ./node_modules/.bin/electron . --remote-debugging-port=9222
 */

const fs = require('node:fs');
const path = require('node:path');

const CDP_PORT = process.env.CDP_PORT || 9222;

async function targets() {
  const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json`);
  if (!res.ok) throw new Error(`CDP HTTP ${res.status}`);
  return res.json();
}

function cdp(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let nextId = 1;

  const ready = new Promise((resolve, reject) => {
    socket.addEventListener('open', () => resolve());
    socket.addEventListener('error', () => reject(new Error('CDP socket error')));
  });

  socket.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    const waiter = pending.get(msg.id);
    if (waiter) {
      pending.delete(msg.id);
      waiter(msg);
    }
  });

  return {
    ready,
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve) => pending.set(id, resolve));
    },
    close() { socket.close(); },
  };
}

(async () => {
  const list = await targets();
  const page = list.find((t) => t.type === 'page' && /127\.0\.0\.1/.test(t.url));
  if (!page) throw new Error(`no app page among targets: ${list.map((t) => t.url).join(', ')}`);

  const conn = cdp(page.webSocketDebuggerUrl);
  await conn.ready;

  const result = await conn.send('Runtime.evaluate', {
    expression: `(() => {
      const text = document.body.innerText;
      const badges = [...document.querySelectorAll('*')]
        .filter(el => el.children.length === 0 && /connect/i.test(el.textContent || ''))
        .map(el => el.textContent.trim())
        .slice(0, 12);
      return JSON.stringify({
        url: location.href,
        title: document.title,
        bodyChars: text.length,
        badges,
        hasReadings: /\\b100\\b/.test(text),
        text: text.slice(0, 4000),
      });
    })()`,
    returnByValue: true,
    awaitPromise: true,
  });

  const payload = JSON.parse(result.result.result.value);
  console.log('URL      :', payload.url);
  console.log('TITLE    :', payload.title);
  console.log('BODYCHARS:', payload.bodyChars);
  console.log('BADGES   :', JSON.stringify(payload.badges));
  console.log('--- body text ---');
  console.log(payload.text);

  const shot = await conn.send('Page.captureScreenshot', { format: 'png' });
  if (shot.result && shot.result.data) {
    const out = path.join('/tmp', 'keysight-window.png');
    fs.writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
    console.log(`\nscreenshot: ${out}`);
  }

  conn.close();
})().catch((err) => {
  console.error('probe failed:', err.message);
  process.exit(1);
});
