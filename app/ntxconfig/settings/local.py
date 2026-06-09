from .base import *  # noqa: F403

SECRET_KEY = "django-insecure-a05-a+#0b%6o-_)^htihcv5ikk302*g#9$wyqu4m2_)pye08sc"

DEBUG = True

ALLOWED_HOSTS = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}
