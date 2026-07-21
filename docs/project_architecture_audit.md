# Smart Inventory Sales Monitoring System - Architecture Audit

Audit date: 21 July 2026
Stack: HTML/CSS/JavaScript frontend, FastAPI backend, MongoDB, JWT, Google OAuth, reCAPTCHA, Docker, PDF/CSV reports

## Executive Summary

The project has a broad feature set and is runnable through Docker with frontend, backend, and MongoDB services. The application includes authentication, role-based dashboards, products, inventory, purchases, sales, suppliers, analytics, reports, notifications, settings, product scanning, PDF/CSV export, and warehouse-aware data.

The main deployment blocker is not missing functionality. The main blocker is production hardening: authentication security, modularization, logging, test coverage, configuration management, and separating the large monolithic backend/frontend files into maintainable modules.

## Scores

| Area | Score |
| --- | ---: |
| Overall architecture | 6.5 / 10 |
| Code quality | 5.5 / 10 |
| Security | 4.0 / 10 |
| Performance | 6.5 / 10 |
| Maintainability | 5.0 / 10 |
| Folder structure | 6.0 / 10 |
| Deployment readiness | 6.5 / 10 |

Final readiness: 62%

## Validation Results

Passed:

- `python -m py_compile` for core backend files.
- `node --check frontend/assets/js/script.js`.
- Docker files exist for backend, frontend, and compose orchestration.
- `.gitignore` ignores `.env`, `.venv`, cache folders, and generated files.
- MongoDB connection helper detects Atlas vs local Docker URI style.
- Main collections and indexes are initialized in `backend/app/db/database.py`.

Observed risks:

- Backend is highly monolithic: `backend/app/main.py` is about 8,741 lines.
- Frontend main controller is highly monolithic: `frontend/assets/js/script.js` is about 5,232 lines.
- Password hashing is not production safe in `backend/app/core/auth.py`.
- JWT secret is hardcoded in `backend/app/core/jwt_handler.py`.
- Debug `print()` and `console.log()` statements remain in production paths.
- Several response schemas are plain dictionaries rather than explicit Pydantic response models.
- Some generated/cache files exist locally and must stay ignored.

## Critical Issues

### 1. Insecure password hashing

File: `backend/app/core/auth.py`

Current implementation prefixes passwords with `hashed_`. This is not secure and is unsuitable for deployment.

Required fix:

- Use `passlib[bcrypt]` or `argon2-cffi`.
- Store only secure salted password hashes.
- Add migration path for development users.

### 2. Hardcoded JWT secret

File: `backend/app/core/jwt_handler.py`

`SECRET_KEY = "mysecretkey"` is hardcoded. Anyone with code access can forge JWTs.

Required fix:

- Read `JWT_SECRET_KEY` from `.env`.
- Require a strong random secret in production.
- Add token expiry with `exp` claim.
- Add separate admin/session lifetime policy.

### 3. Secrets/config duplicated between backend and frontend

File: `frontend/assets/js/config.js`

Frontend has hardcoded config placeholders and Google client ID. Site key is okay to expose, but config should be generated from environment or `/public-config` consistently.

Required fix:

- Keep secrets backend-only.
- Use Docker entrypoint-generated frontend config for public values.
- Never commit real `.env`.

### 4. Monolithic backend

File: `backend/app/main.py`

A single 8k+ line file contains auth, reports, dashboard, products, purchases, sales, inventory, suppliers, settings, utilities, document rendering, middleware, and startup maintenance.

Required fix:

- Split into routers, services, repositories, schemas, utilities.
- Keep `main.py` as app factory/bootstrap only.

### 5. Monolithic frontend controller

File: `frontend/assets/js/script.js`

A single 5k+ line file handles login, dashboard, notifications, products, inventory, reports, users, modals, settings, exports, charts, and routing.

Required fix:

- Split by page/module.
- Use shared API client and shared UI helpers.
- Keep page-specific code in page modules.

## High Issues

### Logging uses print statements

Files:

- `backend/app/main.py`
- multiple backend scripts
- frontend JS files

Required fix:

- Replace backend `print()` with Python `logging`.
- Use structured levels: INFO, WARNING, ERROR, CRITICAL.
- Remove debug frontend `console.log()` from production paths.

### Incomplete response modeling

Many endpoints return raw dictionaries. This makes API contracts harder to verify.

Required fix:

- Add Pydantic response models for auth, products, purchases, sales, reports, analytics, users, warehouses.
- Use `response_model=` in FastAPI endpoints.

### Blocking operations inside backend request paths

PDF generation, SMTP, and large MongoDB operations can block request handling.

Required fix:

- Keep SMTP in threadpool/background task.
- Consider async job/export queue for large PDFs.
- Add request timeouts and logs around slow report generation.

### Frontend localStorage token storage

