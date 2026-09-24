// cdp_render.mjs - render HTML cards and web pages to PNG on the Chrome sidecar.
//
// Usage: node cdp_render.mjs <jobs.json>
//   jobs.json = [{"mode":"html","url":"file:///app/workspace/...","out":"/abs/x.png",
//                 "width":1080,"height":1920,"scale":2},
//                {"mode":"url","url":"https://example.com","out":"/abs/y.png",
//                 "viewport":"mobile","maxHeight":5760}]
// Prints one line per job: "OK <out> <w>x<h>" or "ERR <out> <reason>"; exit 1 if any failed.
//
// Why a separate incognito browser context: the sidecar is shared with GoClaw's
// browser tool, and a page screenshot must not carry anyone's cookies or storage.
// Why the IP: Chrome's /json endpoints reject a hostname Host header.
// Node 24 ships fetch and WebSocket, so this has no dependencies.
import fs from 'node:fs';
import { lookup } from 'node:dns/promises';
import net from 'node:net';

const CDP_HOST = process.env.CDP_HOST || 'chrome';
const CDP_PORT = process.env.CDP_PORT || '9222';
const NAV_TIMEOUT_MS = 25000;
const CONNECT_TIMEOUT_MS = 15000;

// Mirrors vfcore/netguard.py. The Python check resolves the name once, but Chrome
// resolves it again on its own, so a record that changes in between (DNS
// rebinding) would slip past; every response a page loads is checked here, on the
// address Chrome actually connected to.
const PRIVATE_V4 = [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16],
  ['172.16.0.0', 12], ['192.168.0.0', 16], ['224.0.0.0', 3]];
const v4num = (ip) => ip.split('.').reduce((n, part) => n * 256 + Number(part), 0);
function isPrivateAddress(raw) {
  let ip = String(raw || '').replace(/^\[|\]$/g, '').toLowerCase();
  const mapped = ip.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mapped) ip = mapped[1];
  if (net.isIPv4(ip)) {
    return PRIVATE_V4.some(([base, bits]) => Math.floor(v4num(ip) / 2 ** (32 - bits)) === Math.floor(v4num(base) / 2 ** (32 - bits)));
  }
  if (net.isIPv6(ip)) {
    return ip === '::' || ip === '::1' || /^f[cd]/.test(ip) || /^fe[89ab]/.test(ip) || ip.startsWith('ff');
  }
  return false;   // no address (served from cache or a data: URL)
}
const MOBILE_UA =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/128.0.0.0 Mobile Safari/537.36';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data.toString());
      if (msg.id && this.pending.has(msg.id)) {
        const { res, rej, timer } = this.pending.get(msg.id);
        clearTimeout(timer);
        this.pending.delete(msg.id);
        if (msg.error) rej(new Error(msg.error.message || JSON.stringify(msg.error)));
        else res(msg.result);
      } else if (msg.method) {
        for (const fn of this.listeners) fn(msg);
      }
    };
  }
  send(method, params = {}, sessionId = undefined, timeoutMs = 30000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rej(new Error(`${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { res, rej, timer });
      const frame = { id, method, params };
      if (sessionId) frame.sessionId = sessionId;
      this.ws.send(JSON.stringify(frame));
    });
  }
  waitFor(method, sessionId, timeoutMs) {
    return new Promise((res) => {
      const timer = setTimeout(() => { remove(); res(false); }, timeoutMs);
      const fn = (msg) => {
        if (msg.method === method && (!sessionId || msg.sessionId === sessionId)) {
          clearTimeout(timer);
          remove();
          res(true);
        }
      };
      const remove = () => { this.listeners = this.listeners.filter((f) => f !== fn); };
      this.listeners.push(fn);
    });
  }
}

async function connect() {
  const { address } = await lookup(CDP_HOST);
  const version = await (await fetch(`http://${address}:${CDP_PORT}/json/version`,
    { signal: AbortSignal.timeout(CONNECT_TIMEOUT_MS) })).json();
  const wsUrl = version.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//${address}:${CDP_PORT}/`);
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => {
    const timer = setTimeout(() => rej(new Error('ws: connect timed out')), CONNECT_TIMEOUT_MS);
    ws.onopen = () => { clearTimeout(timer); res(); };
    ws.onerror = (e) => { clearTimeout(timer); rej(new Error('ws: ' + (e.message || 'connect failed'))); };
  });
  return new Cdp(ws);
}

