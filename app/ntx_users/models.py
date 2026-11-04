"""
Custom user model based on Django's AbstractUser for extensibility.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(DjangoUserManager["User"]):
    def create_user(self, username, email=None, password=None, **extra_fields):
        # Every user works in the admin; project roles decide what they can see there.
        extra_fields.setdefault("is_staff", True)
        return super().create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    """
    Extendable user model.

    All users work in the Django admin; project roles decide what they can see and change.
    """

    is_staff = models.BooleanField(
        _("staff status"),
        default=True,
        help_text=_("Designates whether the user can log into this admin site."),
    )

    objects: ClassVar[UserManager] = UserManager()
