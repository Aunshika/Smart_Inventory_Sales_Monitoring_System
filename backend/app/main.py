from contextlib import asynccontextmanager
import asyncio
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from pathlib import Path as FilePath
from typing import Optional

from dotenv import load_dotenv
import requests

BASE_DIR = FilePath(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=False)
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4
from xml.sax.saxutils import escape as xml_escape
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = timezone(timedelta(hours=5, minutes=30))

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pymongo import DeleteMany, InsertOne, UpdateMany, UpdateOne
from fastapi.staticfiles import StaticFiles
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core.auth import hash_password, password_needs_rehash, verify_password
from app.core.jwt_handler import create_access_token, decode_access_token
from app.core.logging_config import configure_logging
from app.core.roles import check_role

logger = configure_logging()
from app.db.database import (
    initialize_database,
    products_collection,
    users_collection,
    categories_collection,
    suppliers_collection,
    inventory_history_collection,
    purchases_collection,
    sales_collection,
    locations_collection,
    restock_queue_collection,
    system_settings_collection,
    warehouses_collection,
    warehouse_inventory_collection,
    customers_collection,
    stock_movements_collection,
    sales_items_collection,
    purchase_items_collection,
    low_stock_alerts_collection,
    notifications_collection,
    activity_logs_collection,
    returns_collection,
    damaged_stock_collection
)
from app.models.documents import (
    object_id,
    inventory_history_document,
    product_document,
    purchase_document,
    sale_document,
    category_document,
    supplier_document,
    serialize_document,
    serialize_documents,
    user_document
)
from app.schemas import (
    ProductCreate,
    CategoryCreate,
    ChangePasswordRequest,
    ConfirmPasswordRequest,
    ForgotPasswordRequest,
    PurchaseCreate,
    RestockQueueBulkRequest,
    RestockQueueAddAllRequest,
    RestockQueueSelection,
    ResetPasswordRequest,
    SaleCreate,
    SupplierCreate,
    StockMovementCreate,
    ProfileUpdate,
    GoogleLogin
)


def run_startup_maintenance():
    started = time.perf_counter()
    tasks = [
        ("reset dynamic warehouse mapping", reset_users_and_static_warehouses_once),
        ("verify supplier IDs", ensure_supplier_ids),
        ("assign unscoped records", assign_unscoped_records_to_first_warehouse),
        ("sync warehouse fields", sync_location_fields_for_existing_records),
        ("seed module collections", seed_module_collections),
    ]
    print("[startup-maintenance] background maintenance started", file=sys.stderr)
    for label, task in tasks:
        task_started = time.perf_counter()
        try:
            task()
            elapsed_ms = (time.perf_counter() - task_started) * 1000
            print(f"[startup-maintenance] {label} completed in {elapsed_ms:.1f}ms", file=sys.stderr)
        except Exception as exc:
            print(f"[startup-maintenance] {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc()
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"[startup-maintenance] finished in {elapsed_ms:.1f}ms", file=sys.stderr)


@asynccontextmanager
async def lifespan(app):
    startup_started = time.perf_counter()
    try:
        initialize_database()
        elapsed_ms = (time.perf_counter() - startup_started) * 1000
        print(f"[startup] database indexes initialized in {elapsed_ms:.1f}ms", file=sys.stderr)
    except Exception as exc:
        print(f"[startup] database initialization failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
    warn_if_brevo_missing()
    warn_if_recaptcha_missing()
    if clean_env_value("RUN_STARTUP_MAINTENANCE", "0") in {"1", "true", "yes", "on"}:
        threading.Thread(target=run_startup_maintenance, daemon=True).start()
    else:
        print("[startup] startup maintenance skipped; set RUN_STARTUP_MAINTENANCE=1 to run it", file=sys.stderr)
    yield


openapi_tags = [
    {"name": "Home", "description": "Project welcome endpoint"},
    {"name": "Auth", "description": "User registration and login"},
    {"name": "Dashboards", "description": "Role based dashboard checks"},
    {"name": "Users", "description": "User management endpoints"},
    {"name": "Products", "description": "Product inventory endpoints"},
    {"name": "Purchases", "description": "Purchase entry and records"},
    {"name": "Sales", "description": "Sales entry and history"},
    {"name": "Reports", "description": "Daily, weekly, and monthly reports"},
    {"name": "Invoices", "description": "Sales and purchase invoices"},
    {"name": "Inventory", "description": "Stock movement and history endpoints"},
    {"name": "Alerts", "description": "Low stock alerts and reorder reminders"},
    {"name": "Categories", "description": "Product category endpoints"},
    {"name": "Suppliers", "description": "Supplier management endpoints"}
]


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    openapi_tags=openapi_tags
)

FRONTEND_DIR = FilePath(__file__).resolve().parents[2] / "frontend"
UPLOADS_DIR = FilePath(__file__).resolve().parents[1] / "uploads"
PRODUCT_UPLOAD_DIR = UPLOADS_DIR / "products"
EXPORTS_DIR = FilePath(__file__).resolve().parents[2] / "exports"
LOGS_DIR = FilePath(__file__).resolve().parents[2] / "logs"
PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_ROUTES = {
    "/login",
    "/register",
    "/dashboard",
    "/products",
    "/inventory",
    "/sales",
    "/purchases",
    "/suppliers",
    "/reports",
    "/analytics",
    "/users",
    "/settings",
    "/profile"
}

app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR / "assets"),
    name="frontend-assets"
)

app.mount(
    "/uploads",
    StaticFiles(directory=UPLOADS_DIR),
    name="uploads"
)



@app.middleware("http")
async def serve_frontend_clean_routes(request: Request, call_next):
    accepts_html = "text/html" in request.headers.get("accept", "")
    if request.method == "GET" and accepts_html and request.url.path in {
        "/index.html",
        "/frontend/index.html"
    }:
        return RedirectResponse("/login")
    if request.method == "GET" and accepts_html and request.url.path in {
        "/reset-password",
        "/reset-password.html",
        "/frontend/reset-password.html"
    }:
        return FileResponse(FRONTEND_DIR / "reset-password.html")
    if request.method == "GET" and accepts_html and request.url.path == "/":
        return RedirectResponse("/login")
    is_frontend_route = request.url.path.rstrip("/") in FRONTEND_ROUTES
    if request.method == "GET" and accepts_html and is_frontend_route:
        return FileResponse(FRONTEND_DIR / "index.html")
    return await call_next(request)

def configured_cors_origins():
    raw_origins = os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:3000,http://localhost:3000"
    )
    origins = [origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()]
    if os.getenv("ENVIRONMENT", "development").lower() != "production":
        origins.append("null")
    return sorted(set(origins))


app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Type"]
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response



@app.middleware("http")
async def request_timing_logger(request: Request, call_next):
    request_id = uuid4().hex[:12]
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        logger.exception("[req:%s] %s %s failed", request_id, request.method, request.url.path)
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("[req:%s] %s %s %s %.1fms", request_id, request.method, request.url.path, status_code, elapsed_ms)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

@app.get("/health", tags=["Home"])
def health_check():
    return {"status": "ok"}


LOGIN_WINDOW_SECONDS = 60
MAX_FAILED_LOGIN_ATTEMPTS = 5
ACCOUNT_LOCK_SECONDS = 15 * 60
ADMIN_SESSION_SECONDS = int(str(os.getenv("JWT_ADMIN_TOKEN_EXPIRE_MINUTES", "15")).strip() or "15") * 60
LOGIN_ATTEMPTS = {}
LOGIN_LOCKS = {}
LOGIN_DATABASE_TIMEOUT_SECONDS = 8
LOGIN_PASSWORD_TIMEOUT_SECONDS = 5


def find_login_user(login_identifier, email_identifier):
    clean_login = str(login_identifier or "").strip()
    clean_email = str(email_identifier or clean_login).strip().lower()
    username_regex = f"^{re.escape(clean_login)}$"
    return users_collection.find_one({
        "$or": [
            {"username": clean_login},
            {"username": {"$regex": username_regex, "$options": "i"}},
            {"email": clean_email},
            {"email": clean_login.lower()}
        ]
    })


def verify_login_password(password, stored_password):
    return verify_password(password, stored_password or "")



def get_user_password_hash(user):
    if not user:
        return ""
    return user.get("hashed_password") or user.get("password") or user.get("password_hash") or ""


def user_has_any_password_field(user):
    return bool(user and (user.get("hashed_password") or user.get("password") or user.get("password_hash")))


def password_update_document(new_password):
    return {
        "$set": {"hashed_password": hash_password(new_password)},
        "$unset": {"password": "", "password_hash": ""}
    }

def utc_now():
    return datetime.now(timezone.utc)


def iso_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else ""


def get_request_ip(request: Request):
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def login_security_key(username, request: Request):
    normalized = str(username or "").strip().casefold()
    return f"{get_request_ip(request)}:{normalized}"


def assert_login_not_locked(username, request: Request):
    key = login_security_key(username, request)
    lock_until = LOGIN_LOCKS.get(key)
    now = utc_now()
    if lock_until and lock_until > now:
        retry_after = max(1, int((lock_until - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)}
        )
    if lock_until:
        LOGIN_LOCKS.pop(key, None)


def register_failed_login(username, request: Request):
    key = login_security_key(username, request)
    now = utc_now()
    window_start = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    attempts = [item for item in LOGIN_ATTEMPTS.get(key, []) if item > window_start]
    attempts.append(now)
    LOGIN_ATTEMPTS[key] = attempts
    if len(attempts) >= MAX_FAILED_LOGIN_ATTEMPTS:
        LOGIN_LOCKS[key] = now + timedelta(seconds=ACCOUNT_LOCK_SECONDS)
        LOGIN_ATTEMPTS.pop(key, None)
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(ACCOUNT_LOCK_SECONDS)}
        )


def clear_failed_login(username, request: Request):
    key = login_security_key(username, request)
    LOGIN_ATTEMPTS.pop(key, None)
    LOGIN_LOCKS.pop(key, None)


def generic_invalid_login():
    raise HTTPException(status_code=401, detail="Incorrect username/email or password.")


def transient_secret_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def clean_env_value(name, default=""):
    value = os.getenv(name, default)
    return str(value).strip().strip('"').strip("'") if value is not None else ""


BREVO_EMAIL_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_TIMEOUT_SECONDS = 20


def frontend_base_url():
    return clean_env_value("FRONTEND_URL", "http://127.0.0.1:5500").rstrip("/")


def brevo_settings():
    return {
        "api_key": clean_env_value("BREVO_API_KEY"),
        "sender_email": clean_env_value("EMAIL_FROM"),
        "frontend_url": frontend_base_url()
    }


def warn_if_brevo_missing():
    settings = brevo_settings()
    print(f"Brevo API key loaded: {bool(settings.get('api_key'))}", file=sys.stderr)
    print(f"Brevo sender email loaded: {bool(settings.get('sender_email'))}", file=sys.stderr)
    missing = [key for key in ("api_key", "sender_email") if not settings.get(key)]
    if missing:
        print(
            f"WARNING: Brevo email is not fully configured. Missing: {', '.join(missing)}. "
            "Forgot-password emails will fail until .env is fixed.",
            file=sys.stderr
        )


def send_brevo_email(recipient_email: str, subject: str, html_content: str) -> None:
    settings = brevo_settings()
    api_key = settings["api_key"]
    sender_email = settings["sender_email"]
    missing = [name for name, value in (("BREVO_API_KEY", api_key), ("EMAIL_FROM", sender_email)) if not value]
    if missing:
        logger.error("Brevo email configuration missing: %s", ", ".join(missing))
        raise HTTPException(status_code=503, detail="Unable to send email. Please try again.")

    payload = {
        "sender": {"name": "Smart Inventory", "email": sender_email},
        "to": [{"email": recipient_email}],
        "subject": subject,
        "htmlContent": html_content
    }
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    logger.info("Brevo request started for recipient=%s", recipient_email)
    try:
        response = requests.post(
            BREVO_EMAIL_API_URL,
            headers=headers,
            json=payload,
            timeout=BREVO_TIMEOUT_SECONDS
        )
    except requests.Timeout as exc:
        logger.error("Brevo network or timeout error for recipient=%s: timeout", recipient_email)
        raise HTTPException(status_code=503, detail="Unable to send email. Please try again.") from exc
    except requests.RequestException as exc:
        logger.error("Brevo network or timeout error for recipient=%s: %s", recipient_email, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Unable to send email. Please try again.") from exc

    if response.status_code in {200, 201, 202}:
        logger.info("Brevo email sent successfully for recipient=%s status=%s", recipient_email, response.status_code)
        return

    logger.error("Brevo request failed with status code %s for recipient=%s", response.status_code, recipient_email)
    raise HTTPException(status_code=503, detail="Unable to send email. Please try again.")


def has_real_recaptcha_site_key():
    site_key = clean_env_value("RECAPTCHA_SITE_KEY")
    return bool(site_key and site_key != "your_public_site_key" and site_key != "PASTE_YOUR_RECAPTCHA_SITE_KEY_HERE")


def has_real_recaptcha_secret_key():
    secret = clean_env_value("RECAPTCHA_SECRET_KEY")
    return bool(secret and secret != "your_private_secret_key" and secret != "PASTE_YOUR_RECAPTCHA_SECRET_KEY_HERE")


def recaptcha_requested_enabled():
    return clean_env_value("RECAPTCHA_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def recaptcha_enforcement_enabled():
    return recaptcha_requested_enabled() and has_real_recaptcha_site_key() and has_real_recaptcha_secret_key()


def warn_if_recaptcha_missing():
    enabled = recaptcha_requested_enabled()
    print(f"reCAPTCHA enabled: {enabled}", file=sys.stderr)
    print(f"reCAPTCHA site key loaded: {has_real_recaptcha_site_key()}", file=sys.stderr)
    print(f"reCAPTCHA secret key loaded: {has_real_recaptcha_secret_key()}", file=sys.stderr)
    if enabled and not recaptcha_enforcement_enabled():
        print("ERROR: reCAPTCHA is enabled but real site/secret keys are not configured. Login, registration, and forgot-password verification will fail until .env is fixed.", file=sys.stderr)

def build_login_response(user, message="Login successful"):
    full_name = user.get("full_name") or user["username"]
    account_created = user.get("account_created") or user["_id"].generation_time
    last_login = utc_now()
    role = user.get("role")
    location = location_details(user.get("location_id", "ALL"))

    update_fields = {
        "account_created": account_created,
        "full_name": full_name,
        "last_login": last_login,
        **location_fields(location["location_id"])
    }
    users_collection.update_one({"_id": user["_id"]}, {"$set": update_fields})

    token_payload = {
        "sub": user["username"],
        "email": user["email"],
        "role": role,
        "location_id": location["location_id"],
        "warehouse_id": location.get("warehouse_id"),
        "warehouse_name": location.get("warehouse_name"),
        "location": location.get("location"),
        "state": location.get("state")
    }
    admin_session_expires_at = None
    if role == "Admin":
        admin_session_expires_at = utc_now() + timedelta(seconds=ADMIN_SESSION_SECONDS)
        token_payload["admin_session_expires_at"] = admin_session_expires_at.isoformat()

    access_token = create_access_token(token_payload)
    response = {
        "message": message,
        "username": user["username"],
        "full_name": full_name,
        "email": user["email"],
        "phone": user.get("phone"),
        "role": role,
        **location,
        "account_created": iso_datetime(account_created),
        "last_login": last_login.isoformat(),
        "access_token": access_token,
        "token_type": "bearer"
    }
    if admin_session_expires_at:
        response["admin_session_expires_at"] = admin_session_expires_at.isoformat()
    return response

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)

    if not payload or "role" not in payload or "sub" not in payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    if payload.get("role") == "Admin":
        expires_at = as_utc_datetime(payload.get("admin_session_expires_at"))
        if not expires_at or expires_at < utc_now():
            raise HTTPException(
                status_code=401,
                detail="Admin session expired. Please sign in again."
            )

    return payload


def get_current_role(current_user: dict = Depends(get_current_user)):
    return current_user["role"]


def normalize_warehouse_name(value):
    name = validate_required(value, "Warehouse Name")
    return re.sub(r"\s+", " ", name).strip()


def warehouse_key(value):
    return normalize_warehouse_name(value).casefold()

def generate_unique_username(full_name, email):
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", full_name.strip().lower().replace(" ", "."))
    if not base:
        base = re.sub(r"[^a-zA-Z0-9_.-]", "", email.split("@", 1)[0].lower()) or "user"
    username = base
    suffix = 1
    while users_collection.find_one({"username": username}):
        suffix += 1
        username = f"{base}{suffix}"
    return username


def generate_next_warehouse_id():
    used_ids = {
        item["warehouse_id"]
        for item in locations_collection.find(
            {"warehouse_id": {"$regex": r"^WH\d{3}$"}},
            {"warehouse_id": 1}
        )
        if item.get("warehouse_id")
    }
    for number in range(1, 1000):
        warehouse_id = f"WH{number:03d}"
        if warehouse_id not in used_ids:
            return warehouse_id
    raise HTTPException(status_code=400, detail="Warehouse ID limit reached.")


def get_or_create_warehouse(warehouse_name):
    name = normalize_warehouse_name(warehouse_name)
    key = name.casefold()
    existing = locations_collection.find_one({"warehouse_key": key})
    if existing:
        return location_details(existing.get("location_id") or existing.get("warehouse_id"))

    warehouse_id = generate_next_warehouse_id()
    document = {
        "location_id": warehouse_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": name,
        "warehouse_key": key,
        "location_name": name,
        "location": name,
        "state": "",
        "created_at": datetime.now(timezone.utc)
    }
    try:
        locations_collection.insert_one(document)
    except DuplicateKeyError:
        existing = locations_collection.find_one({"warehouse_key": key})
        if existing:
            return location_details(existing.get("location_id") or existing.get("warehouse_id"))
        raise
    return location_details(warehouse_id)


def location_details(location_id):
    if not location_id or location_id == "ALL":
        return {
            "location_id": "ALL",
            "warehouse_id": "",
            "warehouse_name": "All Warehouses",
            "location_name": "All Warehouses",
            "location": "All Warehouses",
            "state": ""
        }
    location = locations_collection.find_one({"$or": [{"location_id": location_id}, {"warehouse_id": location_id}]}) or {}
    warehouse_id = location.get("warehouse_id") or location_id
    warehouse_name = location.get("warehouse_name") or location.get("location_name") or location.get("location") or warehouse_id
    return {
        "location_id": location.get("location_id") or warehouse_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "location_name": warehouse_name,
        "location": warehouse_name,
        "state": location.get("state") or ""
    }


def location_fields(location_id):
    details = location_details(location_id)
    return {
        "location_id": details["location_id"],
        "warehouse_id": details.get("warehouse_id"),
        "warehouse_name": details.get("warehouse_name"),
        "location": details.get("location"),
        "state": details.get("state")
    }


def reset_users_and_static_warehouses_once():
    marker_key = "dynamic_warehouse_registration_reset_v2"
    if system_settings_collection.find_one({"key": marker_key}):
        return
    try:
        users_collection.delete_many({"role": {"$ne": "Admin"}})
        for admin in users_collection.find({"role": "Admin"}, {"_id": 1, "username": 1, "full_name": 1}):
            users_collection.update_one(
                {"_id": admin["_id"]},
                {
                    "$set": {
                        "full_name": admin.get("full_name") or admin.get("username") or "Admin",
                        "location_id": "ALL",
                        "warehouse_id": "",
                        "warehouse_name": "All Warehouses",
                        "location": "All Warehouses",
                        "state": ""
                    }
                }
            )

        locations_collection.delete_many({})
        unset_scope = {
            "$unset": {
                "location_id": "",
                "warehouse_id": "",
                "warehouse_name": "",
                "location_name": "",
                "location": "",
                "state": ""
            }
        }
        for collection in [
            products_collection,
            suppliers_collection,
            inventory_history_collection,
            purchases_collection,
            sales_collection,
            restock_queue_collection,
            customers_collection,
            stock_movements_collection,
            sales_items_collection,
            purchase_items_collection,
            low_stock_alerts_collection,
            notifications_collection,
            activity_logs_collection,
            returns_collection,
            damaged_stock_collection
        ]:
            collection.update_many({}, unset_scope)

        system_settings_collection.update_one(
            {"key": marker_key},
            {"$set": {"key": marker_key, "completed_at": datetime.now(timezone.utc)}},
            upsert=True
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to reset static warehouse data: {exc}") from exc

def user_location_id(current_user):
    """Resolve location from MongoDB so an Admin assignment takes effect immediately."""
    if current_user["role"] == "Admin":
        return None
    user = users_collection.find_one(
        {"username": current_user["sub"]},
        {"location_id": 1}
    )
    location_id = (user or {}).get("location_id")
    if not location_id or location_id == "ALL":
        raise HTTPException(
            status_code=403,
            detail="Your account is not assigned to a store or warehouse."
        )
    return location_id


def location_query(current_user):
    location_id = user_location_id(current_user)
    return {} if location_id is None else {"location_id": location_id}


def scoped_product_query(product_id, current_user):
    return {"product_id": product_id, **location_query(current_user)}


def create_inventory_history(
    product,
    movement_type,
    movement_quantity,
    previous_stock,
    current_stock,
    current_user,
    note=None
):
    movement_id = f"MOV-{uuid4().hex.upper()}"
    inventory_history_collection.insert_one(
        inventory_history_document(
            movement_id=movement_id,
            product_id=product["product_id"],
            product_name=product["product_name"],
            movement_type=movement_type,
            quantity=movement_quantity,
            previous_stock=previous_stock,
            current_stock=current_stock,
            performed_by=current_user["sub"],
            role=current_user["role"],
            created_at=datetime.now(timezone.utc),
            note=note,
            **location_fields(product.get("location_id", "ALL"))
        )
    )
    return movement_id


def get_product_metadata(product):
    category = None
    supplier = None

    if product.get("category_id"):
        category = categories_collection.find_one(
            {"category_id": product["category_id"]}
        )
    if product.get("supplier_id"):
        supplier = suppliers_collection.find_one(
            {"supplier_id": product["supplier_id"]}
        )

    return {
        "category_id": product.get("category_id"),
        "category": category.get("category_name") if category else None,
        "category_description": (
            category.get("description") if category else None
        ),
        "supplier_id": product.get("supplier_id"),
        "supplier": supplier.get("supplier_name") if supplier else None,
        "supplier_email": supplier.get("email") if supplier else None,
        "supplier_phone": supplier.get("phone") if supplier else None,
        "supplier_address": supplier.get("address") if supplier else None
    }


def enrich_product(product):
    item = serialize_document(product)
    item.update(get_product_metadata(item))
    return item




def normalize_optional_form_value(value):
    if value in (None, "", "null", "undefined"):
        return None
    return value


def coerce_product_payload(data, include_product_id=True):
    payload = {}
    if include_product_id:
        payload["product_id"] = validate_required(
            normalize_optional_form_value(data.get("product_id")),
            "Product ID"
        )
    payload["product_name"] = validate_required(
        normalize_optional_form_value(data.get("product_name")),
        "Product name"
    )
    try:
        payload["quantity"] = int(data.get("quantity", 0))
        payload["price"] = float(data.get("price", 0))
        payload["reorder_level"] = int(data.get("reorder_level", 35))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Product numeric fields are invalid.") from exc

    unit_cost = normalize_optional_form_value(data.get("unit_cost"))
    if unit_cost is not None:
        try:
            payload["unit_cost"] = float(unit_cost)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unit cost is invalid.") from exc
    else:
        payload["unit_cost"] = None

    payload["category_id"] = normalize_optional_form_value(data.get("category_id"))
    payload["supplier_id"] = normalize_optional_form_value(data.get("supplier_id"))
    payload["barcode_value"] = normalize_optional_form_value(data.get("barcode_value"))
    payload["qr_code_value"] = normalize_optional_form_value(data.get("qr_code_value"))
    return payload




async def read_product_request(request: Request, include_product_id=True):
    content_type = request.headers.get("content-type", "")
    upload = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        data = dict(form)
        upload = data.pop("product_image", None)
    else:
        try:
            data = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid product request body.") from exc

    return coerce_product_payload(data, include_product_id=include_product_id), upload


def save_product_image(upload, product_id):
    if not upload or not getattr(upload, "filename", None):
        return None

    original_name = FilePath(upload.filename).name
    extension = FilePath(original_name).suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    allowed_content_types = {"image/jpeg", "image/png", "image/webp"}

    if extension not in allowed_extensions or getattr(upload, "content_type", None) not in allowed_content_types:
        raise HTTPException(status_code=400, detail="Only JPG, JPEG, PNG, and WEBP images are allowed.")

    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)
    if size > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Product image must be 2MB or smaller.")

    safe_product_id = re.sub(r"[^a-zA-Z0-9_-]", "_", product_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    filename = f"{safe_product_id}_{timestamp}{extension}"
    destination = PRODUCT_UPLOAD_DIR / filename

    with destination.open("wb") as output:
        output.write(upload.file.read())

    return f"/uploads/products/{filename}"

def low_stock_filter():
    return {
        "$expr": {
            "$lte": [
                "$quantity",
                {"$ifNull": ["$reorder_level", 35]}
            ]
        }
    }


def build_stock_alert(product):
    metadata = get_product_metadata(product)
    reorder_level = product.get("reorder_level", 35)
    current_stock = product.get("quantity", 0)
    recommended_order_quantity = max(
        reorder_level * 2 - current_stock,
        reorder_level
    )
    severity = "critical" if current_stock == 0 else "warning"

    return {
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        "current_stock": current_stock,
        "reorder_level": reorder_level,
        "shortage_quantity": max(reorder_level - current_stock, 0),
        "recommended_order_quantity": recommended_order_quantity,
        "severity": severity,
        "message": (
            f"{product['product_name']} is out of stock"
            if severity == "critical"
            else (
                f"{product['product_name']} is below reorder level "
                f"({current_stock}/{reorder_level})"
            )
        ),
        "category_id": product.get("category_id"),
        "category_name": metadata.get("category"),
        "supplier_id": product.get("supplier_id"),
        "supplier_name": metadata.get("supplier"),
        "supplier_email": metadata.get("supplier_email"),
        "supplier_phone": metadata.get("supplier_phone")
    }


def not_found(detail):
    raise HTTPException(
        status_code=404,
        detail=detail
    )


def conflict(detail):
    raise HTTPException(
        status_code=409,
        detail=detail
    )


def validate_required(value, field_name):
    if not value or not value.strip():
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is required"
        )

    return value.strip()


def validate_email(value):
    email = validate_required(value, "Email")

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(
            status_code=400,
            detail="Invalid email format"
        )

    return email.strip().lower()


def validate_phone(value):
    phone = validate_required(value, "Phone number")
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7 or len(digits) > 15:
        raise HTTPException(
            status_code=400,
            detail="Phone number must contain 7 to 15 digits"
        )
    return phone



def clean_env_setting(name, default=""):
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip().strip('"').strip("'")


def is_recaptcha_enabled():
    return recaptcha_requested_enabled()


def verify_recaptcha_or_skip(token, action="request"):
    if not is_recaptcha_enabled():
        return {"success": True, "skipped": True}

    if not has_real_recaptcha_site_key() or not has_real_recaptcha_secret_key():
        print(f"[recaptcha] configuration missing during {action}: site_key={has_real_recaptcha_site_key()} secret_key={has_real_recaptcha_secret_key()}", file=sys.stderr)
        raise HTTPException(
            status_code=503,
            detail="reCAPTCHA is not configured on the backend."
        )

    secret = clean_env_setting("RECAPTCHA_SECRET_KEY")

    token = validate_required(token or "", "reCAPTCHA verification")
    data = urlencode({"secret": secret, "response": token}).encode("utf-8")

    try:
        with urlopen("https://www.google.com/recaptcha/api/siteverify", data=data, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"[recaptcha] verification failed during {action}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=503,
            detail="reCAPTCHA verification service is unavailable. Please try again."
        ) from exc

    if not payload.get("success"):
        print(f"[recaptcha] rejected during {action}: {payload.get('error-codes', [])}", file=sys.stderr)
        raise HTTPException(
            status_code=400,
            detail="reCAPTCHA verification failed. Please try again."
        )

    return payload

def verify_google_credential(credential):
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Google Sign-In is not configured on the backend"
        )

    try:
        query = urlencode({"id_token": credential})
        with urlopen(
            f"https://oauth2.googleapis.com/tokeninfo?{query}",
            timeout=10
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Google credential could not be verified"
        ) from exc

    if payload.get("aud") != client_id:
        raise HTTPException(status_code=401, detail="Google credential audience is invalid")
    if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Google credential issuer is invalid")
    if payload.get("email_verified") not in {True, "true"}:
        raise HTTPException(status_code=401, detail="Google email is not verified")

    return payload


def reset_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def as_utc_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def build_password_reset_email_html(reset_link: str) -> str:
    safe_reset_link = xml_escape(reset_link)
    return f"""
    <!doctype html>
    <html>
      <body style="margin:0;background:#f4f7fb;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7fb;padding:32px 12px;">
          <tr>
            <td align="center">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:16px;box-shadow:0 16px 40px rgba(15,23,42,0.10);overflow:hidden;">
                <tr>
                  <td style="background:#1e3a5f;padding:28px 32px;color:#ffffff;">
                    <h1 style="margin:0;font-size:24px;line-height:1.25;font-weight:800;">Smart Inventory</h1>
                    <p style="margin:6px 0 0;color:#dbeafe;font-size:14px;">Sales Monitoring System</p>
                  </td>
                </tr>
                <tr>
                  <td style="padding:32px;">
                    <h2 style="margin:0 0 12px;font-size:22px;color:#111827;">Reset your password</h2>
                    <p style="margin:0 0 22px;font-size:15px;line-height:1.65;color:#4b5563;">
                      We received a request to reset the password for your Smart Inventory account.
                      Use the button below to create a new password.
                    </p>
                    <p style="margin:0 0 26px;text-align:center;">
                      <a href="{safe_reset_link}" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:700;padding:13px 24px;border-radius:10px;font-size:15px;">
                        Reset Password
                      </a>
                    </p>
                    <p style="margin:0 0 8px;font-size:13px;line-height:1.55;color:#64748b;">
                      If the button does not work, copy and paste this link into your browser:
                    </p>
                    <p style="margin:0 0 22px;font-size:13px;line-height:1.55;word-break:break-all;">
                      <a href="{safe_reset_link}" style="color:#2563eb;">{safe_reset_link}</a>
                    </p>
                    <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#4b5563;">
                      This reset link expires in <strong>30 minutes</strong> and can be used only once.
                    </p>
                    <p style="margin:0;font-size:14px;line-height:1.6;color:#4b5563;">
                      If you did not request this password reset, you can safely ignore this email.
                    </p>
                  </td>
                </tr>
                <tr>
                  <td style="padding:18px 32px;background:#f8fafc;border-top:1px solid #e5e7eb;color:#64748b;font-size:12px;line-height:1.5;">
                    Smart Inventory Sales Monitoring System
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """


