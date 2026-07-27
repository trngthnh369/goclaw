# ContentFactory Audit Agent Design

## Status

Approved architecture: dedicated blocking audit agent (`cf-auditor`) in the
ContentFactory team.

This document describes runtime configuration only. It does not change GoClaw
source code or database schema.

## Objective

Require an independent factual-accuracy and Vietnam social-content compliance
review before ContentFactory content can be sent to Discord for human approval.
No article may enter the Discord approval flow unless the auditor returns an
explicit `PASS` verdict.

The auditor is a pre-publication risk gate, not a substitute for legal counsel.
It must escalate uncertainty rather than represent uncertain legal conclusions
as definitive advice.

## Existing Flow

```text
cf-director → cf-researcher → cf-writer → cf-designer → zip-crazy → Discord
```

`cf-director` currently instructs direct Discord delivery after receiving the
writer output. That instruction must be replaced so no direct writer-to-Discord
path remains.

## Target Flow

```text
cf-director
  → cf-researcher
  → cf-writer
  → cf-auditor
       PASS → cf-designer → zip-crazy → Discord human approval
       REVISION_REQUIRED / UNCERTAIN → cf-writer → cf-auditor (maximum 2 revisions)
       BLOCKED → zip-crazy sends internal rejection notice only
```

## Agent Definition

- Agent key: `cf-auditor`
- Display name: `Kiểm Duyệt Nội Dung`
- Type: `predefined`
- Provider/model: `antigravity` / `ag-pro`, matching the reasoning-tier ContentFactory agents
- Workspace: `/app/workspace/cf-auditor`
- Team role: `member`
- Tools: read-only research and memory tools only; no message, exec, filesystem
  mutation, or publishing tools
- Tenant: same tenant as ContentFactory

The auditor receives the complete candidate article, research evidence, source
URLs, publication context, and any image description directly in the delegation
message. It must not depend on another agent's workspace.

## Audit Axes

### 1. Factual accuracy

- Extract every externally verifiable claim: names, dates, figures, rankings,
  quotations, causal claims, product claims, public events, and legal claims.
- Require authoritative or independently corroborated sources for material
  claims.
- Treat social posts, anonymous sources, and content farms as leads, not proof.
- Detect stale claims by comparing publication dates and event dates.
- Mark unverifiable, contradicted, or context-stripped claims as blocking.

### 2. Vietnam online-content compliance

The baseline includes:

- Law 24/2018/QH14 on Cybersecurity, including prohibited online information and
  conduct affecting national security, public order, organizations, and
  individuals.
- Decree 147/2024/ND-CP on management, provision, and use of Internet services
  and online information, effective 25 December 2024.
- Article 101 of Decree 15/2020/ND-CP and applicable amendments regarding fake,
  false, distorted, defamatory, or reputation-damaging information on social
  networks.
- Decree 13/2023/ND-CP on personal-data protection.
- Related risks involving personal image/privacy, copyright, advertising,
  defamation, sensitive political/public-order topics, and health, finance,
  investment, or legal claims.

The checklist must flag:

- fake, materially misleading, distorted, or unsupported information;
- accusations of wrongdoing without authoritative evidence;
- insults, defamation, or harm to reputation, dignity, or privacy;
- personal or sensitive data and identifiable images without a valid basis;
- prohibited national-security, public-order, violence-incitement, ethnic,
  religious, or social-unity content;
- disclosure of state, business, or private secrets;
- unlicensed copyrighted text/images or excessive reproduction;
- deceptive advertising, guarantees, and unsupported superlatives;
- medical, financial, investment, or legal advice presented as certainty;
- sensational headlines whose meaning differs materially from the article.

## Verdict Contract

The auditor must return exactly one verdict using this structure:

```text
AUDIT_VERDICT: PASS | REVISION_REQUIRED | BLOCKED | UNCERTAIN
RISK_LEVEL: LOW | MEDIUM | HIGH | CRITICAL

FACT_CHECK:
- claim: <claim>
  status: verified | unsupported | false | needs_context
  source: <authoritative URL or "none">

LEGAL_SOCIAL_COMPLIANCE:
- issue: <issue or "none">
  severity: LOW | MEDIUM | HIGH | CRITICAL
  basis: <law/decree/article or risk rationale>

REQUIRED_FIXES:
1. <specific correction, evidence requirement, or removal>

SAFE_TO_SEND_DISCORD: yes | no
```

