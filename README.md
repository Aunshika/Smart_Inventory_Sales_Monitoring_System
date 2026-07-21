# Smart Inventory & Sales Monitoring System

## Technologies
- Python
- FastAPI
- MongoDB Atlas
- HTML
- CSS
- JavaScript

## Modules
1. Authentication
2. Product Management
3. Inventory Management
4. Supplier Management
5. Sales Management
6. Reports & Analytics

## API datasets

The project uses endpoint-specific CSV datasets in:

`backend/datasets/api`

Each file has the same fields shown in the corresponding FastAPI Swagger
request schema. This avoids repeated product data and keeps categories,
suppliers, products, purchases, sales, stock movements, and users separate.

Generate or import it with:

```powershell
pip install -r requirements.txt
python backend/scripts/generate_inventory_dataset.py
python backend/scripts/seed_data.py
```

The import is idempotent and populates every MongoDB collection used by
the data-entry endpoints.
