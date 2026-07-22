from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

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
    "/profile",
}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get("/")
def root():
    return RedirectResponse("/login")


@app.get("/index.html")
@app.get("/frontend/index.html")
def old_index_paths():
    return RedirectResponse("/login")


@app.get("/reset-password")
@app.get("/reset-password.html")
@app.get("/frontend/reset-password.html")
def reset_password_page():
    return FileResponse(FRONTEND_DIR / "reset-password.html")


@app.get("/{route_name}")
def frontend_route(route_name: str):
    route = f"/{route_name}"
    if route in FRONTEND_ROUTES:
        return FileResponse(FRONTEND_DIR / "index.html")
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    uvicorn.run("frontend_server:app", host="127.0.0.1", port=5500, reload=True)
