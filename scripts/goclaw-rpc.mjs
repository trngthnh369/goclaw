#!/usr/bin/env node
// goclaw-rpc.mjs — one-shot WS RPC client cho gateway (Node >= 22, global WebSocket).
// Dùng cho các method CHƯA có HTTP route / CLI flag đầy đủ (vd heartbeat.set với channel+active_hours).
//
//   GOCLAW_TOKEN=... node scripts/goclaw-rpc.mjs <method> '<params-json>' [ws-url]
//   ws-url mặc định ws://127.0.0.1:18790/ws
//
// Token: KHÔNG truyền qua argv (lộ trong ps/log) — chỉ đọc từ env GOCLAW_TOKEN.

const [method, paramsRaw, urlArg] = process.argv.slice(2);
const url = urlArg || process.env.GOCLAW_WS || 'ws://127.0.0.1:18790/ws';
const token = process.env.GOCLAW_TOKEN;

if (!method) {
  console.error('usage: GOCLAW_TOKEN=... node goclaw-rpc.mjs <method> \'<params-json>\' [ws-url]');
  process.exit(2);
}
if (!token) {
  console.error('GOCLAW_TOKEN env is required');
  process.exit(2);
}

const params = paramsRaw ? JSON.parse(paramsRaw) : {};
const ws = new WebSocket(url);
const timer = setTimeout(() => {
  console.error('timeout waiting for gateway');
  process.exit(1);
}, 30000);

let stage = 'connect';

ws.addEventListener('open', () => {
  ws.send(JSON.stringify({ type: 'req', id: 'c1', method: 'connect', params: { token } }));
});

ws.addEventListener('message', (ev) => {
  const frame = JSON.parse(ev.data);
  if (frame.type !== 'res') return; // ignore event frames
  if (!frame.ok) {
    clearTimeout(timer);
    console.error(`${stage} failed:`, JSON.stringify(frame.error));
    process.exit(1);
  }
  if (stage === 'connect') {
    stage = method;
    ws.send(JSON.stringify({ type: 'req', id: 'c2', method, params }));
    return;
  }
  clearTimeout(timer);
  console.log(JSON.stringify(frame.payload, null, 2));
  ws.close();
  process.exit(0);
});

ws.addEventListener('error', (e) => {
  clearTimeout(timer);
  console.error('ws error:', e.message || e.type);
  process.exit(1);
});