def send_password_reset_email(recipient, reset_link):
    send_brevo_email(
        recipient_email=recipient,
        subject="Reset Your Smart Inventory Password",
        html_content=build_password_reset_email_html(reset_link)
    )

def parse_report_date(value, field_name):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must use YYYY-MM-DD format"
        ) from exc



def build_date_query(start_date=None, end_date=None, field_name="created_at"):
    date_filter = {}
    parsed_start = parse_report_date(start_date, "start_date")
    parsed_end = parse_report_date(end_date, "end_date")

    if parsed_start:
        date_filter["$gte"] = parsed_start

    if parsed_end:
        date_filter["$lt"] = parsed_end + timedelta(days=1)

    return {field_name: date_filter} if date_filter else {}



def parse_ist_date(value, field_name):
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
        return parsed.replace(tzinfo=IST)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must use YYYY-MM-DD format") from exc


def resolve_dashboard_date_range(range_name="last_7_days", start_date=None, end_date=None):
    normalized = str(range_name or "last_7_days").strip().lower().replace("-", "_")
    aliases = {"last7": "last_7_days", "last30": "last_30_days", "month": "this_month"}
    normalized = aliases.get(normalized, normalized)
    now_ist = datetime.now(IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    if normalized == "today":
        start_ist = today_start
        end_ist = today_start + timedelta(days=1)
    elif normalized == "last_30_days":
        start_ist = today_start - timedelta(days=29)
        end_ist = today_start + timedelta(days=1)
    elif normalized == "this_month":
        start_ist = today_start.replace(day=1)
        end_ist = start_ist.replace(year=start_ist.year + 1, month=1, day=1) if start_ist.month == 12 else start_ist.replace(month=start_ist.month + 1, day=1)
    elif normalized == "custom":
        start_ist = parse_ist_date(start_date, "start_date")
        end_day_ist = parse_ist_date(end_date, "end_date")
        if not start_ist or not end_day_ist:
            raise HTTPException(status_code=400, detail="Custom range requires start_date and end_date")
        if start_ist > end_day_ist:
            raise HTTPException(status_code=400, detail="start_date cannot be after end_date")
        end_ist = end_day_ist + timedelta(days=1)
    else:
        normalized = "last_7_days"
        start_ist = today_start - timedelta(days=6)
        end_ist = today_start + timedelta(days=1)

    duration = end_ist - start_ist
    previous_start_ist = start_ist - duration
    return {
        "range": normalized,
        "start_ist": start_ist,
        "end_ist": end_ist,
        "start_utc": start_ist.astimezone(timezone.utc),
        "end_utc": end_ist.astimezone(timezone.utc),
        "previous_start_utc": previous_start_ist.astimezone(timezone.utc),
    }


def dashboard_period_match(range_info, field_name="created_at", include_previous=False):
    start_value = range_info["previous_start_utc"] if include_previous else range_info["start_utc"]
    return {field_name: {"$gte": start_value, "$lt": range_info["end_utc"]}}


def aggregate_period_total(collection, base_query, range_info, amount_field, field_name="created_at"):
    query = {**base_query, **dashboard_period_match(range_info, field_name)}
    pipeline = [
        {"$match": query},
        {"$group": {"_id": None, "total": {"$sum": {"$toDouble": {"$ifNull": [f"${amount_field}", 0]}}}, "count": {"$sum": 1}}}
    ]
    rows = list(collection.aggregate(pipeline))
    row = rows[0] if rows else {}
    return float(row.get("total", 0) or 0), int(row.get("count", 0) or 0)
def normalize_supplier_id(value):
    if not value:
        return None
    value = value.strip().upper()
    match = re.fullmatch(r"SUP(\d{1,3})", value)
    if not match:
        return None
    number = int(match.group(1))
    if number < 1 or number > 990:
        return None
    return f"SUP{number:03d}"


def generate_next_supplier_id():
    used_ids = {
        supplier["supplier_id"]
        for supplier in suppliers_collection.find(
            {"supplier_id": {"$regex": r"^SUP\d{3}$"}},
            {"supplier_id": 1}
        )
    }
    for number in range(1, 991):
        supplier_id = f"SUP{number:03d}"
        if supplier_id not in used_ids:
            return supplier_id
    raise HTTPException(status_code=400, detail="Supplier ID limit reached.")


def ensure_supplier_ids():
    try:
        suppliers = list(suppliers_collection.find({}, {"_id": 1, "supplier_id": 1}).sort("supplier_name", 1))
        used_ids = set()
        updates = []
        next_number = 1

        for supplier in suppliers:
            current_id = normalize_supplier_id(supplier.get("supplier_id"))
            if current_id and current_id not in used_ids:
                if current_id != supplier.get("supplier_id"):
                    updates.append(UpdateOne({"_id": supplier["_id"]}, {"$set": {"supplier_id": current_id}}))
                used_ids.add(current_id)
                continue

            while next_number <= 990 and f"SUP{next_number:03d}" in used_ids:
                next_number += 1

            if next_number > 990:
                break

            new_id = f"SUP{next_number:03d}"
            used_ids.add(new_id)
            updates.append(UpdateOne({"_id": supplier["_id"]}, {"$set": {"supplier_id": new_id}}))

        if updates:
            suppliers_collection.bulk_write(updates, ordered=False)
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to verify supplier IDs: {exc}") from exc


def sync_location_fields_for_existing_records():
    try:
        locations = {
            item["location_id"]: location_fields(item["location_id"])
            for item in locations_collection.find({}, {"location_id": 1})
            if item.get("location_id")
        }
        locations["ALL"] = location_fields("ALL")

        scoped_collections = [
            (users_collection, True),
            (products_collection, False),
            (suppliers_collection, False),
            (inventory_history_collection, False),
            (purchases_collection, False),
            (sales_collection, False),
            (restock_queue_collection, False),
            (customers_collection, False),
            (stock_movements_collection, False),
            (sales_items_collection, False),
            (purchase_items_collection, False),
            (low_stock_alerts_collection, False),
            (notifications_collection, False),
            (activity_logs_collection, False),
            (returns_collection, False),
            (damaged_stock_collection, False)
        ]
        for collection, is_user_collection in scoped_collections:
            updates = []
            for document in collection.find({}, {"_id": 1, "role": 1, "location_id": 1, "warehouse_id": 1, "warehouse_name": 1, "location": 1, "state": 1}):
                location_id = document.get("location_id")
                if not location_id:
                    if is_user_collection and document.get("role") == "Admin":
                        location_id = "ALL"
                    else:
                        continue
                details = locations.get(location_id) or location_fields(location_id)
                set_values = {
                    "location_id": details["location_id"],
                    "warehouse_id": details.get("warehouse_id"),
                    "warehouse_name": details.get("warehouse_name"),
                    "location": details.get("location"),
                    "state": details.get("state")
                }
                if any(document.get(key) != value for key, value in set_values.items()):
                    updates.append(UpdateOne({"_id": document["_id"]}, {"$set": set_values}))
            if updates:
                collection.bulk_write(updates, ordered=False)
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to sync warehouse mapping: {exc}") from exc


def first_registered_warehouse_id():
    user = users_collection.find_one(
        {
            "role": {"$in": ["Manager", "Staff"]},
            "location_id": {"$nin": [None, "", "ALL"]}
        },
        {"location_id": 1},
        sort=[("account_created", 1), ("created_at", 1)]
    )
    if user and user.get("location_id"):
        return user["location_id"]

    location = locations_collection.find_one(
        {"location_id": {"$nin": [None, "", "ALL"]}},
        {"location_id": 1},
        sort=[("created_at", 1), ("warehouse_id", 1)]
    )
    return location.get("location_id") if location else None


def assign_unscoped_records_to_first_warehouse():
    try:
        location_id = first_registered_warehouse_id()
        if not location_id:
            return

        fields = location_fields(location_id)
        unscoped_query = {
            "$or": [
                {"location_id": {"$exists": False}},
                {"location_id": None},
                {"location_id": ""},
                {"location_id": "ALL"}
            ]
        }
        scoped_collections = [
            products_collection,
            suppliers_collection,
            inventory_history_collection,
            purchases_collection,
            sales_collection,
            restock_queue_collection,
            customers_collection,
            stock_movements_collection,
            sales_items_collection,
            purchase_items_collection,
            low_stock_alerts_collection,
            notifications_collection,
            activity_logs_collection,
            returns_collection,
            damaged_stock_collection
        ]
        for collection in scoped_collections:
            collection.update_many(unscoped_query, {"$set": fields})
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to assign warehouse data: {exc}") from exc
def sync_warehouses_collection():
    """Mirror dynamic locations into the warehouses collection without hardcoded warehouses."""
    operations = []
    for location in locations_collection.find({}, {"_id": 0}):
        warehouse_id = location.get("warehouse_id") or location.get("location_id")
        warehouse_name = location.get("warehouse_name") or location.get("location_name") or location.get("location") or warehouse_id
        if not warehouse_id or warehouse_id == "ALL":
            continue
        operations.append(UpdateOne(
            {"warehouse_id": warehouse_id},
            {"$set": {
                "warehouse_id": warehouse_id,
                "location_id": location.get("location_id") or warehouse_id,
                "warehouse_name": warehouse_name,
                "location_name": warehouse_name,
                "location": location.get("location") or warehouse_name,
                "state": location.get("state") or "",
                "created_at": location.get("created_at") or datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "source": "dynamic_registration"
            }},
            upsert=True
        ))
    if operations:
        warehouses_collection.bulk_write(operations, ordered=False)


def seed_module_collections(force=False):
    """Seed realistic module collections from existing products, suppliers, sales, and purchases."""
    marker_key = "module_collections_seed_v1"
    if not force and system_settings_collection.find_one({"key": marker_key}):
        sync_warehouses_collection()
        return

    try:
        sync_warehouses_collection()
        now = utc_now()
        warehouses = list(warehouses_collection.find({}, {"_id": 0}).sort("warehouse_id", 1))
        if not warehouses:
            return

        users = list(users_collection.find({}, {"_id": 0, "username": 1, "full_name": 1, "role": 1, "location_id": 1, "warehouse_id": 1}))
        user_by_location = {}
        for user in users:
            user_by_location.setdefault(user.get("location_id") or "ALL", user)

        product_docs = list(products_collection.find({}, {"_id": 0}).sort("product_id", 1).limit(80))
        supplier_docs = list(suppliers_collection.find({}, {"_id": 0}).sort("supplier_id", 1).limit(40))
        sales_docs = list(sales_collection.find({}, {"_id": 0}).sort("created_at", -1).limit(80))
        purchase_docs = list(purchases_collection.find({}, {"_id": 0}).sort("created_at", -1).limit(80))
        if not product_docs:
            return

        product_by_id = {item.get("product_id"): item for item in product_docs}
        supplier_by_id = {item.get("supplier_id"): item for item in supplier_docs}

        customers = []
        customer_names = [
            "Anaya Retail", "Fresh Basket", "Metro Foods", "Daily Mart", "Blue Cart",
            "Urban Pantry", "Green Leaf Cafe", "Quick Basket", "City Grocers", "Prime Kitchen"
        ]
        for index, name in enumerate(customer_names, start=1):
            warehouse = warehouses[(index - 1) % len(warehouses)]
            customers.append({
                "customer_id": f"CUS{index:03d}",
                "customer_name": name,
                "email": f"customer{index:03d}@example.com",
                "phone": f"+91 90000 {index:05d}",
                "customer_type": "Retail" if index % 2 else "Wholesale",
                "created_at": now - timedelta(days=index * 3),
                **location_fields(warehouse.get("location_id") or warehouse.get("warehouse_id"))
            })
        if customers:
            customers_collection.bulk_write([
                UpdateOne({"customer_id": item["customer_id"]}, {"$set": item}, upsert=True)
                for item in customers
            ], ordered=False)

        movement_ops = []
        alert_ops = []
        notification_ops = []
        damage_ops = []
        activity_ops = []
        for index, product in enumerate(product_docs[:60], start=1):
            location_id = product.get("location_id") or warehouses[(index - 1) % len(warehouses)].get("location_id")
            location = location_fields(location_id)
            quantity = int(product.get("quantity") or 0)
            reorder_level = int(product.get("reorder_level") or 20)
            movement_type = "Stock In" if index % 2 else "Stock Out"
            movement_qty = max(1, min(25, reorder_level // 2 or 5))
            movement = {
                "movement_id": f"STM{index:04d}",
                "product_id": product.get("product_id"),
                "product_name": product.get("product_name"),
                "movement_type": movement_type,
                "quantity": movement_qty,
                "previous_stock": max(0, quantity - movement_qty) if movement_type == "Stock In" else quantity + movement_qty,
                "current_stock": quantity,
                "reference_type": "Seeded Inventory Movement",
                "reference_id": product.get("product_id"),
                "performed_by": (user_by_location.get(location_id) or {}).get("username") or "system",
                "created_at": now - timedelta(days=index % 14, hours=index),
                **location
            }
            movement_ops.append(UpdateOne({"movement_id": movement["movement_id"]}, {"$set": movement}, upsert=True))

            activity = {
                "activity_id": f"ACT{index:04d}",
                "user_id": movement["performed_by"],
                "action": "Updated stock" if index % 2 else "Reviewed low stock",
                "module": "Inventory",
                "description": f"{movement['movement_type']} recorded for {product.get('product_name')}",
                "created_at": movement["created_at"],
                **location
            }
            activity_ops.append(UpdateOne({"activity_id": activity["activity_id"]}, {"$set": activity}, upsert=True))

            if quantity <= reorder_level:
                severity = "Critical" if quantity == 0 or quantity <= max(1, reorder_level // 2) else "Low"
                alert = {
                    "alert_id": f"LSA{index:04d}",
                    "product_id": product.get("product_id"),
                    "product_name": product.get("product_name"),
                    "current_stock": quantity,
                    "reorder_level": reorder_level,
                    "severity": severity,
                    "status": "Open",
                    "message": f"{product.get('product_name')} is below reorder level.",
                    "created_at": now - timedelta(days=index % 7),
                    **location
                }
                alert_ops.append(UpdateOne({"alert_id": alert["alert_id"]}, {"$set": alert}, upsert=True))
                notification = {
                    "notification_id": f"NOT{index:04d}",
                    "user_id": "ALL",
                    "role": "Manager",
                    "type": "Low Stock",
                    "title": f"Low stock: {product.get('product_name')}",
                    "description": alert["message"],
                    "is_read": False,
                    "created_at": alert["created_at"],
                    **location
                }
                notification_ops.append(UpdateOne({"notification_id": notification["notification_id"]}, {"$set": notification}, upsert=True))

            if index <= 12:
                damaged = {
                    "damage_id": f"DMG{index:04d}",
                    "product_id": product.get("product_id"),
                    "product_name": product.get("product_name"),
                    "quantity": max(1, index % 5),
                    "reason": ["Packaging damage", "Expired batch", "Transit damage", "Quality issue"][index % 4],
                    "status": "Reported" if index % 3 else "Resolved",
                    "reported_by": movement["performed_by"],
                    "reported_at": now - timedelta(days=index + 1),
                    **location
                }
                damage_ops.append(UpdateOne({"damage_id": damaged["damage_id"]}, {"$set": damaged}, upsert=True))

        if movement_ops:
            stock_movements_collection.bulk_write(movement_ops, ordered=False)
        if alert_ops:
            low_stock_alerts_collection.bulk_write(alert_ops, ordered=False)
        if notification_ops:
            notifications_collection.bulk_write(notification_ops, ordered=False)
        if damage_ops:
            damaged_stock_collection.bulk_write(damage_ops, ordered=False)
        if activity_ops:
            activity_logs_collection.bulk_write(activity_ops, ordered=False)

        sales_item_ops = []
        return_ops = []
        for index, sale in enumerate(sales_docs, start=1):
            product = product_by_id.get(sale.get("product_id")) or sale
            customer = customers[(index - 1) % len(customers)] if customers else {}
            item = {
                "sales_item_id": f"SIT{index:04d}",
                "sale_id": sale.get("sale_id") or f"SALE-SEED-{index:04d}",
                "product_id": sale.get("product_id") or product.get("product_id"),
                "product_name": sale.get("product_name") or product.get("product_name"),
                "category_id": sale.get("category_id") or product.get("category_id"),
                "customer_id": customer.get("customer_id"),
                "quantity": int(sale.get("quantity") or 1),
                "unit_price": float(sale.get("unit_price") or product.get("price") or 0),
                "discount": 0,
                "tax": 0,
                "line_total": float(sale.get("total_amount") or 0),
                "created_at": sale.get("created_at") or now - timedelta(days=index % 10),
                **location_fields(sale.get("location_id") or product.get("location_id") or customer.get("location_id"))
            }
            sales_item_ops.append(UpdateOne({"sales_item_id": item["sales_item_id"]}, {"$set": item}, upsert=True))
            if index <= 10:
                returned = {
                    "return_id": f"RET{index:04d}",
                    "sale_id": item["sale_id"],
                    "product_id": item["product_id"],
                    "product_name": item["product_name"],
                    "customer_id": item.get("customer_id"),
                    "quantity": 1,
                    "reason": "Customer return" if index % 2 else "Damaged on delivery",
                    "status": "Approved" if index % 2 else "Pending",
                    "refund_amount": item["unit_price"],
                    "created_at": item["created_at"] + timedelta(hours=6),
                    **location_fields(item.get("location_id"))
                }
                return_ops.append(UpdateOne({"return_id": returned["return_id"]}, {"$set": returned}, upsert=True))
        if sales_item_ops:
            sales_items_collection.bulk_write(sales_item_ops, ordered=False)
        if return_ops:
            returns_collection.bulk_write(return_ops, ordered=False)

        purchase_item_ops = []
        for index, purchase in enumerate(purchase_docs, start=1):
            product = product_by_id.get(purchase.get("product_id")) or purchase
            supplier = supplier_by_id.get(purchase.get("supplier_id")) or {}
            item = {
                "purchase_item_id": f"PIT{index:04d}",
                "purchase_id": purchase.get("purchase_id") or f"PUR-SEED-{index:04d}",
                "product_id": purchase.get("product_id") or product.get("product_id"),
                "product_name": purchase.get("product_name") or product.get("product_name"),
                "supplier_id": purchase.get("supplier_id") or supplier.get("supplier_id"),
                "supplier_name": supplier.get("supplier_name"),
                "quantity": int(purchase.get("quantity") or 1),
                "unit_cost": float(purchase.get("unit_cost") or product.get("unit_cost") or 0),
                "tax": 0,
                "line_total": float(purchase.get("total_cost") or 0),
                "status": purchase.get("status") or "Pending",
                "created_at": purchase.get("created_at") or now - timedelta(days=index % 10),
                **location_fields(purchase.get("location_id") or product.get("location_id") or supplier.get("location_id"))
            }
            purchase_item_ops.append(UpdateOne({"purchase_item_id": item["purchase_item_id"]}, {"$set": item}, upsert=True))
        if purchase_item_ops:
            purchase_items_collection.bulk_write(purchase_item_ops, ordered=False)

        system_settings_collection.update_one(
            {"key": marker_key},
            {"$set": {"key": marker_key, "completed_at": now}},
            upsert=True
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to seed module collections: {exc}") from exc

MODULE_COLLECTIONS = {
    "warehouses": {"collection": warehouses_collection, "date_field": "created_at", "scoped": False, "search": ["warehouse_id", "warehouse_name", "location"]},
    "customers": {"collection": customers_collection, "date_field": "created_at", "scoped": True, "search": ["customer_id", "customer_name", "email", "phone"]},
    "stock_movements": {"collection": stock_movements_collection, "date_field": "created_at", "scoped": True, "search": ["movement_id", "product_id", "product_name", "movement_type"]},
    "sales_items": {"collection": sales_items_collection, "date_field": "created_at", "scoped": True, "search": ["sales_item_id", "sale_id", "product_id", "product_name", "customer_id"]},
    "purchase_items": {"collection": purchase_items_collection, "date_field": "created_at", "scoped": True, "search": ["purchase_item_id", "purchase_id", "product_id", "product_name", "supplier_id"]},
    "low_stock_alerts": {"collection": low_stock_alerts_collection, "date_field": "created_at", "scoped": True, "search": ["alert_id", "product_id", "product_name", "severity", "status"]},
    "notifications": {"collection": notifications_collection, "date_field": "created_at", "scoped": True, "search": ["notification_id", "title", "description", "type", "role"]},
    "activity_logs": {"collection": activity_logs_collection, "date_field": "created_at", "scoped": True, "search": ["activity_id", "user_id", "action", "module", "description"]},
    "returns": {"collection": returns_collection, "date_field": "created_at", "scoped": True, "search": ["return_id", "sale_id", "product_id", "product_name", "customer_id", "status"]},
    "damaged_stock": {"collection": damaged_stock_collection, "date_field": "reported_at", "scoped": True, "search": ["damage_id", "product_id", "product_name", "reason", "status"]}
}


@app.get("/module-data/collections", tags=["Reports"])
def list_module_collections(current_user: dict = Depends(get_current_user)):
    return {"collections": sorted(MODULE_COLLECTIONS.keys())}


@app.get("/module-data/summary", tags=["Reports"])
def module_data_summary(current_user: dict = Depends(get_current_user)):
    try:
        scoped_query = location_query(current_user)
        summary = {}
        for name, config in MODULE_COLLECTIONS.items():
            query = dict(scoped_query) if config.get("scoped") else {}
            summary[name] = config["collection"].count_documents(query)
        return {"summary": summary}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to load module summary: {exc}") from exc


@app.get("/module-data/{collection_name}", tags=["Reports"])
def get_module_collection_data(
    collection_name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    config = MODULE_COLLECTIONS.get(collection_name)
    if not config:
        raise HTTPException(status_code=404, detail="Collection is not available.")

    try:
        query = dict(location_query(current_user)) if config.get("scoped") else {}
        if status:
            query["status"] = status
        if search:
            query["$or"] = [
                {field: {"$regex": search, "$options": "i"}}
                for field in config.get("search", [])
            ]
        query.update(build_date_query(start_date, end_date, config.get("date_field", "created_at")))

        skip = (page - 1) * limit
        collection = config["collection"]
        total = collection.count_documents(query)
        items = collection.find(query).sort(config.get("date_field", "created_at"), -1).skip(skip).limit(limit)
        return {
            "items": serialize_documents(items),
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if total else 0
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to load {collection_name}: {exc}") from exc

def build_sales_report(
    group_id,
    sort_spec,
    start_date=None,
    end_date=None,
    product_id=None
):
    query = {}

    if product_id:
        query["product_id"] = product_id

    date_filter = {}
    parsed_start = parse_report_date(start_date, "start_date")
    parsed_end = parse_report_date(end_date, "end_date")

    if parsed_start:
        date_filter["$gte"] = parsed_start

    if parsed_end:
        date_filter["$lt"] = parsed_end + timedelta(days=1)

    if date_filter:
        query["created_at"] = date_filter

    pipeline = []
    if query:
        pipeline.append({"$match": query})

    pipeline.extend([
        {
            "$group": {
                "_id": group_id,
                "sales_count": {"$sum": 1},
                "units_sold": {"$sum": "$quantity"},
                "revenue": {"$sum": "$total_amount"},
                "cost": {"$sum": {"$ifNull": ["$cost_amount", 0]}},
                "profit": {"$sum": {"$ifNull": ["$profit", 0]}}
            }
        },
        {"$sort": sort_spec}
    ])

    report_items = []
    totals = {
        "sales_count": 0,
        "units_sold": 0,
        "revenue": 0,
        "cost": 0,
        "profit": 0
    }

    for item in sales_collection.aggregate(pipeline):
        item_id = item.pop("_id")
        item.update(item_id if isinstance(item_id, dict) else {"period": item_id})
        item["average_order_value"] = (
            round(item["revenue"] / item["sales_count"], 2)
            if item["sales_count"]
            else 0
        )
        report_items.append(item)

        for key in totals:
            totals[key] += item[key]

    totals["average_order_value"] = (
        round(totals["revenue"] / totals["sales_count"], 2)
        if totals["sales_count"]
        else 0
    )

    return {
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "product_id": product_id
        },
        "summary": totals,
        "items": report_items
    }


def format_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else ""


def build_invoice_response(invoice_type, invoice):
    return {
        "invoice_number": invoice["invoice_number"],
        "invoice_type": invoice_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "invoice": invoice
    }


def add_record_id_aliases(items, alias_name):
    for item in items:
        if alias_name not in item:
            item[alias_name] = item["id"]

    return items


def generate_sale_id():
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_part = uuid4().hex[:8].upper()
    return f"SALE-{date_part}-{random_part}"


def generate_purchase_id():
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_part = uuid4().hex[:8].upper()
    return f"PURCHASE-{date_part}-{random_part}"


def escape_pdf_text(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_invoice(title, invoice):
    lines = [
        "Smart Inventory Sales Monitoring System",
        title,
        "",
        f"Invoice No: {invoice['invoice_number']}",
        f"Date: {invoice['date']}",
        "",
        f"Product ID: {invoice['product_id']}",
        f"Product Name: {invoice['product_name']}",
        f"Quantity: {invoice['quantity']}",
        f"Unit Price/Cost: {invoice['unit_value']}",
        f"Total: {invoice['total']}",
        "",
        f"Previous Stock: {invoice['previous_stock']}",
        f"Current Stock: {invoice['current_stock']}",
        f"Handled By: {invoice['handled_by']}",
        f"Role: {invoice['role']}"
    ]

    if invoice.get("party_label") and invoice.get("party_name"):
        lines.insert(6, f"{invoice['party_label']}: {invoice['party_name']}")

    if invoice.get("note"):
        lines.extend(["", f"Note: {invoice['note']}"])

    content_lines = [
        "BT",
        "/F1 18 Tf",
        "50 780 Td",
        f"({escape_pdf_text(lines[0])}) Tj",
        "/F1 14 Tf",
        "0 -28 Td",
        f"({escape_pdf_text(lines[1])}) Tj",
        "/F1 11 Tf"
    ]

    for line in lines[2:]:
        content_lines.extend([
            "0 -18 Td",
            f"({escape_pdf_text(line)}) Tj"
        ])

    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length " + str(len(content)).encode("ascii") +
            b" >>\nstream\n" + content + b"\nendstream"
        )
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")

    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )

    return bytes(pdf)


def get_record_by_id(collection, record_id, not_found_message):
    mongo_id = object_id(record_id)

    if not mongo_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid invoice record ID"
        )

    record = collection.find_one({
        "_id": mongo_id
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail=not_found_message
        )

    return record


def get_record_by_id_or_field(
    collection,
    record_id,
    field_name,
    not_found_message
):
    record = collection.find_one({
        field_name: record_id
    })

    if record:
        return record

    return get_record_by_id(
        collection,
        record_id,
        not_found_message
    )


def build_sales_invoice(sale):
    return {
        "invoice_number": sale.get("sale_id", f"SALE-{str(sale['_id'])}"),
        "date": format_datetime(sale.get("created_at")),
        "product_id": sale["product_id"],
        "product_name": sale["product_name"],
        "quantity": sale["quantity"],
        "unit_value": sale["unit_price"],
        "total": sale["total_amount"],
        "previous_stock": sale["previous_stock"],
        "current_stock": sale["current_stock"],
        "handled_by": sale["sold_by"],
        "role": sale["role"],
        "party_label": "Customer",
        "party_name": sale.get("customer_name"),
        "note": sale.get("note")
    }


def build_purchase_invoice(purchase):
    return {
        "invoice_number": purchase.get(
            "purchase_id",
            f"PURCHASE-{str(purchase['_id'])}"
        ),
        "date": format_datetime(purchase.get("created_at")),
        "product_id": purchase["product_id"],
        "product_name": purchase["product_name"],
        "quantity": purchase["quantity"],
        "unit_value": purchase["unit_cost"],
        "total": purchase["total_cost"],
        "previous_stock": purchase["previous_stock"],
        "current_stock": purchase["current_stock"],
        "handled_by": purchase["purchased_by"],
        "role": purchase["role"],
        "party_label": "Supplier ID",
        "party_name": purchase.get("supplier_id"),
        "note": purchase.get("note")
    }


def invoice_pdf_response(filename, title, invoice):
    pdf = build_pdf_invoice(title, invoice)

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


def report_filename(report_name, extension):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe_name = report_name.lower().replace(" ", "_").replace("/", "_")
    return f"{safe_name}_{timestamp}.{extension}"


def numeric_id_sort_key(row, preferred_fields=None):
    if not isinstance(row, dict):
        return (1, "", 0, str(row or ""))

    fields = preferred_fields or [
        "product_id",
        "supplier_id",
        "sale_id",
        "purchase_id",
        "inventory_id",
        "customer_id",
        "warehouse_id",
        "user_id",
        "category_id"
    ]
    selected_field = None
    selected_value = ""
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            selected_field = field
            selected_value = str(value).strip()
            break

    if not selected_value:
        return (1, "", 0, "")

    match = re.search(r"(\d+)", selected_value)
    number = int(match.group(1)) if match else 0
    prefix = re.sub(r"\d+", "", selected_value).upper()
    secondary = str(row.get("warehouse_id") or row.get("warehouse_name") or "")
    return (0, selected_field or "", prefix, number, selected_value.upper(), secondary.upper())


def sort_records_by_numeric_id(rows, preferred_fields=None):
    if not rows:
        return []
    return sorted(list(rows), key=lambda row: numeric_id_sort_key(row, preferred_fields))


def csv_export_value(value):
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(str(item) for item in value)
    if value is None:
        return ""
    return value


def csv_report_response(filename, sections):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    for title, rows in sections:
        writer.writerow([title])

        if rows:
            rows = sort_records_by_numeric_id(rows)
            fieldnames = list(rows[0].keys())
            writer.writerow(fieldnames)
            for row in rows:
                writer.writerow([csv_export_value(row.get(field)) for field in fieldnames])
        else:
            writer.writerow(["No records"])

        writer.writerow([])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


def supplier_csv_report_response(filename, report):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "supplier_id",
        "supplier_name",
        "email",
        "phone",
        "address",
        "warehouse_id",
        "warehouse_name",
        "location",
        "total_purchases",
        "status"
    ])

    for row in sort_records_by_numeric_id(report.get("items", []), ["supplier_id"]):
        writer.writerow([
            csv_export_value(row.get("supplier_id", "")),
            csv_export_value(row.get("supplier_name", "")),
            csv_export_value(row.get("email", "")),
            csv_export_value(row.get("phone", "")),
            csv_export_value(row.get("address", "")),
            csv_export_value(row.get("warehouse_id", "")),
            csv_export_value(row.get("warehouse_name", "")),
            csv_export_value(row.get("location", "")),
            csv_export_value(row.get("purchase_orders", 0)),
            csv_export_value(row.get("status", "Active"))
        ])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )



def require_reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Image, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as pdf_canvas
        return {
            "colors": colors,
            "TA_CENTER": TA_CENTER,
            "TA_LEFT": TA_LEFT,
            "TA_RIGHT": TA_RIGHT,
            "A4": A4,
            "ParagraphStyle": ParagraphStyle,
            "getSampleStyleSheet": getSampleStyleSheet,
            "mm": mm,
            "Image": Image,
            "Paragraph": Paragraph,
            "SimpleDocTemplate": SimpleDocTemplate,
            "Spacer": Spacer,
            "Table": Table,
            "LongTable": LongTable,
            "TableStyle": TableStyle,
            "ImageReader": ImageReader,
            "pdf_canvas": pdf_canvas
        }
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="ReportLab is not installed. Run pip install -r requirements.txt and restart the backend."
        ) from exc


def report_date_label(filters):
    start_date = (filters or {}).get("start_date")
    end_date = (filters or {}).get("end_date")
    if start_date and end_date:
        return f"{start_date} to {end_date}"
    if start_date:
        return f"From {start_date}"
    if end_date:
        return f"Until {end_date}"
    return "All Dates"


def current_user_label(current_user):
    return current_user.get("username") or current_user.get("full_name") or current_user.get("email") or "System"


def current_warehouse_label(current_user):
    if current_user.get("role") == "Admin":
        return "All Warehouses"
    return current_user.get("warehouse_name") or current_user.get("location") or current_user.get("warehouse_id") or "Assigned Warehouse"


def report_money(value):
    try:
        return f"Rs {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "Rs 0.00"


def report_number(value):
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "0"


def safe_report_text(value):
    if value is None:
        return "-"
    text = str(value)
    return xml_escape(text) if text.strip() else "-"


def product_image_path_for_report(row):
    raw_path = row.get("product_image")
    candidates = []
    if raw_path:
        cleaned = str(raw_path).lstrip("/")
        candidates.append(FRONTEND_DIR / cleaned)
        candidates.append(FilePath(__file__).resolve().parents[1] / cleaned)

    product_name = row.get("product_name") or row.get("product")
    if product_name:
        filename = re.sub(r"[^a-z0-9]+", "_", product_name.lower()).strip("_") + ".png"
        candidates.append(FRONTEND_DIR / "assets" / "images" / "products" / filename)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    return None


def make_pdf_paragraph(value, style):
    return style["Paragraph"](safe_report_text(value), style["cell"])


def make_summary_cards(cards, rl):
    Paragraph = rl["Paragraph"]
    Table = rl["Table"]
    LongTable = rl["LongTable"]
    TableStyle = rl["TableStyle"]
    colors = rl["colors"]
    mm = rl["mm"]
    cell_style = rl["ParagraphStyle"](
        "summaryLabel",
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#64748B"),
        alignment=rl["TA_CENTER"]
    )
    value_style = rl["ParagraphStyle"](
        "summaryValue",
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#1D4ED8"),
        alignment=rl["TA_CENTER"]
    )
    card_cells = []
    for card in cards:
        card_cells.append([
            Paragraph(card.get("icon", "*"), value_style),
            Paragraph(safe_report_text(card.get("value", 0)), value_style),
            Paragraph(safe_report_text(card.get("label", "")), cell_style)
        ])
    card_width = min(35, 180 / max(len(card_cells), 1)) * mm
    table = Table([card_cells], colWidths=[card_width] * len(card_cells), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6)
    ]))
    return table


