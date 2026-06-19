#!/usr/bin/env node
// ws_rpc.mjs — tiny WS RPC caller for the GoClaw gateway. Connects (admin via gateway token),
// runs ONE method, prints the JSON payload, exits. Used by host automation to toggle/verify cron
// without standing up a full client.
//
// Usage (inside goclaw container, node 24 has global WebSocket):
//   GOCLAW_GATEWAY_TOKEN=... node ws_rpc.mjs <method> '<paramsJSON>'
// e.g. node ws_rpc.mjs cron.toggle '{"jobId":"019e973c-...","enabled":false}'
const [method, paramsJson] = process.argv.slice(2);
const TOKEN = process.env.GOCLAW_GATEWAY_TOKEN || "";
const USER = process.env.GOCLAW_RPC_USER || "trngthnh369";
const URL = process.env.GOCLAW_WS_URL || "ws://127.0.0.1:18790/ws";
if (!method) { console.error("usage: ws_rpc.mjs <method> '<paramsJSON>'"); process.exit(2); }

const ws = new WebSocket(URL);
const id = () => Math.random().toString(36).slice(2);
let connId = id(), callId = id(), done = false;

const fail = (m) => { if (!done) { done = true; console.error("RPC_FAIL", m); process.exit(1); } };
setTimeout(() => fail("timeout"), 15000);

ws.addEventListener("open", () => {
  ws.send(JSON.stringify({ type: "req", id: connId, method: "connect",
    params: { token: TOKEN, user_id: USER } }));
});
ws.addEventListener("error", (e) => fail(String(e.message || e)));
ws.addEventListener("message", (ev) => {
  let f; try { f = JSON.parse(ev.data); } catch { return; }
  if (f.type !== "res") return;
  if (f.id === connId) {
    if (!f.ok) return fail("connect: " + JSON.stringify(f.error));
    ws.send(JSON.stringify({ type: "req", id: callId, method,
      params: paramsJson ? JSON.parse(paramsJson) : {} }));
  } else if (f.id === callId) {
    done = true;
    if (!f.ok) { console.error("ERR", JSON.stringify(f.error)); process.exit(1); }
    console.log(JSON.stringify(f.payload));
    process.exit(0);
  }
});
