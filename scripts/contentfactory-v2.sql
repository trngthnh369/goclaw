\set ON_ERROR_STOP on

-- ContentFactory v2 runtime contract
-- Purpose: make the pipeline claim-centric, preserve evidence through revision,
-- require terminal idempotency for Discord review/abort sends, and keep Facebook
-- publish out of cron/wake canary runs.
-- Idempotent: every section has a v2 marker and is appended once.

BEGIN;

CREATE TEMP TABLE cf_v2_target_tenant AS
SELECT id AS director_id, tenant_id
FROM agents
WHERE agent_key = 'cf-director'
  AND deleted_at IS NULL;

DO $tenant_guard$
DECLARE
    tenant_count integer;
BEGIN
    SELECT COUNT(*) INTO tenant_count FROM cf_v2_target_tenant;
    IF tenant_count <> 1 THEN
        RAISE EXCEPTION 'ContentFactory v2 expected exactly one cf-director tenant, found %', tenant_count;
    END IF;
END
$tenant_guard$;

--------------------------------------------------------------------
-- cf-director: evidence continuity + terminal idempotency
--------------------------------------------------------------------
UPDATE agent_context_files AS f
SET content = f.content || $section$

## ContentFactory evidence continuity v2 — 2026-08-02

This section supersedes older prose-only research, writer, audit, revision, and continuity rules when they conflict.

### Evidence package invariant

Every ContentFactory run must carry one explicit evidence package through all handoffs:

```text
EVIDENCE_PACKAGE_ID: <stable id>
EVIDENCE_PACKAGE_VERSION: <integer>
USABLE_SOURCE_COUNT: <integer>
VALIDATED_SOURCE_INVENTORY:
- source_id: SRC-001
  canonical_url: <url>
  source_tier: TIER_1_PRIMARY | TIER_2_INDEPENDENT | TIER_3_SIGNAL | REJECTED
  validation_status: USABLE | SIGNAL_ONLY | REJECTED
  supports_claim_ids: [CLM-001]
ALLOWED_CLAIMS:
- claim_id: CLM-001
  claim: <exact proposition allowed in article>
  required_source_ids: [SRC-001]
COUNTER_EVIDENCE:
RISKS_AND_UNKNOWNS:
PROHIBITED_OR_UNSUPPORTED_CLAIMS:
```

`RAW_URL_COUNT` is diagnostic only. `USABLE_SOURCE_COUNT` must count only unique `USABLE` source records that support material claim IDs. A source-count threshold never replaces claim coverage: every material user-facing claim must appear in `ALLOWED_CLAIMS` and cite valid `source_id` values.

### Handoff invariant

For writer, auditor, revision, and re-audit delegations, copy the evidence package block verbatim. Do not summarize, recount, rewrite, or omit `VALIDATED_SOURCE_INVENTORY`, `ALLOWED_CLAIMS`, `COUNTER_EVIDENCE`, or `RISKS_AND_UNKNOWNS`.

If a required audit fix needs a claim or source not present in the evidence package, delegate back to `cf-researcher` for a new package version. Do not ask `cf-writer` to infer or invent evidence.

### Terminal action invariant

Maintain these state variables mentally for each run:

```text
REVISION_COUNT: 0 | 1 | 2
DESIGN_ATTEMPTS: 0 | 1
TERMINAL_ACTION_STATE: OPEN | REVIEW_SENT | ABORT_SENT
```

Only exact `AUDIT_VERDICT: PASS` plus `SAFE_TO_SEND_DISCORD: yes` unlocks `cf-designer`. After PASS, call `cf-designer` at most once. Designer must produce exactly one usable `MEDIA:` image path from the sync delegation result. If image output is missing, text-only, malformed, or multiple images, abort instead of retrying.

Every terminal Discord review or abort send to `cf-discord` / `1530127001602625677` must include:

```json
{"idempotency_key":"contentfactory-terminal"}
```

After a terminal message tool returns `status=sent` or `status=duplicate_suppressed`, set `TERMINAL_ACTION_STATE` and call no more tools. Final output must be exactly `NO_REPLY`.

