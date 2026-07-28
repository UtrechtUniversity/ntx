import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ImproperlyConfigured(f"{name} must be set")
    return value


def env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean")


SECRET_KEY = require_env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = [host.strip() for host in require_env("ALLOWED_HOSTS").split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()
]
# OpenShift router must replace client-supplied X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

_secure_cookies = env_bool("DJANGO_SECURE_COOKIES", default=True)
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_SECURE = _secure_cookies

DATABASES = {
    "default": dj_database_url.parse(
        require_env("DATABASE_URL"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
CSRF_TRUSTED_ORIGINS = ["https://app.ntx.uu.nl"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
MIDDLEWARE = [  # noqa: F405
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],  # noqa: F405
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
