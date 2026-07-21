from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from ntx_users.models import User


class Command(BaseCommand):
    help = "Create the configured Django superuser if it does not already exist"

    def handle(self, *args, **options):
        username = self._required_env("DJANGO_SUPERUSER_USERNAME")
        password = self._required_env("DJANGO_SUPERUSER_PASSWORD")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "")

        existing_user = User.objects.filter(username=username).first()
        if existing_user is not None:
            self._validate_existing_user(existing_user, username)
            self.stdout.write(
                self.style.SUCCESS(f"Superuser '{username}' already exists; no changes were made.")
            )
            return

        try:
            with transaction.atomic():
                User.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password,
                )
        except IntegrityError as exc:
            # Another container may have created the same account after our
            # initial lookup. Accept that race only when the resulting account
            # has all properties needed for Django admin access.
            existing_user = User.objects.filter(username=username).first()
            if existing_user is None:
                raise CommandError(
                    f"Could not create superuser '{username}' due to a database conflict."
                ) from exc
            self._validate_existing_user(existing_user, username)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superuser '{username}' was created concurrently; no changes were made."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))

    @staticmethod
    def _required_env(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise CommandError(f"{name} must be set and non-empty.")
        return value

    @staticmethod
    def _validate_existing_user(user: User, username: str) -> None:
        if not (user.is_active and user.is_staff and user.is_superuser):
            raise CommandError(
                f"User '{username}' already exists but is not an active staff superuser; "
                "not modifying existing account."
            )