In cron/wake ContentFactory runs, `message(action="post")`, `channel="fb-page"`, and approval fast-path publishing are forbidden regardless of audit status.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-director'
  AND f.file_name = 'CAPABILITIES.md'
  AND f.content NOT LIKE '%## ContentFactory evidence continuity v2 — 2026-08-02%';

UPDATE agent_context_files AS f
SET content = replace(
        f.content,
        '#### Cron execution continuity — BẮT BUỘC',
        '#### Cron execution continuity — BẮT BUỘC

CONTINUITY_V2: Follow `ContentFactory evidence continuity v2 — 2026-08-02` from CAPABILITIES.md. Evidence package must remain claim-centric and verbatim through every handoff. Terminal Discord send must use idempotency_key="contentfactory-terminal". Facebook post action is forbidden in cron/wake runs.'
    ),
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-director'
  AND f.file_name = 'SOUL.md'
  AND f.content LIKE '%#### Cron execution continuity — BẮT BUỘC%'
  AND f.content NOT LIKE '%CONTINUITY_V2:%';

--------------------------------------------------------------------
-- cf-researcher: usable-source and claim-ledger contract
--------------------------------------------------------------------
UPDATE agent_context_files AS f
SET content = f.content || $section$

## Validated source inventory contract v2 — 2026-08-02

For `RESEARCH_MODE: DEEP_VERIFY`, return a claim-centric evidence package. `RESEARCH_STATUS: COMPLETE` requires:

- `USABLE_SOURCE_COUNT >= 12`;
- every material claim in `ALLOWED_CLAIMS` has at least one usable Tier 1 or Tier 2 supporting source, unless explicitly labeled as low-risk writer inference;
- social/community sources are `TIER_3_SIGNAL` and must not be used to prove factual, product-capability, legal, algorithm, numeric, or causal claims;
- `RISKS_AND_UNKNOWNS`, `COUNTER_EVIDENCE`, and `PROHIBITED_OR_UNSUPPORTED_CLAIMS` are present even when empty.

Required output block:

```text
EVIDENCE_PACKAGE_ID:
EVIDENCE_PACKAGE_VERSION: 1
RAW_URL_COUNT:
USABLE_SOURCE_COUNT:
VALIDATED_SOURCE_INVENTORY:
- source_id:
  canonical_url:
  source_name:
  source_tier: TIER_1_PRIMARY | TIER_2_INDEPENDENT | TIER_3_SIGNAL | REJECTED
  validation_status: USABLE | SIGNAL_ONLY | REJECTED
  supports_claim_ids:
  evidence_excerpt:
  rejection_reason:
ALLOWED_CLAIMS:
- claim_id:
  claim:
  claim_type:
  required_source_ids:
COUNTER_EVIDENCE:
RISKS_AND_UNKNOWNS:
PROHIBITED_OR_UNSUPPORTED_CLAIMS:
```

Raw URL count is diagnostic only and cannot satisfy the 12-source gate.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-researcher'
  AND f.file_name = 'CAPABILITIES.md'
  AND f.content NOT LIKE '%## Validated source inventory contract v2 — 2026-08-02%';

--------------------------------------------------------------------
-- cf-writer: allowed-claim-only article contract
--------------------------------------------------------------------
UPDATE agent_context_files AS f
SET content = f.content || $section$

## Allowed-claims writing contract v2 — 2026-08-02

You may only write material user-facing claims that appear in `ALLOWED_CLAIMS` and cite valid `source_id` values in `CLAIM_MAP`.

Rules:
- Do not add new factual, product, algorithm, numeric, causal, legal, or local-market claims outside the evidence package.
- Social/community-only evidence is not allowed in final article claims; remove those claims instead of rephrasing them.
- Claims with audit status `unsupported`, `false`, or `needs_context` must be deleted unless a new researcher package supplies usable evidence.
- If a required edit needs a claim/source outside the package, return `WRITER_STATUS: NEEDS_RESEARCH`.
- For Discord review with image, keep the final `ARTICLE_BODY` under 1800 UTF-8 bytes.

