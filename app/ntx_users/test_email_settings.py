import runpy

import pytest
from django.core.exceptions import ImproperlyConfigured


@pytest.fixture
def production_env(monkeypatch):
    values = {
        "DJANGO_SECRET_KEY": "test-settings-only",
        "ALLOWED_HOSTS": "testserver",
        "DATABASE_URL": "sqlite:///:memory:",
        "EMAIL_HOST": "smtp.example.com",
        "EMAIL_HOST_USER": "test-user",
        "EMAIL_HOST_PASSWORD": "test-password",
        "DEFAULT_FROM_EMAIL": "Accounts <accounts@example.com>",
        "EMAIL_PORT": "2525",
        "EMAIL_USE_TLS": "False",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_production_email_settings(production_env):
    config = runpy.run_module("ntxconfig.settings.production")
    for name in ("EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL"):
        assert config[name] == production_env[name]
    assert config["EMAIL_BACKEND"] == "django.core.mail.backends.smtp.EmailBackend"
    assert config["EMAIL_PORT"] == 2525
    assert config["EMAIL_USE_TLS"] is False
    assert 0 < config["EMAIL_TIMEOUT"] < 60


@pytest.mark.parametrize(
    "name", ["EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL"]
)
@pytest.mark.parametrize("value", [None, ""])
def test_production_requires_email_settings(production_env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(ImproperlyConfigured, match=f"{name} must be set"):
        runpy.run_module("ntxconfig.settings.production")
