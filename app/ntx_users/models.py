"""
Custom user model based on Django's AbstractUser for extensibility.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Extendable user model; currently using Django's default fields.
    """

    pass
