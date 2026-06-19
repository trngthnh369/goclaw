# Haravan API Reference

## Auth: OAuth2

```bash
# Step 1: Redirect user to authorize
# https://accounts.haravan.com/connect/authorize?response_type=code&scope=openid profile email org com.read_products com.write_products com.read_orders com.write_orders&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}

# Step 2: Exchange code for token
exec curl -X POST https://accounts.haravan.com/connect/token \
  -d "grant_type=authorization_code&code={CODE}&redirect_uri={REDIRECT_URI}&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}"

# Step 3: Refresh token
exec curl -X POST https://accounts.haravan.com/connect/token \
  -d "grant_type=refresh_token&refresh_token={REFRESH_TOKEN}&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}"
```

**Headers for all requests:**
```
Authorization: Bearer {access_token}
Content-Type: application/json
```

## Base URL
`https://apis.haravan.com/com`

## Top Endpoints

### Products
```bash
# List products (paginated)
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/products.json?page=1&limit=50"

# Get single product
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/products/{product_id}.json"

# Create product
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://apis.haravan.com/com/products.json" \
  -d '{"product":{"title":"Test Product","body_html":"<p>Description</p>","vendor":"MyBrand","product_type":"Áo","variants":[{"price":"199000","sku":"SKU001","inventory_quantity":100}]}}'

# Update product
exec curl -X PUT -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://apis.haravan.com/com/products/{product_id}.json" \
  -d '{"product":{"title":"Updated Title"}}'

# Delete product
exec curl -X DELETE -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/products/{product_id}.json"
```

### Orders
```bash
# List orders
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/orders.json?status=any&limit=50&page=1"

# Get single order
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/orders/{order_id}.json"

# Update order fulfillment
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://apis.haravan.com/com/orders/{order_id}/fulfillments.json" \
  -d '{"fulfillment":{"tracking_number":"VN123456","tracking_company":"GHN","line_items":[{"id":{line_item_id}}]}}'
```

### Inventory
```bash
# Get inventory levels
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/inventory_levels.json?inventory_item_ids={item_id}"

# Adjust inventory
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://apis.haravan.com/com/inventory_levels/adjust.json" \
  -d '{"location_id":{loc_id},"inventory_item_id":{item_id},"available_adjustment":10}'
```

### Customers
```bash
# List customers
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/customers.json?limit=50"

# Search customers
exec curl -H "Authorization: Bearer {TOKEN}" \
  "https://apis.haravan.com/com/customers/search.json?query=email:test@email.com"
```

## Webhooks
```bash
# Create webhook
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" \
  "https://apis.haravan.com/com/webhooks.json" \
  -d '{"webhook":{"topic":"orders/create","address":"https://your-domain.com/webhook/haravan","format":"json"}}'

# Topics: orders/create, orders/updated, orders/paid, orders/cancelled, products/create, products/update, products/delete
```

## Pagination
- Page-based: `?page=1&limit=50` (max 250)
- Response includes `count` for total items

## Rate Limits
- 2 requests/second per shop (burst: 40 per bucket)
- 429 response with `Retry-After` header

## Error Codes
| Code | Meaning |
|------|---------|
| 401 | Token expired/invalid |
| 404 | Resource not found |
| 422 | Validation error (check response body) |
| 429 | Rate limited |
| 500 | Haravan server error |
