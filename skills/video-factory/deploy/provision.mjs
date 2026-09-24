// provision.mjs - create (or update) the Video Factory agents, context files, team and links.
//
// Runs INSIDE the gateway container (the API listens on 127.0.0.1:18790 there):
//   GOCLAW_GATEWAY_TOKEN=... node provision.mjs <agents-dir> [--dry-run]
// <agents-dir> holds <agent_key>.{SOUL,IDENTITY,CAPABILITIES}.md.
// Idempotent: an existing agent is updated with PUT (never deleted), files are re-set,
// the team and links are created only when missing.
import fs from 'node:fs';
import path from 'node:path';

const TOKEN = process.env.GOCLAW_GATEWAY_TOKEN || '';
const USER = process.env.GOCLAW_RPC_USER || 'trngthnh369';
const BASE = process.env.GOCLAW_HTTP || 'http://127.0.0.1:18790';
const WS_URL = process.env.GOCLAW_WS_URL || 'ws://127.0.0.1:18790/ws';
const [agentsDir, flag] = process.argv.slice(2);
const DRY = flag === '--dry-run';
if (!TOKEN || !agentsDir) { console.error('usage: GOCLAW_GATEWAY_TOKEN=... node provision.mjs <agents-dir>'); process.exit(2); }

const COMPACTION = { maxHistoryShare: 0.25, keepLastMessages: 6, reserveTokensFloor: 20000 };
const fallback = (candidates) => ({ enabled: true, strategy: 'priority_order', candidates, max_attempts: candidates.length + 1, cooldown_enabled: true });

// Tool policy is a deny list. Only the director may exec generally, create images and message;
// the other two may exec (studio.py submit) but never message, create media or orchestrate.
// The scriptwriter and reviewer keep write_file: they hand JSON to `submit --file` through the
// job inbox, because exec's shell guard scans a heredoc body like any other command text.
const COMMON_DENY = ['spawn', 'team_tasks', 'cron', 'sessions_send', 'publish_skill', 'skill_manage', 'browser',
  'create_video', 'create_audio', 'tts', 'stt', 'read_audio', 'read_video', 'sandbox_exec', 'sandbox_write'];
const AGENTS = [
  {
    agent_key: 'vf-director', display_name: 'Video Factory Director', emoji: '🎬',
    frontmatter: 'Lead of the Video Factory team: turns a topic into a reviewed short video (research, script, images, voice, render) and delivers it for human approval.',
    provider: 'agy-claude', model: 'ag-opus', fallback: [{ provider: 'antigravity', model: 'ag-pro' }],
    max_tool_iterations: 90,
    deny: [...COMMON_DENY, 'edit', 'write_file'],
  },
  {
    agent_key: 'vf-scriptwriter', display_name: 'Video Factory Scriptwriter', emoji: '✍️',
    frontmatter: 'Researches a topic with real sources and writes a fact-cited Vietnamese short-video script for the Video Factory.',
    provider: 'agy-claude', model: 'ag-opus', fallback: [{ provider: 'antigravity', model: 'ag-pro' }],
    max_tool_iterations: 60,
    deny: [...COMMON_DENY, 'message', 'delegate', 'create_image', 'read_image', 'edit'],
  },
  {
    agent_key: 'vf-reviewer', display_name: 'Video Factory Reviewer', emoji: '🔍',
    frontmatter: 'Independent fact-checker and visual QA for the Video Factory: verifies every cited fact at its source and inspects every rendered frame.',
    provider: 'antigravity', model: 'ag-pro', fallback: [{ provider: 'antigravity', model: 'ag-pro-low' }],
    max_tool_iterations: 50,
    deny: [...COMMON_DENY, 'message', 'delegate', 'create_image', 'edit'],
  },
];
const TEAM = { name: 'Video Factory', lead: 'vf-director', members: ['vf-scriptwriter', 'vf-reviewer'],
  description: 'Short-form knowledge videos: research -> script -> fact check -> images + voice -> render -> visual QA -> human review.' };

const headers = { Authorization: `Bearer ${TOKEN}`, 'X-GoClaw-User-Id': USER, 'Content-Type': 'application/json' };
async function http(method, url, body) {
  if (DRY && method !== 'GET') { console.log('DRY', method, url); return {}; }
  const res = await fetch(BASE + url, { method, headers, body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30000) });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${url} -> ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : {};
}

