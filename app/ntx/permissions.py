"""
Project role based permissions.

Every project-scoped model belongs to exactly one Project (see `project_path` on the model).
Every logged-in user can view all projects and their records. For anything else, the user's
role in the object's project decides, following the rules below. The Django admin and the
frontend views both ask `user.has_perm("ntx.<action>_<model>", obj)` and filter lists with
`Model.objects.visible_to(user)`, so both apply the same rules.

Admins (the `ntx.manage_all_projects` permission, granted through the Admins group) act as
manager in every project. Superusers can do everything (Django's `User.has_perm`
alwas returns True for them). Global models (chemicals, concentration units) and project creation
use regular Django permissions, granted through the Researchers and Admins groups (see
`ntx.groups`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from django.contrib.auth.backends import BaseBackend
from django.db import models

from .models import (
    AnyUser,
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentIngest,
    ExperimentIngestGroup,
    NeuronalMetricsFrame,
    Project,
    ProjectMembership,
    ProjectRole,
    is_project_admin,
)

__all__ = ["is_project_admin"]

APP_LABEL: Final = "ntx"


class Action(StrEnum):
    VIEW = "view"
    ADD = "add"
    CHANGE = "change"
    DELETE = "delete"


@dataclass(frozen=True)
class Rule:
    """
    `everyone` grants the action to every logged-in user, member or not. Otherwise `role`
    grants it on any object in the project, and `owner_role` is a (lower) role that is
    sufficient for objects the user created.
    """

    role: ProjectRole | None = None
    owner_role: ProjectRole | None = None
    everyone: bool = False

    def minimum_role(self) -> ProjectRole | None:
        """
        The lowest project role that grants the action on at least some objects. Not
        meaningful for "everyone" rules, which don't need any role.
        """
        candidates = [role for role in (self.role, self.owner_role) if role is not None]
        return min(candidates, key=lambda role: role.rank, default=None)

    def allows(self, role: ProjectRole | None, *, is_owner: bool) -> bool:
        if self.everyone:
            return True
        if role is None:
            return False
        if self.role is not None and role.at_least(self.role):
            return True
        return is_owner and self.owner_role is not None and role.at_least(self.owner_role)

    def allows_somewhere(self, roles: Iterable[ProjectRole]) -> bool:
        """
        Whether a user with these project roles may perform the action in at least one place.
        """
        if self.everyone:
            return True
        minimum = self.minimum_role()
        return minimum is not None and any(role.at_least(minimum) for role in roles)


NOBODY: Final = Rule()
EVERYONE: Final = Rule(everyone=True)
EDITORS: Final = Rule(role=ProjectRole.EDITOR)
MANAGERS: Final = Rule(role=ProjectRole.MANAGER)
MANAGERS_OR_OWNING_EDITOR: Final = Rule(role=ProjectRole.MANAGER, owner_role=ProjectRole.EDITOR)

# This backend grants only the actions listed here.
# Global grants and superusers are handled separately.
PROJECT_RULES: Final[dict[type[models.Model], dict[Action, Rule]]] = {
    # Adding projects is granted through the Researchers group, not by a project role.
    Project: {
        Action.VIEW: EVERYONE,
        Action.CHANGE: MANAGERS,
        Action.DELETE: MANAGERS,
    },
    ProjectMembership: {
        Action.VIEW: EVERYONE,
        Action.ADD: MANAGERS,
        Action.CHANGE: MANAGERS,
        Action.DELETE: MANAGERS,
    },
    ExperimentIngest: {
        Action.VIEW: EVERYONE,
        Action.ADD: EDITORS,
        Action.CHANGE: MANAGERS_OR_OWNING_EDITOR,
        Action.DELETE: MANAGERS_OR_OWNING_EDITOR,
    },
    ExperimentIngestGroup: {
        Action.VIEW: EVERYONE,
        Action.ADD: MANAGERS_OR_OWNING_EDITOR,
        Action.CHANGE: MANAGERS_OR_OWNING_EDITOR,
        Action.DELETE: MANAGERS_OR_OWNING_EDITOR,
    },
    # Experiments are created by promoting an ingest; "change" means replacing one by
    # re-promotion.
    Experiment: {
        Action.VIEW: EVERYONE,
        Action.ADD: EDITORS,
        Action.CHANGE: MANAGERS_OR_OWNING_EDITOR,
        Action.DELETE: MANAGERS_OR_OWNING_EDITOR,
    },
    # Created by ingestion and removed together with their experiment.
    Condition: {Action.VIEW: EVERYONE},
    ExperimentFile: {Action.VIEW: EVERYONE},
    NeuronalMetricsFrame: {Action.VIEW: EVERYONE},
}

# Global models every logged-in user may view. Changes need Django permissions.
GLOBAL_VIEW_MODELS: Final[frozenset[type[models.Model]]] = frozenset({Chemical, ConcentrationUnit})

_MODELS_BY_NAME: Final[dict[str, type[models.Model]]] = {
    model._meta.model_name: model
    for model in (*PROJECT_RULES, *GLOBAL_VIEW_MODELS)
    if model._meta.model_name
}

_ROLE_CACHE_ATTR: Final = "_ntx_project_roles"


def perm_name(action: Action, model: type[models.Model]) -> str:
    return f"{APP_LABEL}.{action}_{model._meta.model_name}"


def user_can(
    user: AnyUser, action: Action, model: type[models.Model], obj: models.Model | None = None
) -> bool:
    """
    Typed shortcut for `user.has_perm("ntx.<action>_<model>", obj)`.
    """
    has_perm = getattr(user, "has_perm", None)
    return bool(has_perm and has_perm(perm_name(action, model), obj))


def project_roles(user: AnyUser) -> dict[int, ProjectRole]:
    """
    Return the user's {project_id: membership role}, cached on the user object for the
    request. Does not include the manager rights admins have everywhere (see `role_in`).
    """
    cached: dict[int, ProjectRole] | None = getattr(user, _ROLE_CACHE_ATTR, None)
    if cached is not None:
        return cached
    roles: dict[int, ProjectRole] = {}
    if user.is_authenticated and user.is_active:
        memberships = ProjectMembership.objects.filter(user_id=user.pk).values_list(
            "project_id", "role"
        )
        roles = {project_id: ProjectRole(role) for project_id, role in memberships}
    setattr(user, _ROLE_CACHE_ATTR, roles)
    return roles


def clear_role_cache(user: AnyUser) -> None:
    """
    Forget cached roles, e.g. after changing the user's memberships in this request.
    """
    try:
        delattr(user, _ROLE_CACHE_ATTR)
    except AttributeError:
        pass


def role_in(user: AnyUser, project: Project | int | None) -> ProjectRole | None:
    """
    The role the user acts with in the project: manager for admins, else their membership
    role (None for non-members).
    """
    if project is None:
        return None
    if is_project_admin(user):
        return ProjectRole.MANAGER
    project_id = project if isinstance(project, int) else project.pk
    return project_roles(user).get(project_id)


def can_in_project(
    user: AnyUser, action: Action, model: type[models.Model], project: Project | int | None
) -> bool:
    """
    Whether the user may perform `action` on any `model` object in the project, e.g. add an
    ingest to it. Ownership is not considered.
    """
    if not (user.is_active and user.is_authenticated):
        return False
    if getattr(user, "is_superuser", False):
        return True
    rule = PROJECT_RULES.get(model, {}).get(action, NOBODY)
    return rule.allows(role_in(user, project), is_owner=False)


def _related_id(obj: models.Model, path: str) -> int | None:
    """
    Follow a lookup path like "experiment__project" and return the final related object's
    id without loading it.
    """
    if not path:
        return obj.pk
    *parents, last = path.split("__")
    target: object = obj
    for name in parents:
        target = getattr(target, name, None)
        if target is None:
            return None
    return getattr(target, f"{last}_id", None)


def project_id_of(obj: models.Model) -> int | None:
    return _related_id(obj, getattr(type(obj), "project_path"))


def is_owner(user: AnyUser, obj: models.Model) -> bool:
    owner_path: str | None = getattr(type(obj), "owner_path", None)
    if owner_path is None or user.pk is None:
        return False
    return _related_id(obj, owner_path) == user.pk


def _parse_perm(perm: str) -> tuple[Action, type[models.Model]] | None:
    app_label, _, codename = perm.partition(".")
    if app_label != APP_LABEL:
        return None
    action_name, _, model_name = codename.partition("_")
    try:
        action = Action(action_name)
    except ValueError:
        return None
    model = _MODELS_BY_NAME.get(model_name)
    if model is None:
        return None
    return action, model


class ProjectRoleBackend(BaseBackend):
    """
    Answers `ntx` permissions from project roles.

    Without an object the question is "might the user do this anywhere?", which the admin
    uses to decide which models, lists and buttons to show. With an object, the user's role
    in that object's project decides.

    These are this backend's answers. ModelBackend can also grant objectless checks through
    global user/group permissions, but supplies no permissions when an object is passed.
    Callers must check the specific object or target project before performing an action.
    """

    def has_perm(self, user_obj: AnyUser, perm: str, obj: models.Model | None = None) -> bool:
        if not (user_obj.is_active and user_obj.is_authenticated):
            return False
        parsed = _parse_perm(perm)
        if parsed is None:
            return False
        action, model = parsed

        if model in GLOBAL_VIEW_MODELS:
            return action == Action.VIEW

        rule = PROJECT_RULES[model].get(action, NOBODY)
        if obj is None:
            if is_project_admin(user_obj):
                return rule.allows_somewhere([ProjectRole.MANAGER])
            return rule.allows_somewhere(project_roles(user_obj).values())

        if not isinstance(obj, model):
            return False
        role = role_in(user_obj, project_id_of(obj))
        return rule.allows(role, is_owner=is_owner(user_obj, obj))

    def has_module_perms(self, user_obj: AnyUser, app_label: str) -> bool:
        return app_label == APP_LABEL and user_obj.is_active and user_obj.is_authenticated
