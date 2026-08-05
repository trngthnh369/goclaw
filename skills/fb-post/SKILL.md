---
name: fb-post
description: "Post approved content to a Facebook Fanpage. Uses the message tool with action='post' to publish text + optional single image to the page feed via Graph API. Trigger on: user replies 'duyệt', 'approve', 'đăng', 'post' to a content message. REQUIRES: a Facebook channel instance named in the skill config (default: 'fb-page') with valid Page Access Token."
metadata:
  author: trngthnh369
  version: "1.0.0"
  channel: fb-page
---

# Facebook Fanpage Post

## Overview

Publish approved content (article + optional image) to a Facebook Fanpage feed. Works through the native `message` tool with `action="post"`, which routes to the Facebook channel's Graph API client. Credentials are encrypted in the channel store — never handle tokens directly.

## CRITICAL RULES

1. **NEVER post without explicit user approval.** Only trigger on clear approval commands: "duyệt", "approve", "đăng", "post" (NOT questions like "có nên đăng không?").
2. **Check workspace state BEFORE posting.** Read the article file — if it contains `status: posted`, STOP and report "Bài này đã đăng rồi."
3. **Update workspace state AFTER posting.** Immediately prepend `status: posted` + `posted_at: <timestamp>` to the article file. The gateway also keeps a durable reservation keyed to the Discord review message; workspace state is only a secondary operator hint.
4. **Single reviewed image only.** The image bytes must match the attachment on the reviewed Discord message. If the review has zero or multiple images, do not select a different/newest image; request a fresh review message with exactly one primary image.
4b. **Articles >2000 characters (Discord message limit):**
   Content binding supports 3 matching modes for Discord review messages:
   - **Exact text match**: For short articles (<= 2000 chars) where the full article fits in the Discord message body.
   - **Attached `.md` document**: Deliver the article draft as an attached `.md` file (`MEDIA:path/article.md`). The gateway computes the SHA-256 digest of the attached file in Go for byte-exact verification.
   - **Go-computed Message Digest Tag**: Include `[Article SHA-256: <64-hex-chars>]` in the Discord review message. The gateway extracts the tag and compares `sha256(normalizeApprovalContent(postContent))` in Go.
   *(Note: Never rely on LLM prompts to calculate SHA-256 hashes — digests are computed programmatically in Go.)*
5. **Fail closed on errors.** Report the stable tool error without adding internal details. Do NOT retry automatically. A failed/ambiguous post keeps a `pending_unknown` reservation and requires operator reconciliation.

## When to Use

- User replies "duyệt" (or variants) to a ContentFactory article delivered on Discord
- User explicitly asks to post a specific article to the fanpage by replying to reviewed ContentFactory content

Do not use this skill for cron/scheduled publishing; feed posts require a live user approval reply.

## How to Post

### Step 1: Identify the approved article

Use the article content bound to the Discord reply being approved. Do **not** pick the newest file in `articles/` as a fallback.

Allowed sources:
- The `[Replying to]` block in the user's message (contains the reviewed article preview/content)
- A workspace article file only if its content matches the replied Discord content/article preview

If you cannot bind the workspace file/content to the replied Discord message, STOP and ask for a fresh approval reply to the correct draft.

### Step 2: Check dedup guard

```
Read the article file → check first lines for "status: posted"
If found → reply "Bài này đã đăng rồi" → STOP
```

### Step 3: Prepare content

- Use the exact reviewed article body; only normalize outer whitespace or CRLF/LF transport differences
- Do not rewrite casing, URLs, paragraph breaks, markdown, punctuation, or claims after review
- Locate the accompanying image only when its bytes match the single image attached to the reviewed Discord message

### Step 4: Post to Facebook

Call the message tool:

```json
{
  "action": "post",
  "channel": "fb-page",
  "target": "feed",
  "forward": true,
  "forward_reason": "User replied 'duyệt' to this ContentFactory article",
  "message": "<article content>\nMEDIA:<image path>"
}
```

- `action="post"` — triggers feed posting mode (NOT DM/comment)
- `channel="fb-page"` — the Facebook channel instance name
- `target="feed"` — explicit feed target; do not inherit the Discord chat ID
- `forward=true` and `forward_reason` are REQUIRED. The reason must quote the user's literal approval context (for example: `User replied 'duyệt' to this ContentFactory article`). Never fabricate approval.
- If no image: omit the `MEDIA:` line

### Step 5: Update workspace state

On success:
1. Prepend to the article file:
   ```
   status: posted
   posted_at: 2026-07-25T15:30:00+07:00
   ```
2. Reply on Discord: "✅ Đã đăng bài lên fanpage"

On error:
1. Do NOT update the file
2. Reply on Discord with the stable tool error only
3. Do NOT retry, even if the error looks transient; ask an operator to reconcile the fanpage and reservation state first

## Error Handling

| Error | Action |
|-------|--------|
| `feed post failed; status is unknown and automatic retry is blocked` | Operator checks the fanpage and gateway logs, then reconciles the durable reservation; never retry from the agent |
| `feed post is already reserved; operator reconciliation is required` | Treat as posted/pending/unknown until an operator verifies the fanpage and ledger |
| `feed post content/media does not match...` | Request a fresh Discord review message for the exact draft and single image |
| `post action requires synchronous outbound dispatcher` | Gateway is not wired or needs restart/rebuild; ask the operator |
| `feed post approval sender is not explicitly allowlisted` | The approver's Discord user ID is missing from the review channel's `approval_allow_from`; an operator must add it (being in `allow_from` is NOT enough) |

## Configuration

### Discord review channel — who may approve

Publish authority is granted by a dedicated `approval_allow_from` list on the Discord
channel instance config, deliberately separate from `allow_from` (which only controls who
may chat with the bot). **Empty means nobody can approve**, so this is opt-in per deployment.

```json
{
  "config": {
    "allow_from": ["<chat-user-id>", "..."],
    "approval_allow_from": ["<approver-discord-user-id>"]
  }
}
```

### Facebook page

The skill requires a Facebook channel instance. Create via API:

```bash
curl -X POST "http://localhost:18790/v1/channels/instances" \
  -H "Authorization: Bearer <api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fb-page",
    "display_name": "Facebook Fanpage",
    "channel_type": "facebook",
    "agent_id": "019d1b58-ae00-7b64-8594-89b6158f327b",
    "credentials": {
      "page_access_token": "<TOKEN>",
      "app_secret": "<SECRET>",
      "verify_token": "<VERIFY>"
    },
    "config": {
      "page_id": "<PAGE_ID>",
      "features": {"comment_reply": false, "first_inbox": false, "messenger_auto_reply": false}
    }
  }'
```
