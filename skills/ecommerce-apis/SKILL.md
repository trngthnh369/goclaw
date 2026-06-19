---
name: ecommerce-apis
description: Ecommerce platform API reference for 7 platforms (Haravan, KiotViet, Facebook Marketing, Shopee, Lazada, TikTok Shop, Shopify). Use when user asks about connecting to ecommerce APIs, syncing products/orders/inventory, building integrations, or any ecommerce platform operations. Covers auth flows, endpoints, webhooks, and request signing.
license: Internal
metadata:
  author: trngthnh369
  version: "1.0.0"
---

# Ecommerce APIs

Unified reference for 7 ecommerce platforms. Each platform has a detailed reference in `references/`.

## Platform Overview

| Platform | Auth Type | Base URL | Rate Limit | Webhook | Reference |
|----------|-----------|----------|------------|---------|-----------|
| Haravan | OAuth2 | `apis.haravan.com` | 2 req/s per shop | ✅ | `haravan-api.md` |
| KiotViet | Client Credentials | `public.kiotapi.com` | 5 req/s | ✅ | `kiotviet-api.md` |
| Facebook Marketing | OAuth2 + Graph API | `graph.facebook.com/v21.0` | 200 calls/hr | ✅ Webhooks | `facebook-marketing-api.md` |
| Shopee | Partner+Shop token + HMAC | `partner.shopeemobile.com` | 10 req/s | ✅ Push | `shopee-api.md` |
| Lazada | App Key + Sign | `api.lazada.vn` | 50 req/s | ✅ Push | `lazada-api.md` |
| TikTok Shop | App Key + Sign | `open-api.tiktokglobalshop.com` | 10 req/s | ✅ Push | `tiktokshop-api.md` |
| Shopify | OAuth2 / API Key | `{shop}.myshopify.com/admin/api/2024-10` | 40 req/s (Plus) | ✅ | `shopify-api.md` |

## When to Use Which Platform

- **Vietnam local**: Haravan (Shopify-like), KiotViet (POS + inventory)
- **Southeast Asia marketplaces**: Shopee, Lazada (owned by Alibaba)
- **Social commerce**: TikTok Shop, Facebook Marketing
- **Global/DTC**: Shopify

## Common Workflows

### Product Sync
1. Read reference for source platform → fetch products (paginated)
2. Transform fields (title, price, SKU, images, variants)
3. Read reference for target platform → create/update products

### Order Processing
1. Setup webhook for `order.created` on platform
2. Receive order → normalize fields
3. Check inventory → confirm/reject
4. Update fulfillment status

### Inventory Sync
1. Fetch inventory from ERP/source system
2. Match by SKU across platforms
3. Update stock quantities (batch where possible)

## Request Signing (SEA Marketplaces)

Shopee, Lazada, and TikTok Shop require HMAC request signing. Use scripts in `scripts/`:

```bash
# Shopee: generate signed URL
exec python3 scripts/sign_shopee.py \
  --partner_id 123456 \
  --partner_key "your_key" \
  --path "/api/v2/product/get_item_list" \
  --shop_id 789 \
  --access_token "token_here"

# Lazada: generate signed URL  
exec python3 scripts/sign_lazada.py \
  --app_key "your_app_key" \
  --app_secret "your_secret" \
  --path "/products/get" \
  --params '{"filter":"live","limit":"50"}'

# TikTok Shop: generate signed URL
exec python3 scripts/sign_tiktok.py \
  --app_key "your_app_key" \
  --app_secret "your_secret" \
  --path "/product/202309/products/search" \
  --access_token "token_here" \
  --params '{"page_size":"20"}'
```

Each script outputs the full signed URL ready for `exec curl`.

## Agent Workflow

1. User asks about a specific platform → read the corresponding reference file
2. Identify the required endpoint from the reference
3. For SEA platforms requiring signing → use the signing script
4. Build `exec curl` command with proper headers and signed URL
5. Execute and parse JSON response
6. If paginated, loop with cursor/offset from response

## Error Handling

- **401/403**: Token expired → guide user to re-authenticate
- **429**: Rate limited → wait and retry (check `Retry-After` header)
- **500**: Platform issue → retry 3x with exponential backoff
- **Signature error**: Check timestamp (most platforms allow ±5 min clock skew)
