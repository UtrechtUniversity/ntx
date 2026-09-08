from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from django.core import mail
from django.urls import reverse

from ntx_users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def user() -> User:
    return User.objects.create_user(
        username="alice",
        email="alice@example.com",
        password="secret123",
    )


def test_password_reset_form_renders(client):
    response = client.get(reverse("password_reset"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Reset password" in content
    assert 'name="email"' in content


def test_password_reset_through_login(client, settings, user: User):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "ntx app <accounts@example.com>"

    response = client.post(reverse("password_reset"), {"email": user.email}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("password_reset_done"), 302)]
    assert len(mail.outbox) == 1

    message = mail.outbox[0]
    assert message.to == [user.email]
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert user.get_username() in message.body

    reset_link = re.search(r"https?://\S+", str(message.body))
    assert reset_link is not None
    reset_url = reset_link.group()
    response = client.get(urlsplit(reset_url).path, follow=True)
    assert response.status_code == 200
    assert response.context["validlink"]
    confirm_url = response.redirect_chain[-1][0]

    password = "New-otter-password-873!"
    response = client.post(
        confirm_url,
        {"new_password1": password, "new_password2": password},
        follow=True,
    )
    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("password_reset_complete"), 302)]
    assert f'href="{reverse("login")}"' in response.content.decode()

    response = client.get(reverse("login"))
    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()
    assert 'name="password"' in response.content.decode()
    response = client.post(reverse("login"), {"username": user.username, "password": password})
    assert response.status_code == 302
    assert response["Location"] == reverse("ntx:home")
    assert client.session["_auth_user_id"] == str(user.pk)

    response = client.get(urlsplit(reset_url).path, follow=True)
    assert response.status_code == 200
    assert "This password reset link is invalid or has expired." in response.content.decode()


def test_login_preserves_next_and_shows_errors(client, user: User):
    destination = reverse("ntx:experiments")
    response = client.get(reverse("login"), {"next": destination})
    assert f'name="next" value="{destination}"' in response.content.decode()

    credentials = {"username": user.username, "password": "wrong", "next": destination}
    response = client.post(reverse("login"), credentials)
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert "Please enter a correct username and password" in response.content.decode()
    assert f'name="next" value="{destination}"' in response.content.decode()

    credentials["password"] = "secret123"
    response = client.post(reverse("login"), credentials)
    assert response.status_code == 302
    assert response["Location"] == destination
