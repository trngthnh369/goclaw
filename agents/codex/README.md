> Mirror of the live DB `agent_context_files` rows for agent `codex`.
> Not mounted into the container - the DB is the source of truth. Created 2026-09-23.

Routing: `openai-codex/gpt-6-sol` -> `antigravity/ag-pro` -> `antigravity/ag-flash-38` (see `tier-policy.json`, group X).
Codex quota is reserved for this agent only; every other agent and cron runs on Gemini.
`exec` is DENIED. Env secrets are scrubbed since binary `v3.14.0-hotfix.env-scrub` (2026-09-23), but exec can still read mounted OAuth files (`/app/.codex-host/auth.json`, `/app/.claude/.credentials.json`); enable only after those are out of reach.
Discord: its own bot (channel instance `codex-discord`), `allow_from` = owner user ID only (DM and any channel the bot can see; allowlist matches chat OR sender, so channel scoping is done via Discord permissions).

Token handover rule: the ChatGPT OAuth identity is shared with the host Codex CLI.
Any `codex login`, `/worker`, `/delegate codex` or plan-review bus run on the host rotates the refresh token, and this agent then silently falls to Gemini.
After using the CLI, hand the token back: `bash skills/daily-report/sync_codex_token.sh`.