Rules:

- `PASS` requires `SAFE_TO_SEND_DISCORD: yes` and no unresolved material claim.
- `REVISION_REQUIRED`, `BLOCKED`, and `UNCERTAIN` require
  `SAFE_TO_SEND_DISCORD: no`.
- Missing/invalid output is treated as `UNCERTAIN` and blocked.
- The approved policy is fail-closed: `UNCERTAIN` returns the article for
  revision or additional verification.

## Director Orchestration Rules

1. Delegate the writer's full article and complete research evidence to
   `cf-auditor`.
2. Parse the verdict contract; never infer PASS from prose.
3. On `PASS`, delegate the approved article to `cf-designer`, then to
   `zip-crazy` for Discord human review. Include a short audit summary.
4. On `REVISION_REQUIRED` or `UNCERTAIN`, send `REQUIRED_FIXES` and the article
   back to `cf-writer`, then re-audit the revised article.
5. Allow no more than two revision cycles.
6. If still not PASS after two cycles, abort and ask `zip-crazy` to deliver only
   an internal audit-failure notice. Do not include content as an approvable
   draft.
7. On `BLOCKED`, abort immediately and send only the risk summary to Discord.
8. Auditor timeout, tool error, empty response, or malformed verdict fails
   closed. Retry audit once; if it fails again, abort.
9. No instruction may permit direct `cf-writer → zip-crazy` delivery.

## Team and Persistence Changes

- Create the `cf-auditor` agent through the authenticated GoClaw agent API.
- Add the new agent to `agent_team_members` through the team-member API or the
  existing runtime-safe administrative path.
- Create `SOUL.md`, `CAPABILITIES.md`, `IDENTITY.md`, and normal predefined-agent
  bootstrap context.
- Update `cf-director` `SOUL.md` and `CAPABILITIES.md` to encode the blocking
  gate and structured verdict handling.
- Do not modify `zip-crazy` Facebook approval behavior; it remains downstream of
  Discord human approval.

## Error Handling

- Unsupported source or inaccessible paywall: mark the claim `needs_context`;
  do not assume it is true.
- Conflicting authoritative sources: `UNCERTAIN`, with both sources listed.
- Legal ambiguity: `UNCERTAIN`, identify the issue, and block.
- High/critical issue: do not rewrite it into publication-safe language without
  another full audit cycle.
- Audit agent unavailable: no bypass or manual auto-PASS.

## Verification

1. Confirm `cf-auditor` exists, is active, uses the expected model, and has no
   publishing or shell tools.
2. Confirm ContentFactory has six members and `cf-auditor` is a member.
3. Confirm director context contains the audit step before designer and Discord.
4. Confirm no direct writer-to-Discord instruction remains.
5. Exercise three controlled cases:
   - sourced, low-risk article → `PASS`;
   - unsupported factual claim → blocked as `REVISION_REQUIRED` or `UNCERTAIN`;
   - defamatory/prohibited/high-risk content → `BLOCKED`.
6. Confirm malformed or empty audit output prevents Discord delivery.
7. Confirm revision loops stop after two cycles.

## Legal Sources

- Law 24/2018/QH14 on Cybersecurity, issued 12 June 2018 and effective
  1 January 2019:
  <https://vanban.chinhphu.vn/?docid=206114&pageid=27160>
- Decree 147/2024/ND-CP, issued 9 November 2024 and effective
  25 December 2024:
  <https://vanban.chinhphu.vn/?classid=1&docid=211654&orggroupid=2&pageid=27160>
- Official Gazette copy of Decree 147/2024/ND-CP:
  <https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-147-2024-nd-cp-43155/52699.htm>
- Ministry summary of Article 101, Decree 15/2020/ND-CP:
  <https://mst.gov.vn/tung-tin-gia-mao-sai-su-that-tren-mang-xa-hoi-bi-phat-den-20-trieu-dong-197140630.htm>
- Ministry of Public Security introduction to Decree 13/2023/ND-CP:
  <https://mps.gov.vn/chinh-sach-phap-luat/bai-viet/chinh-phu-ban-hanh-nghi-dinh-bao-ve-du-lieu-ca-nhan-d3-t982>
