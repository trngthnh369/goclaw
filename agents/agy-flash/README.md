> Mirror of the live DB `agent_context_files` rows for agent `agy-flash`.
> Not mounted into the container - the DB is the source of truth. Created 2026-09-24.
> README itself is repo-only: `agents.files.set` does not accept it.

Member of team "Codex Crew" (lead `codex`), plan: `~/.claude/plans/h-y-l-n-plan-c-u-eventual-snail.md`.
Routing: `antigravity/ag-flash-38` -> `antigravity/ag-pro`, Gemini only (see `tier-policy.json`, group Y).
No channel instance, no `agent_links`: work arrives only as dispatched team tasks, plus HTTP/WS calls by operator-key holders.

This member reads untrusted web content by design, and it has exec.
`/app/workspace/_daily-report/.gwtoken` (operator.write key) is readable by exec; the owner accepted this residual on 2026-09-24 (plan option A).

Tool policy is a deny list copied from `codex` (minus `team_tasks`) plus `delegate`, `spawn`, `memory_search`, `memory_get`, `read_audio`, `read_video`, `create_video`, `create_audio`, `tts`, `stt`.
Known gap: a tool added in a later binary is enabled by default until it is added to the deny list.

Session: one per (member, team, chat). If it grows or mixes tasks, reset the `team:` session of this agent.