def make_key_value_table(title, rows, rl):
    Paragraph = rl["Paragraph"]
    Table = rl["Table"]
    LongTable = rl["LongTable"]
    TableStyle = rl["TableStyle"]
    colors = rl["colors"]
    styles = get_enterprise_pdf_styles(rl)
    data = [[Paragraph(title, styles["section"]), ""]]
    data.extend([[Paragraph(safe_report_text(k), styles["cellMuted"]), Paragraph(safe_report_text(v), styles["cell"])] for k, v in rows])
    table = Table(data, colWidths=[85 * rl["mm"], 85 * rl["mm"]], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DBEAFE")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 1), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7)
    ]))
    return table


def get_enterprise_pdf_styles(rl):
    colors = rl["colors"]
    ParagraphStyle = rl["ParagraphStyle"]
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16, leading=18, textColor=colors.HexColor("#1E3A5F")),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#64748B")),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=colors.HexColor("#1E3A5F")),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#1F2937")),
        "cellMuted": ParagraphStyle("cellMuted", fontName="Helvetica-Bold", fontSize=6.1, leading=7.8, textColor=colors.HexColor("#64748B")),
        "tableHeader": ParagraphStyle("tableHeader", fontName="Helvetica-Bold", fontSize=6.2, leading=8.0, textColor=colors.white, alignment=rl["TA_CENTER"]),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#6B7280")),
        "right": ParagraphStyle("right", fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#64748B"), alignment=rl["TA_RIGHT"]),
        "centerCell": ParagraphStyle("centerCell", fontName="Helvetica", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#1F2937"), alignment=rl["TA_CENTER"]),
        "positiveCell": ParagraphStyle("positiveCell", fontName="Helvetica-Bold", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#15803D"), alignment=rl["TA_CENTER"]),
        "warningCell": ParagraphStyle("warningCell", fontName="Helvetica-Bold", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#D97706"), alignment=rl["TA_CENTER"]),
        "criticalCell": ParagraphStyle("criticalCell", fontName="Helvetica-Bold", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#DC2626"), alignment=rl["TA_CENTER"]),
        "idCell": ParagraphStyle("idCell", fontName="Helvetica-Bold", fontSize=6.2, leading=8.2, textColor=colors.HexColor("#1E3A5F"), alignment=rl["TA_CENTER"]),
        "numCell": ParagraphStyle("numCell", fontName="Helvetica", fontSize=6.0, leading=7.6, textColor=colors.HexColor("#1F2937"), alignment=rl["TA_RIGHT"])
    }


def pdf_status_style(value, styles):
    status = safe_report_text(value).strip().lower()
    if any(term in status for term in ("critical", "out of stock", "inactive", "failed", "cancelled")):
        return styles["criticalCell"]
    if any(term in status for term in ("warning", "low", "pending", "partial")):
        return styles["warningCell"]
    if any(term in status for term in ("active", "completed", "healthy", "paid", "success")):
        return styles["positiveCell"]
    return styles["centerCell"]


def report_logo_path():
    logo_path = FRONTEND_DIR / "assets" / "images" / "smart_inventory_cube.png"
    return logo_path if logo_path.exists() else None


def get_ist_datetime():
    return datetime.now(IST)


def format_ist_datetime():
    return get_ist_datetime().strftime("%d %b %Y, %I:%M %p IST")


def ist_timestamp_label():
    return format_ist_datetime()


def draw_enterprise_header_footer(canvas, doc, report_title, generated_by, generated_on, date_range, warehouse, page_number=None, total_pages=None):
    rl = require_reportlab()
    colors = rl["colors"]
    width, height = rl["A4"]
    canvas.saveState()
    logo_path = report_logo_path()
    if logo_path:
        canvas.drawImage(rl["ImageReader"](str(logo_path)), 32, height - 58, width=28, height=28, mask="auto", preserveAspectRatio=True)
    else:
        canvas.setFillColor(colors.HexColor("#2563EB"))
        canvas.roundRect(32, height - 58, 28, 28, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#1E3A5F"))
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(68, height - 34, "Smart Inventory")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(68, height - 46, "Sales Monitoring System")
    canvas.setFillColor(colors.HexColor("#1E3A5F"))
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawRightString(width - 32, height - 34, report_title)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(width - 32, height - 46, f"Generated On: {generated_on}")
    canvas.setStrokeColor(colors.HexColor("#2563EB"))
    canvas.setLineWidth(1.1)
    canvas.line(32, height - 68, width - 32, height - 68)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(32, 28, "Smart Inventory Sales Monitoring System")
    canvas.drawCentredString(width / 2, 28, f"Generated By: {generated_by} | Date Range: {date_range} | Warehouse: {warehouse}")
    page = page_number or doc.page
    page_label = f"Page {page} of {total_pages}" if total_pages else f"Page {page}"
    canvas.drawRightString(width - 32, 28, page_label)
    canvas.restoreState()


def build_enterprise_pdf(filename, report_title, summary_cards, columns, rows, insights, chart_sections, current_user, filters):
    print(f"[reports] ACTIVE_GENERATOR=enterprise_pdf_v3_blue_corporate report={report_title} filename={filename}")
    total_start = time.perf_counter()
    rl = require_reportlab()
    colors = rl["colors"]
    Paragraph = rl["Paragraph"]
    SimpleDocTemplate = rl["SimpleDocTemplate"]
    Spacer = rl["Spacer"]
    Table = rl["Table"]
    LongTable = rl["LongTable"]
    TableStyle = rl["TableStyle"]
    Image = rl["Image"]
    mm = rl["mm"]
    styles = get_enterprise_pdf_styles(rl)
    buffer = io.BytesIO()
    generated_on = ist_timestamp_label()
    generated_by = current_user_label(current_user)
    date_range = report_date_label(filters)
    warehouse = current_warehouse_label(current_user)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=rl["A4"],
        rightMargin=24,
        leftMargin=24,
        topMargin=86,
        bottomMargin=48
    )

    story = []
    meta_rows = [
        ("Generated By", generated_by),
        ("Generated On", generated_on),
        ("Date Range", date_range),
        ("Warehouse", warehouse)
    ]
    story.append(make_key_value_table("Report Details", meta_rows, rl))
    story.append(Spacer(1, 8))
    story.append(make_summary_cards(summary_cards, rl))
    story.append(Spacer(1, 10))

    for title, chart_rows in chart_sections:
        if chart_rows:
            story.append(make_key_value_table(title, chart_rows[:8], rl))
            story.append(Spacer(1, 8))

    story.append(Paragraph("Detailed Records", styles["section"]))
    id_columns = [key for key, _, kind, _ in columns if kind == "id"]
    rows = sort_records_by_numeric_id(rows, id_columns)
    table_data = [[Paragraph(label, styles["tableHeader"] ) for _, label, _, _ in columns]]
    use_row_images = len(rows) <= 100
    for index, row in enumerate(rows, start=1):
        row_cells = []
        for key, _, kind, width in columns:
            if key == "row_number":
                value = index
            elif kind == "image":
                if not use_row_images:
                    row_cells.append(Paragraph("-", styles["centerCell" ]))
                    continue
                image_path = product_image_path_for_report(row)
                if image_path:
                    try:
                        img = Image(str(image_path), width=13 * mm, height=13 * mm)
                        row_cells.append(img)
                        continue
                    except Exception:
                        value = "-"
                else:
                    value = "-"
            else:
                value = row.get(key)

            if kind == "money":
                row_cells.append(Paragraph(report_money(value), styles["numCell"]))
            elif kind == "number":
                row_cells.append(Paragraph(report_number(value), styles["numCell"]))
            elif kind == "id":
                row_cells.append(Paragraph(safe_report_text(value), styles["idCell"]))
            elif key in {"status", "stock_status"}:
                row_cells.append(Paragraph(safe_report_text(value), pdf_status_style(value, styles)))
            elif kind == "center":
                row_cells.append(Paragraph(safe_report_text(value), styles["centerCell"]))
            else:
                row_cells.append(Paragraph(safe_report_text(value), styles["cell"]))
        table_data.append(row_cells)

    if len(table_data) == 1:
        table_data.append([Paragraph("No records available for the selected filters.", styles["cell"])] + [""] * (len(columns) - 1))

    col_widths = [width * mm for _, _, _, width in columns]
    records_table = LongTable(table_data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    records_table.splitByRow = 1
    records_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E40AF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 6.2),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5)
    ]))
    story.append(records_table)
    story.append(Spacer(1, 12))

    if insights:
        story.append(make_key_value_table("Report Insights", insights, rl))
        story.append(Spacer(1, 14))

    signature_rows = [
        ("Generated By", generated_by),
        ("Generated On", generated_on),
        ("Date Range", date_range),
        ("Authorised Signature", "________________________")
    ]
    story.append(make_key_value_table("Approval", signature_rows, rl))

    class NumberedEnterpriseCanvas(rl["pdf_canvas"].Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                draw_enterprise_header_footer(
                    self,
                    doc,
                    report_title,
                    generated_by,
                    generated_on,
                    date_range,
                    warehouse,
                    page_number=self._pageNumber,
                    total_pages=total_pages
                )
                super().showPage()
            super().save()

    pdf_start = time.perf_counter()
    doc.build(story, canvasmaker=NumberedEnterpriseCanvas)
    buffer.seek(0)
    pdf_elapsed = time.perf_counter() - pdf_start
    total_elapsed = time.perf_counter() - total_start
    print(f"[reports] {report_title} PDF generated rows={len(rows)} pdf_build={pdf_elapsed:.2f}s total={total_elapsed:.2f}s")

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def inventory_pdf_report_response(filename, report, current_user):
    summary = report.get("summary", {})
    rows = report.get("inventory_summary") or report.get("product_summary", [])
    report_mode = (report.get("report_mode") or report.get("filters", {}).get("report_mode") or "product_summary").strip().lower()
    cards = [
        {"icon": "P", "value": report_number(summary.get("total_products")), "label": "Total Products"},
        {"icon": "S", "value": report_number(summary.get("total_stock_units")), "label": "Stock Units"},
        {"icon": "V", "value": report_money(summary.get("inventory_value")), "label": "Inventory Value"},
        {"icon": "L", "value": report_number(summary.get("low_stock_products")), "label": "Low Stock Items"},
        {"icon": "O", "value": report_number(summary.get("out_of_stock_products")), "label": "Out of Stock"}
    ]
    if report_mode == "warehouse_detail":
        columns = [
            ("row_number", "#", "center", 6),
            ("product_id", "Product ID", "id", 16),
            ("product_name", "Product Name", "text", 24),
            ("warehouse_id", "Warehouse ID", "id", 13),
            ("warehouse_name", "Warehouse", "text", 24),
            ("category_name", "Category", "text", 15),
            ("supplier_name", "Supplier", "text", 17),
            ("quantity", "Stock", "number", 10),
            ("unit_cost", "Unit Cost", "money", 13),
            ("price", "Price", "money", 14),
            ("stock_value", "Stock Value", "money", 15),
            ("reorder_level", "Reorder", "number", 10),
            ("stock_status", "Status", "center", 12)
        ]
    else:
        columns = [
            ("row_number", "#", "center", 6),
            ("product_id", "Product ID", "id", 17),
            ("product_name", "Product Name", "text", 28),
            ("category_name", "Category", "text", 18),
            ("supplier_name", "Supplier", "text", 20),
            ("warehouse_count", "Warehouses", "number", 12),
            ("quantity", "Total Stock", "number", 12),
            ("unit_cost", "Unit Cost", "money", 14),
            ("price", "Selling Price", "money", 15),
            ("stock_value", "Stock Value", "money", 16),
            ("reorder_level", "Reorder", "number", 12),
            ("stock_status", "Status", "center", 13)
        ]
    category_chart = [(item.get("category_name") or item.get("category_id") or "Uncategorized", report_money(item.get("inventory_value"))) for item in report.get("category_summary", [])]
    stock_distribution = [("Low Stock", summary.get("low_stock_products", 0)), ("Out of Stock", summary.get("out_of_stock_products", 0)), ("Total Units", summary.get("total_stock_units", 0))]
    highest_stock = max(rows, key=lambda item: item.get("quantity", 0), default={})
    highest_value = max(rows, key=lambda item: item.get("stock_value", 0), default={})
    lowest_stock = min(rows, key=lambda item: item.get("quantity", 0), default={})
    insights = [
        ("Highest Stock Item", f"{highest_stock.get('product_name', '-')} ({highest_stock.get('quantity', 0)} units)"),
        ("Highest Value Item", f"{highest_value.get('product_name', '-')} ({report_money(highest_value.get('stock_value', 0))})"),
        ("Lowest Stock Item", f"{lowest_stock.get('product_name', '-')} ({lowest_stock.get('quantity', 0)} units)")
    ]
    return build_enterprise_pdf(filename, "Inventory Report", cards, columns, rows, insights, [("Inventory Value by Category", category_chart), ("Stock Distribution", stock_distribution)], current_user, report.get("filters", {}))


def supplier_enterprise_pdf_response(filename, report, current_user):
    summary = report.get("summary", {})
    rows = report.get("items", [])
    top_supplier = max(rows, key=lambda item: item.get("purchase_cost", 0), default={})
    average_purchase = (summary.get("total_purchase_cost", 0) / summary.get("total_purchase_orders", 1)) if summary.get("total_purchase_orders") else 0
    cards = [
        {"icon": "S", "value": report_number(summary.get("total_suppliers")), "label": "Total Suppliers"},
        {"icon": "A", "value": report_number(summary.get("active_suppliers")), "label": "Active Suppliers"},
        {"icon": "V", "value": report_money(summary.get("total_purchase_cost")), "label": "Purchase Value"},
        {"icon": "C", "value": report_money(average_purchase), "label": "Avg Purchase Cost"},
        {"icon": "T", "value": top_supplier.get("supplier_name", "-"), "label": "Top Supplier"}
    ]
    columns = [
        ("row_number", "#", "center", 6),
        ("supplier_id", "Supplier ID", "id", 18),
        ("supplier_name", "Supplier Name", "text", 28),
        ("email", "Email", "text", 31),
        ("phone", "Phone", "text", 18),
        ("address", "Address", "text", 36),
        ("warehouse_name", "Warehouse", "text", 21),
        ("purchase_orders", "Purchases", "number", 16),
        ("status", "Status", "center", 14)
    ]
    normalized_rows = list(rows)
    status_counts = {}
    for row in rows:
        status = row.get("status", "Active")
        status_counts[status] = status_counts.get(status, 0) + 1
    top_purchase_chart = [(row.get("supplier_name", "-"), report_money(row.get("purchase_cost", 0))) for row in sorted(rows, key=lambda item: item.get("purchase_cost", 0), reverse=True)[:8]]
    insights = [
        ("Top Supplier", f"{top_supplier.get('supplier_name', '-')} ({report_money(top_supplier.get('purchase_cost', 0))})"),
        ("Total Purchase Orders", report_number(summary.get("total_purchase_orders"))),
        ("Low Stock Products From Suppliers", report_number(summary.get("low_stock_products")))
    ]
    return build_enterprise_pdf(filename, "Supplier Report", cards, columns, normalized_rows, insights, [("Top Suppliers by Purchase", top_purchase_chart), ("Supplier Status", list(status_counts.items()))], current_user, report.get("filters", {}))


def sales_rows_for_pdf(start_date=None, end_date=None, product_id=None, current_user=None):
    query = location_query(current_user or {})
    if product_id:
        query["product_id"] = product_id
    query.update(build_date_query(start_date, end_date))
    return list(sales_collection.find(
        query,
        {
            "_id": 0,
            "sale_id": 1,
            "product_name": 1,
            "customer_name": 1,
            "quantity": 1,
            "unit_price": 1,
            "total_amount": 1,
            "payment_method": 1,
            "date": 1,
            "created_at": 1,
            "sold_by": 1,
            "category": 1
        }
    ).sort("created_at", 1))


def sales_enterprise_pdf_response(filename, title, report, current_user, start_date=None, end_date=None, product_id=None):
    summary = report.get("summary", {})
    rows = sort_records_by_numeric_id(sales_rows_for_pdf(start_date, end_date, product_id, current_user), ["sale_id", "product_id"])
    customers = {row.get("customer_name") for row in rows if row.get("customer_name")}
    cards = [
        {"icon": "S", "value": report_number(summary.get("sales_count")), "label": "Total Sales"},
        {"icon": "R", "value": report_money(summary.get("revenue")), "label": "Revenue"},
        {"icon": "U", "value": report_number(summary.get("units_sold")), "label": "Units Sold"},
        {"icon": "P", "value": report_money(summary.get("profit")), "label": "Profit"},
        {"icon": "A", "value": report_money(summary.get("average_order_value")), "label": "Average Order Value"},
        {"icon": "C", "value": report_number(len(customers)), "label": "Total Customers"}
    ]
    columns = [
        ("row_number", "#", "center", 7),
        ("sale_id", "Sale ID", "id", 24),
        ("product_name", "Product", "text", 30),
        ("customer_name", "Customer", "text", 24),
        ("quantity", "Qty", "number", 10),
        ("unit_price", "Unit Price", "money", 18),
        ("total_amount", "Revenue", "money", 18),
        ("payment_method", "Payment", "center", 18),
        ("date", "Date", "center", 18),
        ("sold_by", "Sold By", "text", 22)
    ]
    product_totals = {}
    payment_totals = {}
    category_totals = {}
    for row in rows:
        product = row.get("product_name", "Unknown")
        product_totals[product] = product_totals.get(product, 0) + float(row.get("total_amount", 0) or 0)
        payment = row.get("payment_method", "Unknown")
        payment_totals[payment] = payment_totals.get(payment, 0) + 1
        category = row.get("category", "Uncategorized")
        category_totals[category] = category_totals.get(category, 0) + float(row.get("total_amount", 0) or 0)
    top_product = max(product_totals.items(), key=lambda item: item[1], default=("-", 0))
    insights = [
        ("Top Selling Product", f"{top_product[0]} ({report_money(top_product[1])})"),
        ("Highest Revenue Period", max(report.get("items", []), key=lambda item: item.get("revenue", 0), default={}).get("month") or "Selected Range"),
        ("Average Order Value", report_money(summary.get("average_order_value")))
    ]
    trend = [(str(item.get("date") or item.get("month") or item.get("period") or item.get("week") or "Period"), report_money(item.get("revenue", 0))) for item in report.get("items", [])]
    charts = [
        ("Daily Sales Trend / Revenue Chart", trend),
        ("Category Sales", [(key, report_money(value)) for key, value in category_totals.items()]),
        ("Payment Method", list(payment_totals.items()))
    ]
    normalized_rows = [
        {**row, "date": row.get("date") or format_datetime(row.get("created_at"))[:10]}
        for row in rows
    ]
    return build_enterprise_pdf(filename, title, cards, columns, normalized_rows, insights, charts, current_user, {"start_date": start_date, "end_date": end_date, "product_id": product_id})

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Smart Inventory Sales Monitoring System",
        version="1.0.0",
        routes=app.routes
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html():
    swagger_html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="Smart Inventory Sales Monitoring System - Docs",
        swagger_ui_parameters={
            "persistAuthorization": True
        }
    )
    content = swagger_html.body.decode("utf-8").replace(
        "</head>",
        """
        <style>
            .swagger-ui .scheme-container {
                position: sticky;
                top: 0;
                z-index: 10;
                padding: 12px 0;
            }

            .swagger-ui .scheme-container .schemes.wrapper {
                display: flex;
                justify-content: flex-end;
                max-width: none;
                padding: 0 30px;
            }

            .swagger-ui .scheme-container .auth-wrapper {
                margin-left: auto;
            }
        </style>
        </head>
        """
    )
    return HTMLResponse(content)


@app.get("/", tags=["Home"])
def home():
    return {
        "message": "Welcome to Smart Inventory Sales Monitoring System"
    }




@app.get("/public-config", tags=["Auth"])
def public_config():
    return {
        "recaptcha_enabled": recaptcha_requested_enabled(),
        "recaptcha_site_key": clean_env_value("RECAPTCHA_SITE_KEY") if has_real_recaptcha_site_key() else "",
        "google_client_id": clean_env_value("GOOGLE_CLIENT_ID")
    }


@app.get("/locations", tags=["Users"])
def get_locations():
    """Return the stores and warehouses available for user assignment."""
    try:
        return serialize_documents(
            locations_collection.find().sort("location_name", 1)
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch locations: {exc}"
        ) from exc


@app.post("/register", tags=["Auth"])
def register(
    full_name: str,
    email: str,
    password: str,
    confirm_password: str,
    role: str,
    warehouse_name: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    recaptcha_token: Optional[str] = Query(None)
):

    verify_recaptcha_or_skip(recaptcha_token, "register")

    full_name = validate_required(full_name, "Full Name")
    email = validate_email(email)
    phone = validate_phone(phone) if phone else ""
    password = validate_required(password, "Password")
    confirm_password = validate_required(confirm_password, "Confirm password")
    role = validate_required(role, "Role")

    if password != confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    if role not in ["Manager", "Staff"]:
        if role == "Admin":
            raise HTTPException(
                status_code=400,
                detail="Admin accounts cannot be created through registration."
            )
        raise HTTPException(
            status_code=400,
            detail="Choose Manager or Staff as the role."
        )

    if not warehouse_name:
        raise HTTPException(
            status_code=400,
            detail="Warehouse Name is required for Manager and Staff."
        )
    location = get_or_create_warehouse(warehouse_name)

    try:
        existing_email = users_collection.find_one({"email": email})
        if existing_email:
            conflict("Email already exists")

        username = generate_unique_username(full_name, email)
        hashed_password = hash_password(password)
        account_created = datetime.now(timezone.utc)
        users_collection.insert_one(
            user_document(
                username=username,
                full_name=full_name,
                email=email,
                password=hashed_password,
                role=role,
                phone=phone,
                account_created=account_created,
                **location_fields(location["location_id"])
            )
        )
        if role in ["Manager", "Staff"]:
            assign_unscoped_records_to_first_warehouse()
            sync_location_fields_for_existing_records()
            seed_module_collections(force=True)

        return {
            "message": "User Registered Successfully",
            "username": username,
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "role": role,
            **location,
            "account_created": account_created.isoformat()
        }
    except DuplicateKeyError:
        conflict("Username or email already exists")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {exc}"
        )
@app.post("/login", tags=["Auth"])
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    started = time.perf_counter()

    def login_log(step):
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"[login] {step} in {elapsed_ms:.1f}ms", file=sys.stderr)

    login_log("request received")
    username = validate_required(form_data.username, "Username")
    password = validate_required(form_data.password, "Password")
    form = await request.form()
    recaptcha_token = form.get("recaptcha_token")
    login_identifier = username.strip()
    email_identifier = login_identifier.lower()
    login_log("request parsed")

    assert_login_not_locked(email_identifier, request)
    login_log("rate-limit check completed")
    await run_in_threadpool(verify_recaptcha_or_skip, recaptcha_token, "login")
    login_log("reCAPTCHA verification completed")

    try:
        user = await asyncio.wait_for(
            run_in_threadpool(find_login_user, login_identifier, email_identifier),
            timeout=LOGIN_DATABASE_TIMEOUT_SECONDS
        )
        login_log("user lookup completed")
        print(
            "[login] diagnostics "
            f"identifier={email_identifier} "
            f"user_found={bool(user)} "
            f"role={(user or {}).get('role') if user else ''} "
            f"status={(user or {}).get('status') if user else ''} "
            f"password_field_exists={user_has_any_password_field(user)}",
            file=sys.stderr
        )
    except asyncio.TimeoutError as exc:
        login_log("user lookup timed out")
        raise HTTPException(
            status_code=503,
            detail="Login database lookup timed out. Please try again."
        ) from exc
    except PyMongoError as exc:
        print(f"Login database error for {email_identifier}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=503,
            detail="Login database is unavailable. Please try again."
        ) from exc

    try:
        stored_password = get_user_password_hash(user)
        password_ok = bool(user and await asyncio.wait_for(
            run_in_threadpool(verify_login_password, password, stored_password),
            timeout=LOGIN_PASSWORD_TIMEOUT_SECONDS
        ))
        login_log("password verification completed")
        print(f"[login] password_verified={password_ok}", file=sys.stderr)
    except asyncio.TimeoutError as exc:
        login_log("password verification timed out")
        raise HTTPException(status_code=503, detail="Login verification timed out. Please try again.") from exc
    if not password_ok:
        register_failed_login(email_identifier, request)
        login_log("failed login registered")
        generic_invalid_login()

    if str(user.get("status", "Active")).lower() not in {"active", "enabled"}:
        login_log("inactive account blocked")
        raise HTTPException(status_code=403, detail="Account inactive or unauthorized.")

    if password_needs_rehash(stored_password):
        await run_in_threadpool(
            users_collection.update_one,
            {"_id": user["_id"]},
            password_update_document(password)
        )
        user = await run_in_threadpool(users_collection.find_one, {"_id": user["_id"]}) or user
        login_log("legacy password hash upgraded")

    clear_failed_login(email_identifier, request)
    login_log("failed-login counters cleared")

    role = user.get("role")
    login_log(f"role detected: {role}")

    try:
        response = await asyncio.wait_for(
            run_in_threadpool(build_login_response, user),
            timeout=LOGIN_DATABASE_TIMEOUT_SECONDS
        )
        login_log("JWT/session response returned")
        return response
    except asyncio.TimeoutError as exc:
        login_log("JWT/session response timed out")
        raise HTTPException(status_code=503, detail="Login session creation timed out. Please try again.") from exc
    except PyMongoError as exc:
        print(f"Login session database error for {email_identifier}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="Login session could not be created. Please try again.") from exc

@app.post("/auth/confirm-password", tags=["Auth"])
def confirm_password_for_sensitive_action(
    request: ConfirmPasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        user = users_collection.find_one({"username": current_user["sub"]}, {"hashed_password": 1, "password": 1, "password_hash": 1})
        if not user or not verify_password(request.password, get_user_password_hash(user)):
            raise HTTPException(status_code=401, detail="Password confirmation failed.")
        return {"message": "Password confirmed."}
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to confirm password. Please try again."
        ) from exc

@app.post("/auth/forgot-password", tags=["Auth"])
def forgot_password(request: ForgotPasswordRequest):
    verify_recaptcha_or_skip(request.recaptcha_token, "forgot-password")
    email = validate_email(request.email)
    safe_message = "Password reset instructions have been sent to your registered email."

    try:
        user = users_collection.find_one({"email": email})
        if not user:
            return {"message": safe_message}

        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(minutes=30)
        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "password_reset_token_hash": reset_token_hash(token),
                "password_reset_expires_at": expires_at,
                "password_reset_used_at": None
            }}
        )

        reset_link = f"{frontend_base_url()}/reset-password?token={token}"
        send_password_reset_email(user["email"], reset_link)
        return {"message": safe_message}
    except HTTPException:
        raise
    except PyMongoError as exc:
        print(f"Password reset database error for {email}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail="Unable to create password reset request. Please try again."
        ) from exc