JWT is stored in localStorage. This is common for student projects but vulnerable to XSS.

Required production option:

- Prefer HttpOnly Secure SameSite cookies.
- If keeping localStorage, enforce strict CSP and sanitize all HTML rendering.

### HTML generation with innerHTML

Frontend builds many screens using template strings and `innerHTML`. It often uses `escapeHtml`, which helps, but the pattern remains risky and error-prone.

Required fix:

- Centralize rendering helpers.
- Audit every dynamic interpolation.
- Consider a framework or safe DOM builder.

## Medium Issues

### Folder structure is partially organized but not industry-standard

Good:

- `backend/app/core`
- `backend/app/db`
- `backend/app/models`
- `backend/app/schemas`
- `backend/scripts`
- `frontend/assets`
- `docker`
- `docs`

Needs improvement:

- No `backend/app/api/routers`.
- No `backend/app/services`.
- No `backend/app/repositories`.
- No dedicated report generator package.
- Frontend page files exist but main logic is still concentrated in `script.js`.

### Test coverage is minimal

Existing tests are mostly smoke tests. There are no full workflow tests for auth, products, inventory, sales, purchases, reports, warehouse filtering, Docker DB, or reCAPTCHA.

Required fix:

- Add pytest tests with test database.
- Add endpoint tests using FastAPI TestClient.
- Add frontend smoke tests for route rendering.

### Database schema consistency

Collections exist for products, warehouse inventory, sales, purchases, suppliers, warehouses, users, notifications, etc. Indexing is good overall, but the data model is mixed: some stock lives on product records and some in `warehouse_inventory`.

Required fix:

- Treat `products` as master data.
- Treat `warehouse_inventory` as stock by warehouse.
- Avoid future calculations from `products.quantity` where warehouse stock is required.

### Module responsibility overlap

Examples:

- Dashboard calculations mix live products, warehouse inventory, sales, purchases, and reports.
- Inventory/restock/purchases workflows overlap.
- Reports contain query logic and PDF formatting in the same file.

Required fix:

- Move business rules to services.
- Move MongoDB access to repository layer.
- Move PDF/CSV generation to report utilities.

## Low Issues

- Many scripts are development utilities and should be documented in `docs/scripts.md`.
- Screenshot folder should not be included in Docker build context.
- Frontend image assets are large; consider compression/lazy loading.
- Some docs exist but need alignment with the final Docker flow.
- More consistent naming is needed: `location_id`, `warehouse_id`, `warehouse_name`, `location` are sometimes used together.

## API Review

Strengths:

- Endpoints cover most modules.
- Most protected endpoints use `Depends(get_current_user)` and `check_role`.
- Pagination exists for large pages such as products, purchases, sales, users.
- Warehouse scoping exists for Manager/Staff data.

Weaknesses:

- Too many endpoints in one file.
- Some endpoints use raw query params instead of request schemas.
- Status codes and response shapes are not fully standardized.
- No API version prefix such as `/api/v1`.
- No central exception handler for consistent error responses.

Recommended API structure:

- `/api/v1/auth/*`
- `/api/v1/products/*`
- `/api/v1/inventory/*`
- `/api/v1/purchases/*`
- `/api/v1/sales/*`
- `/api/v1/suppliers/*`
- `/api/v1/reports/*`
- `/api/v1/analytics/*`
- `/api/v1/users/*`
- `/api/v1/warehouses/*`
- `/api/v1/notifications/*`
- `/api/v1/settings/*`

## Database Review

Important collections:

- `users`
- `products`
- `warehouse_inventory`
- `warehouses`
- `categories`
- `suppliers`
- `sales`
- `sales_items`
- `purchases`
- `purchase_items`
- `inventory_history`
- `stock_movements`
- `low_stock_alerts`
- `notifications`
- `reports`
- `activity_logs`
- `returns`
- `damaged_stock`

Indexes are initialized for many important fields in `backend/app/db/database.py`.

Recommended additions:

- Compound indexes for common date + warehouse queries.
- Unique compound index on `warehouse_inventory(product_id, warehouse_id)`.
- Standardized date fields: prefer `created_at` plus business date where needed.
- Data validation rules in MongoDB for critical collections.

## Security Review

Must fix before production:

1. Replace fake password hashing.
2. Move JWT secret to `.env`.
3. Add JWT expiry and refresh/re-login rules.
4. Remove production debug logs.
5. Use production CORS allowlist only.
6. Validate all file uploads by content and size.
7. Avoid localStorage tokens for production or add CSP hardening.
8. Make rate limiting persistent/shared if deployed with multiple workers.
9. Use HTTPS in production.
10. Never commit `.env`.

## Frontend Review

Strengths:

- Rich UI with many modules.
- Modern dashboard experience.
- Role-aware sidebar behavior.
- Loading and empty states exist in several pages.
- Product image fallback and QR/barcode support exist.

