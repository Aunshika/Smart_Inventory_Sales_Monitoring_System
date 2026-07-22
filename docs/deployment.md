# Smart Inventory Deployment Guide

This guide deploys the Smart Inventory Sales Monitoring System with Docker containers.

## Services

- `frontend`: Nginx serving HTML/CSS/JavaScript
- `backend`: FastAPI API on port 8080 inside the Docker network
- `database`: MongoDB 7 with persistent Docker volume

## Files

- `docker-compose.yml` - local development Docker setup
- `docker-compose.prod.yml` - production Docker setup with HTTPS-ready Nginx
- `backend/Dockerfile` - FastAPI image
- `frontend/Dockerfile` - Nginx frontend image
- `docker/nginx/default.conf` - local frontend Nginx config
- `docker/nginx/default.prod.conf` - production HTTPS Nginx config
- `.env.development.example` - development env template
- `.env.production.example` - production env template

## Local Docker Run

```powershell
copy .env.development.example .env
# edit .env values
.\docker\start.ps1
```

Open:

```text
http://localhost:5500/login
http://localhost:8080/docs
```

Stop:

```powershell
.\docker\stop.ps1
```

## Production Docker Run on a VPS

1. Copy the production env file:

```powershell
copy .env.production.example .env.production
```

2. Edit `.env.production` and set real values.

3. Add TLS certificates:

```text
docker/nginx/certs/fullchain.pem
docker/nginx/certs/privkey.pem
```

You can use Let's Encrypt/Certbot on the server to generate these files.

4. Start production containers:

```powershell
.\docker\start.ps1 -Prod
```

Linux/macOS:

```bash
sh docker/start.sh prod
```

5. Stop production containers:

```powershell
.\docker\stop.ps1 -Prod
```

Linux/macOS:

```bash
sh docker/stop.sh prod
```

## Production URLs

For a single-domain deployment, set:

```env
FRONTEND_URL=https://your-domain.com
PUBLIC_BACKEND_URL=https://your-domain.com/api
CORS_ORIGINS=https://your-domain.com
```

The production Nginx config proxies:

```text
https://your-domain.com/api/* -> backend:8080
https://your-domain.com/uploads/* -> backend uploads
```

## Required Environment Variables

```env
MONGODB_URI=mongodb://database:27017/Smartinventory
DATABASE_NAME=Smartinventory
ENVIRONMENT=production
FRONTEND_URL=https://your-domain.com
CORS_ORIGINS=https://your-domain.com
PUBLIC_BACKEND_URL=https://your-domain.com/api

JWT_SECRET_KEY=replace_with_a_long_random_secret
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_ADMIN_TOKEN_EXPIRE_MINUTES=15

GOOGLE_CLIENT_ID=your_google_oauth_client_id
RECAPTCHA_SITE_KEY=your_recaptcha_public_site_key
RECAPTCHA_SECRET_KEY=your_recaptcha_private_secret_key
RECAPTCHA_ENABLED=true

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=your_sender_email
SMTP_PASSWORD=your_gmail_app_password

ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_EMAIL=admin@your-domain.com
ADMIN_INITIAL_PASSWORD=replace_with_secure_initial_password
SEED_USER_INITIAL_PASSWORD=replace_with_secure_seed_password

LOG_LEVEL=INFO
LOG_DIR=logs
RUN_STARTUP_MAINTENANCE=0
```

## Persistent Data

Docker volumes persist:

- MongoDB data: `smart_inventory_mongo_data`
- Uploaded product images: `smart_inventory_uploads`
- Generated exports: `smart_inventory_exports`
- Backend logs: `smart_inventory_logs`

Do not run this unless you intentionally want to delete all Docker database data:

```powershell
docker compose down -v
```

## Production Readiness Checklist

- [ ] `.env.production` exists and contains no placeholders
- [ ] `JWT_SECRET_KEY` is strong and private
- [ ] Google OAuth authorized origins include production domain
- [ ] reCAPTCHA domain includes production domain
- [ ] SMTP App Password works
- [ ] TLS cert files exist in `docker/nginx/certs/`
- [ ] `CORS_ORIGINS` contains only trusted production origins
- [ ] MongoDB volume is persistent
- [ ] Uploads volume is persistent
- [ ] Logs volume is persistent
- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.production config` passes
- [ ] `docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build` starts all services

## Verification Checklist

After deployment, test:

- [ ] Login page loads over HTTPS
- [ ] reCAPTCHA appears and validates
- [ ] Normal username/email login works
- [ ] Google Login works
- [ ] JWT protected dashboard opens
- [ ] Admin, Manager, Staff role dashboards work
- [ ] Product CRUD works
- [ ] Product image upload persists after restart
- [ ] QR code, barcode, and scanner flows work
- [ ] Inventory health and restock workflow work
- [ ] Purchases and sales update stock
- [ ] Reports page loads
- [ ] PDF export downloads
- [ ] CSV export downloads
- [ ] Forgot Password sends email and reset link works
- [ ] Notifications dropdown loads
- [ ] Warehouse filtering works

## Hosting Recommendations

### Best free/low-cost option for the complete Docker Compose app

Oracle Cloud Always Free VM is the best fit for this project because it can run Docker Compose with frontend, backend, and MongoDB together on one VM. You manage Linux, Docker, firewall, DNS, backups, and TLS yourself.

### Easiest hosted app option

Render can deploy Docker web services and static sites, but its free web services spin down after inactivity and free service filesystems are ephemeral, so local uploaded files should not be treated as permanent storage. Use MongoDB Atlas for the database and object storage or paid persistent disk for uploads.

### Good short demo option

Railway can run code and databases during its trial/free-credit model, but usage credits and data retention limits make it better for demos than long-term free production.

### Database recommendation

MongoDB Atlas Free cluster is good for demos and development. For production, use a paid Atlas tier or a backed-up MongoDB server. Atlas free clusters are limited to one free cluster per project.

## Platform Notes

### Render/Railway style deployment

For hosted platforms, deploy backend and frontend as separate services:

- Backend service uses `backend/Dockerfile`
- Frontend service uses `frontend/Dockerfile`
- Database uses MongoDB Atlas
- Set `MONGODB_URI=mongodb+srv://...`
- Set frontend `API_BASE_URL` to the public backend URL
- Use platform-managed HTTPS

### VPS/Oracle/AWS style deployment

Use this repository directly:

```bash
git clone <repo-url>
cd Smart_Inventory_Sales_Monitoring_System
cp .env.production.example .env.production
# edit .env.production
sh docker/start.sh prod
```

For AWS EC2, Oracle Cloud, or any Linux VPS, open ports 80 and 443 in the firewall/security group.
