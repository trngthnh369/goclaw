# Facebook Marketing API Reference

## Auth: OAuth2 (Graph API)

```bash
# Step 1: Get user access token via Login Dialog
# https://www.facebook.com/v21.0/dialog/oauth?client_id={APP_ID}&redirect_uri={REDIRECT_URI}&scope=ads_management,ads_read,business_management,pages_read_engagement

# Step 2: Exchange code for token
exec curl "https://graph.facebook.com/v21.0/oauth/access_token?client_id={APP_ID}&redirect_uri={REDIRECT_URI}&client_secret={APP_SECRET}&code={CODE}"

# Step 3: Exchange for long-lived token (60 days)
exec curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id={APP_ID}&client_secret={APP_SECRET}&fb_exchange_token={SHORT_TOKEN}"

# Debug token (check expiry, scopes)
exec curl "https://graph.facebook.com/v21.0/debug_token?input_token={TOKEN}&access_token={APP_ID}|{APP_SECRET}"
```

**Headers:**
```
Authorization: Bearer {access_token}
```

## Base URL
`https://graph.facebook.com/v21.0`

## Top Endpoints

### Ad Accounts
```bash
# List ad accounts
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/me/adaccounts?fields=id,name,account_status,currency,timezone_name,amount_spent"

# Get ad account details
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}?fields=id,name,balance,currency,spend_cap"
```

### Campaigns
```bash
# List campaigns
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/campaigns?fields=id,name,objective,status,daily_budget,lifetime_budget&limit=50"

# Create campaign
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/campaigns" \
  -d '{"name":"Q1 Sales","objective":"OUTCOME_SALES","status":"PAUSED","special_ad_categories":[]}'

# Update campaign
exec curl -X POST -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/{CAMPAIGN_ID}" \
  -d "name=Updated Campaign&status=ACTIVE"

# Delete campaign
exec curl -X DELETE -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/{CAMPAIGN_ID}"
```

### Ad Sets
```bash
# List ad sets in campaign
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/{CAMPAIGN_ID}/adsets?fields=id,name,status,daily_budget,targeting,optimization_goal&limit=50"

# Create ad set
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/adsets" \
  -d '{"name":"Vietnam 25-45","campaign_id":"{CAMPAIGN_ID}","daily_budget":500000,"billing_event":"IMPRESSIONS","optimization_goal":"OFFSITE_CONVERSIONS","targeting":{"geo_locations":{"countries":["VN"]},"age_min":25,"age_max":45},"status":"PAUSED"}'
```

### Insights (Reporting)
```bash
# Campaign insights (last 7 days)
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/{CAMPAIGN_ID}/insights?fields=impressions,clicks,spend,cpc,cpm,ctr,actions&date_preset=last_7d"

# Ad account insights by day
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/insights?fields=impressions,clicks,spend,actions&time_increment=1&date_preset=last_30d"

# Breakdown by age and gender
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/insights?fields=impressions,clicks,spend&breakdowns=age,gender&date_preset=last_7d"
```

### Custom Audiences
```bash
# List audiences
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/act_{AD_ACCOUNT_ID}/customaudiences?fields=id,name,approximate_count,subtype&limit=50"
```

## Webhooks (Subscriptions)
```bash
# Subscribe to lead gen
exec curl -X POST -H "Authorization: Bearer {TOKEN}" \
  "https://graph.facebook.com/v21.0/{PAGE_ID}/subscribed_apps" \
  -d "subscribed_fields=leadgen"

# App-level webhook subscription (requires app dashboard or API)
# Topics: ad_account, campaigns, adsets, ads, leadgen
```

## Pagination
- Cursor-based: response has `paging.cursors.after`, use `&after={cursor}`
- Or `paging.next` URL for next page

## Rate Limits
- 200 calls/hour per ad account (Business Use Case rate)
- Check `x-business-use-case-usage` header
- Insights: 1 call per 5 minutes for large date ranges

## Error Codes
| Code | Meaning |
|------|---------|
| 190 | Access token expired |
| 17 | Rate limited (User request limit) |
| 4 | API too many calls |
| 100 | Invalid parameter |
| 200 | Permissions error |
| 2635 | Ad account disabled |
