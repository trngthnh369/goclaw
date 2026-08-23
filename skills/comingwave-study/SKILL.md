---
name: comingwave-study
description: Deterministic study pipeline for The Coming Wave Podcast - fetches an episode's Vietnamese auto-captions, splits them into analysable parts, walks a resumable stage machine over them, merges a thesis ledger with stable identity, and delivers a Study Pack to Discord. Agents only reason over immutable artifacts; the skill owns ingest, validation, ledger identity, rendering and delivery.
license: Internal
metadata:
  author: trngthnh369
  version: "0.1.0"
  bundle_revision: "2026-08-23-006"
  runtime: python3
  forked_from: aiwave-research
  channel_id: UCCoUJTzD-gV_otqzFU85-MQ
---

# Coming Wave Study

Turns episodes of **The Coming Wave Podcast** (Linh & Son - AI, the compute and
energy supply chain, capital flows, macro) into: a Study Pack in a dedicated
Discord channel, a permanent vault document, a memory entry, a line in a
cross-episode thesis ledger, and share-ready drafts.

Forked from `aiwave-research` and reusing its shape, not its code: Python owns
everything mechanical, the model only reasons over immutable artifacts. That
split exists because `internal/pipeline/think_stage.go:138` breaks the agent loop
on any turn without a tool call - announcing the next step ends a run exactly
like finishing it - so no published artifact may depend on what a model typed.

## The three invariants

**1. A stage is done when its validator passes, not when its file exists.**
A provider can truncate tool-call arguments (`internal/tools/registry.go:257`
handles the empty-arguments case), and a model handed part 1 of 4 can simply
stop. Both produce well-formed, wrong artifacts. So `validate.py` checks schema
*and* coverage: part count against the manifest, and how far into a part's time
window the last segment reaches. A truncated artifact sends the planner back to
that stage instead of through it.

**2. Artifacts reach the model through `exec` + stdout, never `read_file`.**
Predefined agents resolve to `<base>/<agentID>` (`internal/workspace/resolver_impl.go`)
and `RestrictToWorkspace` is forced true at creation (`internal/http/agents.go:273`).
On top of that, `read_file` caps at 50 000 chars (`internal/tools/filesystem.go:230`)
and `exec` at 30 000 (`internal/tools/exec_output_cap.go:33`) - both **silently**.
Measured: one 61-minute episode is 61 809 characters, and the 97-minute Q&A
format is roughly double. Parts are sized at 20 000 chars so the emitted material
stays comfortably under the smaller cap.

**3. Write first, publish second.**
`MemoryInterceptor` (`internal/tools/filesystem_write.go:136-159`) stores the
memory document in Postgres, triggers knowledge-graph extraction, and returns -
**nothing lands on disk**. A stage machine that reads progress off the filesystem
therefore cannot see a missing memory write. So S4 is split: S4a writes the vault
and memory documents and records the memory write; S4b publishes, and refuses
without that record.

## Measured facts (2026-08-23, both formats, from the production container)

| | EP 45 `zP9R0JD80Io` | Q&A `1QsHRsawAl0` |
|---|---|---|
| duration | 61 min | 97 min |
| transcript | 61 809 chars (1012/min) | 97 408 chars (1004/min) |
| turns | 169 | 395 |
| `>>` markers | 168 | 394 |
| parts at 20k | 4, largest 19 728 | 5, largest 19 976 |
| oversized parts | none | none |

Both formats sit at ~1000 chars per minute, so part count is predictable from
duration alone: roughly one part per 20 minutes. json3 produced **zero** adjacent
duplicate cues in both - the rolling-window dedup a vtt pipeline needs reduces
here to dropping the empty `aAppend` events (exactly half of them).

The `>>` markers are why host attribution is worth attempting at all: the model
does not have to guess *where* a turn changed, only *who* it belongs to - and it
must carry `high` / `medium` / `unknown` confidence for that guess. `unknown` is
a valid, and often correct, answer.

## Commands

Resolve the skill directory dynamically. `UpsertSystemSkill` bumps the version on
every SKILL.md hash change, so a hard-coded `/1/` path breaks on the next edit:

```bash
SKILLDIR=$(ls -d /app/data/skills-store/comingwave-study/*/ | sort -V | tail -1)
```

`exec` is denied under `<dataDir>` except for `skills-store/` and `tenants/`
(`cmd/gateway_setup.go:218-224`), so scripts must run from there.

```bash
# once, before anything else - records the current feed as history, queues nothing
python3 "$SKILLDIR/scripts/watch.py" init --workspace /app/workspace/comingwave-study

# the only decision point: what should this run do
python3 "$SKILLDIR/scripts/plan_run.py" next --workspace /app/workspace/comingwave-study

# material for one stage (S1 needs --part)
python3 "$SKILLDIR/scripts/plan_run.py" emit --workspace W --video-id V --stage S2

# after the model has written its artifact
python3 "$SKILLDIR/scripts/validate.py" --workspace W --video-id V

# S4a, then the two write_file TOOL calls, then:
python3 "$SKILLDIR/scripts/mark_memory.py" --workspace W --video-id V
python3 "$SKILLDIR/scripts/publish_pack.py" --workspace W --video-id V

# ledger
python3 "$SKILLDIR/scripts/ledger.py" show|rebuild|revert --workspace W [--video-id V]

# by hand only, 1-2 episodes at a time
python3 "$SKILLDIR/scripts/backfill.py" list
python3 "$SKILLDIR/scripts/backfill.py" queue --workspace W --ids VIDEOID
```

## Deployment - the repo bind mount does NOT deploy this skill