Return:
```text
WRITER_STATUS: COMPLETE | NEEDS_RESEARCH
EVIDENCE_PACKAGE_ID:
ARTICLE_BODY:
CLAIM_MAP:
- article_claim:
  claim_id:
  source_ids:
  confidence:
SOURCE_IDS_USED:
RISKS_AND_UNKNOWNS_ACKNOWLEDGED: yes
```
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-writer'
  AND f.file_name = 'CAPABILITIES.md'
  AND f.content NOT LIKE '%## Allowed-claims writing contract v2 — 2026-08-02%';

UPDATE agent_context_files AS f
SET content = f.content || $section$

## Social-signal guard v2

Never convert a Facebook Group, anonymous social post, forum rumor, or community anecdote into a user-facing claim about algorithms, platform ranking, product capability, law, or market behavior. Use such material only as a research lead or reject it.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-writer'
  AND f.file_name = 'SOUL.md'
  AND f.content NOT LIKE '%## Social-signal guard v2%';

--------------------------------------------------------------------
-- cf-auditor: source-id and package continuity audit
--------------------------------------------------------------------
UPDATE agent_context_files AS f
SET content = f.content || $section$

## Source inventory audit v2 — 2026-08-02

Audit the exact `CLAIM_MAP` against `VALIDATED_SOURCE_INVENTORY` and `ALLOWED_CLAIMS`.

Return `UNCERTAIN` or `REVISION_REQUIRED` with `SAFE_TO_SEND_DISCORD: no` if:
- `EVIDENCE_PACKAGE_ID` is missing or changes between handoffs;
- `VALIDATED_SOURCE_INVENTORY`, `ALLOWED_CLAIMS`, source tiers, `COUNTER_EVIDENCE`, or `RISKS_AND_UNKNOWNS` is missing;
- any material article claim lacks known `claim_id` or valid `source_id`;
- a social/community signal is used as evidence for factual/product/legal/algorithm/numeric/causal claims;
- article byte length appears unsafe for Discord-with-image delivery.

Verdict must include:
```text
AUDITED_EVIDENCE_PACKAGE_ID:
SOURCE_INVENTORY_AUDIT:
- claimed_usable_source_count:
- auditor_verified_usable_source_count:
- rejected_source_ids:
- threshold_met: yes | no
AUDIT_VERDICT: PASS | REVISION_REQUIRED | BLOCKED | UNCERTAIN
SAFE_TO_SEND_DISCORD: yes | no
```

`PASS` requires every material claim to be supported by the cited source IDs and exact `SAFE_TO_SEND_DISCORD: yes`.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-auditor'
  AND f.file_name = 'CAPABILITIES.md'
  AND f.content NOT LIKE '%## Source inventory audit v2 — 2026-08-02%';

UPDATE agent_context_files AS f
SET content = f.content || $section$

## Social-signal audit guard v2

Treat social/community posts as leads only. They cannot prove algorithm behavior, platform ranking, product capability, legal claims, numeric uplift, or broad market behavior. If final article relies on such a source, return `REVISION_REQUIRED` or `UNCERTAIN` and `SAFE_TO_SEND_DISCORD: no`.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-auditor'
  AND f.file_name = 'SOUL.md'
  AND f.content NOT LIKE '%## Social-signal audit guard v2%';

--------------------------------------------------------------------
-- cf-designer: exactly one image contract
--------------------------------------------------------------------
UPDATE agent_context_files AS f
SET content = f.content || $section$

## One-image review contract v2 — 2026-08-02

Run only after exact `AUDIT_VERDICT: PASS` and `SAFE_TO_SEND_DISCORD: yes` are present in the delegation. Call `create_image` at most once. Return exactly one image result:

```text
DESIGN_STATUS: COMPLETE | FAILED
IMAGE_COUNT: 1
IMAGE_PATH: MEDIA:<path from tool result>
```

If image generation fails, returns text-only, or produces multiple images, return `DESIGN_STATUS: FAILED`. Do not retry, do not send Discord, and do not publish Facebook.
$section$,
    updated_at = NOW()
FROM agents AS a
WHERE a.id = f.agent_id
  AND a.tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND a.deleted_at IS NULL
  AND a.agent_key = 'cf-designer'
  AND f.file_name = 'SOUL.md'
  AND f.content NOT LIKE '%## One-image review contract v2 — 2026-08-02%';

