// discord_channel.mjs - create (or update) the "vf-discord" channel instance for the Video Factory.
//
// Runs INSIDE the gateway container; the bot token arrives on stdin so it never appears
// on a command line, in a log or in this script's output:
//   docker exec -i -u goclaw goclaw-goclaw-1 node /tmp/vfdc/discord_channel.mjs <review-channel-id> < vf-discord.token
// The gateway token comes from the container's own GOCLAW_GATEWAY_TOKEN.
//
// The instance is bound to vf-director. Its review channel is the only chat whose approvals
// may publish Reels (reels_review_chat_ids); only APPROVER may approve (approval_allow_from).
import fs from 'node:fs';

const TOKEN = process.env.GOCLAW_GATEWAY_TOKEN || '';
const USER = process.env.GOCLAW_RPC_USER || 'trngthnh369';
const BASE = process.env.GOCLAW_HTTP || 'http://127.0.0.1:18790';
const NAME = 'vf-discord';
const DIRECTOR = 'vf-director';
const APPROVER = process.env.VF_APPROVER || '896694335670726676';
const [chatId] = process.argv.slice(2);

if (!TOKEN || !/^\d{15,22}$/.test(chatId || '')) {
  console.error('usage: node discord_channel.mjs <discord-channel-id> < bot.token');
  process.exit(2);
}
const botToken = fs.readFileSync(0, 'utf8').trim();
if (botToken.length < 50 || /\s/.test(botToken)) {
  console.error('stdin does not look like a Discord bot token');
  process.exit(2);
}

const headers = { Authorization: `Bearer ${TOKEN}`, 'X-GoClaw-User-Id': USER, 'Content-Type': 'application/json' };
async function http(method, url, body) {
  const res = await fetch(BASE + url, { method, headers, body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30000) });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${url} -> ${res.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : {};
}
const list = (x, key) => (Array.isArray(x) ? x : (x[key] || x.items || []));

const config = {
  dm_policy: 'disabled',
  group_policy: 'allowlist',
  allow_from: [APPROVER],
  approval_allow_from: [APPROVER],
  reels_review_chat_ids: [chatId],
  require_mention: true,
  history_limit: 30,
  reaction_level: 'full',
  default_chat_id: chatId,
};

async function main() {
  const agents = list(await http('GET', '/v1/agents'), 'agents');
  const director = agents.find((a) => a.agent_key === DIRECTOR);
  if (!director) throw new Error(`agent ${DIRECTOR} not found`);
  const instances = list(await http('GET', '/v1/channels/instances'), 'instances');
  const existing = instances.find((i) => i.name === NAME);
  const body = { agent_id: director.id, credentials: { token: botToken }, config, enabled: true };
  if (existing) {
    // The store replaces config as a whole (only credentials are merged), so keep
    // what was set elsewhere, e.g. in the web UI, and override only our keys.
    await http('PUT', `/v1/channels/instances/${existing.id}`,
      { ...body, config: { ...(existing.config || {}), ...config } });
    console.log('UPDATED', NAME, existing.id, 'review chat', chatId);
  } else {
    const created = await http('POST', '/v1/channels/instances',
      { ...body, name: NAME, display_name: 'Video Factory', channel_type: 'discord' });
    console.log('CREATED', NAME, created.id || '', 'review chat', chatId);
  }
}

main().catch((e) => { console.error('DISCORD_CHANNEL_FAIL', e.message); process.exit(1); });
