import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env", override=False)


def clean_env(name, default=""):
    value = os.getenv(name, default)
    return str(value).strip().strip('"').strip("'") if value is not None else ""


def env_bool(name, default=False):
    raw = clean_env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def env_int(name, default):
    raw = clean_env(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def required_env(name, default=""):
    value = clean_env(name, default)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value