-- Designer only needs create_image for this role. Removing list_files closes the
-- observed tool-loop path after a successful image generation.
UPDATE agents
SET tools_config = jsonb_set(
        COALESCE(tools_config, '{}'::jsonb),
        '{deny}',
        COALESCE(tools_config->'deny', '[]'::jsonb) || '["list_files"]'::jsonb,
        true
    ),
    updated_at = NOW()
WHERE tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND deleted_at IS NULL
  AND agent_key = 'cf-designer'
  AND NOT (COALESCE(tools_config->'deny', '[]'::jsonb) ? 'list_files');

--------------------------------------------------------------------
-- Cron payload: reinforce v2 for scheduled runs
--------------------------------------------------------------------
UPDATE cron_jobs
SET payload = jsonb_set(
        payload,
        '{message}',
        to_jsonb(payload->>'message' || E'\n\nCONTENTFACTORY_V2 HARD RULE: use claim-centric EVIDENCE_PACKAGE_ID, VALIDATED_SOURCE_INVENTORY, ALLOWED_CLAIMS, RISKS_AND_UNKNOWNS, and source_ids through every handoff. Writer may not use social/community-only claims. Discord terminal send requires idempotency_key="contentfactory-terminal". For cron/wake runs, Facebook post action is forbidden. Designer runs once only after exact audit PASS + SAFE_TO_SEND_DISCORD yes.'),
        true
    ),
    updated_at = NOW()
WHERE tenant_id = (SELECT tenant_id FROM cf_v2_target_tenant)
  AND agent_id = (SELECT director_id FROM cf_v2_target_tenant)
  AND name = 'content-factory-daily'
  AND payload->>'message' NOT LIKE '%CONTENTFACTORY_V2 HARD RULE:%';

--------------------------------------------------------------------
-- Verification
--------------------------------------------------------------------
DO $verify$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(item, ', ') INTO missing
    FROM (VALUES
        ('cf-director evidence v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-director' AND f.file_name='CAPABILITIES.md' AND f.content LIKE '%## ContentFactory evidence continuity v2 — 2026-08-02%' AND f.content LIKE '%contentfactory-terminal%')),
        ('cf-director continuity v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-director' AND f.file_name='SOUL.md' AND f.content LIKE '%CONTINUITY_V2:%')),
        ('cf-researcher inventory v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-researcher' AND f.file_name='CAPABILITIES.md' AND f.content LIKE '%VALIDATED_SOURCE_INVENTORY%' AND f.content LIKE '%USABLE_SOURCE_COUNT%')),
        ('cf-writer allowed claims v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-writer' AND f.file_name='CAPABILITIES.md' AND f.content LIKE '%ALLOWED_CLAIMS%' AND f.content LIKE '%1800 UTF-8 bytes%')),
        ('cf-auditor inventory audit v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-auditor' AND f.file_name='CAPABILITIES.md' AND f.content LIKE '%SOURCE_INVENTORY_AUDIT%' AND f.content LIKE '%AUDITED_EVIDENCE_PACKAGE_ID%')),
        ('cf-designer one image v2', EXISTS (SELECT 1 FROM agent_context_files f JOIN agents a ON a.id=f.agent_id WHERE a.tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND a.deleted_at IS NULL AND a.agent_key='cf-designer' AND f.file_name='SOUL.md' AND f.content LIKE '%IMAGE_COUNT: 1%' AND f.content LIKE '%create_image%')),
        ('cf-designer list_files denied', EXISTS (SELECT 1 FROM agents WHERE tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND deleted_at IS NULL AND agent_key='cf-designer' AND COALESCE(tools_config->'deny', '[]'::jsonb) ? 'list_files')),
        ('cron v2', EXISTS (SELECT 1 FROM cron_jobs WHERE tenant_id=(SELECT tenant_id FROM cf_v2_target_tenant) AND agent_id=(SELECT director_id FROM cf_v2_target_tenant) AND name='content-factory-daily' AND payload->>'message' LIKE '%CONTENTFACTORY_V2 HARD RULE:%'))
    ) AS checks(item, ok)
    WHERE NOT ok;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'ContentFactory v2 verification failed: %', missing;
    END IF;

    RAISE NOTICE 'ContentFactory v2 runtime contract installed/verified.';
END
$verify$;

COMMIT;
