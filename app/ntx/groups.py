"""
Global (non-project) permissions granted through Django groups.

Keep project-scoped rights in memberships. Regular group grants can satisfy objectless
permission checks without a project role, but ModelBackend ignores them when an object is
supplied, so they do not override object-specific role checks.
The intended global grants are project creation (`add_project`), permissions for shared
models, and `manage_all_projects`, which the role layer checks to make Admins managers
everywhere.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps as global_apps
from django.db import DEFAULT_DB_ALIAS

from .models import MANAGE_ALL_PROJECTS

RESEARCHERS_GROUP = "Researchers"
ADMINS_GROUP = "Admins"

RESEARCHERS_PERMISSIONS: tuple[str, ...] = (
    "add_project",
    "add_chemical",
    "change_chemical",
    "delete_chemical",
    "view_chemical",
    "add_concentrationunit",
    "change_concentrationunit",
    "delete_concentrationunit",
    "view_concentrationunit",
)

# Admins can do everything researchers can, and act as manager in every project.
ADMINS_PERMISSIONS: tuple[str, ...] = (*RESEARCHERS_PERMISSIONS, MANAGE_ALL_PROJECTS)

GROUP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    RESEARCHERS_GROUP: RESEARCHERS_PERMISSIONS,
    ADMINS_GROUP: ADMINS_PERMISSIONS,
}


def ensure_groups(
    *, apps: Any = global_apps, using: str = DEFAULT_DB_ALIAS, **kwargs: object
) -> None:
    """
    Create the Researchers and Admins groups and add their permissions.
    Safe to run repeatedly.
    """
    try:
        Group = apps.get_model("auth", "Group")
        Permission = apps.get_model("auth", "Permission")
    except LookupError:
        return

    for name, codenames in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=name)
        permissions = Permission.objects.using(using).filter(
            content_type__app_label="ntx",
            codename__in=codenames,
        )
        group.permissions.add(*permissions)