async function renderOne(cdp, sessionId, job) {
  const isHtml = job.mode === 'html';
  let width, height, scale, mobile;
  if (isHtml) {
    width = job.width; height = job.height; scale = job.scale || 1; mobile = false;
  } else if (job.viewport === 'desktop') {
    width = 1440; height = 900; scale = job.scale || 1.3334; mobile = false;
  } else {
    width = 412; height = 915; scale = job.scale || 2.622; mobile = true;
  }
  await cdp.send('Emulation.setDeviceMetricsOverride',
    { width, height, deviceScaleFactor: scale, mobile }, sessionId);
  await cdp.send('Emulation.setUserAgentOverride',
    { userAgent: mobile ? MOBILE_UA : 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36' },
    sessionId);
  // An empty override clears a transparent background left by a previous job.
  await cdp.send('Emulation.setDefaultBackgroundColorOverride',
    job.transparent ? { color: { r: 0, g: 0, b: 0, a: 0 } } : {}, sessionId);
  let privateHit = null;
  const watch = (msg) => {
    if (msg.method === 'Network.responseReceived' && msg.sessionId === sessionId
        && isPrivateAddress(msg.params.response.remoteIPAddress)) {
      privateHit = privateHit || `${msg.params.response.url} from ${msg.params.response.remoteIPAddress}`;
    }
  };
  if (!isHtml) cdp.listeners.push(watch);
  try {
    const loaded = cdp.waitFor('Page.loadEventFired', sessionId, NAV_TIMEOUT_MS);
    const nav = await cdp.send('Page.navigate', { url: job.url }, sessionId, NAV_TIMEOUT_MS);
    if (nav.errorText) throw new Error('navigation failed: ' + nav.errorText);
    await loaded;
    if (privateHit) throw new Error('refused: the page loaded ' + privateHit + ', a non-public address');
  } finally {
    cdp.listeners = cdp.listeners.filter((f) => f !== watch);
  }
  // Fonts and late layout: wait for document.fonts, then a short settle.
  await cdp.send('Runtime.evaluate',
    { expression: 'document.fonts ? document.fonts.ready.then(() => true) : true', awaitPromise: true },
    sessionId, 15000).catch(() => {});
  await sleep(isHtml ? 250 : 1800);

  let clipHeight = height;
  if (!isHtml) {
    const metrics = await cdp.send('Page.getLayoutMetrics', {}, sessionId);
    const content = metrics.cssContentSize || metrics.contentSize;
    const maxCss = Math.floor((job.maxHeight || height * scale * 3) / scale);
    clipHeight = Math.max(height, Math.min(Math.ceil(content.height), maxCss));
  }
  if (privateHit) throw new Error('refused: the page loaded ' + privateHit + ', a non-public address');
  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: !isHtml,
    fromSurface: true,
    clip: { x: 0, y: 0, width, height: clipHeight, scale },
  }, sessionId, 60000);
  fs.writeFileSync(job.out, Buffer.from(shot.data, 'base64'));
  return `${Math.round(width * scale)}x${Math.round(clipHeight * scale)}`;
}

async function main() {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const cdp = await connect();
  const { browserContextId } = await cdp.send('Target.createBrowserContext', { disposeOnDetach: true });
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank', browserContextId });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Network.enable', {}, sessionId);
  let failed = 0;
  for (const job of jobs) {
    try {
      const size = await renderOne(cdp, sessionId, job);
      console.log(`OK ${job.out} ${size}`);
    } catch (e) {
      failed++;
      console.log(`ERR ${job.out} ${(e && e.message) || e}`);
    }
  }
  await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  await cdp.send('Target.disposeBrowserContext', { browserContextId }).catch(() => {});
  cdp.ws.close();
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.log('ERR * ' + ((e && e.message) || e));
  process.exit(1);
});