@app.post("/auth/reset-password", tags=["Auth"])
def reset_password(request: ResetPasswordRequest):
    token_hash = reset_token_hash(request.token)
    now = utc_now()

    try:
        user = users_collection.find_one({"password_reset_token_hash": token_hash, "password_reset_used_at": None})
        if not user:
            raise HTTPException(
                status_code=400,
                detail="Reset link is invalid or expired."
            )

        expires_at = as_utc_datetime(user.get("password_reset_expires_at"))
        if not expires_at or expires_at < now:
            users_collection.update_one(
                {"_id": user["_id"]},
                {"$unset": {
                    "password_reset_token_hash": "",
                    "password_reset_expires_at": "",
                    "password_reset_used_at": ""
                }}
            )
            raise HTTPException(
                status_code=400,
                detail="Reset link is invalid or expired."
            )

        update_doc = password_update_document(request.new_password)
        update_doc["$unset"].update({
            "password_reset_token_hash": "",
            "password_reset_expires_at": "",
            "password_reset_used_at": ""
        })
        users_collection.update_one({"_id": user["_id"]}, update_doc)
        return {"message": "Password reset successful."}
    except PyMongoError as exc:
        print(f"Password reset update error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail="Unable to reset password. Please try again."
        ) from exc


@app.post("/auth/change-password", tags=["Auth"])
def change_password(
    request: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        user = users_collection.find_one({"username": current_user["sub"]})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not verify_password(request.current_password, get_user_password_hash(user)):
            raise HTTPException(
                status_code=400,
                detail="Current password is incorrect."
            )

        users_collection.update_one(
            {"_id": user["_id"]},
            password_update_document(request.new_password)
        )
        return {"message": "Password changed successfully."}
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update password: {exc}"
        ) from exc


@app.post("/auth/google", tags=["Auth"])
def google_login(google_user: GoogleLogin):
    try:
        verified_user = verify_google_credential(google_user.credential)
        name = validate_required(verified_user.get("name") or "", "Name")
        email = validate_email(verified_user.get("email") or "")
        google_id = validate_required(verified_user.get("sub") or "", "Google ID")

        user = users_collection.find_one({
            "$or": [
                {"google_id": google_id},
                {"email": email}
            ]
        })

        if not user:
            account_created = utc_now()
            username = generate_unique_username(name, email)
            location_id = first_registered_warehouse_id()
            location = location_fields(location_id) if location_id else location_fields("ALL")
            user_doc = user_document(
                username=username,
                full_name=name,
                email=email,
                password="",
                role="Staff",
                phone="",
                account_created=account_created,
                google_id=google_id,
                **location
            )
            users_collection.insert_one(user_doc)
            user = users_collection.find_one({"username": username})
        else:
            account_created = user.get("account_created") or user["_id"].generation_time
            users_collection.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "google_id": google_id,
                    "email": email,
                    "full_name": user.get("full_name") or name,
                    "account_created": account_created
                }}
            )
            user = users_collection.find_one({"_id": user["_id"]})

        if str(user.get("status", "Active")).lower() not in {"active", "enabled"}:
            raise HTTPException(status_code=403, detail="Account inactive or unauthorized.")

        response = build_login_response(user, message="Login successful")
        response["user"] = {
            "username": response.get("username"),
            "full_name": response.get("full_name"),
            "email": response.get("email"),
            "phone": response.get("phone") or "",
            "role": response.get("role"),
            "location_id": response.get("location_id"),
            "warehouse_id": response.get("warehouse_id"),
            "warehouse_name": response.get("warehouse_name"),
            "location": response.get("location"),
            "state": response.get("state"),
            "account_created": response.get("account_created"),
            "last_login": response.get("last_login")
        }
        return response
    except HTTPException:
        raise
    except DuplicateKeyError:
        conflict("A user with this Google account already exists")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Google login failed: {exc}"
        ) from exc
    except Exception as exc:
        print(f"Google authentication failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise HTTPException(
            status_code=500,
            detail="Google authentication failed"
        ) from exc


@app.put("/profile", tags=["Users"])
def update_profile(
    profile: ProfileUpdate,
    current_user: dict = Depends(get_current_user)
):
    full_name = validate_required(profile.full_name, "Full name")
    email = validate_email(profile.email)
    phone = validate_phone(profile.phone)

    try:
        user = users_collection.find_one({"username": current_user["sub"]})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        existing_email = users_collection.find_one({
            "email": email,
            "username": {"$ne": current_user["sub"]}
        })
        if existing_email:
            conflict("Email already exists")

        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"full_name": full_name, "email": email, "phone": phone}}
        )

        return {
            "username": user["username"],
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "role": user["role"],
            "account_created": user.get("account_created").isoformat()
            if user.get("account_created") else None,
            "last_login": user.get("last_login").isoformat()
            if user.get("last_login") else None,
            **location_details(user.get("location_id", "ALL"))
        }
    except DuplicateKeyError:
        conflict("Email already exists")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update profile: {exc}"
        ) from exc


@app.get("/admin", tags=["Dashboards"])
def admin_dashboard(current_role: str = Depends(get_current_role)):

    check_role(
        current_role,
        ["Admin"]
    )

    return {
        "message": "Welcome Admin"
    }


@app.get("/manager", tags=["Dashboards"])
def manager_dashboard(current_role: str = Depends(get_current_role)):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    return {
        "message": "Welcome Manager"
    }


@app.get("/staff", tags=["Dashboards"])
def staff_dashboard(current_role: str = Depends(get_current_role)):

    check_role(
        current_role,
        ["Admin", "Manager", "Staff"]
    )

    return {
        "message": "Welcome Staff"
    }


@app.get("/dashboard/inventory", tags=["Dashboards"])
def inventory_dashboard(current_user: dict = Depends(get_current_user)):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        scoped_location = location_query(current_user)
        inventory_pipeline = []
        if scoped_location:
            inventory_pipeline.append({"$match": scoped_location})
        inventory_pipeline.append({
            "$group": {
                "_id": None,
                "total_products": {"$sum": 1},
                "total_stock_units": {"$sum": "$quantity"},
                "inventory_value": {
                    "$sum": {"$multiply": ["$quantity", "$price"]}
                }
            }
        })
        totals = list(products_collection.aggregate(inventory_pipeline))
        totals = totals[0] if totals else {
            "total_products": 0,
            "total_stock_units": 0,
            "inventory_value": 0
        }

        out_query = {"quantity": 0, **scoped_location}
        low_query = low_stock_filter()
        low_query.update(scoped_location)
        out_of_stock_products = products_collection.count_documents(out_query)
        low_stock_products = products_collection.count_documents(low_query)

        category_names = {
            category["category_id"]: category["category_name"]
            for category in categories_collection.find(
                {},
                {"category_id": 1, "category_name": 1}
            )
        }

        category_pipeline = []
        if scoped_location:
            category_pipeline.append({"$match": scoped_location})
        category_pipeline.extend([
            {
                "$group": {
                    "_id": "$category_id",
                    "product_count": {"$sum": 1},
                    "stock_units": {"$sum": "$quantity"},
                    "inventory_value": {
                        "$sum": {"$multiply": ["$quantity", "$price"]}
                    }
                }
            },
            {"$sort": {"stock_units": -1}}
        ])

        stock_by_category = []
        for item in products_collection.aggregate(category_pipeline):
            stock_by_category.append({
                "category_id": item["_id"],
                "category_name": category_names.get(item["_id"]),
                "product_count": item["product_count"],
                "stock_units": item["stock_units"],
                "inventory_value": item["inventory_value"]
            })

        recent_movements = inventory_history_collection.find(scoped_location).sort(
            "created_at",
            -1
        ).limit(10)

        return {
            "role": current_user["role"],
            "warehouse": location_details(current_user.get("location_id", "ALL")),
            "total_products": totals["total_products"],
            "total_stock_units": totals["total_stock_units"],
            "inventory_value": totals["inventory_value"],
            "low_stock_products": low_stock_products,
            "out_of_stock_products": out_of_stock_products,
            "stock_by_category": stock_by_category,
            "recent_movements": serialize_documents(recent_movements)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load inventory dashboard: {exc}"
        )

@app.get("/dashboard/sales", tags=["Dashboards"])
def sales_dashboard(current_user: dict = Depends(get_current_user)):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        scoped_location = location_query(current_user)
        totals_pipeline = []
        if scoped_location:
            totals_pipeline.append({"$match": scoped_location})
        totals_pipeline.append({
            "$group": {
                "_id": None,
                "total_sales_records": {"$sum": 1},
                "total_units_sold": {"$sum": "$quantity"},
                "total_revenue": {"$sum": "$total_amount"},
                "total_cost": {"$sum": {"$ifNull": ["$cost_amount", 0]}},
                "total_profit": {"$sum": {"$ifNull": ["$profit", 0]}}
            }
        })
        totals = list(sales_collection.aggregate(totals_pipeline))
        totals = totals[0] if totals else {
            "total_sales_records": 0,
            "total_units_sold": 0,
            "total_revenue": 0,
            "total_cost": 0,
            "total_profit": 0
        }
        average_order_value = (
            round(totals["total_revenue"] / totals["total_sales_records"], 2)
            if totals["total_sales_records"]
            else 0
        )

        top_products_pipeline = []
        if scoped_location:
            top_products_pipeline.append({"$match": scoped_location})
        top_products_pipeline.extend([
            {
                "$group": {
                    "_id": {
                        "product_id": "$product_id",
                        "product_name": "$product_name"
                    },
                    "units_sold": {"$sum": "$quantity"},
                    "revenue": {"$sum": "$total_amount"}
                }
            },
            {"$sort": {"revenue": -1}},
            {"$limit": 5}
        ])
        top_products = [
            {
                "product_id": item["_id"]["product_id"],
                "product_name": item["_id"]["product_name"],
                "units_sold": item["units_sold"],
                "revenue": item["revenue"]
            }
            for item in sales_collection.aggregate(top_products_pipeline)
        ]

        region_match = {"region": {"$nin": [None, ""]}, **scoped_location}
        sales_by_region = [
            {
                "region": item["_id"],
                "orders": item["orders"],
                "revenue": item["revenue"]
            }
            for item in sales_collection.aggregate([
                {"$match": region_match},
                {
                    "$group": {
                        "_id": "$region",
                        "orders": {"$sum": 1},
                        "revenue": {"$sum": "$total_amount"}
                    }
                },
                {"$sort": {"revenue": -1}}
            ])
        ]

        recent_sales = sales_collection.find(scoped_location).sort(
            "created_at",
            -1
        ).limit(10)

        return {
            "role": current_user["role"],
            "warehouse": location_details(current_user.get("location_id", "ALL")),
            "total_sales_records": totals["total_sales_records"],
            "total_units_sold": totals["total_units_sold"],
            "total_revenue": totals["total_revenue"],
            "total_cost": totals["total_cost"],
            "total_profit": totals["total_profit"],
            "average_order_value": average_order_value,
            "top_products": top_products,
            "sales_by_region": sales_by_region,
            "recent_sales": serialize_documents(recent_sales)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load sales dashboard: {exc}"
        )

@app.get("/analytics/summary", tags=["Analytics"])
def analytics_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        scoped_location = location_query(current_user)
        date_query = build_date_query(start_date, end_date)

        product_query = dict(scoped_location)
        sales_query = dict(scoped_location)
        purchases_query = dict(scoped_location)
        sales_item_query = dict(scoped_location)
        purchase_item_query = dict(scoped_location)
        if date_query:
            sales_query.update(date_query)
            purchases_query.update(date_query)
            sales_item_query.update(date_query)
            purchase_item_query.update(date_query)

        total_products = products_collection.count_documents(product_query)
        low_query = low_stock_filter()
        low_query.update(product_query)
        low_stock_count = products_collection.count_documents(low_query)

        inventory_totals = list(products_collection.aggregate([
            {"$match": product_query},
            {
                "$group": {
                    "_id": None,
                    "inventory_value": {
                        "$sum": {
                            "$multiply": [
                                {"$ifNull": ["$quantity", 0]},
                                {"$ifNull": ["$price", 0]}
                            ]
                        }
                    },
                    "stock_units": {"$sum": {"$ifNull": ["$quantity", 0]}}
                }
            }
        ]))
        inventory_totals = inventory_totals[0] if inventory_totals else {"inventory_value": 0, "stock_units": 0}

        sales_totals = list(sales_collection.aggregate([
            {"$match": sales_query},
            {
                "$group": {
                    "_id": None,
                    "total_sales": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                    "sales_count": {"$sum": 1},
                    "units_sold": {"$sum": {"$ifNull": ["$quantity", 0]}}
                }
            }
        ]))
        sales_totals = sales_totals[0] if sales_totals else {"total_sales": 0, "sales_count": 0, "units_sold": 0}

        purchase_totals = list(purchases_collection.aggregate([
            {"$match": purchases_query},
            {
                "$group": {
                    "_id": None,
                    "total_purchases": {"$sum": {"$ifNull": ["$total_cost", 0]}},
                    "purchase_count": {"$sum": 1}
                }
            }
        ]))
        purchase_totals = purchase_totals[0] if purchase_totals else {"total_purchases": 0, "purchase_count": 0}

        sales_trend_match = dict(sales_query)
        sales_trend_match["created_at"] = {
            **sales_trend_match.get("created_at", {}),
            "$exists": True
        }
        sales_trend = [
            {"date": item["_id"], "total": item["total"], "count": item["count"]}
            for item in sales_collection.aggregate([
                {"$match": sales_trend_match},
                {
                    "$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                        "total": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                        "count": {"$sum": 1}
                    }
                },
                {"$sort": {"_id": 1}}
            ])
        ]

        purchase_trend_match = dict(purchases_query)
        purchase_trend_match["created_at"] = {
            **purchase_trend_match.get("created_at", {}),
            "$exists": True
        }
        purchase_trend = [
            {"date": item["_id"], "total": item["total"], "count": item["count"]}
            for item in purchases_collection.aggregate([
                {"$match": purchase_trend_match},
                {
                    "$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                        "total": {"$sum": {"$ifNull": ["$total_cost", 0]}},
                        "count": {"$sum": 1}
                    }
                },
                {"$sort": {"_id": 1}}
            ])
        ]

        category_names = {
            category.get("category_id"): category.get("category_name")
            for category in categories_collection.find({}, {"category_id": 1, "category_name": 1})
        }
        product_lookup = {
            product.get("product_id"): {
                "product_name": product.get("product_name"),
                "category_id": product.get("category_id"),
                "category_name": category_names.get(product.get("category_id")) or product.get("category_id") or "Unassigned"
            }
            for product in products_collection.find(product_query, {"product_id": 1, "product_name": 1, "category_id": 1})
        }

        sales_by_category_map = {}
        top_products_map = {}
        for item in sales_items_collection.find(
            sales_item_query,
            {"product_id": 1, "product_name": 1, "quantity": 1, "total_amount": 1, "revenue": 1}
        ):
            product_id = item.get("product_id")
            metadata = product_lookup.get(product_id, {})
            category_name = metadata.get("category_name") or "Unassigned"
            revenue = item.get("total_amount", item.get("revenue", 0)) or 0
            quantity = item.get("quantity", 0) or 0
            sales_by_category_map.setdefault(category_name, {"category_name": category_name, "revenue": 0, "units_sold": 0})
            sales_by_category_map[category_name]["revenue"] += revenue
            sales_by_category_map[category_name]["units_sold"] += quantity
            product_name = item.get("product_name") or metadata.get("product_name") or product_id or "Unknown Product"
            top_products_map.setdefault(product_id or product_name, {"product_id": product_id, "product_name": product_name, "revenue": 0, "units_sold": 0})
            top_products_map[product_id or product_name]["revenue"] += revenue
            top_products_map[product_id or product_name]["units_sold"] += quantity

        if not top_products_map:
            for item in sales_collection.aggregate([
                {"$match": sales_query},
                {
                    "$group": {
                        "_id": {"product_id": "$product_id", "product_name": "$product_name"},
                        "units_sold": {"$sum": {"$ifNull": ["$quantity", 0]}},
                        "revenue": {"$sum": {"$ifNull": ["$total_amount", 0]}}
                    }
                },
                {"$sort": {"revenue": -1}},
                {"$limit": 8}
            ]):
                product_id = item["_id"].get("product_id")
                product_name = item["_id"].get("product_name") or product_lookup.get(product_id, {}).get("product_name") or "Unknown Product"
                top_products_map[product_id or product_name] = {
                    "product_id": product_id,
                    "product_name": product_name,
                    "units_sold": item.get("units_sold", 0),
                    "revenue": item.get("revenue", 0)
                }
                category_name = product_lookup.get(product_id, {}).get("category_name") or "Unassigned"
                sales_by_category_map.setdefault(category_name, {"category_name": category_name, "revenue": 0, "units_sold": 0})
                sales_by_category_map[category_name]["revenue"] += item.get("revenue", 0)
                sales_by_category_map[category_name]["units_sold"] += item.get("units_sold", 0)

        stock_performance = []
        for product in products_collection.find(
            product_query,
            {"product_id": 1, "product_name": 1, "quantity": 1, "reorder_level": 1, "price": 1}
        ).sort("quantity", 1).limit(10):
            quantity = product.get("quantity", 0) or 0
            reorder_level = product.get("reorder_level", 35) or 35
            status = "Out of Stock" if quantity <= 0 else ("Low Stock" if quantity <= reorder_level else "Healthy")
            stock_performance.append({
                "product_id": product.get("product_id"),
                "product_name": product.get("product_name"),
                "stock": quantity,
                "reorder_level": reorder_level,
                "stock_value": quantity * (product.get("price", 0) or 0),
                "status": status
            })

        supplier_contribution = [
            {
                "supplier_id": item["_id"].get("supplier_id"),
                "supplier_name": item["_id"].get("supplier_name") or item["_id"].get("supplier_id") or "Unknown Supplier",
                "total_purchase_cost": item.get("total_purchase_cost", 0),
                "purchase_count": item.get("purchase_count", 0)
            }
            for item in purchases_collection.aggregate([
                {"$match": purchases_query},
                {
                    "$group": {
                        "_id": {"supplier_id": "$supplier_id", "supplier_name": "$supplier_name"},
                        "total_purchase_cost": {"$sum": {"$ifNull": ["$total_cost", 0]}},
                        "purchase_count": {"$sum": 1}
                    }
                },
                {"$sort": {"total_purchase_cost": -1}},
                {"$limit": 8}
            ])
        ]

        return {
            "total_sales": sales_totals.get("total_sales", 0),
            "total_purchases": purchase_totals.get("total_purchases", 0),
            "total_products": total_products,
            "inventory_value": inventory_totals.get("inventory_value", 0),
            "low_stock_count": low_stock_count,
            "sales_count": sales_totals.get("sales_count", 0),
            "purchase_count": purchase_totals.get("purchase_count", 0),
            "sales_trend": sales_trend,
            "purchase_trend": purchase_trend,
            "sales_by_category": sorted(sales_by_category_map.values(), key=lambda item: item["revenue"], reverse=True)[:8],
            "top_products": sorted(top_products_map.values(), key=lambda item: item["revenue"], reverse=True)[:8],
            "stock_performance": stock_performance,
            "supplier_contribution": supplier_contribution
        }
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to load analytics data: {exc}") from exc

def scoped_warehouse_filter(current_user, warehouse_id=None):
    role = current_user.get("role")
    assigned_warehouse = current_user.get("warehouse_id") or ""
    if role == "Admin":
        return {"warehouse_id": warehouse_id} if warehouse_id else {}
    if not assigned_warehouse:
        raise HTTPException(status_code=403, detail="Your account is not assigned to a warehouse.")
    if warehouse_id and warehouse_id != assigned_warehouse:
        raise HTTPException(status_code=403, detail="Access denied. You do not have permission to view this warehouse.")
    return {"warehouse_id": assigned_warehouse}


def warehouse_label_map():
    return {
        warehouse.get("warehouse_id"): warehouse
        for warehouse in warehouses_collection.find({}, {"_id": 0})
    }


def build_inventory_row(inventory, product_lookup=None, warehouse_lookup=None):
    product_lookup = product_lookup or {}
    warehouse_lookup = warehouse_lookup or {}
    product = product_lookup.get(inventory.get("product_id")) or products_collection.find_one(
        {"product_id": inventory.get("product_id")},
        {"_id": 0, "product_id": 1, "product_name": 1, "category_id": 1, "supplier_id": 1, "price": 1, "unit_price": 1, "unit_cost": 1, "reorder_level": 1, "status": 1}
    ) or {}
    warehouse = warehouse_lookup.get(inventory.get("warehouse_id")) or warehouses_collection.find_one(
        {"warehouse_id": inventory.get("warehouse_id")},
        {"_id": 0}
    ) or {}
    quantity = int(inventory.get("quantity") or 0)
    reorder_level = int(inventory.get("reorder_level") or product.get("reorder_level") or 0)
    unit_price = product.get("unit_price", product.get("price", 0)) or 0
    return {
        "inventory_id": inventory.get("inventory_id"),
        "product_id": inventory.get("product_id"),
        "product_name": product.get("product_name", inventory.get("product_name", "")),
        "category_id": product.get("category_id"),
        "supplier_id": product.get("supplier_id"),
        "warehouse_id": inventory.get("warehouse_id"),
        "warehouse_name": warehouse.get("warehouse_name", inventory.get("warehouse_name", "")),
        "city": warehouse.get("city", ""),
        "state": warehouse.get("state", ""),
        "quantity": quantity,
        "current_stock": quantity,
        "reorder_level": reorder_level,
        "unit_price": unit_price,
        "price": unit_price,
        "unit_cost": product.get("unit_cost", 0) or 0,
        "stock_value": quantity * unit_price,
        "stock_status": "Low Stock" if quantity <= reorder_level else "In Stock",
        "last_updated": inventory.get("last_updated")
    }


def inventory_attention_status(quantity, reorder_level):
    quantity = int(quantity or 0)
    reorder_level = int(reorder_level or 0)
    if quantity <= 0:
        return "Out of Stock", "Purchase Immediately"
    if reorder_level and quantity <= reorder_level:
        return "Low Stock", "Restock"
    if reorder_level and quantity >= reorder_level * 3:
        return "Overstocked", "Review Demand"
    return "Healthy", "No action required"


def inventory_health_query(base_query, attention_only=False, status=None):
    query = dict(base_query or {})
    if status:
        clean_status = status.strip().lower()
        if clean_status in {"out", "out-of-stock", "out of stock", "critical"}:
            query["quantity"] = {"$lte": 0}
        elif clean_status in {"low", "low-stock", "low stock"}:
            query["$expr"] = {"$and": [{"$gt": ["$quantity", 0]}, {"$lte": ["$quantity", "$reorder_level"]}]}
        elif clean_status in {"overstocked", "overstock"}:
            query["$expr"] = {"$gte": ["$quantity", {"$multiply": ["$reorder_level", 3]}]}
        return query
    if attention_only:
        query["$or"] = [
            {"quantity": {"$lte": 0}},
            {"$expr": {"$and": [{"$gt": ["$quantity", 0]}, {"$lte": ["$quantity", "$reorder_level"]}]}},
            {"$expr": {"$gte": ["$quantity", {"$multiply": ["$reorder_level", 3]}]}}
        ]
    return query


def restock_eligible_inventory_query(base_query):
    query = dict(base_query or {})
    query["$or"] = [
        {"quantity": {"$lte": 0}},
        {"$expr": {"$and": [{"$gt": ["$quantity", 0]}, {"$lte": ["$quantity", "$reorder_level"]}]}}
    ]
    return query


def queue_scope_query(current_user, product_ids=None, warehouse_ids=None):
    query = {}
    clean_product_ids = [product_id for product_id in (product_ids or []) if product_id]
    if clean_product_ids:
        query["product_id"] = {"$in": clean_product_ids}

    role = current_user.get("role")
    if role == "Admin":
        clean_warehouse_ids = [warehouse_id for warehouse_id in (warehouse_ids or []) if warehouse_id]
        if clean_warehouse_ids:
            query["$or"] = [
                {"warehouse_id": {"$in": clean_warehouse_ids}},
                {"location_id": {"$in": clean_warehouse_ids}}
            ]
        return query

    assigned_warehouse = current_user.get("warehouse_id") or current_user.get("location_id") or ""
    if assigned_warehouse:
        query["$or"] = [
            {"warehouse_id": assigned_warehouse},
            {"location_id": assigned_warehouse}
        ]
    return query


def inventory_health_counts(base_query):
    total = warehouse_inventory_collection.count_documents(base_query)
    out_of_stock = warehouse_inventory_collection.count_documents({**base_query, "quantity": {"$lte": 0}})
    low_stock = warehouse_inventory_collection.count_documents({
        **base_query,
        "$expr": {"$and": [{"$gt": ["$quantity", 0]}, {"$lte": ["$quantity", "$reorder_level"]}]}
    })
    overstocked = warehouse_inventory_collection.count_documents({
        **base_query,
        "$expr": {"$gte": ["$quantity", {"$multiply": ["$reorder_level", 3]}]}
    })
    healthy = max(total - out_of_stock - low_stock - overstocked, 0)
    health_percentage = round((healthy / total) * 100) if total else 100
    return {
        "total_products": total,
        "healthy_products": healthy,
        "low_stock_products": low_stock,
        "out_of_stock_products": out_of_stock,
        "overstocked_products": overstocked,
        "inventory_health_percentage": health_percentage
    }


def build_health_attention_row(inventory, product_lookup, warehouse_lookup):
    row = build_inventory_row(inventory, product_lookup, warehouse_lookup)
    status, action = inventory_attention_status(row.get("quantity"), row.get("reorder_level"))
    row["status"] = status
    row["suggested_action"] = action
    row["suggested_quantity"] = max((int(row.get("reorder_level") or 0) * 2) - int(row.get("quantity") or 0), int(row.get("reorder_level") or 1), 1)
    return row


@app.get("/warehouses", tags=["Warehouses"])
def get_warehouses(current_user: dict = Depends(get_current_user)):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    query = scoped_warehouse_filter(current_user)
    try:
        warehouses = list(warehouses_collection.find(query, {"_id": 0}).sort("warehouse_id", 1))
        return {"total": len(warehouses), "items": warehouses}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch warehouses: {exc}") from exc


@app.get("/warehouses/{warehouse_id}", tags=["Warehouses"])
def get_warehouse(warehouse_id: str, current_user: dict = Depends(get_current_user)):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    query = {"warehouse_id": warehouse_id, **scoped_warehouse_filter(current_user, warehouse_id)}
    try:
        warehouse = warehouses_collection.find_one(query, {"_id": 0})
        if not warehouse:
            not_found("Warehouse not found")
        return warehouse
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch warehouse: {exc}") from exc


@app.get("/warehouses/{warehouse_id}/users", tags=["Warehouses"])
def get_warehouse_users(warehouse_id: str, current_user: dict = Depends(get_current_user)):
    check_role(current_user["role"], ["Admin", "Manager"])
    scoped_warehouse_filter(current_user, warehouse_id)
    try:
        users = list(users_collection.find(
            {"warehouse_id": warehouse_id},
            {"password": 0, "password_reset_token_hash": 0}
        ).sort("role", 1))
        return {"total": len(users), "items": [serialize_document(user) for user in users]}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch warehouse users: {exc}") from exc