Weaknesses:

- Main script is too large.
- Many `innerHTML` render paths.
- State is spread across localStorage, globals, and route cache.
- Accessibility needs systematic review for keyboard navigation, focus traps, modal labels, and contrast.
- Some modules are registered but logic remains in `script.js`.

## Backend Review

Strengths:

- Docker-aware MongoDB connection.
- Startup index initialization.
- Role checks across many endpoints.
- PDF/CSV exports implemented.
- Warehouse scoping exists.

Weaknesses:

- `main.py` handles too much.
- Utilities, schemas, business logic, DB access, and report generation are mixed.
- Logging should use `logging` module.
- Security core needs immediate hardening.

## Docker Review

Strengths:

- Backend Dockerfile uses non-root user.
- Frontend uses Nginx.
- MongoDB uses official image.
- Compose includes health checks and persistent volumes.
- Backend waits for MongoDB health.
- Docker Mongo URI uses service name `database`.

Improvements:

- Add production-specific compose override.
- Add resource limits.
- Add log rotation.
- Add backup/restore instructions for Mongo volume.
- Avoid copying entire frontend into backend image unless needed.
- Use pinned dependency versions.

## Performance Review

Good:

- Indexes exist for many frequent fields.
- Pagination exists for key list pages.
- PDF timeout has been increased for large reports.
- Dashboard date cache now includes selected range.

Needs improvement:

- Large PDF generation should become asynchronous for big data.
- Some aggregation/report logic should move closer to MongoDB pipelines.
- Avoid N+1 supplier/product lookups in report generation.
- Compress/optimize image assets.
- Split frontend JS to reduce initial payload.

## Recommended Folder Structure

```text
Smart_Inventory_Sales_Monitoring_System/
+-- backend/
¦   +-- app/
¦   ¦   +-- api/
¦   ¦   ¦   +-- v1/
¦   ¦   ¦       +-- auth.py
¦   ¦   ¦       +-- dashboard.py
¦   ¦   ¦       +-- products.py
¦   ¦   ¦       +-- inventory.py
¦   ¦   ¦       +-- purchases.py
¦   ¦   ¦       +-- sales.py
¦   ¦   ¦       +-- suppliers.py
¦   ¦   ¦       +-- users.py
¦   ¦   ¦       +-- warehouses.py
¦   ¦   ¦       +-- reports.py
¦   ¦   ¦       +-- analytics.py
¦   ¦   ¦       +-- notifications.py
¦   ¦   +-- core/
¦   ¦   +-- db/
¦   ¦   +-- models/
¦   ¦   +-- schemas/
¦   ¦   +-- services/
¦   ¦   +-- repositories/
¦   ¦   +-- reports/
¦   ¦   +-- utils/
¦   ¦   +-- middleware/
¦   ¦   +-- main.py
¦   +-- scripts/
¦   +-- tests/
+-- frontend/
¦   +-- assets/
¦   ¦   +-- css/
¦   ¦   +-- js/
¦   ¦   +-- images/
¦   +-- components/
¦   +-- pages/
¦   +-- index.html
+-- docker/
+-- docs/
+-- reports/
+-- uploads/
+-- logs/
+-- Dockerfile.backend
+-- Dockerfile.frontend
+-- docker-compose.yml
+-- .env.example
+-- .gitignore
+-- README.md
```

## Production Roadmap

### Phase 1: Security blockers

1. Replace password hashing with bcrypt/argon2.
2. Move JWT secret to environment variable.
3. Add token expiry and refresh/session policy.
4. Remove frontend/backend debug logs.
5. Verify `.env` is not tracked.

### Phase 2: Modular backend

1. Create `api/v1` routers.
2. Move DB operations into repositories.
3. Move business rules into services.
4. Move PDF/CSV code into `backend/app/reports`.
5. Add common response/error handler.

### Phase 3: Modular frontend

1. Split `script.js` by route/page.
2. Centralize API client.
3. Centralize modal/toast/loading utilities.
4. Replace repeated `innerHTML` logic with safe render helpers.
5. Add accessibility/focus management tests.

### Phase 4: Testing

1. Add auth tests.
2. Add role-permission tests.
3. Add product/inventory workflow tests.
4. Add purchase/sale workflow tests.
5. Add report export tests.
6. Add Docker smoke test.

### Phase 5: Deployment readiness

1. Add production env template.
2. Add backup/restore docs.
3. Add CI checks: Python compile, tests, JS syntax, Docker build.
4. Add monitoring/logging.
5. Add HTTPS/reverse proxy deployment docs.

## Final Assessment

The system is a strong internship/project implementation with many functional modules and Docker support. It is good for demos and local deployment. Before production deployment, the top priorities are security hardening, modularization, logging cleanup, test coverage, and stable environment/configuration management.