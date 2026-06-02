"""Django settings for the NMD Contest Suite."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Core ---------------------------------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "core",
    "registration",
    "portal",
    "scoring",
    "admin_module",
    "public",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "nmdsuite.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.active_contest",
            ],
        },
    },
]

WSGI_APPLICATION = "nmdsuite.wsgi.application"
ASGI_APPLICATION = "nmdsuite.asgi.application"


# --- Database -----------------------------------------------------------------------------------

_DB_PATH = Path(os.environ.get("NMD_DB_PATH", str(BASE_DIR / "data" / "nmdsuite.sqlite3")))
# SQLite refuses to create missing parent directories.
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_DB_PATH),
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;",
            # SQLite's default 5 s is too tight for our gunicorn-with-3-workers
            # layout: even brief contention between writes ("database is
            # locked") trips the page. 30 s is generous enough that the only
            # way to actually hit it is a real deadlock worth alerting on.
            "timeout": 30,
        },
    }
}


# --- Auth ---------------------------------------------------------------------------------------

# Argon2 first; PBKDF2 retained as fallback so admin-created users continue to work after upgrades.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "portal:login"
LOGIN_REDIRECT_URL = "portal:dashboard"
LOGOUT_REDIRECT_URL = "portal:login"


# --- Reverse-proxy mounting --------------------------------------------------------------------
#
# In production NMDSuite is mounted under a path prefix (e.g.
# ``/nmdsuite``) behind a reverse proxy. Setting ``DJANGO_SCRIPT_NAME``
# tells Django to include that prefix in every reversed URL, every
# static asset URL, and every redirect. In dev the env var is unset
# and Django runs at the root as before.

FORCE_SCRIPT_NAME = os.environ.get("DJANGO_SCRIPT_NAME") or None

# Origins the CSRF middleware will accept for cross-origin POSTs. The
# proxy terminates TLS, so the browser POSTs to https://<host>/...
# while the container only sees http://. Comma-separated list of full
# origins (scheme + host) in the env var.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# Trust the proxy's X-Forwarded-Proto header so request.is_secure() and
# build_absolute_uri() know we're behind HTTPS. Combined with
# USE_X_FORWARDED_HOST below for the host header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# django-axes: lock account after 5 failed attempts within 30 minutes; lock lasts 1 hour.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True


# --- I18n / l10n / time -------------------------------------------------------------------------

LANGUAGE_CODE = os.environ.get("DJANGO_LANGUAGE_CODE", "de")
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("it", "Italiano"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]


# --- Static / media -----------------------------------------------------------------------------

_URL_PREFIX = (FORCE_SCRIPT_NAME or "").rstrip("/")
STATIC_URL = f"{_URL_PREFIX}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Whitenoise warns at startup if STATIC_ROOT is missing — make sure the
# directory exists even before the first `collectstatic` (incl. during tests).
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
# Manifest storage hashes filenames and requires `collectstatic` first;
# only worth the trade in production. Dev/test use the plain compressing storage.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "whitenoise.storage.CompressedStaticFilesStorage"
        ),
    },
}

MEDIA_URL = f"{_URL_PREFIX}/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Email --------------------------------------------------------------------------------------

# Custom SMTP backend that optionally rewrites every recipient to a
# single override address read from EMAIL_REDIRECT_TO. Lets us point
# test deployments at a sink mailbox so dev/staging data — including
# anything seeded from production — can't accidentally mail real
# participants. When EMAIL_REDIRECT_TO is empty/unset, the backend
# behaves identically to the standard SMTP backend.
EMAIL_BACKEND = "nmdsuite.email_backends.RedirectingSMTPEmailBackend"
EMAIL_HOST = os.environ.get("SMTP_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "") or ""
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASSWORD", "") or ""
EMAIL_USE_TLS = env_bool("SMTP_USE_TLS", default=False)
EMAIL_USE_SSL = env_bool("SMTP_USE_SSL", default=False)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "nmd@uska.ch")


# --- App-specific settings ----------------------------------------------------------------------

NMD_BASE_URL = os.environ.get("NMD_BASE_URL", "http://localhost:8000")
SWISSTOPO_HEIGHT_API = os.environ.get(
    "SWISSTOPO_HEIGHT_API", "https://api3.geo.admin.ch/rest/services/height"
)
SWISSTOPO_IDENTIFY_API = os.environ.get(
    "SWISSTOPO_IDENTIFY_API",
    "https://api3.geo.admin.ch/rest/services/api/MapServer/identify",
)


# --- Logging ------------------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "nmdsuite": {"handlers": ["console"], "level": "DEBUG" if DEBUG else "INFO", "propagate": False},
    },
}