@app.get("/warehouses/{warehouse_id}/inventory", tags=["Warehouses"])
def get_warehouse_inventory(warehouse_id: str, current_user: dict = Depends(get_current_user)):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    scoped_warehouse_filter(current_user, warehouse_id)
    try:
        inventory = list(warehouse_inventory_collection.find({"warehouse_id": warehouse_id}, {"_id": 0}).sort("product_id", 1))
        product_ids = [item.get("product_id") for item in inventory]
        product_lookup = {
            product.get("product_id"): product
            for product in products_collection.find({"product_id": {"$in": product_ids}}, {"_id": 0})
        }
        warehouse_lookup = warehouse_label_map()
        return {
            "total": len(inventory),
            "items": [build_inventory_row(item, product_lookup, warehouse_lookup) for item in inventory]
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch warehouse inventory: {exc}") from exc


@app.get("/inventory", tags=["Inventory"])
def inventory_records(
    warehouse_id: Optional[str] = Query(None, description="Filter inventory by warehouse ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    query = scoped_warehouse_filter(current_user, warehouse_id)
    try:
        skip = (page - 1) * limit
        total = warehouse_inventory_collection.count_documents(query)
        rows = list(warehouse_inventory_collection.find(query, {"_id": 0}).sort([("warehouse_id", 1), ("product_id", 1)]).skip(skip).limit(limit))
        product_ids = [item.get("product_id") for item in rows]
        product_lookup = {
            product.get("product_id"): product
            for product in products_collection.find({"product_id": {"$in": product_ids}}, {"_id": 0})
        }
        warehouse_lookup = warehouse_label_map()
        return {
            "page": page,
            "limit": limit,
            "page_size": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
            "has_next": page * limit < total,
            "has_previous": page > 1,
            "items": [build_inventory_row(item, product_lookup, warehouse_lookup) for item in rows]
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch inventory: {exc}") from exc



@app.get("/inventory/product/{product_id}", tags=["Inventory"])
def get_inventory_for_product(
    product_id: str,
    warehouse_id: Optional[str] = Query(None, description="Filter inventory by warehouse ID"),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    product_id = validate_required(product_id, "Product ID")

    try:
        query = {"product_id": product_id, **scoped_warehouse_filter(current_user, warehouse_id)}
        rows = list(warehouse_inventory_collection.find(query, {"_id": 0}).sort("warehouse_id", 1))
        product = products_collection.find_one(scoped_product_query(product_id, current_user)) or products_collection.find_one({"product_id": product_id})
        product_lookup = {product_id: product} if product else {}
        warehouse_lookup = warehouse_label_map()
        return {
            "total": len(rows),
            "items": [build_inventory_row(row, product_lookup, warehouse_lookup) for row in rows]
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch product inventory: {exc}")


@app.get("/inventory/health/summary", tags=["Inventory"])
def inventory_health_summary(
    warehouse_id: Optional[str] = Query(None, description="Filter inventory health by warehouse ID"),
    current_user: dict = Depends(get_current_user)
):
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    base_query = scoped_warehouse_filter(current_user, warehouse_id)
    try:
        counts = inventory_health_counts(base_query)
        low = counts["low_stock_products"]
        out = counts["out_of_stock_products"]
        total = counts["total_products"]
        eligible_count = low + out
        low_percent = (low / total) * 100 if total else 0
        queue_count = restock_queue_collection.count_documents(queue_scope_query(
            current_user,
            warehouse_ids=[warehouse_id] if warehouse_id else None
        ))
        if out > 0 or low_percent > 40:
            status = "Critical"
            message = f"{eligible_count} products require immediate restocking. Click here to view affected products."
        elif low_percent >= 20:
            status = "Warning"
            message = f"{low} products are below their reorder level."
        else:
            status = "Healthy"
            message = "All products are sufficiently stocked."
        return {
            **counts,
            "status": status,
            "message": message,
            "restock_eligible_count": eligible_count,
            "restock_queue_count": queue_count
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch inventory health: {exc}") from exc


@app.get("/inventory/health/attention", tags=["Inventory"])
def inventory_health_attention(
    warehouse_id: Optional[str] = Query(None, description="Filter inventory health by warehouse ID"),
    status: Optional[str] = Query(None, description="Low Stock, Out of Stock, Overstocked"),
    search: Optional[str] = Query(None, description="Search product name or ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    page_size: Optional[int] = Query(None, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    if page_size is not None:
        limit = min(page_size, 100)
    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    base_query = scoped_warehouse_filter(current_user, warehouse_id)
    query = inventory_health_query(base_query, attention_only=True, status=status)
    try:
        if search:
            clean_search = search.strip()
            product_ids = [
                product.get("product_id")
                for product in products_collection.find(
                    {"$or": [
                        {"product_id": {"$regex": clean_search, "$options": "i"}},
                        {"product_name": {"$regex": clean_search, "$options": "i"}}
                    ]},
                    {"_id": 0, "product_id": 1}
                ).limit(250)
            ]
            query["product_id"] = {"$in": product_ids} if product_ids else {"$in": []}
        skip = (page - 1) * limit
        total = warehouse_inventory_collection.count_documents(query)
        rows = list(
            warehouse_inventory_collection.find(
                query,
                {"_id": 0, "inventory_id": 1, "product_id": 1, "warehouse_id": 1, "quantity": 1, "reorder_level": 1, "last_updated": 1}
            ).sort([("quantity", 1), ("product_id", 1)]).skip(skip).limit(limit)
        )
        product_ids = [row.get("product_id") for row in rows]
        product_lookup = {
            product.get("product_id"): product
            for product in products_collection.find(
                {"product_id": {"$in": product_ids}},
                {"_id": 0, "product_id": 1, "product_name": 1, "category_id": 1, "supplier_id": 1, "price": 1, "unit_price": 1, "unit_cost": 1, "reorder_level": 1, "status": 1}
            )
        }
        warehouse_lookup = warehouse_label_map()
        items = [build_health_attention_row(row, product_lookup, warehouse_lookup) for row in rows]
        warehouse_ids = [item.get("warehouse_id") or item.get("location_id") for item in items]
        queued_docs = list(restock_queue_collection.find(
            queue_scope_query(current_user, product_ids=product_ids, warehouse_ids=warehouse_ids),
            {"_id": 0, "product_id": 1, "warehouse_id": 1, "location_id": 1, "status": 1, "queue_status": 1}
        )) if product_ids else []
        queued_status = {
            f"{doc.get('product_id')}::{doc.get('warehouse_id') or doc.get('location_id') or ''}": doc.get("status") or doc.get("queue_status") or "Queued"
            for doc in queued_docs
        }
        for item in items:
            item_key = f"{item.get('product_id')}::{item.get('warehouse_id') or item.get('location_id') or ''}"
            item["queue_status"] = queued_status.get(item_key, "Not Queued")
        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
            "has_next": page * limit < total,
            "has_previous": page > 1,
            "items": items
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch inventory health details: {exc}") from exc


@app.post("/inventory/restock-all", tags=["Inventory"])
def restock_all_inventory(
    payload: Optional[dict] = Body(default=None),
    current_user: dict = Depends(get_current_user)
):
    """Immediately restock all low/out-of-stock inventory rows in the scoped warehouse."""
    check_role(current_user["role"], ["Admin", "Manager"])
    requested_warehouse = (payload or {}).get("warehouse_id")
    warehouse_id = requested_warehouse.strip().upper() if isinstance(requested_warehouse, str) and requested_warehouse.strip() else None
    base_query = scoped_warehouse_filter(current_user, warehouse_id)
    query = restock_eligible_inventory_query(base_query)
    now = datetime.now(timezone.utc)
    started = time.perf_counter()

    try:
        inventory_rows = list(warehouse_inventory_collection.find(
            query,
            {
                "_id": 1,
                "inventory_id": 1,
                "product_id": 1,
                "warehouse_id": 1,
                "location_id": 1,
                "warehouse_name": 1,
                "location": 1,
                "state": 1,
                "quantity": 1,
                "reorder_level": 1
            }
        ).sort([("warehouse_id", 1), ("quantity", 1), ("product_id", 1)]))

        total_eligible = len(inventory_rows)
        if not inventory_rows:
            return {
                "message": "No eligible products require restocking.",
                "total_eligible": 0,
                "success_count": 0,
                "failure_count": 0,
                "total_quantity_added": 0,
                "total_purchase_cost": 0,
                "results": []
            }

        product_ids = sorted({row.get("product_id") for row in inventory_rows if row.get("product_id")})
        product_lookup = {
            product.get("product_id"): product
            for product in products_collection.find(
                {"product_id": {"$in": product_ids}},
                {
                    "_id": 1,
                    "product_id": 1,
                    "product_name": 1,
                    "supplier_id": 1,
                    "unit_cost": 1,
                    "reorder_level": 1
                }
            )
        }

        supplier_ids = sorted({
            product.get("supplier_id")
            for product in product_lookup.values()
            if product.get("supplier_id")
        })
        valid_supplier_ids = {
            supplier.get("supplier_id")
            for supplier in suppliers_collection.find(
                {"supplier_id": {"$in": supplier_ids}} if supplier_ids else {"supplier_id": {"$in": []}},
                {"_id": 0, "supplier_id": 1}
            )
        }

        inventory_ops = []
        product_ops = []
        purchase_ops = []
        history_ops = []
        alert_ops = []
        notification_ops = []
        queue_delete_filters = []
        results = []
        success_pairs = []
        total_quantity_added = 0
        total_purchase_cost = 0.0
        batch_id = f"RST-{uuid4().hex.upper()}"

        for row in inventory_rows:
            product_id = row.get("product_id")
            warehouse_for_row = row.get("warehouse_id") or row.get("location_id")
            product = product_lookup.get(product_id)
            current_stock = int(row.get("quantity") or 0)
            reorder_level = int(row.get("reorder_level") or (product or {}).get("reorder_level") or 0)

            if not product:
                results.append({"product_id": product_id, "warehouse_id": warehouse_for_row, "status": "failed", "reason": "Product master record not found."})
                continue
            if not warehouse_for_row:
                results.append({"product_id": product_id, "warehouse_id": None, "status": "failed", "reason": "Warehouse ID missing."})
                continue
            if reorder_level <= 0:
                results.append({"product_id": product_id, "warehouse_id": warehouse_for_row, "status": "failed", "reason": "Reorder level missing or invalid."})
                continue

            supplier_id = product.get("supplier_id")
            if not supplier_id or supplier_id not in valid_supplier_ids:
                results.append({
                    "product_id": product_id,
                    "product_name": product.get("product_name") or product_id,
                    "warehouse_id": warehouse_for_row,
                    "status": "failed",
                    "reason": "Valid supplier is not assigned."
                })
                continue

            try:
                unit_cost = float(product.get("unit_cost") or 0)
            except (TypeError, ValueError):
                unit_cost = 0
            if unit_cost <= 0:
                results.append({
                    "product_id": product_id,
                    "product_name": product.get("product_name") or product_id,
                    "warehouse_id": warehouse_for_row,
                    "status": "failed",
                    "reason": "Unit cost missing or invalid."
                })
                continue

            restock_quantity = int(max((reorder_level * 2) - current_stock, reorder_level, 1))
            new_stock = current_stock + restock_quantity
            total_cost = round(restock_quantity * unit_cost, 2)
            purchase_id = generate_purchase_id()
            transaction_id = f"TXN-{uuid4().hex[:12].upper()}"
            location = location_fields(warehouse_for_row)
            product_name = product.get("product_name") or product_id

            inventory_ops.append(UpdateOne(
                {"_id": row["_id"]},
                {"$inc": {"quantity": restock_quantity}, "$set": {"last_updated": now, "updated_at": now}}
            ))
            product_ops.append(UpdateOne(
                {"_id": product["_id"]},
                {"$inc": {"quantity": restock_quantity}, "$set": {"updated_at": now}}
            ))
            purchase_ops.append(InsertOne(purchase_document(
                purchase_id=purchase_id,
                product_id=product_id,
                product_name=product_name,
                supplier_id=supplier_id,
                quantity=restock_quantity,
                unit_cost=unit_cost,
                total_cost=total_cost,
                previous_stock=current_stock,
                current_stock=new_stock,
                purchased_by=current_user["sub"],
                role=current_user["role"],
                created_at=now,
                note="Automatic Restock All from Inventory Health",
                status="Completed",
                transaction_id=transaction_id,
                restock_batch_id=batch_id,
                **location
            )))
            movement = inventory_history_document(
                movement_id=f"MOV-{uuid4().hex.upper()}",
                product_id=product_id,
                product_name=product_name,
                movement_type="Stock In",
                quantity=restock_quantity,
                previous_stock=current_stock,
                current_stock=new_stock,
                performed_by=current_user["sub"],
                role=current_user["role"],
                created_at=now,
                note=f"Automatic restock all purchase {purchase_id}",
                **location
            )
            movement.update({"reference_type": "Purchase", "reference_id": purchase_id, "purchase_id": purchase_id, "restock_batch_id": batch_id})
            history_ops.append(InsertOne(movement))
            alert_ops.append(UpdateMany(
                {
                    "product_id": product_id,
                    "$or": [{"warehouse_id": warehouse_for_row}, {"location_id": warehouse_for_row}],
                    "status": {"$nin": ["Resolved", "Closed", "Completed"]}
                },
                {"$set": {"status": "Resolved", "resolved_at": now, "updated_at": now}}
            ))
            notification_ops.append(UpdateMany(
                {
                    "product_id": product_id,
                    "$or": [{"warehouse_id": warehouse_for_row}, {"location_id": warehouse_for_row}],
                    "type": {"$regex": "low|stock|inventory", "$options": "i"}
                },
                {"$set": {"is_read": True, "status": "Resolved", "updated_at": now}}
            ))
            queue_delete_filters.append({
                "product_id": product_id,
                "$or": [{"warehouse_id": warehouse_for_row}, {"location_id": warehouse_for_row}]
            })
            total_quantity_added += restock_quantity
            total_purchase_cost += total_cost
            success_pairs.append((product_id, warehouse_for_row))
            results.append({
                "product_id": product_id,
                "product_name": product_name,
                "warehouse_id": warehouse_for_row,
                "status": "success",
                "quantity_added": restock_quantity,
                "previous_stock": current_stock,
                "current_stock": new_stock,
                "purchase_id": purchase_id,
                "total_cost": total_cost
            })

        if inventory_ops:
            warehouse_inventory_collection.bulk_write(inventory_ops, ordered=False)
        if product_ops:
            products_collection.bulk_write(product_ops, ordered=False)
        if purchase_ops:
            purchases_collection.bulk_write(purchase_ops, ordered=False)
        if history_ops:
            inventory_history_collection.bulk_write(history_ops, ordered=False)
        if alert_ops:
            low_stock_alerts_collection.bulk_write(alert_ops, ordered=False)
        if notification_ops:
            notifications_collection.bulk_write(notification_ops, ordered=False)
        if queue_delete_filters:
            for index in range(0, len(queue_delete_filters), 100):
                restock_queue_collection.delete_many({"$or": queue_delete_filters[index:index + 100]})

        success_count = len(success_pairs)
        failure_count = max(total_eligible - success_count, 0)
        updated_counts = inventory_health_counts(base_query)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "message": f"{success_count} product(s) restocked successfully.",
            "total_eligible": total_eligible,
            "success_count": success_count,
            "failure_count": failure_count,
            "total_quantity_added": total_quantity_added,
            "total_purchase_cost": round(total_purchase_cost, 2),
            "restock_batch_id": batch_id,
            "updated_inventory_health": updated_counts,
            "elapsed_ms": elapsed_ms,
            "results": results[:250]
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to restock inventory: {exc}") from exc


@app.get("/inventory/restock-candidates", tags=["Inventory"])
def inventory_restock_candidates(
    warehouse_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=50),
    page_size: Optional[int] = Query(None, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    if page_size is not None:
        limit = min(page_size, 100)
    return inventory_health_attention(warehouse_id=warehouse_id, status="low stock", search=None, page=page, limit=limit, page_size=None, current_user=current_user)
@app.get("/users", tags=["Users"])
def get_users(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(25, ge=1, le=100, description="Users per page"),
    search: Optional[str] = Query(None, description="Search username, email, or role"),
    role: Optional[str] = Query(None, description="Filter by role"),
    warehouse_id: Optional[str] = Query(None, description="Filter by warehouse ID"),
    status: Optional[str] = Query(None, description="Filter by account status"),
    sort: str = Query("username", description="Sort field"),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin"]
    )

    try:
        query = {}
        if search:
            search = search.strip()
            query["$or"] = [
                {"username": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"role": {"$regex": search, "$options": "i"}}
            ]
        if role:
            query["role"] = role
        if warehouse_id:
            query["warehouse_id"] = warehouse_id
        if status:
            query["status"] = status

        sort_field = sort if sort in {"username", "email", "role", "created_at", "warehouse_id", "status"} else "username"
        skip = (page - 1) * limit
        total = users_collection.count_documents(query)

        warehouse_lookup = {
            warehouse.get("warehouse_id"): warehouse
            for warehouse in warehouses_collection.find(
                {},
                {"_id": 0, "warehouse_id": 1, "warehouse_name": 1, "city": 1, "state": 1, "status": 1}
            )
        }
        role_counts = {"Admin": 0, "Manager": 0, "Staff": 0}
        warehouse_counts = {}
        summary_cursor = users_collection.find(
            query,
            {"_id": 0, "role": 1, "warehouse_id": 1, "warehouse_name": 1, "status": 1}
        )
        for summary_user in summary_cursor:
            user_role = summary_user.get("role") or "Unknown"
            role_counts[user_role] = role_counts.get(user_role, 0) + 1
            if user_role == "Admin":
                continue
            user_warehouse_id = summary_user.get("warehouse_id")
            if not user_warehouse_id:
                continue
            warehouse = warehouse_lookup.get(user_warehouse_id, {})
            key = user_warehouse_id
            if key not in warehouse_counts:
                warehouse_counts[key] = {
                    "warehouse_id": user_warehouse_id,
                    "warehouse_name": warehouse.get("warehouse_name") or summary_user.get("warehouse_name") or user_warehouse_id,
                    "city": warehouse.get("city", ""),
                    "state": warehouse.get("state", ""),
                    "status": warehouse.get("status", "Active"),
                    "users": 0,
                    "managers": 0,
                    "staff": 0
                }
            warehouse_counts[key]["users"] += 1
            if user_role == "Manager":
                warehouse_counts[key]["managers"] += 1
            elif user_role == "Staff":
                warehouse_counts[key]["staff"] += 1

        users = users_collection.find(
            query,
            {"password": 0, "hashed_password": 0, "password_hash": 0, "reset_token": 0, "reset_token_expiry": 0}
        ).sort(sort_field, 1).skip(skip).limit(limit)
        items = [
            {
                **serialize_document(user),
                **location_details(user.get("location_id", "ALL")),
            }
            for user in users
        ]
        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
            "has_next": page * limit < total,
            "has_previous": page > 1,
            "items": items,
            "summary": {
                "total_users": total,
                "role_counts": role_counts,
                "warehouses": sorted(warehouse_counts.values(), key=lambda item: item.get("warehouse_id", ""))
            }
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch users: {exc}"
        )

@app.put("/users/{username}/location", tags=["Users"])
def assign_user_location(
    username: str,
    location_id: str,
    current_role: str = Depends(get_current_role)
):
    """Admin-only assignment of a Staff or Manager user to a location."""
    check_role(current_role, ["Admin"])
    if location_id != "ALL" and not locations_collection.find_one({
        "location_id": location_id
    }):
        not_found("Location not found")

    try:
        result = users_collection.update_one(
            {"username": username},
            {"$set": location_fields(location_id)}
        )
        if not result.matched_count:
            not_found("User not found")
        return {
            "message": "User location assigned successfully",
            "username": username,
            **location_details(location_id),
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to assign user location: {exc}"
        ) from exc


@app.post("/products", tags=["Products"])
async def add_product(
    request: Request,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    payload, product_image_upload = await read_product_request(request)
    product = ProductCreate(**payload)

    try:
        existing_product = products_collection.find_one({
            "product_id": product.product_id
        })

        if existing_product:
            conflict("Product ID Already Exists")

        if product.category_id:
            category = categories_collection.find_one({
                "category_id": product.category_id
            })

            if not category:
                not_found("Category Not Found")

        if product.supplier_id:
            supplier = suppliers_collection.find_one({
                "supplier_id": product.supplier_id
            })

            if not supplier:
                raise HTTPException(status_code=400, detail="Supplier ID does not exist.")

        product_location = location_fields(user_location_id(current_user) or "ALL")
        product_image = save_product_image(product_image_upload, product.product_id)
        new_product = product_document(
            product_id=product.product_id,
            product_name=product.product_name,
            quantity=product.quantity,
            price=product.price,
            unit_cost=product.unit_cost,
            reorder_level=product.reorder_level,
            category_id=product.category_id,
            supplier_id=product.supplier_id,
            barcode_value=product.barcode_value,
            qr_code_value=product.qr_code_value,
            product_image=product_image,
            **product_location
        )

        products_collection.insert_one(new_product)

        movement_id = create_inventory_history(
            product=new_product,
            movement_type="Initial Stock",
            movement_quantity=product.quantity,
            previous_stock=0,
            current_stock=product.quantity,
            current_user=current_user,
            note="Product created"
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add product: {exc}"
        )

    return {
        "message": "Product Added Successfully",
        "product_id": product.product_id,
        "inventory_movement_id": movement_id,
        "initial_stock": product.quantity,
        "product_image": product_image
    }
@app.get("/products", tags=["Products"])
def view_products(
    search: Optional[str] = Query(
        None,
        description="Search products by product ID or product name"
    ),
    category_id: Optional[str] = Query(
        None,
        description="Filter products by category ID"
    ),
    supplier_id: Optional[str] = Query(
        None,
        description="Filter products by supplier ID"
    ),
    warehouse_id: Optional[str] = Query(
        None,
        description="Filter products by warehouse ID"
    ),
    page: int = Query(
        1,
        ge=1,
        description="Page number"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Products per page"
    ),
    current_user: dict = Depends(get_current_user)
):

    try:
        query = location_query(current_user)

        if search:
            search = search.strip()

        if search:
            query["$or"] = [
                {
                    "product_id": {
                        "$regex": search,
                        "$options": "i"
                    }
                },
                {
                    "product_name": {
                        "$regex": search,
                        "$options": "i"
                    }
                }
            ]

        if category_id:
            query["category_id"] = category_id

        if supplier_id:
            query["supplier_id"] = supplier_id

        if warehouse_id:
            requested_warehouse_id = warehouse_id.strip().upper()
            if current_user.get("role") != "Admin":
                assigned_warehouse_id = (current_user.get("warehouse_id") or "").strip().upper()
                if requested_warehouse_id != assigned_warehouse_id:
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied for selected warehouse"
                    )
            query["warehouse_id"] = requested_warehouse_id

        skip = (page - 1) * limit
        total = products_collection.count_documents(query)
        total_pages = (total + limit - 1) // limit
        products = products_collection.find(query).sort(
            "product_id",
            1
        ).skip(skip).limit(limit)

        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
            "items": [enrich_product(product) for product in products]
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch products: {exc}"
        )


@app.get("/products/scan/{code}", tags=["Products"])
def scan_product(
    code: str,
    current_user: dict = Depends(get_current_user)
):
    code = validate_required(code, "Scanned code")
    query = {
        **location_query(current_user),
        "$or": [
            {"product_id": code},
            {"barcode_value": code},
            {"qr_code_value": code}
        ]
    }

    try:
        product = products_collection.find_one(query)
        if not product:
            not_found("Product Not Found For Scanned Code")
        return {
            "message": "Product found",
            "code": code,
            "product": enrich_product(product)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to scan product: {exc}"
        )



@app.get("/products/{product_id}", tags=["Products"])
def get_product_detail(
    product_id: str,
    warehouse_id: Optional[str] = Query(None, description="Filter product by warehouse ID"),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])
    product_id = validate_required(product_id, "Product ID")

    try:
        query = scoped_product_query(product_id, current_user)
        if warehouse_id:
            requested_warehouse_id = warehouse_id.strip().upper()
            if current_user.get("role") != "Admin":
                assigned_warehouse_id = (current_user.get("warehouse_id") or current_user.get("location_id") or "").strip().upper()
                if requested_warehouse_id != assigned_warehouse_id:
                    raise HTTPException(status_code=403, detail="Access denied for selected warehouse")
            query["$or"] = [
                {"warehouse_id": requested_warehouse_id},
                {"location_id": requested_warehouse_id}
            ]

        product = products_collection.find_one(query)
        if not product and current_user.get("role") == "Admin" and warehouse_id:
            product = products_collection.find_one({"product_id": product_id})
        if not product:
            not_found("Product Not Found")
        return {"product": enrich_product(product)}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch product: {exc}")


@app.put("/products/{product_id}", tags=["Products"])
async def update_product(
    product_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    product_id = validate_required(product_id, "Product ID")
    payload, product_image_upload = await read_product_request(request, include_product_id=False)
    product_update = ProductCreate(product_id=product_id, **payload)
    product_name = product_update.product_name
    quantity = product_update.quantity
    price = product_update.price
    unit_cost = product_update.unit_cost
    reorder_level = product_update.reorder_level
    category_id = product_update.category_id
    supplier_id = product_update.supplier_id
    barcode_value = product_update.barcode_value
    qr_code_value = product_update.qr_code_value

    if quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity cannot be negative")

    if price <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than zero")

    if unit_cost is not None and unit_cost < 0:
        raise HTTPException(status_code=400, detail="Unit cost cannot be negative")

    if reorder_level < 0:
        raise HTTPException(status_code=400, detail="Reorder level cannot be negative")

    try:
        product = products_collection.find_one(scoped_product_query(product_id, current_user))

        if not product:
            not_found("Product Not Found")

        if category_id:
            category = categories_collection.find_one({"category_id": category_id})
            if not category:
                not_found("Category Not Found")

        if supplier_id:
            supplier = suppliers_collection.find_one({"supplier_id": supplier_id})
            if not supplier:
                raise HTTPException(status_code=400, detail="Supplier ID does not exist.")

        update_data = {
            "product_name": product_name,
            "quantity": quantity,
            "price": price,
            "reorder_level": reorder_level,
            "barcode_value": barcode_value or product_id,
            "qr_code_value": qr_code_value or product_id
        }

        if unit_cost is not None:
            update_data["unit_cost"] = unit_cost

        if category_id is not None:
            update_data["category_id"] = category_id

        if supplier_id is not None:
            update_data["supplier_id"] = supplier_id

        product_image = None
        if product_image_upload:
            product_image = save_product_image(product_image_upload, product_id)
            update_data["product_image"] = product_image

        products_collection.update_one(
            scoped_product_query(product_id, current_user),
            {"$set": update_data}
        )

        previous_stock = product["quantity"]

        if previous_stock != quantity:
            updated_product = {
                **product,
                "product_name": product_name,
                "quantity": quantity,
                "product_image": product_image or product.get("product_image")
            }
            stock_delta = quantity - previous_stock
            movement_type = "Stock Adjustment Increase" if stock_delta > 0 else "Stock Adjustment Decrease"

            create_inventory_history(
                product=updated_product,
                movement_type=movement_type,
                movement_quantity=abs(stock_delta),
                previous_stock=previous_stock,
                current_stock=quantity,
                current_user=current_user,
                note="Product quantity updated"
            )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update product: {exc}"
        )

    return {
        "message": "Product Updated Successfully",
        "product_id": product_id,
        "previous_stock": previous_stock,
        "current_stock": quantity,
        "product_image": product_image or product.get("product_image")
    }
@app.delete("/products/{product_id}", tags=["Products"])
def delete_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    product_id = validate_required(product_id, "Product ID")

    try:
        result = products_collection.delete_one(
            scoped_product_query(product_id, current_user)
        )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to delete product: {exc}"
        )

    if result.deleted_count == 0:
        not_found("Product Not Found")

    return {
        "message": "Product Deleted Successfully"
    }

def build_restock_queue_item(product, quantity=None, unit_cost=None, supplier_id=None, warehouse_id=None):
    metadata = get_product_metadata(product)
    selected_supplier_id = supplier_id or product.get("supplier_id")
    if selected_supplier_id and selected_supplier_id != metadata.get("supplier_id"):
        supplier = suppliers_collection.find_one({"supplier_id": selected_supplier_id})
        if supplier:
            metadata.update({
                "supplier_id": selected_supplier_id,
                "supplier": supplier.get("supplier_name"),
                "supplier_email": supplier.get("email"),
                "supplier_phone": supplier.get("phone"),
                "supplier_address": supplier.get("address")
            })
    current_stock = int(product.get("quantity", 0) or 0)
    reorder_level = int(product.get("reorder_level", 35) or 35)
    suggested_quantity = int(quantity or max(reorder_level * 2 - current_stock, reorder_level, 1))
    selected_unit_cost = int(unit_cost or product.get("unit_cost") or max(1, round(float(product.get("price", 1) or 1) * 0.62)))
    target_warehouse = warehouse_id or product.get("warehouse_id") or product.get("location_id", "ALL")
    return {
        "product_id": product["product_id"],
        "product_name": product.get("product_name", product["product_id"]),
        "current_stock": current_stock,
        "reorder_level": reorder_level,
        "suggested_quantity": suggested_quantity,
        "supplier_id": metadata.get("supplier_id"),
        "supplier": metadata.get("supplier"),
        "unit_cost": selected_unit_cost,
        "total_cost": suggested_quantity * selected_unit_cost,
        "queue_status": "Queued",
        "status": "Queued",
        "location_id": target_warehouse,
        "warehouse_id": target_warehouse,
        "updated_at": datetime.now(timezone.utc)
    }


def build_restock_queue_item_from_inventory(inventory, product):
    product = product or {}
    warehouse_id = inventory.get("warehouse_id") or inventory.get("location_id") or product.get("warehouse_id") or product.get("location_id") or "ALL"
    source = {
        **product,
        "product_id": inventory.get("product_id"),
        "product_name": product.get("product_name") or inventory.get("product_name") or inventory.get("product_id"),
        "quantity": int(inventory.get("quantity") or 0),
        "reorder_level": int(inventory.get("reorder_level") or product.get("reorder_level") or 35),
        "warehouse_id": warehouse_id,
        "location_id": warehouse_id
    }
    return build_restock_queue_item(source)

def purchase_location_scope(current_user):
    if current_user.get("role") == "Admin":
        return {}
    location_id = current_user.get("warehouse_id") or current_user.get("location_id") or "ALL"
    return {
        "$or": [
            {"location_id": location_id},
            {"warehouse_id": location_id},
            {"location_id": {"$exists": False}}
        ]
    }
def restock_selection_filter(request, current_user):
    query = location_query(current_user)
    item_filters = []
    for item in getattr(request, "items", []) or []:
        item_query = {"product_id": item.product_id}
        if item.warehouse_id:
            item_query["$or"] = [
                {"warehouse_id": item.warehouse_id},
                {"location_id": item.warehouse_id}
            ]
        item_filters.append(item_query)
    if item_filters:
        query["$or"] = item_filters
    elif request.product_ids:
        query["product_id"] = {"$in": request.product_ids}
    return query


def update_restock_queue_status(product_ids, current_user, status, items=None, extra_fields=None):
    if not product_ids and not items:
        return 0
    class Selection:
        pass
    request = Selection()
    request.product_ids = product_ids or []
    request.items = items or []
    update_fields = {"status": status, "queue_status": status, "updated_at": utc_now()}
    if extra_fields:
        update_fields.update(extra_fields)
    result = restock_queue_collection.update_many(restock_selection_filter(request, current_user), {"$set": update_fields})
    return result.modified_count


def remove_restock_queue_products(product_ids, current_user, items=None):
    if not product_ids and not items:
        return 0
    class Selection:
        pass
    request = Selection()
    request.product_ids = product_ids or []
    request.items = items or []
    result = restock_queue_collection.delete_many(restock_selection_filter(request, current_user))
    return result.deleted_count


@app.get("/restock-queue", tags=["Inventory"])
def get_restock_queue(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=50),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        query = location_query(current_user)
        if search:
            clean_search = search.strip()
            query["$or"] = [
                {"product_id": {"$regex": clean_search, "$options": "i"}},
                {"product_name": {"$regex": clean_search, "$options": "i"}}
            ]
        skip = (page - 1) * limit
        total = restock_queue_collection.count_documents(query)
        items = list(restock_queue_collection.find(query, {"_id": 0}).sort(
            "updated_at",
            -1
        ).skip(skip).limit(limit))
        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
            "has_next": page * limit < total,
            "has_previous": page > 1,
            "items": serialize_documents(items)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch restock queue: {exc}"
        )


@app.post("/restock-queue", tags=["Inventory"])
def upsert_restock_queue(
    request: RestockQueueBulkRequest,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        saved_items = []
        for item in request.items:
            target_warehouse = item.warehouse_id or (None if current_user["role"] == "Admin" else user_location_id(current_user))
            if target_warehouse:
                scoped_warehouse_filter(current_user, target_warehouse)
            product = products_collection.find_one({"product_id": item.product_id})
            if not product:
                not_found(f"Product Not Found: {item.product_id}")
            inventory = None
            if target_warehouse:
                inventory = warehouse_inventory_collection.find_one(
                    {"product_id": item.product_id, "warehouse_id": target_warehouse},
                    {"_id": 0, "product_id": 1, "warehouse_id": 1, "quantity": 1, "reorder_level": 1}
                )
            if inventory:
                queue_item = build_restock_queue_item_from_inventory(inventory, product)
                if item.quantity:
                    queue_item["suggested_quantity"] = int(item.quantity)
                    queue_item["total_cost"] = int(item.quantity) * int(item.unit_cost or queue_item.get("unit_cost") or 1)
                if item.unit_cost:
                    queue_item["unit_cost"] = int(item.unit_cost)
                    queue_item["total_cost"] = int(queue_item["suggested_quantity"]) * int(item.unit_cost)
                if item.supplier_id:
                    queue_item = build_restock_queue_item(
                        {**product, "quantity": inventory.get("quantity"), "reorder_level": inventory.get("reorder_level"), "warehouse_id": inventory.get("warehouse_id")},
                        quantity=queue_item.get("suggested_quantity"),
                        unit_cost=queue_item.get("unit_cost"),
                        supplier_id=item.supplier_id,
                        warehouse_id=inventory.get("warehouse_id")
                    )
            else:
                queue_item = build_restock_queue_item(
                    product,
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                    supplier_id=item.supplier_id,
                    warehouse_id=target_warehouse
                )
            restock_queue_collection.update_one(
                {
                    "product_id": item.product_id,
                    "location_id": queue_item["location_id"]
                },
                {"$set": queue_item},
                upsert=True
            )
            saved_items.append(queue_item)
        return {
            "message": "Restock queue updated",
            "count": len(saved_items),
            "items": saved_items
        }
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update restock queue: {exc}"
        )


@app.post("/restock-queue/add-all", tags=["Inventory"])
def add_all_restock_queue(
    request: RestockQueueAddAllRequest,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        base_query = scoped_warehouse_filter(current_user, request.warehouse_id)
        query = restock_eligible_inventory_query(base_query)
        total_eligible = warehouse_inventory_collection.count_documents(query)
        batch_size = 50
        added_count = 0
        already_queued_count = 0
        failed_count = 0

        cursor = warehouse_inventory_collection.find(
            query,
            {"_id": 0, "product_id": 1, "warehouse_id": 1, "quantity": 1, "reorder_level": 1}
        ).sort([("warehouse_id", 1), ("quantity", 1), ("product_id", 1)])

        batch = []

        def process_batch(rows):
            nonlocal added_count, already_queued_count, failed_count
            if not rows:
                return
            product_ids = [row.get("product_id") for row in rows if row.get("product_id")]
            warehouse_ids = [row.get("warehouse_id") for row in rows if row.get("warehouse_id")]
            product_lookup = {
                product.get("product_id"): product
                for product in products_collection.find(
                    {"product_id": {"$in": product_ids}},
                    {"_id": 0, "product_id": 1, "product_name": 1, "category_id": 1, "supplier_id": 1, "price": 1, "unit_price": 1, "unit_cost": 1, "reorder_level": 1, "status": 1}
                )
            }
            existing_query = queue_scope_query(current_user, product_ids=product_ids, warehouse_ids=warehouse_ids)
            existing_query["status"] = {"$nin": ["Received", "Cancelled", "Removed"]}
            existing_docs = list(restock_queue_collection.find(
                existing_query,
                {"_id": 0, "product_id": 1, "warehouse_id": 1, "location_id": 1}
            ))
            existing_keys = {
                f"{doc.get('product_id')}::{doc.get('warehouse_id') or doc.get('location_id') or ''}"
                for doc in existing_docs
            }
            operations = []
            for inventory in rows:
                product_id = inventory.get("product_id")
                warehouse_id = inventory.get("warehouse_id") or "ALL"
                key = f"{product_id}::{warehouse_id}"
                if key in existing_keys:
                    already_queued_count += 1
                    continue
                product = product_lookup.get(product_id)
                if not product:
                    failed_count += 1
                    continue
                queue_item = build_restock_queue_item_from_inventory(inventory, product)
                operations.append(UpdateOne(
                    {"product_id": product_id, "location_id": queue_item["location_id"]},
                    {"$set": queue_item},
                    upsert=True
                ))
            if operations:
                result = restock_queue_collection.bulk_write(operations, ordered=False)
                added_count += int(result.upserted_count or 0) + int(result.modified_count or 0)

        for inventory in cursor:
            batch.append(inventory)
            if len(batch) >= batch_size:
                process_batch(batch)
                batch = []
        process_batch(batch)

        return {
            "message": "Eligible products added to restock queue.",
            "total_eligible": total_eligible,
            "added_count": added_count,
            "already_queued_count": already_queued_count,
            "failed_count": failed_count,
            "batch_size": batch_size,
            "batch_count": (total_eligible + batch_size - 1) // batch_size if total_eligible else 0
        }
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add eligible products to restock queue: {exc}"
        )

@app.post("/restock-queue/clear", tags=["Inventory"])
def clear_restock_queue(
    request: RestockQueueSelection,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        deleted_count = remove_restock_queue_products(request.product_ids, current_user, request.items)
        return {
            "message": "Restock queue cleared",
            "deleted_count": deleted_count
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to clear restock queue: {exc}"
        )


def raise_bulk_timeout_error(action, exc):
    print(f"{action} failed: {exc}")
    raise HTTPException(
        status_code=500,
        detail="Bulk stock update is taking too long. Please try again or process in smaller batches."
    )


def request_items_to_queue_items(items, current_user):
    product_ids = [item.product_id for item in items]
    products = list(products_collection.find({
        "product_id": {"$in": product_ids}
    }))
    products_by_id = {product["product_id"]: product for product in products}
    assigned_warehouse = None if current_user["role"] == "Admin" else user_location_id(current_user)
    queue_items = []
    for item in items:
        product = products_by_id.get(item.product_id)
        if not product:
            not_found(f"Product Not Found: {item.product_id}")
        warehouse_id = item.warehouse_id or assigned_warehouse or product.get("warehouse_id") or product.get("location_id") or "ALL"
        queue_items.append(build_restock_queue_item(
            product,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            supplier_id=item.supplier_id,
            warehouse_id=warehouse_id
        ))
    return queue_items


def bulk_create_or_update_pending_purchases(queue_items, current_user, note):
    if not queue_items:
        return {
            "message": "No products selected",
            "created_count": 0,
            "updated_count": 0,
            "duplicate_count": 0,
            "items": []
        }

    product_ids = sorted({item["product_id"] for item in queue_items if item.get("product_id")})
    products = list(products_collection.find({"product_id": {"$in": product_ids}}))
    products_by_id = {product["product_id"]: product for product in products}

    item_warehouse_ids = sorted({
        item.get("warehouse_id") or item.get("location_id") or (None if current_user.get("role") == "Admin" else user_location_id(current_user)) or "ALL"
        for item in queue_items
    })
    pending_query = {"status": "Pending", "product_id": {"$in": product_ids}}
    if item_warehouse_ids:
        pending_query["$or"] = [
            {"location_id": {"$in": item_warehouse_ids}},
            {"warehouse_id": {"$in": item_warehouse_ids}}
        ]
    pending_query.update(purchase_location_scope(current_user))
    existing_pending = list(purchases_collection.find(pending_query))
    existing_by_pair = {
        f"{purchase.get('product_id')}::{purchase.get('warehouse_id') or purchase.get('location_id') or 'ALL'}": purchase
        for purchase in existing_pending
    }

    now = utc_now()
    operations = []
    queue_updates = []
    response_items = []
    created_count = 0
    updated_count = 0

    for item in queue_items:
        product = products_by_id.get(item.get("product_id"))
        if not product:
            continue
        warehouse_id = item.get("warehouse_id") or item.get("location_id") or (None if current_user.get("role") == "Admin" else user_location_id(current_user)) or "ALL"
        if current_user.get("role") != "Admin" and warehouse_id not in {current_user.get("warehouse_id"), current_user.get("location_id")}:
            continue
        quantity = max(1, int(item.get("suggested_quantity") or item.get("quantity") or 1))
        unit_cost = max(1, int(item.get("unit_cost") or product.get("unit_cost") or max(1, round(product.get("price", 1) * 0.62))))
        total_cost = quantity * unit_cost
        supplier_id = item.get("supplier_id") or product.get("supplier_id")
        metadata = get_product_metadata(product)
        pair_key = f"{product['product_id']}::{warehouse_id}"
        existing = existing_by_pair.get(pair_key)

        if existing:
            new_quantity = int(existing.get("quantity", 0) or 0) + quantity
            new_total = new_quantity * unit_cost
            operations.append(UpdateOne(
                {"_id": existing["_id"]},
                {"$set": {
                    "quantity": new_quantity,
                    "unit_cost": unit_cost,
                    "total_cost": new_total,
                    "current_stock": int(product.get("quantity", 0)),
                    "updated_at": now,
                    "note": note,
                    "location_id": warehouse_id,
                    "warehouse_id": warehouse_id
                }}
            ))
            purchase_id = existing.get("purchase_id")
            updated_count += 1
        else:
            purchase_id = generate_purchase_id()
            transaction_id = purchase_id
            previous_stock = int(product.get("quantity", 0))
            operations.append(InsertOne(purchase_document(
                purchase_id=purchase_id,
                product_id=product["product_id"],
                product_name=product["product_name"],
                supplier_id=supplier_id,
                quantity=quantity,
                unit_cost=unit_cost,
                total_cost=total_cost,
                previous_stock=previous_stock,
                current_stock=previous_stock,
                purchased_by=current_user["sub"],
                role=current_user["role"],
                created_at=now,
                note=note,
                transaction_id=transaction_id,
                date=now.date().isoformat(),
                status="Pending",
                received_at=None,
                inventory_movement_id=None,
                movement_type="Purchase Pending",
                operator_username=current_user["sub"],
                operator_role=current_user["role"],
                category_id=metadata["category_id"],
                category=metadata["category"],
                supplier=metadata["supplier"],
                supplier_email=metadata["supplier_email"],
                supplier_phone=metadata["supplier_phone"],
                supplier_address=metadata["supplier_address"],
                location_id=warehouse_id,
                warehouse_id=warehouse_id
            )))
            created_count += 1

        queue_updates.append(UpdateOne(
            {"product_id": product["product_id"], "$or": [{"warehouse_id": warehouse_id}, {"location_id": warehouse_id}]},
            {"$set": {
                "status": "Purchase Order Created",
                "queue_status": "Purchase Order Created",
                "purchase_id": purchase_id,
                "updated_at": now
            }}
        ))
        response_items.append({
            "purchase_id": purchase_id,
            "product_id": product["product_id"],
            "warehouse_id": warehouse_id,
            "quantity": quantity if not existing else int(existing.get("quantity", 0) or 0) + quantity,
            "unit_cost": unit_cost,
            "total_cost": total_cost if not existing else (int(existing.get("quantity", 0) or 0) + quantity) * unit_cost,
            "status": "Pending"
        })

    if operations:
        purchases_collection.bulk_write(operations, ordered=False)
    if queue_updates:
        restock_queue_collection.bulk_write(queue_updates, ordered=False)
    return {
        "message": (
            "Pending purchase orders already exist for some products and were updated."
            if updated_count else "Purchase orders created successfully"
        ),
        "created_count": created_count,
        "updated_count": updated_count,
        "duplicate_count": updated_count,
        "items": response_items
    }


@app.post("/restock-queue/bulk-stock-in", tags=["Inventory"])
def bulk_restock_queue_stock_in(
    request: RestockQueueSelection,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])

    try:
        query = restock_selection_filter(request, current_user)
        query["status"] = {"$nin": ["Received", "Purchase Order Created", "Cancelled", "Removed"]}
        queue_items = list(restock_queue_collection.find(query))
        if not queue_items:
            return {"message": "No products selected", "updated_count": 0, "items": []}

        product_ids = sorted({item["product_id"] for item in queue_items})
        products = list(products_collection.find(
            {"product_id": {"$in": product_ids}},
            {"_id": 1, "product_id": 1, "product_name": 1, "quantity": 1, "unit_cost": 1, "price": 1, "reorder_level": 1, "category_id": 1, "supplier_id": 1}
        ))
        products_by_id = {product["product_id"]: product for product in products}

        warehouse_ids = sorted({
            item.get("warehouse_id") or item.get("location_id")
            for item in queue_items
            if item.get("warehouse_id") or item.get("location_id")
        })
        inventory_query = {"product_id": {"$in": product_ids}}
        if warehouse_ids:
            inventory_query["warehouse_id"] = {"$in": warehouse_ids}
        scoped_inventory = scoped_warehouse_filter(current_user)
        inventory_query.update(scoped_inventory)
        inventory_rows = list(warehouse_inventory_collection.find(inventory_query))
        inventory_by_pair = {
            (row.get("product_id"), row.get("warehouse_id")): row
            for row in inventory_rows
        }

        warehouse_lookup = warehouse_label_map()
        now = utc_now()
        inventory_updates = []
        product_increments = {}
        history_documents = []
        updated = []

        for item in queue_items:
            product_id = item.get("product_id")
            product = products_by_id.get(product_id)
            if not product:
                continue
            warehouse_id = item.get("warehouse_id") or item.get("location_id") or product.get("warehouse_id") or product.get("location_id")
            if not warehouse_id or warehouse_id == "ALL":
                warehouse_id = current_user.get("warehouse_id") or current_user.get("location_id") or "ALL"
            if current_user.get("role") != "Admin" and warehouse_id != current_user.get("warehouse_id") and warehouse_id != current_user.get("location_id"):
                continue

            quantity = max(1, int(item.get("suggested_quantity") or item.get("quantity") or 1))
            inventory = inventory_by_pair.get((product_id, warehouse_id)) or {}
            previous_stock = int(inventory.get("quantity") if inventory else product.get("quantity", 0) or 0)
            current_stock = previous_stock + quantity
            reorder_level = int(inventory.get("reorder_level") or item.get("reorder_level") or product.get("reorder_level") or 35)
            warehouse = warehouse_lookup.get(warehouse_id, {})
            movement_id = f"MOV-{uuid4().hex.upper()}"

            inventory_updates.append(UpdateOne(
                {"product_id": product_id, "warehouse_id": warehouse_id},
                {
                    "$inc": {"quantity": quantity},
                    "$set": {
                        "product_name": product.get("product_name"),
                        "reorder_level": reorder_level,
                        "last_updated": now,
                        "warehouse_name": warehouse.get("warehouse_name") or item.get("warehouse_name") or warehouse_id,
                        "location": warehouse.get("city") or warehouse.get("location") or item.get("location"),
                        "state": warehouse.get("state") or item.get("state")
                    },
                    "$setOnInsert": {
                        "inventory_id": f"INV-{uuid4().hex.upper()}",
                        "created_at": now
                    }
                },
                upsert=True
            ))
            product_increments[product_id] = product_increments.get(product_id, 0) + quantity
            history_documents.append(inventory_history_document(
                movement_id=movement_id,
                product_id=product_id,
                product_name=product.get("product_name"),
                movement_type="Restock",
                quantity=quantity,
                previous_stock=previous_stock,
                current_stock=current_stock,
                performed_by=current_user["sub"],
                role=current_user["role"],
                created_at=now,
                note="Immediate restock from restock queue",
                **location_fields(warehouse_id)
            ))
            updated.append({
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "quantity": quantity,
                "previous_stock": previous_stock,
                "current_stock": current_stock,
                "inventory_movement_id": movement_id
            })

        if inventory_updates:
            warehouse_inventory_collection.bulk_write(inventory_updates, ordered=False)
        product_updates = [
            UpdateOne({"_id": products_by_id[product_id]["_id"]}, {"$inc": {"quantity": quantity}})
            for product_id, quantity in product_increments.items()
            if product_id in products_by_id and quantity > 0
        ]
        if product_updates:
            products_collection.bulk_write(product_updates, ordered=False)
        if history_documents:
            inventory_history_collection.insert_many(history_documents, ordered=False)
        queue_ids = [item["_id"] for item in queue_items if item.get("_id")]
        if queue_ids:
            restock_queue_collection.delete_many({"_id": {"$in": queue_ids}})
        return {
            "message": "Inventory updated successfully",
            "updated_count": len(updated),
            "items": updated
        }
    except PyMongoError as exc:
        raise_bulk_timeout_error("Bulk restock", exc)




@app.post("/inventory/restock/bulk", tags=["Inventory"])
def inventory_restock_bulk(
    request: RestockQueueSelection,
    current_user: dict = Depends(get_current_user)
):
    return bulk_restock_queue_stock_in(request, current_user)


@app.post("/purchases/bulk", tags=["Purchases"])
def bulk_create_purchase_orders(
    request: RestockQueueBulkRequest,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])

    try:
        queue_items = request_items_to_queue_items(request.items, current_user)
        return bulk_create_or_update_pending_purchases(
            queue_items,
            current_user,
            "Created from restock queue"
        )
    except PyMongoError as exc:
        raise_bulk_timeout_error("Bulk purchase order creation", exc)


@app.post("/restock-queue/purchase-all", tags=["Purchases"])
def purchase_all_restock_queue_items(current_user: dict = Depends(get_current_user)):

    check_role(current_user["role"], ["Admin", "Manager"])

    try:
        queue_query = location_query(current_user)
        queue_query["status"] = {"$nin": ["Received", "Purchase Order Created", "Cancelled", "Removed"]}
        queue_items = list(restock_queue_collection.find(queue_query))
        if not queue_items:
            return {
                "message": "No restock queue items found",
                "created_count": 0,
                "updated_count": 0,
                "duplicate_count": 0,
                "items": []
            }
        return bulk_create_or_update_pending_purchases(
            queue_items,
            current_user,
            "Created by Purchase All Products"
        )
    except PyMongoError as exc:
        raise_bulk_timeout_error("Purchase all restock queue items", exc)


@app.post("/purchases", tags=["Purchases"])
def add_purchase(
    purchase: PurchaseCreate,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        requested_warehouse_id = (purchase.warehouse_id or current_user.get("warehouse_id") or current_user.get("location_id") or "").strip().upper()
        if requested_warehouse_id and current_user.get("role") != "Admin":
            assigned_warehouse_id = (current_user.get("warehouse_id") or current_user.get("location_id") or "").strip().upper()
            if requested_warehouse_id != assigned_warehouse_id:
                raise HTTPException(status_code=403, detail="Access denied for selected warehouse")

        product_query = {"product_id": purchase.product_id}
        product = products_collection.find_one(product_query)

        if not product:
            not_found("Product Not Found")

        warehouse = warehouses_collection.find_one({"warehouse_id": requested_warehouse_id}, {"_id": 0}) if requested_warehouse_id else None
        warehouse_name_value = (
            (warehouse or {}).get("warehouse_name")
            or product.get("warehouse_name")
            or product.get("location")
            or current_user.get("warehouse_name")
            or current_user.get("location")
            or requested_warehouse_id
        )

        supplier_id = purchase.supplier_id or product.get("supplier_id")

        if supplier_id:
            supplier = suppliers_collection.find_one({
                "supplier_id": supplier_id
            })

            if not supplier:
                raise HTTPException(status_code=400, detail="Supplier ID does not exist.")

        previous_stock = product["quantity"]
        requested_status = (purchase.status or "Completed").strip().title()
        if requested_status not in {"Pending", "Completed"}:
            raise HTTPException(
                status_code=400,
                detail="Purchase status must be Pending or Completed"
            )
        current_stock = (
            previous_stock
            if requested_status == "Pending"
            else previous_stock + purchase.quantity
        )
        total_cost = purchase.quantity * purchase.unit_cost
        created_at = (
            parse_report_date(purchase.purchase_date, "purchase_date")
            if purchase.purchase_date
            else datetime.now(timezone.utc)
        )
        purchase_id = (
            purchase.purchase_id.strip()
            if purchase.purchase_id
            else generate_purchase_id()
        )

        existing_purchase = purchases_collection.find_one({
            "purchase_id": purchase_id
        })

        if existing_purchase:
            conflict("Purchase ID Already Exists")

        if requested_status == "Completed":
            products_collection.update_one(
                product_query,
                {
                    "$set": {
                        "quantity": current_stock
                    }
                }
            )

        metadata = get_product_metadata(product)
        if supplier_id and supplier:
            metadata.update({
                "supplier_id": supplier_id,
                "supplier": supplier.get("supplier_name"),
                "supplier_email": supplier.get("email"),
                "supplier_phone": supplier.get("phone"),
                "supplier_address": supplier.get("address")
            })
        transaction_id = purchase.transaction_id or purchase_id
        result = purchases_collection.insert_one(
            purchase_document(
                purchase_id=purchase_id,
                product_id=product["product_id"],
                product_name=product["product_name"],
                supplier_id=supplier_id,
                quantity=purchase.quantity,
                unit_cost=purchase.unit_cost,
                total_cost=total_cost,
                previous_stock=previous_stock,
                current_stock=current_stock,
                purchased_by=current_user["sub"],
                role=current_user["role"],
                created_at=created_at,
                note=purchase.note,
                transaction_id=transaction_id,
                date=created_at.date().isoformat(),
                purchase_date=purchase.purchase_date or created_at.date().isoformat(),
                created_by=current_user["sub"],
                status=requested_status,
                received_at=created_at if requested_status == "Completed" else None,
                inventory_movement_id=None,
                movement_type="Purchase" if requested_status == "Completed" else "Purchase Pending",
                operator_username=current_user["sub"],
                operator_role=current_user["role"],
                category_id=metadata["category_id"],
                category=metadata["category"],
                supplier=metadata["supplier"],
                supplier_email=metadata["supplier_email"],
                supplier_phone=metadata["supplier_phone"],
                supplier_address=metadata["supplier_address"],
                location_id=requested_warehouse_id or product.get("location_id") or current_user.get("location_id", "ALL"),
                warehouse_id=requested_warehouse_id or product.get("warehouse_id") or product.get("location_id") or current_user.get("warehouse_id") or current_user.get("location_id"),
                warehouse_name=warehouse_name_value,
                location=(warehouse or {}).get("city") or product.get("location") or current_user.get("location"),
                state=(warehouse or {}).get("state") or product.get("state") or current_user.get("state")
            )
        )

        purchase_items_collection.insert_one({
            "purchase_item_id": f"PITEM-{uuid4().hex.upper()}",
            "purchase_id": purchase_id,
            "transaction_id": transaction_id,
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "supplier_id": supplier_id,
            "supplier": metadata.get("supplier"),
            "quantity": purchase.quantity,
            "unit_cost": purchase.unit_cost,
            "total_cost": total_cost,
            "status": requested_status,
            "created_at": created_at,
            "date": created_at.date().isoformat(),
            "created_by": current_user["sub"],
            "role": current_user["role"],
            "location_id": requested_warehouse_id or product.get("location_id") or current_user.get("location_id", "ALL"),
            "warehouse_id": requested_warehouse_id or product.get("warehouse_id") or product.get("location_id") or current_user.get("warehouse_id") or current_user.get("location_id"),
            "warehouse_name": warehouse_name_value
        })

        movement_id = None
        if requested_status == "Completed":
            movement_id = create_inventory_history(
                product=product,
                movement_type="Purchase",
                movement_quantity=purchase.quantity,
                previous_stock=previous_stock,
                current_stock=current_stock,
                current_user=current_user,
                note=purchase.note
            )
            purchases_collection.update_one(
                {"_id": result.inserted_id},
                {"$set": {"inventory_movement_id": movement_id}}
            )

        return {
            "message": (
                "Purchase Order Created Successfully"
                if requested_status == "Pending"
                else "Purchase Added Successfully"
            ),
            "purchase_id": purchase_id,
            "transaction_id": transaction_id,
            "inventory_movement_id": movement_id,
            "record_id": str(result.inserted_id),
            "product_id": purchase.product_id,
            "quantity": purchase.quantity,
            "unit_cost": purchase.unit_cost,
            "total_cost": total_cost,
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "status": requested_status
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add purchase: {exc}"
        )


@app.get("/purchases", tags=["Purchases"])
def view_purchases(
    product_id: Optional[str] = Query(
        None,
        description="Filter purchases by product ID"
    ),
    supplier_id: Optional[str] = Query(
        None,
        description="Filter purchases by supplier ID"
    ),
    status: Optional[str] = Query(
        None,
        description="Filter purchases by status: Pending or Completed"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    page: int = Query(
        1,
        ge=1,
        description="Page number"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Purchase records per page"
    ),
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        query = location_query(current_user)

        if product_id:
            query["product_id"] = product_id

        if supplier_id:
            query["supplier_id"] = supplier_id

        if status:
            clean_status = status.strip().title()
            if clean_status in {"Pending", "Completed"}:
                query["status"] = clean_status

        query.update(build_date_query(start_date, end_date))

        skip = (page - 1) * limit
        total = purchases_collection.count_documents(query)
        total_pages = (total + limit - 1) // limit
        purchases = purchases_collection.find(query).sort(
            "created_at",
            -1
        ).skip(skip).limit(limit)

        purchase_items = add_record_id_aliases(
            serialize_documents(purchases),
            "purchase_id"
        )

        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
            "items": purchase_items
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch purchases: {exc}"
        )


@app.post("/purchases/{purchase_id}/receive", tags=["Purchases"])
def receive_purchase(
    purchase_id: str,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        purchase = purchases_collection.find_one({
            "purchase_id": purchase_id,
            **purchase_location_scope(current_user)
        })

        if not purchase:
            raise HTTPException(status_code=404, detail="Purchase Not Found")

        if purchase.get("status", "Completed") == "Completed":
            raise HTTPException(
                status_code=400,
                detail="Purchase is already completed"
            )

        result = process_receive_purchase_batch([purchase], current_user, datetime.now(timezone.utc))
        if not result.get("processed_count"):
            raise HTTPException(
                status_code=400,
                detail="Unable to receive this purchase for the assigned warehouse"
            )
        item = result["items"][0]
        return {
            "message": "Inventory updated successfully",
            "purchase_id": item["purchase_id"],
            "product_id": item["product_id"],
            "warehouse_id": item.get("warehouse_id"),
            "quantity": item["quantity"],
            "previous_stock": item["previous_stock"],
            "current_stock": item["current_stock"],
            "status": "Completed",
            "inventory_movement_id": item["inventory_movement_id"]
        }

    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to receive purchase: {exc}"
        )


@app.get("/dashboard/refresh-statistics", tags=["Dashboards"])
def refresh_dashboard_statistics(
    range: str = Query("last_7_days"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        scoped_location = location_query(current_user)
        products = list(products_collection.find(scoped_location))
        total_products = len(products)
        low_stock_products = [
            product for product in products
            if int(product.get("quantity", 0)) <= int(product.get("reorder_level", 35))
        ]
        out_of_stock_products = [
            product for product in products
            if int(product.get("quantity", 0)) <= 0
        ]
        range_info = resolve_dashboard_date_range(range, start_date, end_date)
        purchases_value, purchase_count = aggregate_period_total(purchases_collection, scoped_location, range_info, "total_cost")
        sales_value, sales_count = aggregate_period_total(sales_collection, scoped_location, range_info, "total_amount")
        print(
            f"[dashboard] range={range_info['range']} "
            f"start_utc={range_info['start_utc'].isoformat()} "
            f"end_utc={range_info['end_utc'].isoformat()} "
            f"field=created_at sales_count={sales_count} purchases_count={purchase_count}",
            file=sys.stderr,
            flush=True
        )
        suppliers_total = suppliers_collection.count_documents(scoped_location)
        health_percentage = (
            round(((total_products - len(low_stock_products)) / total_products) * 100, 2)
            if total_products
            else 0
        )
        module_counts = {}
        for name, config in MODULE_COLLECTIONS.items():
            module_query = dict(scoped_location) if config.get("scoped") else {}
            module_counts[name] = config["collection"].count_documents(module_query)

        return {
            "total_products": total_products,
            "total_sales": sales_value,
            "total_sales_records": sales_count,
            "total_purchases": purchases_value,
            "total_purchase_records": purchase_count,
            "total_suppliers": suppliers_total,
            "low_stock_items": len(low_stock_products),
            "out_of_stock_items": len(out_of_stock_products),
            "inventory_health_percentage": health_percentage,
            "restock_queue_items": restock_queue_collection.count_documents(scoped_location),
            "module_counts": module_counts,
            "open_low_stock_alerts": low_stock_alerts_collection.count_documents({**scoped_location, "status": "Open"}),
            "unread_notifications": notifications_collection.count_documents({**scoped_location, "is_read": False}),
            "range": range_info["range"],
            "start_date": range_info["start_utc"].isoformat(),
            "end_date": range_info["end_utc"].isoformat()
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to refresh dashboard statistics: {exc}"
        )


@app.get("/dashboard/overview", tags=["Dashboards"])
def dashboard_overview(
    range: str = Query("last_7_days"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager", "Staff"])

    try:
        scoped_location = location_query(current_user)
        range_info = resolve_dashboard_date_range(range, start_date, end_date)
        since = range_info["previous_start_utc"]
        low_stock_query = {
            **scoped_location,
            "$expr": {
                "$lte": ["$quantity", {"$ifNull": ["$reorder_level", 35]}]
            }
        }
        overstock_query = {
            **scoped_location,
            "$expr": {
                "$gte": [
                    "$quantity",
                    {"$multiply": [{"$ifNull": ["$reorder_level", 35]}, 3]}
                ]
            }
        }
        product_projection = {
            "product_id": 1,
            "product_name": 1,
            "quantity": 1,
            "price": 1,
            "unit_cost": 1,
            "reorder_level": 1,
            "category_id": 1,
            "category": 1,
            "supplier_id": 1,
            "supplier": 1,
            "location_id": 1
        }
        low_stock_products = list(products_collection.find(
            low_stock_query,
            product_projection
        ).sort("quantity", 1).limit(500))
        overstocked_products = list(products_collection.find(
            overstock_query,
            product_projection
        ).sort("quantity", -1).limit(100))
        health_products_by_id = {
            product["product_id"]: product
            for product in [*low_stock_products, *overstocked_products]
        }

        recent_sales = list(sales_collection.find(
            {"created_at": {"$gte": since}, **scoped_location},
            {
                "sale_id": 1,
                "product_id": 1,
                "product_name": 1,
                "quantity": 1,
                "total_amount": 1,
                "sales_amount": 1,
                "category": 1,
                "category_id": 1,
                "customer_name": 1,
                "created_at": 1,
                "date": 1
            }
        ).sort("created_at", -1).limit(1000))
        recent_purchases = list(purchases_collection.find(
            {"created_at": {"$gte": since}, **scoped_location},
            {
                "purchase_id": 1,
                "product_id": 1,
                "product_name": 1,
                "quantity": 1,
                "unit_cost": 1,
                "total_cost": 1,
                "supplier_id": 1,
                "supplier": 1,
                "status": 1,
                "created_at": 1,
                "date": 1
            }
        ).sort("created_at", -1).limit(1000))
        suppliers = list(suppliers_collection.find(
            scoped_location,
            {"supplier_id": 1, "supplier_name": 1, "email": 1, "phone": 1, "location_id": 1}
        ).sort("supplier_name", 1).limit(1000))

        stock_by_category = list(products_collection.aggregate([
            {"$match": scoped_location},
            {
                "$group": {
                    "_id": "$category_id",
                    "product_count": {"$sum": 1},
                    "stock_units": {"$sum": "$quantity"},
                    "inventory_value": {
                        "$sum": {"$multiply": ["$quantity", "$price"]}
                    }
                }
            },
            {"$sort": {"stock_units": -1}},
            {"$limit": 12}
        ]))
        stock_by_category = [
            {
                "category_id": item["_id"],
                "category_name": item["_id"] or "Unassigned",
                "product_count": item["product_count"],
                "stock_units": item["stock_units"],
                "inventory_value": item["inventory_value"]
            }
            for item in stock_by_category
        ]
        recent_movements = inventory_history_collection.find(
            scoped_location,
            {
                "movement_id": 1,
                "product_id": 1,
                "product_name": 1,
                "movement_type": 1,
                "quantity": 1,
                "created_at": 1
            }
        ).sort("created_at", -1).limit(10)

        return {
            "range": range_info["range"],
            "start_date": range_info["start_utc"].isoformat(),
            "end_date": range_info["end_utc"].isoformat(),
            "summary": refresh_dashboard_statistics(range=range_info["range"], start_date=start_date, end_date=end_date, current_user=current_user),
            "inventory": {
                "stock_by_category": stock_by_category,
                "recent_movements": serialize_documents(recent_movements)
            },
            "health_products": serialize_documents(health_products_by_id.values()),
            "low_stock_products": serialize_documents(low_stock_products),
            "sales": serialize_documents(recent_sales),
            "purchases": serialize_documents(recent_purchases),
            "suppliers": serialize_documents(suppliers)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load dashboard overview: {exc}"
        )


@app.get("/purchases/pending-summary", tags=["Purchases"])
def purchase_pending_summary(current_user: dict = Depends(get_current_user)):

    check_role(current_user["role"], ["Admin", "Manager"])

    try:
        scoped_location = purchase_location_scope(current_user)

        pending_count = purchases_collection.count_documents({
            "status": "Pending",
            **scoped_location
        })

        completed_count = purchases_collection.count_documents({
            "status": "Completed",
            **scoped_location
        })

        return {
            "pending_count": pending_count,
            "processed_count": 0,
            "completed_count": completed_count,
            "updated_dashboard_stats": refresh_dashboard_statistics(current_user=current_user)
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch pending purchase summary: {exc}"
        )


def receive_pending_purchases(query, current_user, batch_size=500):
    pending_count = purchases_collection.count_documents(query)
    if not pending_count:
        return {
            "message": "No pending purchase orders to receive.",
            "pending_count": 0,
            "processed_count": 0,
            "completed_count": 0,
            "received_count": 0,
            "updated_dashboard_stats": refresh_dashboard_statistics(current_user=current_user),
            "items": []
        }

    processed_count = 0
    completed_count = 0
    processed_items = []
    restock_product_ids = set()
    received_at = datetime.now(timezone.utc)
    cursor = purchases_collection.find(
        query,
        {
            "purchase_id": 1,
            "product_id": 1,
            "product_name": 1,
            "quantity": 1,
            "location_id": 1,
            "warehouse_id": 1
        },
        batch_size=batch_size
    )

    batch = []
    for purchase in cursor:
        batch.append(purchase)
        if len(batch) >= batch_size:
            result = process_receive_purchase_batch(batch, current_user, received_at)
            processed_count += result["processed_count"]
            completed_count += result["completed_count"]
            processed_items.extend(result["items"][: max(0, 100 - len(processed_items))])
            restock_product_ids.update(result["product_ids"])
            batch = []

    if batch:
        result = process_receive_purchase_batch(batch, current_user, received_at)
        processed_count += result["processed_count"]
        completed_count += result["completed_count"]
        processed_items.extend(result["items"][: max(0, 100 - len(processed_items))])
        restock_product_ids.update(result["product_ids"])

    return {
        "message": "Inventory updated successfully",
        "pending_count": pending_count,
        "processed_count": processed_count,
        "completed_count": completed_count,
        "received_count": processed_count,
        "updated_dashboard_stats": refresh_dashboard_statistics(current_user=current_user),
        "items": processed_items
    }


def process_receive_purchase_batch(purchases, current_user, received_at):
    product_ids = sorted({purchase["product_id"] for purchase in purchases if purchase.get("product_id")})
    products = list(products_collection.find(
        {"product_id": {"$in": product_ids}},
        {"_id": 1, "product_id": 1, "product_name": 1, "quantity": 1, "reorder_level": 1, "unit_cost": 1}
    ))
    products_by_id = {product["product_id"]: product for product in products}

    warehouse_ids = sorted({
        purchase.get("warehouse_id") or purchase.get("location_id") or (None if current_user.get("role") == "Admin" else user_location_id(current_user)) or "ALL"
        for purchase in purchases
    })
    inventory_query = {"product_id": {"$in": product_ids}}
    if warehouse_ids:
        inventory_query["warehouse_id"] = {"$in": warehouse_ids}
    inventory_query.update(scoped_warehouse_filter(current_user))
    inventory_rows = list(warehouse_inventory_collection.find(inventory_query))
    inventory_by_pair = {(row.get("product_id"), row.get("warehouse_id")): row for row in inventory_rows}

    warehouse_lookup = warehouse_label_map()
    running_stock = {
        (row.get("product_id"), row.get("warehouse_id")): int(row.get("quantity", 0) or 0)
        for row in inventory_rows
    }
    product_increments = {}
    purchase_updates = []
    inventory_updates = []
    queue_updates = []
    history_documents = []
    processed_items = []

    for purchase in purchases:
        product = products_by_id.get(purchase.get("product_id"))
        if not product:
            continue
        warehouse_id = purchase.get("warehouse_id") or purchase.get("location_id") or (None if current_user.get("role") == "Admin" else user_location_id(current_user)) or "ALL"
        if current_user.get("role") != "Admin" and warehouse_id not in {current_user.get("warehouse_id"), current_user.get("location_id")}:
            continue
        quantity = int(purchase.get("quantity", 0) or 0)
        if quantity <= 0:
            continue

        pair_key = (product["product_id"], warehouse_id)
        inventory = inventory_by_pair.get(pair_key) or {}
        previous_stock = running_stock.get(pair_key, int(inventory.get("quantity", 0) or 0))
        current_stock = previous_stock + quantity
        running_stock[pair_key] = current_stock
        product_increments[product["product_id"]] = product_increments.get(product["product_id"], 0) + quantity
        movement_id = f"MOV-{uuid4().hex.upper()}"
        warehouse = warehouse_lookup.get(warehouse_id, {})
        reorder_level = int(inventory.get("reorder_level") or product.get("reorder_level") or 35)

        inventory_updates.append(UpdateOne(
            {"product_id": product["product_id"], "warehouse_id": warehouse_id},
            {
                "$inc": {"quantity": quantity},
                "$set": {
                    "product_name": product.get("product_name"),
                    "reorder_level": reorder_level,
                    "last_updated": received_at,
                    "warehouse_name": warehouse.get("warehouse_name") or warehouse_id,
                    "location": warehouse.get("city") or warehouse.get("location"),
                    "state": warehouse.get("state")
                },
                "$setOnInsert": {
                    "inventory_id": f"INV-{uuid4().hex.upper()}",
                    "created_at": received_at
                }
            },
            upsert=True
        ))
        purchase_updates.append(UpdateOne(
            {"_id": purchase["_id"]},
            {"$set": {
                "status": "Completed",
                "received_at": received_at,
                "previous_stock": previous_stock,
                "current_stock": current_stock,
                "inventory_movement_id": movement_id,
                "movement_type": "Purchase Received",
                "location_id": warehouse_id,
                "warehouse_id": warehouse_id
            }}
        ))
        queue_updates.append(UpdateOne(
            {"$or": [
                {"purchase_id": purchase.get("purchase_id")},
                {"product_id": product["product_id"], "warehouse_id": warehouse_id},
                {"product_id": product["product_id"], "location_id": warehouse_id}
            ]},
            {"$set": {"status": "Received", "queue_status": "Received", "received_at": received_at, "updated_at": received_at}}
        ))
        history_documents.append(inventory_history_document(
            movement_id=movement_id,
            product_id=product["product_id"],
            product_name=product["product_name"],
            movement_type="Purchase Received",
            quantity=quantity,
            previous_stock=previous_stock,
            current_stock=current_stock,
            performed_by=current_user["sub"],
            role=current_user["role"],
            created_at=received_at,
            note=f"Received purchase order {purchase['purchase_id']}",
            **location_fields(warehouse_id)
        ))
        processed_items.append({
            "purchase_id": purchase["purchase_id"],
            "product_id": purchase["product_id"],
            "warehouse_id": warehouse_id,
            "quantity": quantity,
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "status": "Completed",
            "inventory_movement_id": movement_id
        })

    product_updates = [
        UpdateOne({"_id": products_by_id[product_id]["_id"]}, {"$inc": {"quantity": quantity}})
        for product_id, quantity in product_increments.items()
        if product_id in products_by_id and quantity > 0
    ]
    if inventory_updates:
        warehouse_inventory_collection.bulk_write(inventory_updates, ordered=False)
    if product_updates:
        products_collection.bulk_write(product_updates, ordered=False)
    if purchase_updates:
        purchases_collection.bulk_write(purchase_updates, ordered=False)
    if queue_updates:
        restock_queue_collection.bulk_write(queue_updates, ordered=False)
    if history_documents:
        inventory_history_collection.insert_many(history_documents, ordered=False)

    return {
        "processed_count": len(processed_items),
        "completed_count": len(processed_items),
        "product_ids": list(product_increments.keys()),
        "items": processed_items
    }


@app.post("/purchases/bulk-receive", tags=["Purchases"])
def bulk_receive_purchases(
    request: RestockQueueSelection,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])

    query = {
        "status": "Pending",
        **purchase_location_scope(current_user)
    }
    if request.product_ids:
        query["purchase_id"] = {"$in": request.product_ids}

    try:
        return receive_pending_purchases(query, current_user)
    except PyMongoError as exc:
        raise_bulk_timeout_error("Receive pending purchases", exc)


@app.post("/purchases/receive-selected", tags=["Purchases"])
def receive_selected_purchases(
    request: RestockQueueSelection,
    current_user: dict = Depends(get_current_user)
):

    return bulk_receive_purchases(request, current_user)


@app.post("/purchases/receive-all", tags=["Purchases"])
def receive_all_pending_purchases(current_user: dict = Depends(get_current_user)):

    return bulk_receive_purchases(RestockQueueSelection(product_ids=[]), current_user)


@app.post("/purchases/{purchase_id}/status", tags=["Purchases"])
def update_purchase_status(
    purchase_id: str,
    status: str = Query(..., description="Pending or Completed"),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])

    next_status = status.strip().title()
    if next_status not in {"Pending", "Completed"}:
        raise HTTPException(
            status_code=400,
            detail="Purchase status must be Pending or Completed"
        )
    if next_status == "Completed":
        return receive_purchase(purchase_id, current_user)

    try:
        result = purchases_collection.update_one(
            {"purchase_id": purchase_id, **location_query(current_user)},
            {"$set": {"status": next_status}}
        )
        if result.matched_count == 0:
            not_found("Purchase Not Found")
        return {
            "message": "Purchase status updated",
            "purchase_id": purchase_id,
            "status": next_status
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update purchase status: {exc}"
        )


@app.post("/sales", tags=["Sales"])
def add_sale(
    sale: SaleCreate,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        product = products_collection.find_one(
            scoped_product_query(sale.product_id, current_user)
        )

        if not product:
            not_found("Product Not Found")

        previous_stock = product["quantity"]

        if previous_stock < sale.quantity:
            raise HTTPException(
                status_code=400,
                detail="Insufficient stock"
            )

        sale_id = sale.sale_id.strip() if sale.sale_id else generate_sale_id()
        existing_sale = sales_collection.find_one({
            "sale_id": sale_id
        })

        if existing_sale:
            conflict("Sale ID Already Exists")

        unit_price = sale.unit_price or product["price"]
        latest_purchase = purchases_collection.find_one(
            {"product_id": sale.product_id},
            sort=[("created_at", -1)]
        )
        unit_cost = (
            sale.unit_cost
            if sale.unit_cost is not None
            else (
                (latest_purchase or {}).get("unit_cost")
                if latest_purchase
                else product.get("unit_cost", 0)
            )
        ) or 0
        current_stock = previous_stock - sale.quantity
        gross_amount = sale.quantity * unit_price
        total_amount = round(
            gross_amount * (1 - sale.discount_percent / 100),
            2
        )
        cost_amount = sale.quantity * unit_cost
        profit = round(total_amount - cost_amount, 2)
        created_at = (
            parse_report_date(sale.sale_date, "sale_date")
            if sale.sale_date
            else datetime.now(timezone.utc)
        )
        transaction_id = sale.transaction_id or sale_id
        metadata = get_product_metadata(product)

        products_collection.update_one(
            scoped_product_query(sale.product_id, current_user),
            {
                "$set": {
                    "quantity": current_stock
                }
            }
        )

        result = sales_collection.insert_one(
            sale_document(
                sale_id=sale_id,
                product_id=product["product_id"],
                product_name=product["product_name"],
                quantity=sale.quantity,
                unit_price=unit_price,
                total_amount=total_amount,
                previous_stock=previous_stock,
                current_stock=current_stock,
                sold_by=current_user["sub"],
                role=current_user["role"],
                created_at=created_at,
                customer_name=sale.customer_name,
                note=sale.note,
                transaction_id=transaction_id,
                date=created_at.date().isoformat(),
                sale_date=sale.sale_date or created_at.date().isoformat(),
                customer_phone=sale.customer_phone,
                customer_email=sale.customer_email,
                created_by=current_user["sub"],
                category_id=metadata["category_id"],
                category=metadata["category"],
                category_description=metadata["category_description"],
                supplier_id=metadata["supplier_id"],
                supplier=metadata["supplier"],
                supplier_email=metadata["supplier_email"],
                supplier_phone=metadata["supplier_phone"],
                supplier_address=metadata["supplier_address"],
                region=sale.region,
                customer_type=sale.customer_type,
                payment_method=sale.payment_method,
                units_sold=sale.quantity,
                unit_cost=unit_cost,
                discount_percent=sale.discount_percent,
                sales_amount=total_amount,
                cost_amount=cost_amount,
                profit=profit,
                opening_stock=previous_stock,
                purchased_quantity=0,
                closing_stock=current_stock,
                reorder_level=sale.reorder_level,
                stock_status=(
                    "Low Stock"
                    if current_stock <= sale.reorder_level
                    else "In Stock"
                ),
                location_id=product.get("location_id", current_user.get("location_id", "ALL")),
                warehouse_id=product.get("warehouse_id") or product.get("location_id") or current_user.get("warehouse_id") or current_user.get("location_id"),
                warehouse_name=product.get("warehouse_name") or product.get("location") or current_user.get("warehouse_name") or current_user.get("location"),
                location=product.get("location") or current_user.get("location"),
                state=product.get("state") or current_user.get("state")
            )
        )

        customer_key = sale.customer_email or sale.customer_phone or sale.customer_name
        if customer_key:
            customers_collection.update_one(
                {"customer_key": customer_key, **location_query(current_user)},
                {
                    "$set": {
                        "customer_name": sale.customer_name,
                        "phone": sale.customer_phone,
                        "email": sale.customer_email,
                        "updated_at": created_at,
                        "location_id": product.get("location_id", current_user.get("location_id", "ALL")),
                        "warehouse_id": product.get("warehouse_id") or product.get("location_id") or current_user.get("warehouse_id") or current_user.get("location_id"),
                        "warehouse_name": product.get("warehouse_name") or product.get("location") or current_user.get("warehouse_name") or current_user.get("location")
                    },
                    "$setOnInsert": {
                        "customer_id": f"CUST-{uuid4().hex[:8].upper()}",
                        "customer_key": customer_key,
                        "created_at": created_at
                    }
                },
                upsert=True
            )

        sales_items_collection.insert_one({
            "sales_item_id": f"SITEM-{uuid4().hex.upper()}",
            "sale_id": sale_id,
            "transaction_id": transaction_id,
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "quantity": sale.quantity,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "total_amount": total_amount,
            "cost_amount": cost_amount,
            "profit": profit,
            "created_at": created_at,
            "date": created_at.date().isoformat(),
            "customer_name": sale.customer_name,
            "customer_phone": sale.customer_phone,
            "customer_email": sale.customer_email,
            "sold_by": current_user["sub"],
            "role": current_user["role"],
            "category_id": metadata["category_id"],
            "category": metadata["category"],
            "supplier_id": metadata["supplier_id"],
            "supplier": metadata["supplier"],
            "location_id": product.get("location_id", current_user.get("location_id", "ALL")),
            "warehouse_id": product.get("warehouse_id") or product.get("location_id") or current_user.get("warehouse_id") or current_user.get("location_id"),
            "warehouse_name": product.get("warehouse_name") or product.get("location") or current_user.get("warehouse_name") or current_user.get("location")
        })

        movement_id = create_inventory_history(
            product=product,
            movement_type="Sale",
            movement_quantity=sale.quantity,
            previous_stock=previous_stock,
            current_stock=current_stock,
            current_user=current_user,
            note=sale.note
        )
        sales_collection.update_one(
            {"_id": result.inserted_id},
            {"$set": {"inventory_movement_id": movement_id}}
        )

        return {
            "message": "Sale Added Successfully",
            "sale_id": sale_id,
            "transaction_id": transaction_id,
            "inventory_movement_id": movement_id,
            "record_id": str(result.inserted_id),
            "product_id": sale.product_id,
            "quantity": sale.quantity,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_percent": sale.discount_percent,
            "total_amount": total_amount,
            "cost_amount": cost_amount,
            "profit": profit,
            "previous_stock": previous_stock,
            "current_stock": current_stock
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add sale: {exc}"
        )


@app.get("/sales", tags=["Sales"])
def view_sales(
    product_id: Optional[str] = Query(
        None,
        description="Filter sales by product ID"
    ),
    sold_by: Optional[str] = Query(
        None,
        description="Filter sales by username"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    page: int = Query(
        1,
        ge=1,
        description="Page number"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="Sales records per page"
    ),
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        query = location_query(current_user)

        if product_id:
            query["product_id"] = product_id

        if sold_by:
            query["sold_by"] = sold_by

        query.update(build_date_query(start_date, end_date))

        skip = (page - 1) * limit
        total = sales_collection.count_documents(query)
        total_pages = (total + limit - 1) // limit
        sales = sales_collection.find(query).sort(
            "created_at",
            -1
        ).skip(skip).limit(limit)

        sale_items = add_record_id_aliases(
            serialize_documents(sales),
            "sale_id"
        )

        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
            "items": sale_items
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch sales: {exc}"
        )


@app.get("/reports/sales/daily", tags=["Reports"])
def daily_sales_report(
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    product_id: Optional[str] = Query(
        None,
        description="Filter report by product ID"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        return build_sales_report(
            group_id={
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at"
                    }
                }
            },
            sort_spec={"_id.date": 1},
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate daily sales report: {exc}"
        )


@app.get("/reports/sales/weekly", tags=["Reports"])
def weekly_sales_report(
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    product_id: Optional[str] = Query(
        None,
        description="Filter report by product ID"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        return build_sales_report(
            group_id={
                "year": {"$isoWeekYear": "$created_at"},
                "week": {"$isoWeek": "$created_at"}
            },
            sort_spec={
                "_id.year": 1,
                "_id.week": 1
            },
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate weekly sales report: {exc}"
        )


@app.get("/reports/sales/monthly", tags=["Reports"])
def monthly_sales_report(
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    product_id: Optional[str] = Query(
        None,
        description="Filter report by product ID"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        return build_sales_report(
            group_id={
                "year": {"$year": "$created_at"},
                "month": {"$month": "$created_at"}
            },
            sort_spec={
                "_id.year": 1,
                "_id.month": 1
            },
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate monthly sales report: {exc}"
        )


def sales_report_by_period(period, start_date, end_date, product_id):
    if period == "daily":
        return build_sales_report(
            group_id={
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at"
                    }
                }
            },
            sort_spec={"_id.date": 1},
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )

    if period == "weekly":
        return build_sales_report(
            group_id={
                "year": {"$isoWeekYear": "$created_at"},
                "week": {"$isoWeek": "$created_at"}
            },
            sort_spec={
                "_id.year": 1,
                "_id.week": 1
            },
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )

    if period == "monthly":
        return build_sales_report(
            group_id={
                "year": {"$year": "$created_at"},
                "month": {"$month": "$created_at"}
            },
            sort_spec={
                "_id.year": 1,
                "_id.month": 1
            },
            start_date=start_date,
            end_date=end_date,
            product_id=product_id
        )

    raise HTTPException(
        status_code=400,
        detail="period must be daily, weekly, or monthly"
    )


@app.get("/reports/sales/{period}/export/csv", tags=["Reports"])
def export_sales_report_csv(
    period: str = Path(
        ...,
        description="Use daily, weekly, or monthly"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    product_id: Optional[str] = Query(
        None,
        description="Filter report by product ID"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        report = sales_report_by_period(
            period,
            start_date,
            end_date,
            product_id
        )
        filename = report_filename(f"sales_{period}_report", "csv")
        return csv_report_response(
            filename,
            [
                ("Summary", [report["summary"]]),
                ("Sales Report", report["items"])
            ]
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to export sales report CSV: {exc}"
        )


@app.get("/reports/sales/{period}/export/pdf", tags=["Reports"])
def export_sales_report_pdf(
    period: str = Path(
        ...,
        description="Use daily, weekly, or monthly"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    product_id: Optional[str] = Query(
        None,
        description="Filter report by product ID"
    ),
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        request_start = time.perf_counter()
        query_start = time.perf_counter()
        report = sales_report_by_period(
            period,
            start_date,
            end_date,
            product_id
        )
        query_elapsed = time.perf_counter() - query_start
        filename = report_filename(f"sales_{period}_report", "pdf")
        response = sales_enterprise_pdf_response(
            filename,
            f"{period.title()} Sales Report",
            report,
            current_user,
            start_date,
            end_date,
            product_id
        )
        total_elapsed = time.perf_counter() - request_start
        print(f"[reports] Sales {period} PDF timings query_prep={query_elapsed:.2f}s request_total={total_elapsed:.2f}s")
        return response
    except PyMongoError as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to query sales report data: {exc}"
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate sales PDF report: {exc}"
        )


@app.get("/reports/inventory", tags=["Reports"])
def inventory_report(
    category_id: Optional[str] = Query(
        None,
        description="Filter report by category ID"
    ),
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    warehouse_id: Optional[str] = Query(
        None,
        description="Filter report by warehouse ID"
    ),
    report_mode: str = Query(
        "product_summary",
        description="Use product_summary for one row per product or warehouse_detail for one row per product and warehouse."
    ),
    start_date: Optional[str] = Query(
        None,
        description="Accepted for date-filter consistency; current inventory is reported as a live snapshot."
    ),
    end_date: Optional[str] = Query(
        None,
        description="Accepted for date-filter consistency; current inventory is reported as a live snapshot."
    ),
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )
    report_mode = (report_mode or "product_summary").strip().lower().replace("-", "_")
    if report_mode not in {"product_summary", "warehouse_detail"}:
        raise HTTPException(
            status_code=400,
            detail="report_mode must be product_summary or warehouse_detail"
        )

    def number_value(value, default=0):
        try:
            if value is None or value == "":
                return default
            if isinstance(value, str):
                value = re.sub(r"[^0-9.\-]", "", value)
                if value in {"", ".", "-"}:
                    return default
            return float(value)
        except (TypeError, ValueError):
            return default

    try:
        inventory_query = scoped_warehouse_filter(current_user, warehouse_id)
        inventory_rows = list(
            warehouse_inventory_collection.find(
                inventory_query,
                {
                    "_id": 0,
                    "inventory_id": 1,
                    "product_id": 1,
                    "warehouse_id": 1,
                    "quantity": 1,
                    "reorder_level": 1,
                    "last_updated": 1
                }
            ).sort([("warehouse_id", 1), ("product_id", 1)])
        )

        product_ids = sorted({row.get("product_id") for row in inventory_rows if row.get("product_id")})
        product_query = {"product_id": {"$in": product_ids}} if product_ids else {"product_id": {"$in": []}}
        if category_id:
            product_query["category_id"] = category_id
        if supplier_id:
            product_query["supplier_id"] = supplier_id

        products = list(
            products_collection.find(
                product_query,
                {
                    "_id": 0,
                    "product_id": 1,
                    "product_name": 1,
                    "category_id": 1,
                    "supplier_id": 1,
                    "unit_cost": 1,
                    "unit_price": 1,
                    "price": 1,
                    "reorder_level": 1,
                    "status": 1,
                    "product_image": 1
                }
            )
        )
        product_lookup = {product.get("product_id"): product for product in products}
        allowed_product_ids = set(product_lookup.keys())
        inventory_rows = [row for row in inventory_rows if row.get("product_id") in allowed_product_ids]

        category_ids = sorted({product.get("category_id") for product in products if product.get("category_id")})
        supplier_ids = sorted({product.get("supplier_id") for product in products if product.get("supplier_id")})
        warehouse_ids = sorted({row.get("warehouse_id") for row in inventory_rows if row.get("warehouse_id")})

        category_names = {
            category.get("category_id"): category.get("category_name")
            for category in categories_collection.find(
                {"category_id": {"$in": category_ids}} if category_ids else {},
                {"_id": 0, "category_id": 1, "category_name": 1}
            )
        }
        supplier_names = {
            supplier.get("supplier_id"): supplier.get("supplier_name")
            for supplier in suppliers_collection.find(
                {"supplier_id": {"$in": supplier_ids}} if supplier_ids else {},
                {"_id": 0, "supplier_id": 1, "supplier_name": 1}
            )
        }
        warehouse_names = {
            warehouse.get("warehouse_id"): warehouse.get("warehouse_name") or warehouse.get("city") or warehouse.get("warehouse_id")
            for warehouse in warehouses_collection.find(
                {"warehouse_id": {"$in": warehouse_ids}} if warehouse_ids else {},
                {"_id": 0, "warehouse_id": 1, "warehouse_name": 1, "city": 1}
            )
        }

        summary = {
            "total_products": len(allowed_product_ids),
            "total_stock_units": 0,
            "units_in_stock": 0,
            "inventory_value": 0,
            "low_stock_products": 0,
            "out_of_stock_products": 0
        }
        warehouse_detail_rows = []
        product_totals = {}
        category_summary = {}
        supplier_summary = {}
        low_stock_items = []

        for inventory in inventory_rows:
            product = product_lookup.get(inventory.get("product_id"), {})
            quantity = int(number_value(inventory.get("quantity"), 0))
            reorder_level = int(number_value(inventory.get("reorder_level"), product.get("reorder_level") or 0))
            unit_cost = number_value(product.get("unit_cost"), 0)
            selling_price = number_value(product.get("unit_price", product.get("price", 0)), 0)
            stock_value = quantity * unit_cost
            category_key = product.get("category_id")
            supplier_key = product.get("supplier_id")
            warehouse_key = inventory.get("warehouse_id")
            stock_status = "Out of Stock" if quantity <= 0 else ("Low Stock" if reorder_level and quantity <= reorder_level else "In Stock")

            summary["total_stock_units"] += quantity
            summary["units_in_stock"] += quantity
            summary["inventory_value"] += stock_value
            if quantity <= 0:
                summary["out_of_stock_products"] += 1
            if reorder_level and quantity <= reorder_level:
                summary["low_stock_products"] += 1

            product_row = {
                "inventory_id": inventory.get("inventory_id"),
                "product_id": product.get("product_id") or inventory.get("product_id"),
                "product_name": product.get("product_name") or inventory.get("product_id") or "Unknown Product",
                "product_image": product.get("product_image"),
                "category_id": category_key,
                "category_name": category_names.get(category_key) or category_key or "Uncategorized",
                "supplier_id": supplier_key,
                "supplier_name": supplier_names.get(supplier_key) or supplier_key or "Unassigned",
                "warehouse_id": warehouse_key,
                "warehouse_name": warehouse_names.get(warehouse_key) or warehouse_key or "Unassigned",
                "quantity": quantity,
                "current_stock": quantity,
                "price": selling_price,
                "unit_price": selling_price,
                "unit_cost": unit_cost,
                "reorder_level": reorder_level,
                "stock_value": stock_value,
                "stock_status": stock_status,
                "last_updated": inventory.get("last_updated")
            }
            warehouse_detail_rows.append(product_row)
            total_row = product_totals.setdefault(
                product_row["product_id"],
                {
                    "product_id": product_row["product_id"],
                    "product_name": product_row["product_name"],
                    "product_image": product_row.get("product_image"),
                    "category_id": category_key,
                    "category_name": product_row["category_name"],
                    "supplier_id": supplier_key,
                    "supplier_name": product_row["supplier_name"],
                    "warehouse_count": 0,
                    "warehouse_stock_breakdown": [],
                    "quantity": 0,
                    "current_stock": 0,
                    "price": selling_price,
                    "unit_price": selling_price,
                    "unit_cost": unit_cost,
                    "reorder_level": reorder_level,
                    "stock_value": 0,
                    "stock_status": "In Stock"
                }
            )
            total_row["warehouse_count"] += 1
            total_row["warehouse_stock_breakdown"].append(
                f"{warehouse_key or '-'}: {quantity}"
            )
            total_row["quantity"] += quantity
            total_row["current_stock"] = total_row["quantity"]
            total_row["stock_value"] += stock_value
            total_row["reorder_level"] = max(int(total_row.get("reorder_level") or 0), reorder_level)
            if total_row["quantity"] <= 0:
                total_row["stock_status"] = "Out of Stock"
            elif total_row["reorder_level"] and total_row["quantity"] <= total_row["reorder_level"]:
                total_row["stock_status"] = "Low Stock"
            else:
                total_row["stock_status"] = "In Stock"

            if reorder_level and quantity <= reorder_level:
                recommended_order_quantity = max((reorder_level * 2) - quantity, reorder_level, 1)
                low_stock_items.append({
                    "product_id": product_row["product_id"],
                    "product_name": product_row["product_name"],
                    "warehouse_id": warehouse_key,
                    "warehouse_name": product_row["warehouse_name"],
                    "current_stock": quantity,
                    "quantity": quantity,
                    "reorder_level": reorder_level,
                    "shortage_quantity": max(reorder_level - quantity, 0),
                    "recommended_order_quantity": recommended_order_quantity,
                    "severity": "critical" if quantity <= 0 else "warning",
                    "status": stock_status,
                    "suggested_action": "Purchase Immediately" if quantity <= 0 else "Restock",
                    "category_id": category_key,
                    "category_name": product_row["category_name"],
                    "supplier_id": supplier_key,
                    "supplier_name": product_row["supplier_name"]
                })

            category_item = category_summary.setdefault(
                category_key or "uncategorized",
                {
                    "category_id": category_key,
                    "category_name": product_row["category_name"],
                    "product_count": 0,
                    "stock_units": 0,
                    "inventory_value": 0,
                    "low_stock_products": 0
                }
            )
            category_item["product_count"] += 1
            category_item["stock_units"] += quantity
            category_item["inventory_value"] += stock_value
            if reorder_level and quantity <= reorder_level:
                category_item["low_stock_products"] += 1

            supplier_item = supplier_summary.setdefault(
                supplier_key or "unassigned",
                {
                    "supplier_id": supplier_key,
                    "supplier_name": product_row["supplier_name"],
                    "product_count": 0,
                    "stock_units": 0,
                    "inventory_value": 0,
                    "low_stock_products": 0
                }
            )
            supplier_item["product_count"] += 1
            supplier_item["stock_units"] += quantity
            supplier_item["inventory_value"] += stock_value
            if reorder_level and quantity <= reorder_level:
                supplier_item["low_stock_products"] += 1

        product_summary = sort_records_by_numeric_id(list(product_totals.values()), ["product_id"])
        warehouse_detail = sort_records_by_numeric_id(warehouse_detail_rows, ["product_id", "warehouse_id"])
        selected_inventory_rows = warehouse_detail if report_mode == "warehouse_detail" else product_summary

        return {
            "filters": {
                "category_id": category_id,
                "supplier_id": supplier_id,
                "warehouse_id": warehouse_id,
                "report_mode": report_mode,
                "start_date": start_date,
                "end_date": end_date
            },
            "summary": summary,
            "report_mode": report_mode,
            "product_summary": product_summary,
            "warehouse_detail": warehouse_detail,
            "inventory_summary": selected_inventory_rows,
            "category_summary": sort_records_by_numeric_id(list(category_summary.values()), ["category_id"]),
            "supplier_summary": sort_records_by_numeric_id(list(supplier_summary.values()), ["supplier_id"]),
            "low_stock_items": sort_records_by_numeric_id(low_stock_items, ["product_id", "warehouse_id"])
        }

    except HTTPException:
        raise
    except PyMongoError as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate inventory report: {exc}"
        )


@app.get("/reports/suppliers", tags=["Reports"])
def supplier_report(
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        supplier_query = location_query(current_user)
        if supplier_id:
            supplier_query["supplier_id"] = supplier_id

        suppliers = list(suppliers_collection.find(
            supplier_query,
            {
                "_id": 0,
                "supplier_id": 1,
                "supplier_name": 1,
                "email": 1,
                "phone": 1,
                "address": 1,
                "warehouse_id": 1,
                "warehouse_name": 1,
                "location": 1,
                "status": 1
            }
        ))
        supplier_ids = [supplier["supplier_id"] for supplier in suppliers]

        summary = {
            "total_suppliers": len(suppliers),
            "active_suppliers": sum(1 for supplier in suppliers if str(supplier.get("status", "Active")).lower() == "active"),
            "total_products_supplied": 0,
            "total_purchase_orders": 0,
            "total_purchase_cost": 0,
            "low_stock_products": 0
        }

        if not supplier_ids:
            return {
                "filters": {
                    "supplier_id": supplier_id,
                    "start_date": start_date,
                    "end_date": end_date
                },
                "summary": summary,
                "items": []
            }

        purchase_scope = {
            **location_query(current_user),
            "supplier_id": {"$in": supplier_ids}
        }
        purchase_scope.update(build_date_query(start_date, end_date))

        def supplier_number_value(value, default=0):
            try:
                if value is None or value == "":
                    return default
                if isinstance(value, str):
                    value = re.sub(r"[^0-9.\-]", "", value)
                    if value in {"", ".", "-"}:
                        return default
                return float(value)
            except (TypeError, ValueError):
                return default

        supplier_products = list(products_collection.find(
            {"supplier_id": {"$in": supplier_ids}},
            {"_id": 0, "product_id": 1, "supplier_id": 1, "unit_cost": 1, "reorder_level": 1}
        ))
        product_lookup = {product.get("product_id"): product for product in supplier_products if product.get("product_id")}
        inventory_query = scoped_warehouse_filter(current_user)
        if product_lookup:
            inventory_query["product_id"] = {"$in": list(product_lookup.keys())}
        inventory_rows = list(warehouse_inventory_collection.find(
            inventory_query,
            {"_id": 0, "product_id": 1, "quantity": 1, "reorder_level": 1, "warehouse_id": 1}
        )) if product_lookup else []

        product_stats = {}
        for inventory in inventory_rows:
            product = product_lookup.get(inventory.get("product_id"))
            if not product:
                continue
            current_supplier_id = product.get("supplier_id")
            item = product_stats.setdefault(
                current_supplier_id,
                {
                    "_id": current_supplier_id,
                    "product_ids": set(),
                    "stock_units": 0,
                    "inventory_value": 0,
                    "low_stock_products": 0
                }
            )
            quantity = int(supplier_number_value(inventory.get("quantity"), 0))
            reorder_level = int(supplier_number_value(inventory.get("reorder_level"), product.get("reorder_level") or 0))
            unit_cost = supplier_number_value(product.get("unit_cost"), 0)
            item["product_ids"].add(product.get("product_id"))
            item["stock_units"] += quantity
            item["inventory_value"] += quantity * unit_cost
            if reorder_level and quantity <= reorder_level:
                item["low_stock_products"] += 1
        for item in product_stats.values():
            item["product_count"] = len(item.pop("product_ids", set()))

        purchase_stats = {
            item["_id"]: item
            for item in purchases_collection.aggregate([
                {"$match": purchase_scope},
                {
                    "$group": {
                        "_id": "$supplier_id",
                        "purchase_orders": {"$sum": 1},
                        "purchase_cost": {"$sum": {"$ifNull": ["$total_cost", 0]}}
                    }
                }
            ])
        }

        report_items = []
        for supplier in suppliers:
            current_supplier_id = supplier["supplier_id"]
            products_for_supplier = product_stats.get(current_supplier_id, {})
            purchases_for_supplier = purchase_stats.get(current_supplier_id, {})

            product_count = products_for_supplier.get("product_count", 0)
            purchase_count = purchases_for_supplier.get("purchase_orders", 0)
            purchase_cost = purchases_for_supplier.get("purchase_cost", 0)
            low_stock_count = products_for_supplier.get("low_stock_products", 0)

            summary["total_products_supplied"] += product_count
            summary["total_purchase_orders"] += purchase_count
            summary["total_purchase_cost"] += purchase_cost
            summary["low_stock_products"] += low_stock_count

            report_items.append({
                "supplier_id": current_supplier_id,
                "supplier_name": supplier.get("supplier_name", "-"),
                "email": supplier.get("email", "-"),
                "phone": supplier.get("phone", "-"),
                "address": supplier.get("address", "-"),
                "warehouse_id": supplier.get("warehouse_id") or supplier.get("location_id") or "-",
                "warehouse_name": supplier.get("warehouse_name") or supplier.get("location") or "-",
                "location": supplier.get("location") or supplier.get("warehouse_name") or "-",
                "status": supplier.get("status", "Active"),
                "product_count": product_count,
                "purchase_orders": purchase_count,
                "purchase_cost": purchase_cost,
                "stock_units": products_for_supplier.get("stock_units", 0),
                "inventory_value": products_for_supplier.get("inventory_value", 0),
                "low_stock_products": low_stock_count
            })

        return {
            "filters": {
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date
            },
            "summary": summary,
            "items": sort_records_by_numeric_id(report_items, ["supplier_id"])
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate supplier report: {exc}"
        )

@app.get("/reports/inventory/export/csv", tags=["Reports"])
def export_inventory_report_csv(
    category_id: Optional[str] = Query(
        None,
        description="Filter report by category ID"
    ),
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    warehouse_id: Optional[str] = Query(
        None,
        description="Filter report by warehouse ID"
    ),
    report_mode: str = Query(
        "product_summary",
        description="Use product_summary or warehouse_detail."
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    current_user: dict = Depends(get_current_user)
):

    report = inventory_report(
        category_id=category_id,
        supplier_id=supplier_id,
        warehouse_id=warehouse_id,
        report_mode=report_mode,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user
    )
    filename = report_filename("inventory_report", "csv")
    return csv_report_response(
        filename,
        [
            ("Summary", [report["summary"]]),
            ("Product Summary" if report.get("report_mode") != "warehouse_detail" else "Warehouse Detail", report["inventory_summary"]),
            ("Category Summary", report["category_summary"]),
            ("Supplier Summary", report["supplier_summary"]),
            ("Low Stock Items", report["low_stock_items"])
        ]
    )


@app.get("/reports/inventory/export/pdf", tags=["Reports"])
def export_inventory_report_pdf(
    category_id: Optional[str] = Query(
        None,
        description="Filter report by category ID"
    ),
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    warehouse_id: Optional[str] = Query(
        None,
        description="Filter report by warehouse ID"
    ),
    report_mode: str = Query(
        "product_summary",
        description="Use product_summary or warehouse_detail."
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    current_user: dict = Depends(get_current_user)
):

    try:
        request_start = time.perf_counter()
        query_start = time.perf_counter()
        report = inventory_report(
            category_id=category_id,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            report_mode=report_mode,
            start_date=start_date,
            end_date=end_date,
            current_user=current_user
        )
        query_elapsed = time.perf_counter() - query_start
        filename = report_filename("inventory_report", "pdf")
        response = inventory_pdf_report_response(filename, report, current_user)
        total_elapsed = time.perf_counter() - request_start
        print(f"[reports] Inventory PDF timings query_prep={query_elapsed:.2f}s request_total={total_elapsed:.2f}s")
        return response
    except PyMongoError as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to query inventory report data: {exc}"
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate inventory PDF report: {exc}"
        )


@app.get("/reports/suppliers/export/csv", tags=["Reports"])
def export_supplier_report_csv(
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    current_user: dict = Depends(get_current_user)
):

    report = supplier_report(
        supplier_id=supplier_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user
    )
    filename = report_filename("supplier_report", "csv")
    return supplier_csv_report_response(filename, report)


@app.get("/reports/suppliers/export/pdf", tags=["Reports"])
def export_supplier_report_pdf(
    supplier_id: Optional[str] = Query(
        None,
        description="Filter report by supplier ID"
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start date in YYYY-MM-DD format"
    ),
    end_date: Optional[str] = Query(
        None,
        description="End date in YYYY-MM-DD format"
    ),
    current_user: dict = Depends(get_current_user)
):

    try:
        request_start = time.perf_counter()
        query_start = time.perf_counter()
        report = supplier_report(
            supplier_id=supplier_id,
            start_date=start_date,
            end_date=end_date,
            current_user=current_user
        )
        query_elapsed = time.perf_counter() - query_start
        filename = report_filename("supplier_report", "pdf")
        response = supplier_enterprise_pdf_response(filename, report, current_user)
        total_elapsed = time.perf_counter() - request_start
        print(f"[reports] Supplier PDF timings query_prep={query_elapsed:.2f}s request_total={total_elapsed:.2f}s")
        return response
    except PyMongoError as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to query supplier report data: {exc}"
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate supplier PDF report: {exc}"
        )


@app.get("/sales/{sale_id}/invoice", tags=["Invoices"])
def get_sales_invoice(
    sale_id: str = Path(
        ...,
        description="Use sale_id from POST /sales or GET /sales"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager", "Staff"]
    )

    try:
        sale = get_record_by_id_or_field(
            sales_collection,
            sale_id,
            "sale_id",
            "Sale Not Found"
        )
        invoice = build_sales_invoice(sale)

        return build_invoice_response(
            "Sales Invoice",
            invoice
        )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate sales invoice: {exc}"
        )


@app.get("/sales/{sale_id}/invoice/pdf", tags=["Invoices"])
def download_sales_invoice_pdf(
    sale_id: str = Path(
        ...,
        description="Use sale_id from POST /sales or GET /sales"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager", "Staff"]
    )

    try:
        sale = get_record_by_id_or_field(
            sales_collection,
            sale_id,
            "sale_id",
            "Sale Not Found"
        )
        invoice = build_sales_invoice(sale)

        return invoice_pdf_response(
            filename=f"{invoice['invoice_number']}.pdf",
            title="Sales Invoice",
            invoice=invoice
        )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to download sales invoice: {exc}"
        )


@app.get("/purchases/{purchase_id}/invoice", tags=["Invoices"])
def get_purchase_invoice(
    purchase_id: str = Path(
        ...,
        description="Use purchase_id from POST /purchases or GET /purchases"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        purchase = get_record_by_id_or_field(
            purchases_collection,
            purchase_id,
            "purchase_id",
            "Purchase Not Found"
        )
        invoice = build_purchase_invoice(purchase)

        return build_invoice_response(
            "Purchase Invoice",
            invoice
        )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to generate purchase invoice: {exc}"
        )


@app.get("/purchases/{purchase_id}/invoice/pdf", tags=["Invoices"])
def download_purchase_invoice_pdf(
    purchase_id: str = Path(
        ...,
        description="Use purchase_id from POST /purchases or GET /purchases"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        purchase = get_record_by_id_or_field(
            purchases_collection,
            purchase_id,
            "purchase_id",
            "Purchase Not Found"
        )
        invoice = build_purchase_invoice(purchase)

        return invoice_pdf_response(
            filename=f"{invoice['invoice_number']}.pdf",
            title="Purchase Invoice",
            invoice=invoice
        )

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to download purchase invoice: {exc}"
        )


@app.post("/inventory/stock-in", tags=["Inventory"])
def stock_in(
    stock: StockMovementCreate,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    try:
        product = products_collection.find_one(
            scoped_product_query(stock.product_id, current_user)
        )

        if not product:
            not_found("Product Not Found")

        previous_stock = product["quantity"]
        current_stock = previous_stock + stock.quantity

        products_collection.update_one(
            scoped_product_query(stock.product_id, current_user),
            {"$set": {"quantity": current_stock}}
        )

        movement_id = create_inventory_history(
            product=product,
            movement_type="Stock In",
            movement_quantity=stock.quantity,
            previous_stock=previous_stock,
            current_stock=current_stock,
            current_user=current_user,
            note=stock.note
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add stock: {exc}"
        )

    return {
        "message": "Stock Added Successfully",
        "inventory_movement_id": movement_id,
        "product_id": stock.product_id,
        "movement_type": "Stock In",
        "quantity": stock.quantity,
        "previous_stock": previous_stock,
        "current_stock": current_stock
    }


@app.post("/inventory/stock-out", tags=["Inventory"])
def stock_out(
    stock: StockMovementCreate,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        product = products_collection.find_one(
            scoped_product_query(stock.product_id, current_user)
        )

        if not product:
            not_found("Product Not Found")

        previous_stock = product["quantity"]

        if previous_stock < stock.quantity:
            raise HTTPException(
                status_code=400,
                detail="Insufficient stock"
            )

        current_stock = previous_stock - stock.quantity

        products_collection.update_one(
            scoped_product_query(stock.product_id, current_user),
            {"$set": {"quantity": current_stock}}
        )

        movement_id = create_inventory_history(
            product=product,
            movement_type="Stock Out",
            movement_quantity=stock.quantity,
            previous_stock=previous_stock,
            current_stock=current_stock,
            current_user=current_user,
            note=stock.note
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to remove stock: {exc}"
        )

    return {
        "message": "Stock Removed Successfully",
        "inventory_movement_id": movement_id,
        "product_id": stock.product_id,
        "movement_type": "Stock Out",
        "quantity": stock.quantity,
        "previous_stock": previous_stock,
        "current_stock": current_stock
    }

@app.get("/inventory/current-stock", tags=["Inventory"])
def current_stock(current_user: dict = Depends(get_current_user)):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        products = products_collection.find(location_query(current_user))

        return [
            {
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                **get_product_metadata(product),
                "current_stock": product["quantity"],
                "price": product["price"],
                "stock_value": product["quantity"] * product["price"],
                "stock_status": (
                    "Low Stock"
                    if product["quantity"] <= product.get(
                        "reorder_level",
                        35
                    )
                    else "In Stock"
                )
            }
            for product in products
        ]
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch current stock: {exc}"
        )


@app.get("/inventory/current-stock/{product_id}", tags=["Inventory"])
def current_stock_by_product(
    product_id: str,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    product_id = validate_required(product_id, "Product ID")

    try:
        product = products_collection.find_one(
            scoped_product_query(product_id, current_user)
        )

        if not product:
            not_found("Product Not Found")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch current stock: {exc}"
        )

    return {
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        **get_product_metadata(product),
        "current_stock": product["quantity"],
        "price": product["price"],
        "stock_value": product["quantity"] * product["price"],
        "stock_status": (
            "Low Stock"
            if product["quantity"] <= product.get("reorder_level", 35)
            else "In Stock"
        )
    }

@app.get("/inventory/low-stock", tags=["Inventory"])
def low_stock_products(
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        query = low_stock_filter()
        query.update(location_query(current_user))
        alerts = [
            build_stock_alert(product)
            for product in products_collection.find(query).sort("quantity", 1)
        ]

        return {
            "total": len(alerts),
            "items": alerts
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch low stock products: {exc}"
        )


@app.get("/alerts/inventory", tags=["Alerts"])
def inventory_alert_notifications(
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager", "Staff"]
    )

    try:
        query = low_stock_filter()
        query.update(location_query(current_user))
        alerts = [
            build_stock_alert(product)
            for product in products_collection.find(query).sort("quantity", 1)
        ]
        critical_count = sum(
            1 for alert in alerts if alert["severity"] == "critical"
        )
        warning_count = sum(
            1 for alert in alerts if alert["severity"] == "warning"
        )

        return {
            "message": "Inventory alerts available" if alerts else "No inventory alerts",
            "total_alerts": len(alerts),
            "critical_alerts": critical_count,
            "warning_alerts": warning_count,
            "notifications": alerts
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch inventory alerts: {exc}"
        )

@app.get("/inventory/reorder-reminders", tags=["Alerts"])
def reorder_reminders(
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:
        reminders = [
            build_stock_alert(product)
            for product in products_collection.find(
                low_stock_filter()
            ).sort("supplier_id", 1)
        ]

        return {
            "message": (
                "Products need reordering"
                if reminders
                else "No reorder reminders"
            ),
            "total": len(reminders),
            "items": reminders
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch reorder reminders: {exc}"
        )


@app.get("/inventory/history", tags=["Inventory"])
def inventory_history(
    product_id: Optional[str] = Query(
        None,
        description="Filter history by product ID"
    ),
    movement_type: Optional[str] = Query(
        None,
        description="Filter by movement type, such as Stock In or Sale"
    ),
    performed_by: Optional[str] = Query(
        None,
        description="Filter by username that performed the movement"
    ),
    page: int = Query(
        1,
        ge=1,
        description="Page number"
    ),
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="History records per page"
    ),
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    query = {}

    if product_id:
        query["product_id"] = product_id.strip()

    if movement_type:
        query["movement_type"] = {
            "$regex": f"^{re.escape(movement_type.strip())}$",
            "$options": "i"
        }

    if performed_by:
        query["performed_by"] = {
            "$regex": f"^{re.escape(performed_by.strip())}$",
            "$options": "i"
        }

    try:
        skip = (page - 1) * limit
        total = inventory_history_collection.count_documents(query)
        total_pages = (total + limit - 1) // limit
        history = inventory_history_collection.find(query).sort(
            "created_at",
            -1
        ).skip(skip).limit(limit)

        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
            "items": serialize_documents(history)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch inventory history: {exc}"
        )


@app.post("/categories", tags=["Categories"])
def add_category(
    category: CategoryCreate,
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    try:

        existing_category = categories_collection.find_one(
            {
                "category_id": category.category_id
            }
        )

        if existing_category:
            conflict("Category ID Already Exists")

        categories_collection.insert_one(
            category_document(
                category_id=category.category_id,
                category_name=category.category_name,
                description=category.description,
                role=current_role
            )
        )

        return {
            "message": "Category Added Successfully"
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add category: {exc}"
        )
@app.get("/categories", tags=["Categories"])
def view_categories():

    try:
        categories = categories_collection.find()

        return serialize_documents(categories)

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch categories: {exc}"
        )
@app.put("/categories/{category_id}", tags=["Categories"])
def update_category(
    category_id: str,
    category_name: str,
    description: str,
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    category_id = validate_required(category_id, "Category ID")
    category_name = validate_required(category_name, "Category name")
    description = validate_required(description, "Description")

    try:
        result = categories_collection.update_one(
            {
                "category_id": category_id
            },
            {
                "$set": {
                    "category_name": category_name,
                    "description": description,
                    "role": current_role
                }
            }
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update category: {exc}"
        )

    if result.matched_count == 0:
        not_found("Category Not Found")

    return {
        "message": "Category Updated Successfully"
    }
@app.delete("/categories/{category_id}", tags=["Categories"])
def delete_category(
    category_id: str,
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    category_id = validate_required(category_id, "Category ID")

    try:
        result = categories_collection.delete_one(
            {
                "category_id": category_id
            }
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to delete category: {exc}"
        )

    if result.deleted_count == 0:
        not_found("Category Not Found")

    return {
        "message": "Category Deleted Successfully"
    }
@app.post("/suppliers", tags=["Suppliers"])
def add_supplier(
    supplier: SupplierCreate,
    current_user: dict = Depends(get_current_user)
):

    check_role(
        current_user["role"],
        ["Admin", "Manager"]
    )

    supplier_email = validate_email(supplier.email)
    supplier_id = normalize_supplier_id(supplier.supplier_id) if supplier.supplier_id else generate_next_supplier_id()
    if supplier.supplier_id and not supplier_id:
        raise HTTPException(
            status_code=400,
            detail="Supplier ID must use SUP001 to SUP990 format."
        )

    try:
        existing_supplier = suppliers_collection.find_one({"supplier_id": supplier_id})

        if existing_supplier:
            conflict("Supplier ID Already Exists")

        suppliers_collection.insert_one(
            supplier_document(
                supplier_id=supplier_id,
                supplier_name=supplier.supplier_name,
                email=supplier_email,
                phone=supplier.phone,
                address=supplier.address,
                role=current_user["role"],
                location_id=user_location_id(current_user) or "ALL"
            )
        )
    except DuplicateKeyError:
        conflict("Supplier email already exists")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to add supplier: {exc}"
        )

    return {
        "message": "Supplier Added Successfully",
        "supplier_id": supplier_id
    }
@app.get("/suppliers", tags=["Suppliers"])
def view_suppliers(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Suppliers per page"),
    search: Optional[str] = Query(None, description="Search suppliers"),
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])

    try:
        query = location_query(current_user)
        if search:
            search = search.strip()
            query["$or"] = [
                {"supplier_id": {"$regex": search, "$options": "i"}},
                {"supplier_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}}
            ]
        skip = (page - 1) * limit
        total = suppliers_collection.count_documents(query)
        suppliers = suppliers_collection.find(query).sort(
            "supplier_id",
            1
        ).skip(skip).limit(limit)

        return {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
            "has_next": page * limit < total,
            "has_previous": page > 1,
            "items": serialize_documents(suppliers)
        }
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to fetch suppliers: {exc}"
        )


@app.get("/suppliers/{supplier_id}", tags=["Suppliers"])
def get_supplier_detail(
    supplier_id: str,
    current_user: dict = Depends(get_current_user)
):

    check_role(current_user["role"], ["Admin", "Manager"])
    supplier_id = validate_required(supplier_id, "Supplier ID").upper()

    try:
        supplier = suppliers_collection.find_one({
            "supplier_id": supplier_id,
            **location_query(current_user)
        }, {"_id": 0})
        if not supplier and current_user.get("role") == "Admin":
            supplier = suppliers_collection.find_one({"supplier_id": supplier_id}, {"_id": 0})
        if not supplier:
            not_found("Supplier Not Found")
        return {"supplier": supplier}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch supplier: {exc}")


@app.put("/suppliers/{supplier_id}", tags=["Suppliers"])
def update_supplier(
    supplier_id: str,
    supplier_name: str,
    email: str,
    phone: str,
    address: str,
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    supplier_id = validate_required(supplier_id, "Supplier ID")
    supplier_name = validate_required(supplier_name, "Supplier name")
    email = validate_email(email)
    phone = validate_required(phone, "Phone")
    address = validate_required(address, "Address")

    try:
        result = suppliers_collection.update_one(
            {
                "supplier_id": supplier_id
            },
            {
                "$set": {
                    "supplier_name": supplier_name,
                    "email": email,
                    "phone": phone,
                    "address": address
                }
            }
        )
    except DuplicateKeyError:
        conflict("Supplier email already exists")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to update supplier: {exc}"
        )

    if result.matched_count == 0:
        raise HTTPException(status_code=400, detail="Supplier ID does not exist.")

    return {
        "message": "Supplier Updated Successfully"
    }
@app.delete("/suppliers/{supplier_id}", tags=["Suppliers"])
def delete_supplier(
    supplier_id: str,
    current_role: str = Depends(get_current_role)
):

    check_role(
        current_role,
        ["Admin", "Manager"]
    )

    supplier_id = validate_required(supplier_id, "Supplier ID")

    try:
        result = suppliers_collection.delete_one(
            {
                "supplier_id": supplier_id
            }
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to delete supplier: {exc}"
        )

    if result.deleted_count == 0:
        raise HTTPException(status_code=400, detail="Supplier ID does not exist.")

    return {
        "message": "Supplier Deleted Successfully"
    }

