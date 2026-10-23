# KiotViet API Reference

## Auth: Client Credentials (OAuth2)

```bash
# Get access token
exec curl -X POST "https://id.kiotviet.vn/connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "scopes=PublicApi.Access&grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}"

# Response: {"access_token":"...","expires_in":86400,"token_type":"Bearer"}
# Token valid for 24 hours
```

**Headers for all requests:**
```
Authorization: Bearer {access_token}
Retailer: {retailer_name}
Content-Type: application/json
```

## Base URL
`https://public.kiotapi.com`

## Top Endpoints

### Products
```bash
# List products
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/products?pageSize=100&currentItem=0&orderBy=createdDate&orderDirection=DESC"

# Get product by ID
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/products/{id}"

# Get product by code
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/products/code/{code}"

# Create product
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" -H "Content-Type: application/json" \
  "https://public.kiotapi.com/products" \
  -d '{"code":"SP001","name":"Test Product","categoryId":123,"basePrice":199000,"retailPrice":299000,"inventory":[{"branchId":1,"onHand":100}]}'

# Update product
exec curl -X PUT -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" -H "Content-Type: application/json" \
  "https://public.kiotapi.com/products/{id}" \
  -d '{"name":"Updated Name","retailPrice":350000}'
```

### Orders (Hóa đơn)
```bash
# List orders
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/orders?pageSize=50&currentItem=0&orderBy=createdDate&orderDirection=DESC"

# Get order by ID
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/orders/{id}"

# Get order by code
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/orders/code/{code}"
```

### Invoices (Hóa đơn bán hàng)
```bash
# List invoices
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/invoices?pageSize=50&currentItem=0"

# Create invoice
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" -H "Content-Type: application/json" \
  "https://public.kiotapi.com/invoices" \
  -d '{"branchId":1,"customerId":123,"invoiceDetails":[{"productId":456,"quantity":2,"price":299000}],"totalPayment":598000,"method":"Cash"}'
```

### Customers
```bash
# List customers
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/customers?pageSize=50&currentItem=0"

# Search customer
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/customers?contactNumber=0901234567"

# Create customer
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" -H "Content-Type: application/json" \
  "https://public.kiotapi.com/customers" \
  -d '{"name":"Nguyen Van A","contactNumber":"0901234567","address":"123 Nguyen Hue, HCM"}'
```

### Inventory
```bash
# Get inventory by product
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/inventories?productId={id}"

# Get inventory by branch
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/inventories?branchId={id}&pageSize=100"
```

### Branches
```bash
# List branches
exec curl -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" \
  "https://public.kiotapi.com/branches"
```

## Webhooks
```bash
# Register webhook
exec curl -X POST -H "Authorization: Bearer {TOKEN}" -H "Retailer: {RETAILER}" -H "Content-Type: application/json" \
  "https://public.kiotapi.com/webhooks" \
  -d '{"webhook":{"type":"product.update","url":"https://your-domain.com/webhook/kiotviet","isActive":true}}'

# Types: product.update, product.delete, stock.update, order.update, invoice.update, customer.update, customer.delete
```

## Pagination
- Offset-based: `?currentItem=0&pageSize=100` (max 100)
- Response: `{"total":500,"pageSize":100,"data":[...]}`
- Next page: `currentItem = currentItem + pageSize`

## Rate Limits
- 5 requests/second
- Daily quota varies by plan

## Error Codes
| Code | Meaning |
|------|---------|
| 401 | Token expired → re-request via client_credentials |
| 403 | Insufficient permissions |
| 404 | Resource not found |
| 409 | Conflict (duplicate code) |
| 429 | Rate limited |
