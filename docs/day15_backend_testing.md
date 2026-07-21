# Day 15 - Backend Testing

Use Postman to test every backend module for successful responses, input validation, and exception handling.

## Setup
- Base URL: `http://127.0.0.1:8080`
- Login first with `POST /login`.
- Copy `access_token`.
- Add this header to protected APIs:

```text
Authorization: Bearer <access_token>
```

## Authentication APIs

### Register User
`POST /register`

Success test:
```text
username=admin1
email=admin1@example.com
password=admin123
confirm_password=admin123
role=Admin
```

Validation tests:
- Password and confirm password do not match: expect `400`.
- Invalid role: expect `400`.
- Duplicate username/email: expect `409`.

### Login
`POST /login`

Body type: `x-www-form-urlencoded`
```text
username=admin1
password=admin123
```

Validation tests:
- Wrong username/password: expect `401`.
- Empty username/password: expect `400`.

## Product APIs

### Add Product
`POST /products`

```json
{
  "product_id": "P001",
  "product_name": "Keyboard",
  "quantity": 10,
  "price": 500
}
```

Validation tests:
- Empty `product_id`: expect `422`.
- Negative `quantity`: expect `422`.
- Zero or negative `price`: expect `422`.
- Duplicate `product_id`: expect `409`.

### View Products
`GET /products`

Filter tests:
```text
/products?search=Keyboard
/products?page=1&limit=5
```

### Update Product
`PUT /products/P001`

Query params:
```text
product_name=Keyboard Pro
quantity=15
price=700
```

Validation tests:
- Unknown product ID: expect `404`.
- Negative quantity: expect `400`.
- Zero price: expect `400`.

### Delete Product
`DELETE /products/P001`

Validation tests:
- Unknown product ID: expect `404`.

## Category APIs

### Add Category
`POST /categories`

```json
{
  "category_id": "C001",
  "category_name": "Electronics",
  "description": "Electronic items"
}
```

Validation tests:
- Empty category fields: expect `422`.
- Duplicate `category_id`: expect `409`.

### View Categories
`GET /categories`

### Update Category
`PUT /categories/C001`

Query params:
```text
category_name=Computer Accessories
description=Accessories for computers
```

Validation tests:
- Unknown category ID: expect `404`.
- Empty query values: expect `400`.

### Delete Category
`DELETE /categories/C001`

Validation tests:
- Unknown category ID: expect `404`.

## Supplier APIs

### Add Supplier
`POST /suppliers`

```json
{
  "supplier_id": "S001",
  "supplier_name": "ABC Traders",
  "email": "abc@example.com",
  "phone": "9876543210",
  "address": "Chennai"
}
```

Validation tests:
- Invalid email: expect `400`.
- Short phone: expect `422`.
- Duplicate supplier ID: expect `409`.

### View Suppliers
`GET /suppliers`

### Update Supplier
`PUT /suppliers/S001`

Query params:
```text
supplier_name=ABC Traders Pvt Ltd
email=abcnew@example.com
phone=9876543210
address=Chennai
```

Validation tests:
- Unknown supplier ID: expect `404`.
- Invalid email: expect `400`.

### Delete Supplier
`DELETE /suppliers/S001`

Validation tests:
- Unknown supplier ID: expect `404`.

## Inventory APIs

### Stock In
`POST /inventory/stock-in`

```json
{
  "product_id": "P001",
  "quantity": 5,
  "note": "New stock received"
}
```

Validation tests:
- Unknown product ID: expect `404`.
- Zero or negative quantity: expect `422`.

### Stock Out
`POST /inventory/stock-out`

```json
{
  "product_id": "P001",
  "quantity": 2,
  "note": "Damaged stock removed"
}
```

Validation tests:
- Unknown product ID: expect `404`.
- Quantity greater than available stock: expect `400`.

### Current Stock
```text
GET /inventory/current-stock
GET /inventory/current-stock/P001
```

Validation tests:
- Unknown product ID: expect `404`.

### Inventory History
```text
GET /inventory/history
GET /inventory/history?product_id=P001
GET /inventory/history?movement_type=stock in
GET /inventory/history?performed_by=admin1
```

Expected movement records:
- `Initial Stock`
- `Stock In`
- `Stock Out`
- `Purchase`
- `Sale`
- `Stock Adjustment Increase`
- `Stock Adjustment Decrease`

## Purchase APIs

### Add Purchase
`POST /purchases`

```json
{
  "product_id": "P001",
  "quantity": 10,
  "unit_cost": 400,
  "supplier_id": "S001",
  "note": "Purchase entry"
}
```

Validation tests:
- Unknown product ID: expect `404`.
- Unknown supplier ID: expect `404`.
- Zero or negative quantity/unit cost: expect `422`.
- Duplicate purchase ID: expect `409`.

### View Purchases
```text
GET /purchases
GET /purchases?product_id=P001
GET /purchases?supplier_id=S001
```

### Purchase Invoice
```text
GET /purchases/{purchase_id}/invoice
GET /purchases/{purchase_id}/invoice/pdf
```

Validation tests:
- Unknown purchase ID: expect `404`.

## Sales APIs

### Add Sale
`POST /sales`

```json
{
  "product_id": "P001",
  "quantity": 2,
  "unit_price": 700,
  "customer_name": "Customer One",
  "note": "Sale entry"
}
```

Validation tests:
- Unknown product ID: expect `404`.
- Quantity greater than current stock: expect `400`.
- Zero or negative quantity/unit price: expect `422`.
- Duplicate sale ID: expect `409`.

### View Sales
```text
GET /sales
GET /sales?product_id=P001
GET /sales?sold_by=admin1
```

### Sales Invoice
```text
GET /sales/{sale_id}/invoice
GET /sales/{sale_id}/invoice/pdf
```

Validation tests:
- Unknown sale ID: expect `404`.

## Final Evidence
Take screenshots of:
- Successful login.
- Successful add product/category/supplier.
- Successful stock-in and stock-out.
- Current stock.
- Inventory history.
- Successful purchase and sale.
- Invoice response/PDF download.
- At least one `400`, `401`, `404`, `409`, and `422` response.
