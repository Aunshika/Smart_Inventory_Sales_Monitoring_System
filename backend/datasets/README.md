# FastAPI Endpoint Datasets

The `api` directory contains one CSV file for each FastAPI data-entry
schema. Its columns match the Swagger request fields exactly.

The current API datasets are generated from an online grocery inventory
and sales dataset. The raw source file is stored at:

`backend/datasets/online_raw/grocery_inventory_sales.csv`

Original source reference:

`https://www.kaggle.com/datasets/salahuddinahmedshuvo/grocery-inventory-and-sales-dataset`

Accessible CSV mirror used by the generator:

`https://github.com/debrupa03/Grocrey_Inventory_and_Sales_Dashboard`

The source dataset contains product details, categories, supplier IDs,
supplier names, stock quantity, reorder level, reorder quantity, unit price,
sales volume, turnover, dates, warehouse location, and status.

Because no online dataset exactly matches this project's Swagger schemas,
`backend/scripts/generate_inventory_dataset.py` transforms the online CSV into
the exact FastAPI request formats. Fields that do not exist in the source,
such as API transaction IDs, supplier email, and supplier phone, are generated
deterministically during transformation.

Price mapping note: the online dataset stores prices as decimal currency
values, such as `$4.50`, while the FastAPI schema uses integer fields.
To preserve accuracy, prices are stored as cents. For example, `$4.50`
becomes `450`.

| File | FastAPI endpoint |
| --- | --- |
| `categories.csv` | `POST /categories` |
| `suppliers.csv` | `POST /suppliers` |
| `products.csv` | `POST /products` |
| `purchases.csv` | `POST /purchases` |
| `sales.csv` | `POST /sales` |
| `stock_in.csv` | `POST /inventory/stock-in` |
| `stock_out.csv` | `POST /inventory/stock-out` |
| `users.csv` | `POST /register` |

Generate the files:

```powershell
py -3.14 backend/scripts/generate_inventory_dataset.py
```

Import them into MongoDB:

```powershell
py -3.14 backend/scripts/seed_data.py
```

Current generated record counts:

- Categories: 8
- Suppliers: 990
- Products: 990
- Purchases: 990
- Sales: 990
- Stock in rows: 990
- Stock out rows: 990
