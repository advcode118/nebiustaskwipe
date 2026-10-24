import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent

load_dotenv(BASE_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") != "0"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")


def _anthropic_model(name, default):
    value = (name or default).strip()
    aliases = {
        "claude-sonnet-5.5": "claude-sonnet-5-5",
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-haiku-4.5": "claude-haiku-4-5",
        "claude-opus-4.5": "claude-opus-4-5",
        "claude-opus-4.1": "claude-opus-4-1",
    }
    return aliases.get(value, value)


def _model_id(name, default):
    return (name or default).strip()


COMPILE_MODEL = _model_id(os.environ.get("COMPILE_MODEL"), "deepseek-ai/DeepSeek-V4-Pro-0813")
RUN_MODEL = _model_id(os.environ.get("RUN_MODEL"), "deepseek-ai/DeepSeek-V4-Pro-0813")
VISION_MODEL = _model_id(os.environ.get("VISION_MODEL"), "deepseek-ai/DeepSeek-V4.1-Flash-1017")
FALLBACK_MODEL = _anthropic_model(os.environ.get("FALLBACK_MODEL"), "claude-sonnet-5-5")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "core.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "core" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = False
USE_TZ = True
