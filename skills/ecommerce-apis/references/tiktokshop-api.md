# TikTok Shop Open API Reference

## Auth: App Key + HMAC-SHA256

```bash
# Step 1: Generate auth URL
# https://services.tiktokshop.com/open/authorize?app_key={APP_KEY}&state={STATE}

# Step 2: Exchange code for tokens
exec python3 scripts/sign_tiktok.py \
  --app_key {APP_KEY} --app_secret {APP_SECRET} \
  --path "/api/v2/token/get" \
  --params '{"auth_code":"{CODE}","grant_type":"authorized_code"}'
# Response: {"access_token":"...","refresh_token":"...","access_token_expire_in":86400}

# Step 3: Refresh token
exec python3 scripts/sign_tiktok.py \
  --app_key {APP_KEY} --app_secret {APP_SECRET} \
  --path "/api/v2/token/refresh" \
  --params '{"refresh_token":"{REFRESH}","grant_type":"refresh_token"}'
```

## Base URL
`https://open-api.tiktokglobalshop.com` (API version in path)

## Request Signing

```
sign = HMAC-SHA256(app_secret, path + sorted_params)
Query: app_key={KEY}&timestamp={UNIX}&sign={SIGN}&access_token={TOKEN}&shop_cipher={CIPHER}
```

Use `scripts/sign_tiktok.py` for automatic signing.

## Top Endpoints (API v202309+)

### Products
```bash
# Search products
# Path: /product/202309/products/search (POST)
# Body: {"page_size":20,"page_token":""}
# Response: {"products":[...],"next_page_token":"...","total_count":100}

# Get product detail
# Path: /product/202309/products/{product_id}

# Create product
# Path: /product/202309/products (POST)
# Body: {"title":"Product Name","description":"<p>HTML</p>","category_id":"601226","brand_id":"0","main_images":[{"uri":"tos-xxx"}],"skus":[{"sales_attributes":[{"attribute_id":"100000","value_id":"100"}],"stock_infos":[{"warehouse_id":"700","available_quantity":50}],"price":{"amount":"199000","currency":"VND"}}]}

# Update product
# Path: /product/202309/products/{product_id} (PUT)

# Update price
# Path: /product/202309/products/prices (POST)
# Body: {"product_id":"123","skus":[{"id":"456","price":{"amount":"250000","currency":"VND"}}]}

# Update inventory
# Path: /product/202309/products/inventory (POST)
# Body: {"product_id":"123","skus":[{"id":"456","stock_infos":[{"warehouse_id":"700","available_quantity":80}]}]}
```

### Orders
```bash
# Search orders
# Path: /order/202309/orders/search (POST)
# Body: {"create_time_ge":1704067200,"create_time_lt":1704153600,"page_size":50,"order_status":"AWAITING_SHIPMENT"}

# Get order detail
# Path: /order/202309/orders (GET)
# Params: {"ids":"576460752303423488,576460752303423489"}

# Ship package
# Path: /fulfillment/202309/packages/{package_id}/ship (POST)
# Body: {"handover_method":"PICKUP","pickup_slot":{"start_time":1704110400,"end_time":1704124800}}
```

### Fulfillment
```bash
# Get shipping providers
# Path: /logistics/202309/delivery_options

# Get package detail
# Path: /fulfillment/202309/packages/{package_id}

# Get tracking
# Path: /fulfillment/202309/packages/{package_id}/shipping_info
```

### Shop
```bash
# Get authorized shops
# Path: /authorization/202309/shops

# Get shop warehouse
# Path: /logistics/202309/warehouses
```

## Webhooks (Event Subscription)
Configure in TikTok Shop Partner Center. Events:

| Event | Trigger |
|-------|---------|
| `ORDER_STATUS_CHANGE` | Order state transitions |
| `PACKAGE_UPDATE` | Shipment tracking updates |
| `RETURN_STATUS_CHANGE` | Return/refund status |
| `PRODUCT_STATUS_CHANGE` | Product audit results |
| `SELLER_AUTHORIZATION` | Shop authorized/deauthorized |

Webhook payload includes `shop_cipher` for multi-shop routing.

## Pagination
- Token-based: `page_token` from response, `page_size` (max 100)
- Response: `{"next_page_token":"xxx","total_count":500}`
- Continue until `next_page_token` is empty

## Rate Limits
- 10 requests/second per shop per API
- Different quotas per endpoint category
- Response header: `x-tts-ratelimit-remaining`

## Error Codes
| Code | Meaning |
|------|---------|
| 0 | Success |
| 105001 | Invalid sign |
| 105002 | Access token expired |
| 105004 | Permission denied |
| 105007 | Rate limited |
| 200001 | Invalid parameter |
| 300002 | Product not found |
