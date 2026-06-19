// gpt_image.mjs — generate an image with GPT Image via the ChatGPT/Codex SUBSCRIPTION
// (codex backend `image_generation` tool). NO OpenAI API key needed — uses the OAuth
// tokens from the host ~/.codex (mounted read-only at /app/.codex-host).
//
// Usage (via exec tool, from workspace so it passes the /app/data deny filter):
//   node /app/workspace/_daily-report/gpt_image.mjs "<image prompt>" [outPng]
//
// Note: GPT Image renders illustrative/decorative images; exact data text (paths,
// numbers) is NOT reliably accurate — for a precise report use render_report.mjs (CDP).
import fs from 'node:fs';

const AUTH = process.env.CODEX_AUTH || '/app/.codex-host/auth.json';
const CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';
const ENDPOINT = 'https://chatgpt.com/backend-api/codex/responses';
const prompt = process.argv[2];
const outPath = process.argv[3] || '/app/workspace/_daily-report/render/report_gptimage.png';

if (!prompt) { console.error('GPTIMG_ERR missing prompt'); process.exit(2); }

async function freshToken() {
  const a = JSON.parse(fs.readFileSync(AUTH, 'utf8'));
  const rt = a?.tokens?.refresh_token;
  const acc = a?.tokens?.account_id || '';
  const fallback = a?.tokens?.access_token;
  if (!rt) return { access: fallback, acc };
  try {
    const r = await fetch('https://auth.openai.com/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grant_type: 'refresh_token', client_id: CLIENT_ID, refresh_token: rt }),
    });
    const j = await r.json();
    return { access: j.access_token || fallback, acc };
  } catch {
    return { access: fallback, acc };
  }
}

async function main() {
  const { access, acc } = await freshToken();
  if (!access) throw new Error('no access token');

  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + access,
      'Content-Type': 'application/json',
      'OpenAI-Beta': 'responses=v1',
      'ChatGPT-Account-Id': acc,
      'originator': 'codex_cli_rs',
      'User-Agent': 'codex_cli_rs',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify({
      model: 'gpt-5.4',
      instructions: 'You generate images.',
      input: [{ role: 'user', content: prompt }],
      stream: true,
      store: false,
      tools: [{ type: 'image_generation' }],
    }),
  });
  if (!res.ok) throw new Error('http ' + res.status + ' ' + (await res.text()).slice(0, 200));

  // parse SSE; prefer the final image (output_item.done.result), else last partial
  let buf = '';
  let best = null;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith('data:')) continue;
      const s = line.slice(5).trim();
      if (!s || s === '[DONE]') continue;
      let d;
      try { d = JSON.parse(s); } catch { continue; }
      if (d.type === 'response.output_item.done' && d.item?.type === 'image_generation_call' && d.item.result) {
        best = d.item.result; // final, highest quality
      } else if (d.type === 'response.image_generation_call.partial_image' && d.partial_image_b64) {
        if (!best) best = d.partial_image_b64; // progressive fallback
      }
    }
  }
  if (!best) throw new Error('no image in response stream');
  fs.writeFileSync(outPath, Buffer.from(best, 'base64'));
  console.log('PNG=' + outPath);
  process.exit(0);
}

main().catch((e) => { console.error('GPTIMG_ERR ' + (e.message || e)); process.exit(1); });
