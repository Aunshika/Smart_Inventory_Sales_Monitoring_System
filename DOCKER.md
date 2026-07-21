# Docker Setup

This project runs with Docker Compose using three services:

- `frontend` - serves the HTML/CSS/JavaScript app on port `5500`
- `backend` - runs the FastAPI API on port `8080`
- `database` - runs MongoDB on port `27017`

Start the full application:

```powershell
docker compose up --build
```

Then open:

```text
http://localhost:5500/login
```

Backend API docs:

```text
http://localhost:8080/docs
```

Inside Docker, the backend uses the Compose MongoDB service:

```text
mongodb://database:27017/Smartinventory
```

Your local `.env` is still used for Google Sign-In, SMTP, reCAPTCHA, and
other secrets. Do not commit `.env`.

To seed the Docker database, run:

```powershell
docker compose exec backend python backend/scripts/seed_data.py
docker compose exec backend python backend/scripts/seed_warehouses_users_inventory.py
```

Stop the containers:

```powershell
docker compose down
```

Remove containers and the local MongoDB Docker volume:

```powershell
docker compose down -v
```