function rpcClient() {
  const ws = new WebSocket(WS_URL);
  const pending = new Map();
  let n = 0;
  // A dropped socket or a stalled gateway must fail the run, not hang it.
  const failAll = (err) => { for (const { rej } of pending.values()) rej(err); pending.clear(); };
  ws.addEventListener('close', () => failAll(new Error('ws closed')));
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', () => {
      const id = 'c' + (++n);
      pending.set(id, { res, rej });
      ws.send(JSON.stringify({ type: 'req', id, method: 'connect', params: { token: TOKEN, user_id: USER } }));
    });
    ws.addEventListener('error', (e) => rej(new Error('ws ' + (e.message || 'error'))));
  });
  ws.addEventListener('message', (ev) => {
    let f; try { f = JSON.parse(ev.data); } catch { return; }
    if (f.type !== 'res' || !pending.has(f.id)) return;
    const { res, rej } = pending.get(f.id); pending.delete(f.id);
    f.ok ? res(f.payload) : rej(new Error(JSON.stringify(f.error)));
  });
  const call = async (method, params) => {
    await ready;
    if (DRY && !method.endsWith('.list') && !method.endsWith('.get')) { console.log('DRY rpc', method); return {}; }
    const id = 'r' + (++n);
    return new Promise((res, rej) => {
      const timer = setTimeout(() => { pending.delete(id); rej(new Error(`${method} timed out`)); }, 30000);
      pending.set(id, { res: (v) => { clearTimeout(timer); res(v); }, rej: (e) => { clearTimeout(timer); rej(e); } });
      ws.send(JSON.stringify({ type: 'req', id, method, params }));
    });
  };
  return { call, close: () => ws.close() };
}

async function main() {
  const rpc = rpcClient();
  const listing = await http('GET', '/v1/agents');
  const existing = new Map((listing.agents || listing || []).map((a) => [a.agent_key, a]));
  for (const spec of AGENTS) {
    const body = {
      agent_key: spec.agent_key, display_name: spec.display_name, frontmatter: spec.frontmatter,
      agent_type: 'predefined', provider: spec.provider, model: spec.model,
      context_window: 200000, max_tool_iterations: spec.max_tool_iterations,
      model_fallback: fallback(spec.fallback), compaction_config: COMPACTION,
      tools_config: { deny: spec.deny }, memory_config: { enabled: true },
      budget_monthly_cents: null, emoji: spec.emoji,
    };
    const found = existing.get(spec.agent_key);
    if (found) {
      await http('PUT', `/v1/agents/${found.id}`, body);
      console.log('UPDATED', spec.agent_key, found.id);
    } else {
      const created = await http('POST', '/v1/agents', body);
      console.log('CREATED', spec.agent_key, created.id || JSON.stringify(created).slice(0, 120));
    }
    for (const file of ['SOUL', 'IDENTITY', 'CAPABILITIES']) {
      const content = fs.readFileSync(path.join(agentsDir, `${spec.agent_key}.${file}.md`), 'utf8');
      await rpc.call('agents.files.set', { agentId: spec.agent_key, name: `${file}.md`, content, propagate: true });
      console.log('  file', `${file}.md`, content.length, 'chars');
    }
  }
  const teams = await rpc.call('teams.list', {});
  const teamList = Array.isArray(teams) ? teams : (teams.teams || teams.items || []);
  if (!teamList.some((t) => t.name === TEAM.name && t.status !== 'archived')) {
    const team = await rpc.call('teams.create', { ...TEAM, settings: {} });
    console.log('TEAM created', (team.team || team).id || '');
  } else {
    console.log('TEAM exists');
  }
  const links = await rpc.call('agents.links.list', { agentId: TEAM.lead, direction: 'from' });
  const linkList = Array.isArray(links) ? links : (links.links || links.items || []);
  for (const member of TEAM.members) {
    if (linkList.some((l) => (l.target_agent_key || l.targetAgentKey || l.target_key) === member && l.status === 'active')) {
      console.log('LINK exists', TEAM.lead, '->', member);
      continue;
    }
    await rpc.call('agents.links.create', { sourceAgent: TEAM.lead, targetAgent: member, direction: 'outbound',
      description: 'Video Factory stage delegation', maxConcurrent: 2 });
    console.log('LINK created', TEAM.lead, '->', member);
  }
  rpc.close();
}

main().catch((e) => { console.error('PROVISION_FAIL', e.message); process.exit(1); });
