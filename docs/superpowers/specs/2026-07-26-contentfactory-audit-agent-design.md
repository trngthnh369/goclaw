# ContentFactory Audit Agent Design

## Status

Approved architecture: dedicated blocking audit agent (`cf-auditor`) in the
ContentFactory team, updated by the 2026-08-02 v2 E2E hardening.

This document describes runtime configuration and the small runtime guards needed
for safe Discord review delivery. It does not introduce database schema changes.

## Objective

Require independent factual-accuracy and Vietnam social-content compliance review
before ContentFactory content can be sent to Discord for human approval. No
article may enter the Discord approval flow unless the auditor returns exact
`AUDIT_VERDICT: PASS` and `SAFE_TO_SEND_DISCORD: yes`.

The auditor is a pre-publication risk gate, not a substitute for legal counsel.
It must escalate uncertainty rather than represent uncertain legal conclusions as
definitive advice.

## Current Flow

```text
cf-director
  → cf-researcher
  → cf-writer
  → cf-auditor
       PASS + SAFE_TO_SEND_DISCORD: yes
         → cf-designer
         → cf-director sends exactly one cf-discord review draft
       REVISION_REQUIRED / UNCERTAIN
         → cf-writer → cf-auditor (maximum 2 revisions)
       BLOCKED / revision budget exhausted
         → cf-director sends exactly one cf-discord abort notice
```

`zip-crazy` is no longer part of the review delivery path. Facebook publishing is
a separate approval-reply path after a human replies to the Discord review.

## Evidence Package Contract

Research output must be claim-centric. Raw URL count is diagnostic only.

```text
EVIDENCE_PACKAGE_ID:
EVIDENCE_PACKAGE_VERSION:
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

`RESEARCH_STATUS: COMPLETE` requires at least 12 usable sources and material
claim coverage. A source is usable only if it supports at least one material claim
and has a source tier appropriate for that claim. Social/community sources are
signals only and cannot prove algorithm behavior, product capability, legal,
numeric, causal, or market claims.

## Writer Rules

- The writer may only write material claims listed in `ALLOWED_CLAIMS`.
- Every material `ARTICLE_BODY` claim must cite valid `source_id` values in
  `CLAIM_MAP`.
- Unsupported, false, needs-context, or social-signal-only claims must be removed,
  not softened into user-facing prose.
- If a required fix needs a new claim/source, the writer returns
  `WRITER_STATUS: NEEDS_RESEARCH`.
- Discord-with-image copy must fit the byte budget, not a loose character count;
  current v2 budget is under 1800 UTF-8 bytes.

## Auditor Rules

The auditor receives the exact article, full claim map, complete evidence package,
source tiers, risks/unknowns, counter-evidence, publication context, and image
metadata if available. It must not depend on another agent's workspace.

Verdict structure:

```text
AUDITED_EVIDENCE_PACKAGE_ID:
SOURCE_INVENTORY_AUDIT:
- claimed_usable_source_count:
- auditor_verified_usable_source_count:
- rejected_source_ids:
- threshold_met: yes | no
AUDIT_VERDICT: PASS | REVISION_REQUIRED | BLOCKED | UNCERTAIN
RISK_LEVEL: LOW | MEDIUM | HIGH | CRITICAL
FACT_CHECK:
LEGAL_SOCIAL_COMPLIANCE:
REQUIRED_FIXES:
SAFE_TO_SEND_DISCORD: yes | no
```

Rules:

- `PASS` requires exact `SAFE_TO_SEND_DISCORD: yes` and no unresolved material
  claim.
- Missing evidence package, unknown source IDs, source-count mismatch, or missing
  risks/unknowns is `UNCERTAIN` or `REVISION_REQUIRED`.
- `REVISION_REQUIRED`, `BLOCKED`, and `UNCERTAIN` require
  `SAFE_TO_SEND_DISCORD: no`.
- Missing/invalid output is treated as `UNCERTAIN` and blocked.

## Designer and Discord Delivery

Designer may run only after exact audit PASS. Designer must call `create_image` at
most once and return exactly one usable image path.

Because delegated media is generated in a child workspace, sync delegation must
stage child media into the parent/director workspace or otherwise expose a
trusted parent-accessible `MEDIA:` reference. The director sends the exact audited
article plus exactly one image to `cf-discord`.

Discord media delivery must fail closed if the text exceeds the platform limit.
It must never silently truncate audited Vietnamese copy, because then Discord
would receive content different from the audited article.

Terminal Discord review/abort sends require per-run idempotency:

```json
{"idempotency_key":"contentfactory-terminal"}
```

After terminal send success or duplicate suppression, the director returns
`NO_REPLY` and performs no additional tool calls.

## Facebook Safety

In cron/wake E2E canaries, Facebook publishing is hard-blocked. The test must
verify zero `message(action="post")`, zero `fb-page` outbound, no
`message.feed_post_approval`, and unchanged feed-post ledger. Human approval
reply remains the only path into Facebook publishing outside the canary.

## Error Handling

- Unsupported source or inaccessible paywall: mark the claim `needs_context`; do
  not assume it is true.
- Conflicting authoritative sources: `UNCERTAIN`, with both sources listed.
- Legal ambiguity: `UNCERTAIN`, identify the issue, and block.
- High/critical issue: do not rewrite it into publication-safe language without
  another full audit cycle.
- Audit agent unavailable: no bypass or manual auto-PASS.
- Image generation failure, text-only result, multiple images, or unavailable
  staged media: abort, do not retry or send a review draft.

## Verification

1. Confirm `cf-auditor` exists, is active, uses the expected model, and has no
   publishing or shell tools.
2. Confirm director context contains the audit step before designer and Discord.
3. Confirm no direct writer-to-Discord or writer-to-Facebook instruction remains.
4. Exercise controlled cases:
   - sourced low-risk article → `PASS`, one designer call, one image, one Discord
     review draft;
   - unsupported factual claim → blocked as `REVISION_REQUIRED` or `UNCERTAIN`;
   - defamatory/prohibited/high-risk content → `BLOCKED`.
5. Confirm malformed or empty audit output prevents Discord delivery.
6. Confirm revision loops stop after two cycles.
7. Confirm `/wake` E2E produces no `unknown channel=wake` breadcrumb warning.
8. Confirm Facebook remains blocked during canary and restored afterward.

## Legal Sources

- Law 24/2018/QH14 on Cybersecurity.
- Decree 147/2024/ND-CP on management, provision, and use of Internet services
  and online information, effective 25 December 2024.
- Article 101 of Decree 15/2020/ND-CP and applicable amendments.
- Decree 13/2023/ND-CP on personal-data protection.