Easy to get wrong, so state it plainly: **the seeder reads only
`/app/bundled-skills/`** (`internal/skills/seeder.go:54`, `os.ReadDir(s.bundledDir)`,
set to `/app/bundled-skills` at `cmd/gateway_setup.go:519`). The
`skills/` -> `/app/data/skills` bind mount does **not** feed it. A skill that
exists in the repo is invisible to agents until it lands in the bundled dir.

And it cannot simply run from the repo either: `exec` is denied under `<dataDir>`
with exemptions only for `skills-store/` and `tenants/`
(`cmd/gateway_setup.go:218-224`), and `/app/data/skills/` is not one of them.

The chain that actually deploys it, on this host:

1. `Dockerfile.claude-cli` copies `skills/comingwave-study/` into
   `/app/bundled-skills/`. That thin image exists because a from-source rebuild of
   the base crashes Docker Desktop here - which is also why `yt-dlp` and `ffmpeg`
   are installed in the thin layer, with the same pin as
   `docker/requirements-skills.txt`.
2. Rebuild the thin image and recreate the container.
3. On startup the seeder copies each bundled slug into
   `/app/data/skills-store/<slug>/<version>/`, bumping `<version>` on every
   **SKILL.md hash change**. `aiwave-research` sits at version 6 for that reason,
   and it is why every pinned command resolves the directory with
   `sort -V | tail -1` rather than hard-coding a number.

`needsReCopy` (`seeder.go:287`) only repairs a copy with *fewer* files than the
source; it does not notice edited content. So changing `scripts/` alone does not
redeploy - bump `metadata.bundle_revision` in this file so the hash changes.

## Cold start

`watch.py init` is mandatory and the planner refuses to run without it. The Atom
feed returns the 15 most recent entries against an empty database, so a plain
first poll would queue the entire visible back catalogue - the opposite of the
agreed behaviour, and a quota spike on day one. Historical episodes go through
`backfill.py`, which caps at 2 per run by default.

## Thesis identity

`ledger.py` owns the ids. S3 receives the current ledger **with** ids and may only
emit an existing `thesis_id` or the literal `"new"`; a delta claiming `updated` or
`contradicted` against an id the ledger has never seen is rejected, and so is a
merge whose episode is older than the newest already merged. Without that rule a
model restates last month's thesis in fresh words, the ledger fills with
near-duplicates, and the pack section that reports what changed becomes noise -
which is the one thing a plain per-episode summariser could not do anyway.

Events are append-only (`thesis-events.ndjson`); `theses-ledger.json` is derived.
A bad merge is fixed with `ledger.py revert --video-id V` (keeps a timestamped
backup), then a corrected `theses-delta.json` and another `finalize.py`.

## Sharing

`essay-fb.md` must fit **one** Discord message. With an image the review path caps
a draft at 2000 bytes, fail-closed and never truncated (`internal/tools/message.go:33`).
Without one the channel adapter chunks it - and approval binds only the message
that was replied to, so a chunked draft gets approved in part and published in
part, on a public page. `plan_essay.py check` refuses on that, reporting the
overflow in characters rather than bytes.

The ContentFactory review send must use `idempotency_key="contentfactory-terminal"`
**exactly**; the message tool rejects any other value for that channel
(`internal/tools/message.go:287-293`).

## Runbook: yt-dlp stops working

Pinning protects against a bad yt-dlp release. It does **not** help when YouTube
changes its player - there the fix is a *newer* yt-dlp, and agents cannot install
one (`pip install` matches the `package_install` deny group; the only way past it
is an interactive admin approval that a cron run cannot obtain).

Recovery, hours to a day:

1. `docker/requirements-skills.txt` - bump `yt-dlp==<new version>`.
2. Rebuild the image **from source** (the claude-cli layer does not compile Go).
3. Redeploy, keeping the previous image digest for rollback.
4. Re-run the ingest for the stuck episode.

Bump `metadata.bundle_revision` in this file whenever `scripts/` changes - editing
scripts alone does not redeploy the skill.

Meanwhile the pipeline must be loud, not quiet: ingest failures land in
`pipeline/metrics/runs.ndjson` with a status, and a silent pipeline is
indistinguishable from a quiet week on a channel that publishes irregularly.

## Refusal is the alarm

Every refusal writes a row to `pipeline/metrics/runs.ndjson`: `asr_suspect`,
`coverage_short`, `ledger_rejected`, `render_noncompliant`, `memory_not_recorded`,
`delivery_failed`. There is deliberately no "post an error notice instead" path -
that would recreate guaranteed delivery of unvalidated content.

A partial delivery is the one case that does **not** get a marker: if some
messages of a pack landed and then the webhook failed, the metrics row records how
far it got and a human decides, because a blind retry would duplicate whatever
already posted.

## Tests

```bash
python3 tests/test_pipeline_e2e.py
```

Walks the whole stage machine on a real 61-minute transcript with synthesised
model output: cold start queues nothing, a truncated part is rejected, a lost
artifact resumes at that stage and not at the beginning, the ledger refuses an
unresolvable id, publishing is blocked until the memory write is recorded, and a
second publish is refused. No pytest dependency - it has to run inside the
container during a gate check.

## Known gaps

- **The audio fallback is not built.** `read_audio` takes only `prompt` and
  `media_id` and describes audio "attached to the conversation"
  (`internal/tools/read_audio.go:62-83`) - there is no file-path parameter, and a
  cron session has no attached media. Deferred deliberately: production runs on a
  residential IP where the subtitle path works. If this ever moves to a
  datacenter IP, that gap becomes the first thing to close.
- **`mark_memory.py` cannot prove the Postgres write succeeded.** It records the
  draft's hash and refuses without the vault document; verifying the write itself
  is what `memory_search` and `knowledge_graph_search` are for.
- **No JS runtime in the image.** yt-dlp warns about it; subtitle extraction works
  without one. Installing `deno` would silence the warning and is untested here.
