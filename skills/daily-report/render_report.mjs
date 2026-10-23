// render_report.mjs — HTML → PNG via Chrome DevTools Protocol (chrome sidecar).
//
// Why: GoClaw's `browser` tool throws "context canceled" under the Codex provider
// (async tool context lifecycle issue). This script drives the chrome sidecar's CDP
// directly (node 24 global fetch + WebSocket), bypassing the browser tool entirely.
// Produces an ACCURATE report image (real HTML text), no API key, no Pillow.
//
// Usage (via exec tool, from workspace so it passes the /app/data deny filter):
//   node /app/workspace/_daily-report/render_report.mjs [fileUrl] [outPng]
import fs from 'node:fs';
import { lookup } from 'node:dns/promises';

const CDP_HOST = process.env.CDP_HOST || 'chrome';
const CDP_PORT = process.env.CDP_PORT || '9222';
const fileUrl = process.argv[2] || 'file:///app/workspace/_daily-report/render/report.html';
const outPath = process.argv[3] || '/app/workspace/_daily-report/render/report.png';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // Chrome's CDP /json endpoints reject hostname Host headers (only IP/localhost),
  // so resolve the sidecar to an IP and use that for the HTTP control calls.
  const { address: ip } = await lookup(CDP_HOST);
  const CDP = `http://${ip}:${CDP_PORT}`;

  // 1. open a fresh tab on the sidecar
  let tab;
  try {
    tab = await (await fetch(`${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
  } catch {
    tab = await (await fetch(`${CDP}/json/new?about:blank`)).json();
  }
  const wsUrl = tab.webSocketDebuggerUrl;
  if (!wsUrl) throw new Error('no webSocketDebuggerUrl from sidecar');

  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = {};
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const i = ++id;
      pending[i] = { res, rej };
      ws.send(JSON.stringify({ id: i, method, params }));
    });

  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = (e) => rej(new Error('ws: ' + (e.message || e)));
  });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data.toString());
    if (m.id && pending[m.id]) {
      if (m.error) pending[m.id].rej(new Error(JSON.stringify(m.error)));
      else pending[m.id].res(m.result);
      delete pending[m.id];
    }
  };

  // 2. portrait viewport @2x, navigate, wait for render
  await send('Emulation.setDeviceMetricsOverride', {
    width: 800, height: 1200, deviceScaleFactor: 2, mobile: false,
  });
  await send('Page.enable');
  await send('Page.navigate', { url: fileUrl });
  await sleep(1800); // let fonts/layout settle

  // 3. full-page screenshot clipped to real content height
  const metrics = await send('Page.getLayoutMetrics');
  const size = metrics.cssContentSize || metrics.contentSize || { width: 800, height: 1200 };
  const shot = await send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: Math.ceil(size.width), height: Math.ceil(size.height), scale: 2 },
  });

  fs.writeFileSync(outPath, Buffer.from(shot.data, 'base64'));
  try { await fetch(`${CDP}/json/close/${tab.id}`); } catch {}
  ws.close();
  console.log('PNG=' + outPath);
  process.exit(0);
}

main().catch((e) => {
  console.error('RENDER_ERR ' + (e.message || e));
  process.exit(1);
});
