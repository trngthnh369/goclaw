# Shopify API Reference

## Auth: OAuth2 or API Key (Custom App)

### OAuth2 (Public App)
```bash
# Step 1: Redirect to install
# https://{shop}.myshopify.com/admin/oauth/authorize?client_id={API_KEY}&scope=read_products,write_products,read_orders,write_orders&redirect_uri={REDIRECT_URI}

# Step 2: Exchange code for permanent token
exec curl -X POST "https://{shop}.myshopify.com/admin/oauth/access_token" \
  -H "Content-Type: application/json" \
  -d '{"client_id":"{API_KEY}","client_secret":"{API_SECRET}","code":"{CODE}"}'
# Response: {"access_token":"shpat_xxx"} — permanent, no refresh needed
```

### Custom App (API Key)
Generate in Shopify Admin → Settings → Apps → Develop Apps. Get `Admin API access token` directly.

**Headers for all requests:**
```
X-Shopify-Access-Token: {access_token}
Content-Type: application/json
```

## Base URL (REST)
`https://{shop}.myshopify.com/admin/api/2024-10`

## Top REST Endpoints

### Products
```bash
# List products
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products.json?limit=50"

# Get product
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products/{product_id}.json"

# Create product
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products.json" \
  -d '{"product":{"title":"Test Product","body_html":"<p>Description</p>","vendor":"MyBrand","product_type":"Shirt","variants":[{"price":"29.99","sku":"SKU001","inventory_quantity":100}]}}'

# Update product
exec curl -X PUT -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products/{product_id}.json" \
  -d '{"product":{"title":"Updated Title","variants":[{"id":{variant_id},"price":"34.99"}]}}'

# Delete product
exec curl -X DELETE -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products/{product_id}.json"

# Count products
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/products/count.json"
```

### Orders
```bash
# List orders
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/orders.json?status=any&limit=50"

# Get order
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/orders/{order_id}.json"

# Create fulfillment
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/fulfillments.json" \
  -d '{"fulfillment":{"line_items_by_fulfillment_order":[{"fulfillment_order_id":{fo_id}}],"tracking_info":{"number":"VN123456","company":"GHN","url":"https://tracking.ghn.vn"}}}'

# Close order
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/orders/{order_id}/close.json"
```

### Inventory
```bash
# Get inventory levels
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/inventory_levels.json?inventory_item_ids={item_id}"

# Set inventory level
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/inventory_levels/set.json" \
  -d '{"location_id":{loc_id},"inventory_item_id":{item_id},"available":50}'

# Adjust inventory
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/inventory_levels/adjust.json" \
  -d '{"location_id":{loc_id},"inventory_item_id":{item_id},"available_adjustment":-5}'
```

### Customers
```bash
# List customers
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/customers.json?limit=50"

# Search customers
exec curl -H "X-Shopify-Access-Token: {TOKEN}" \
  "https://{shop}.myshopify.com/admin/api/2024-10/customers/search.json?query=email:test@email.com"
```

## GraphQL API

```bash
# GraphQL endpoint
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/graphql.json" \
  -d '{"query":"{ products(first: 10) { edges { node { id title variants(first: 5) { edges { node { id price sku } } } } } } }"}'
```

GraphQL is preferred for complex queries (nested data, bulk operations).

## Webhooks
```bash
# Create webhook
exec curl -X POST -H "X-Shopify-Access-Token: {TOKEN}" -H "Content-Type: application/json" \
  "https://{shop}.myshopify.com/admin/api/2024-10/webhooks.json" \
  -d '{"webhook":{"topic":"orders/create","address":"https://your-domain.com/webhook/shopify","format":"json"}}'

# Topics: orders/create, orders/updated, orders/paid, products/create, products/update, products/delete, inventory_levels/update, customers/create
```

## Pagination
- Cursor-based (REST): `Link` header with `rel="next"` and `rel="previous"`
- `?limit=50` (max 250)
- Parse `page_info` from Link header for next page

## Rate Limits
- REST: 40 req/s (Shopify Plus) or 2 req/s (regular)
- GraphQL: 1000 cost points per second
- Check `X-Shopify-Shop-Api-Call-Limit` header (e.g., `39/40`)

## Error Codes
| Code | Meaning |
|------|---------|
| 401 | Invalid API key or token |
| 402 | Shop frozen (payment issue) |
| 403 | Insufficient scope |
| 404 | Resource not found |
| 422 | Validation error |
| 429 | Rate limited (Retry-After header) |
| 430 | Too many requests to this shop |
