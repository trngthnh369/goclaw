# Shopee Open Platform API Reference

## Auth: Partner + Shop Token + HMAC-SHA256

```bash
# Step 1: Generate auth URL (partner-level)
# Sign: SHA256(partner_id + path + timestamp + partner_key)
exec python3 scripts/sign_shopee.py --partner_id {PID} --partner_key {PKEY} --path "/api/v2/shop/auth_partner"
# Output: full URL → redirect shop owner to authorize

# Step 2: Exchange code for tokens (after redirect callback)
exec python3 scripts/sign_shopee.py \
  --partner_id {PID} --partner_key {PKEY} \
  --path "/api/v2/auth/token/get" \
  --body '{"code":"{AUTH_CODE}","shop_id":{SHOP_ID},"partner_id":{PID}}'
# Then: exec curl -X POST "{SIGNED_URL}" -H "Content-Type: application/json" -d '{BODY}'

# Step 3: Refresh token (before expiry, every 4 hours)
exec python3 scripts/sign_shopee.py \
  --partner_id {PID} --partner_key {PKEY} \
  --path "/api/v2/auth/access_token/get" \
  --shop_id {SHOP_ID} \
  --body '{"refresh_token":"{REFRESH}","shop_id":{SHOP_ID},"partner_id":{PID}}'
```

## Base URL
`https://partner.shopeemobile.com`

## Request Signing

All requests require HMAC-SHA256 signature in URL params:
```
partner_id={PID}&timestamp={TS}&sign={SHA256(partner_id + path + timestamp + access_token + shop_id + partner_key)}&access_token={TOKEN}&shop_id={SHOP_ID}
```

Use `scripts/sign_shopee.py` to generate signed URLs automatically.

## Top Endpoints

### Products
```bash
# List products
exec python3 scripts/sign_shopee.py --partner_id {PID} --partner_key {PKEY} \
  --path "/api/v2/product/get_item_list" --shop_id {SID} --access_token {TOKEN} \
  --params '{"offset":0,"page_size":50,"item_status":"NORMAL"}'
# Then: exec curl "{SIGNED_URL}"

# Get product detail
# Path: /api/v2/product/get_item_base_info
# Params: {"item_id_list":"100001,100002"}

# Get product model (variants)
# Path: /api/v2/product/get_model_list
# Params: {"item_id":100001}

# Update price
# Path: /api/v2/product/update_price (POST)
# Body: {"item_id":100001,"price_list":[{"model_id":0,"original_price":199000}]}

# Update stock
# Path: /api/v2/product/update_stock (POST)
# Body: {"item_id":100001,"stock_list":[{"model_id":0,"normal_stock":50}]}
```

### Orders
```bash
# Get order list
# Path: /api/v2/order/get_order_list
# Params: {"time_range_field":"create_time","time_from":{UNIX},"time_to":{UNIX},"page_size":50,"order_status":"READY_TO_SHIP"}

# Get order detail
# Path: /api/v2/order/get_order_detail
# Params: {"order_sn_list":"2301010A1B2C3D","response_optional_fields":"buyer_user_id,item_list,recipient_address"}

# Ship order
# Path: /api/v2/logistics/ship_order (POST)
# Body: {"order_sn":"2301010A1B2C3D","pickup":{"address_id":12345}}
```

### Logistics
```bash
# Get shipping parameter
# Path: /api/v2/logistics/get_shipping_parameter
# Params: {"order_sn":"2301010A1B2C3D"}

# Get tracking info
# Path: /api/v2/logistics/get_tracking_info
# Params: {"order_sn":"2301010A1B2C3D"}
```

### Shop
```bash
# Get shop info
# Path: /api/v2/shop/get_shop_info

# Get shop categories
# Path: /api/v2/product/get_category
# Params: {"language":"vi"}
```

## Webhooks (Push Notifications)
Configure in Partner Center. Events:
- `shopeePushOrderStatus` — order status change
- `shopeePushItemMatch` — product matched
- `shopeePushTradeOrderUpdate` — trade order update
- `shopeeAuthorizedPartner` — shop authorized/deauthorized

Webhook URL receives POST with signature in headers for verification.

## Pagination
- Offset-based: `offset=0&page_size=50`
- Response: `{"more":true,"next_offset":50}`
- Continue until `more=false`

## Rate Limits
- 10 requests/second per shop
- Different limits per endpoint category
- 429 with `retry_after` in response

## Error Codes
| Code | Meaning |
|------|---------|
| error_auth | Invalid signature or expired token |
| error_param | Invalid parameters |
| error_not_found | Resource not found |
| error_permission | Insufficient permissions |
| error_server | Shopee internal error (retry) |
