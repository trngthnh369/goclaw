> Mirror of the live DB `agent_context_files` rows for agent `codex`.
> Not mounted into the container - the DB is the source of truth. Created 2026-09-23.

Routing: `openai-codex/gpt-6-sol` -> `antigravity/ag-pro` -> `antigravity/ag-flash-38` (see `tier-policy.json`, group X).
Codex quota is reserved for this agent only; every other agent and cron runs on Gemini.
`exec` is enabled since 2026-09-23, after env scrubbing (binary `env-scrub3`) and the mount cleanup: no gateway secret in the exec env, no OAuth file or raw transcript in the container. `/app/workspace/_daily-report/.gwtoken` is still readable by exec; it holds an operator.write API key, not the admin token.
Discord: its own bot (channel instance `codex-discord`), `allow_from` = owner user ID only (DM and any channel the bot can see; allowlist matches chat OR sender, so channel scoping is done via Discord permissions).

Token note: the ChatGPT OAuth identity may be shared with the host Codex CLI.
A CLI run that refreshes the token can invalidate this agent's copy (seen 2026-08-26); a 2026-09-24 plan-review run did not refresh (`~/.codex/auth.json` unchanged) and both kept working.
If the agent starts failing with `refresh_token_reused`, hand the token back: `bash skills/daily-report/sync_codex_token.sh`.

Team "Codex Crew" (since 2026-09-24): `codex` is lead, members `agy-pro` and `agy-flash` run Gemini only.
Plan and review artifacts: `~/.claude/plans/h-y-l-n-plan-c-u-eventual-snail.md`.
- Only `team_tasks` is opened for delegation; `delegate` and `spawn` stay denied.
- Team settings: `version:2` (restart recovery), `workspace_scope:isolated`, `allow_user_ids` = owner.
- Kill switch: PUT `codex` `tools_config` with `team_tasks` back in `deny`. New dispatch stops at once; running tasks finish.
- Rollback: `.deploy/codex-crew-20260924/` holds the pre-team `codex` agent JSON and context files (`agents.files.get` snapshot). Git fallback: commit `c37eab3a`.
- Export `team_tasks` before `teams.delete`: delete is a hard delete that cascades tasks.
