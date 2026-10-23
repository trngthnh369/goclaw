---
name: gws-workspace
description: Google Workspace CLI (gws) quick reference. Use when user asks about email, Gmail, Google Drive, Calendar, Sheets, or any Google Workspace operation. Agent executes gws commands via exec tool.
license: Internal
metadata:
  author: trngthnh369
  version: "1.0.0"
---

# Google Workspace CLI Reference

Execute Google Workspace operations via `exec gws <service> <command>`. Requires `gws auth login` (one-time OAuth).

## Gmail

```bash
# List recent messages
exec gws gmail messages list --max-results 10

# Search messages
exec gws gmail messages list --query "from:boss@company.com subject:urgent"
exec gws gmail messages list --query "after:2024/01/01 has:attachment"

# Read a message
exec gws gmail messages get --id <messageId> --format full

# Send email
exec gws gmail messages send --to "recipient@email.com" --subject "Subject" --body "Body text"

# Send with CC/BCC
exec gws gmail messages send --to "a@x.com" --cc "b@x.com" --bcc "c@x.com" --subject "Subject" --body "Body"

# List labels
exec gws gmail labels list

# Apply label
exec gws gmail messages modify --id <messageId> --add-labels "IMPORTANT"
```

### Gmail Search Operators
| Operator | Example | Purpose |
|----------|---------|---------|
| `from:` | `from:john@co.com` | Sender |
| `to:` | `to:team@co.com` | Recipient |
| `subject:` | `subject:invoice` | Subject line |
| `has:attachment` | — | Has files |
| `filename:` | `filename:pdf` | Attachment type |
| `after:` / `before:` | `after:2024/03/01` | Date range |
| `is:unread` | — | Unread only |
| `label:` | `label:work` | By label |

## Google Drive

```bash
# List files in root
exec gws drive files list --max-results 20

# Search files
exec gws drive files list --query "name contains 'report' and mimeType = 'application/pdf'"

# Download file
exec gws drive files download --id <fileId> --output ./downloaded-file.pdf

# Upload file
exec gws drive files upload --file ./report.pdf --name "Q1 Report.pdf" --parent <folderId>

# Create folder
exec gws drive files create --name "New Folder" --mime-type "application/vnd.google-apps.folder"

# Share file
exec gws drive permissions create --file-id <fileId> --type user --role writer --email "user@co.com"

# Get file metadata
exec gws drive files get --id <fileId> --fields "name,size,modifiedTime,webViewLink"
```

### Drive MIME Types
| Type | MIME |
|------|------|
| Folder | `application/vnd.google-apps.folder` |
| Doc | `application/vnd.google-apps.document` |
| Sheet | `application/vnd.google-apps.spreadsheet` |
| Slide | `application/vnd.google-apps.presentation` |

## Google Calendar

```bash
# List upcoming events
exec gws calendar events list --calendar-id primary --max-results 10 --time-min "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Create event
exec gws calendar events create --calendar-id primary \
  --summary "Meeting with Team" \
  --start "2024-03-15T10:00:00+07:00" \
  --end "2024-03-15T11:00:00+07:00" \
  --description "Discuss Q1 roadmap"

# Create event with attendees
exec gws calendar events create --calendar-id primary \
  --summary "Sync" \
  --start "2024-03-15T14:00:00+07:00" \
  --end "2024-03-15T14:30:00+07:00" \
  --attendees "alice@co.com,bob@co.com"

# Delete event
exec gws calendar events delete --calendar-id primary --event-id <eventId>

# List calendars
exec gws calendar calendars list
```

## Google Sheets

```bash
# Read cells
exec gws sheets values get --spreadsheet-id <id> --range "Sheet1!A1:D10"

# Write cells
exec gws sheets values update --spreadsheet-id <id> --range "Sheet1!A1" --values '[["Name","Age"],["Alice",30]]'

# Append rows
exec gws sheets values append --spreadsheet-id <id> --range "Sheet1!A:D" --values '[["New","Row","Data","Here"]]'

# Get spreadsheet metadata
exec gws sheets get --spreadsheet-id <id>

# Create new spreadsheet
exec gws sheets create --title "My New Sheet"
```

## Tips

- **Timezone**: Calendar events default to account timezone. Always specify explicit timezone offset (e.g., `+07:00` for Vietnam)
- **Pagination**: Use `--page-token` from previous response for next page
- **Output format**: Add `--format json` for machine-readable output that's easier to parse
- **Batch operations**: For bulk updates, prefer Sheets batch API over individual cell updates
- **Error handling**: Check exit code. Common errors: 403 (permission), 404 (not found), 429 (rate limit — wait and retry)
