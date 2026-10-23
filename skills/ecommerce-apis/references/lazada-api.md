# Lazada Open Platform API Reference

## Auth: App Key + HMAC-SHA256 Signing

```bash
# Step 1: Get authorization URL
# https://auth.lazada.com/oauth/authorize?response_type=code&force_auth=true&redirect_uri={REDIRECT_URI}&client_id={APP_KEY}

# Step 2: Exchange code for tokens
exec python3 scripts/sign_lazada.py \
  --app_key {APP_KEY} --app_secret {APP_SECRET} \
  --path "/auth/token/create" \
  --params '{"code":"{AUTH_CODE}"}'
# Then: exec curl -X POST "{SIGNED_URL}"
# Response: {"access_token":"...","refresh_token":"...","expires_in":604800}

# Step 3: Refresh token (every 7 days)
exec python3 scripts/sign_lazada.py \
  --app_key {APP_KEY} --app_secret {APP_SECRET} \
  --path "/auth/token/refresh" \
  --params '{"refresh_token":"{REFRESH_TOKEN}"}'
```

## Base URLs
- Vietnam: `https://api.lazada.vn/rest`
- Thailand: `https://api.lazada.co.th/rest`
- Singapore: `https://api.lazada.sg/rest`

## Request Signing

All requests require signature in query params:
```
app_key={KEY}&timestamp={MS}&sign_method=sha256&sign={HMAC_SHA256}&access_token={TOKEN}
```

Sign string = sort all params alphabetically → concatenate key+value → HMAC-SHA256 with app_secret.
Use `scripts/sign_lazada.py` for automatic signing.

## Top Endpoints

### Products
```bash
# Get products
# Path: /products/get
# Params: {"filter":"live","limit":"50","offset":"0"}

# Get product by SKU
# Path: /product/item/get
# Params: {"seller_sku":"SKU001"}

# Create product
# Path: /product/create (POST)
# Body XML (Lazada uses XML for product creation):
# <Request><Product><PrimaryCategory>1234</PrimaryCategory><Skus><Sku><SellerSku>SKU001</SellerSku><price>199000</price><quantity>100</quantity><package_weight>0.5</package_weight></Sku></Skus><Attributes><name>Product Name</name><description>HTML description</description></Attributes></Product></Request>

# Update price/stock
# Path: /product/price_quantity/update (POST)
# Body XML:
# <Request><Product><Skus><Sku><SellerSku>SKU001</SellerSku><Price>250000</Price><Quantity>80</Quantity></Sku></Skus></Product></Request>

# Update product attributes
# Path: /product/update (POST)
```

### Orders
```bash
# Get orders
# Path: /orders/get
# Params: {"created_after":"2024-01-01T00:00:00+07:00","status":"pending","limit":"50","offset":"0","sort_by":"created_at","sort_direction":"DESC"}

# Get order items
# Path: /order/items/get
# Params: {"order_id":"123456789"}

# Get single order
# Path: /order/get
# Params: {"order_id":"123456789"}

# Pack order (Ready to Ship)
# Path: /order/pack (POST)
# Params: {"shipping_provider":"Standard","order_item_ids":"[12345,67890]"}

# Set order status to Ready to Ship
# Path: /order/rts (POST)
# Params: {"order_item_ids":"[12345]","shipment_provider":"Lazada Express","delivery_type":"dropship"}
```

### Finance
```bash
# Get payout status
# Path: /finance/payout/status/get

# Get transaction details
# Path: /finance/transaction/details/get
# Params: {"start_time":"2024-01-01","end_time":"2024-01-31","limit":"50"}
```

## Webhooks (Push)
Configure in Seller Center → App Console. Events:
- `order_status_update` — order transitions
- `guarantee_fund` — funds released
- `return_status_update` — return/refund updates

## Pagination
- Offset-based: `offset=0&limit=50` (max 100)
- Response: `{"data":{"count_total":500,"products":[...]}}`
- Next: `offset = offset + limit`

## Rate Limits
- 50 requests/second per app
- 10,000 requests/hour per seller

## Important Notes
- Product creation uses **XML** format, not JSON
- Price in **cents** for some regions (VN uses whole number VND)
- Async operations: large product uploads return a `batch_id` — poll with `/image/response/get`

## Error Codes
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unknown error |
| 6 | Request too fast (rate limit) |
| 24 | Invalid access token |
| 28 | Access token expired |
| 30 | Missing required param |
| 1000 | Internal error (retry) |
