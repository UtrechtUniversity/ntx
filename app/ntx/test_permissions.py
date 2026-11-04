from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.db import models

from .exposure_types import ExposureType
from .groups import ADMINS_GROUP, RESEARCHERS_GROUP
from .models import (
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
)
from .permissions import (
    Action,
    can_in_project,
    clear_role_cache,
    is_project_admin,
    perm_name,
    project_roles,
    role_in,
)

pytestmark = pytest.mark.django_db

E, M = ProjectRole.EDITOR, ProjectRole.MANAGER
# Every logged-in user, with or without a role in the project.
ALL: Literal["all"] = "all"

type Minimum = ProjectRole | Literal["all"] | None

# (model, action) -> (minimum role for any object, minimum role for objects the user created)
# None means "no role is enough". Written out independently of ntx.permissions on purpose.
EXPECTED: dict[tuple[type[models.Model], Action], tuple[Minimum, Minimum]] = {
    (Project, Action.VIEW): (ALL, ALL),
    (Project, Action.ADD): (None, None),
    (Project, Action.CHANGE): (M, M),
    (Project, Action.DELETE): (M, M),
    (ProjectMembership, Action.VIEW): (ALL, ALL),
    (ProjectMembership, Action.ADD): (M, M),
    (ProjectMembership, Action.CHANGE): (M, M),
    (ProjectMembership, Action.DELETE): (M, M),
    (ExperimentIngest, Action.VIEW): (ALL, ALL),
    (ExperimentIngest, Action.ADD): (E, E),
    (ExperimentIngest, Action.CHANGE): (M, E),
    (ExperimentIngest, Action.DELETE): (M, E),
    (ExperimentIngestGroup, Action.VIEW): (ALL, ALL),
    (ExperimentIngestGroup, Action.ADD): (M, E),
    (ExperimentIngestGroup, Action.CHANGE): (M, E),
    (ExperimentIngestGroup, Action.DELETE): (M, E),
    (Experiment, Action.VIEW): (ALL, ALL),
    (Experiment, Action.ADD): (E, E),
    (Experiment, Action.CHANGE): (M, E),
    (Experiment, Action.DELETE): (M, E),
    **{
        (model, action): ((ALL, ALL) if action == Action.VIEW else (None, None))
        for model in (Condition, ExperimentFile, NeuronalMetricsFrame)
        for action in Action
    },
}

ROLES: list[ProjectRole | None] = [None, E, M]


def _allowed(role: ProjectRole | None, minimum: Minimum) -> bool:
    if minimum == ALL:
        return True
    return role is not None and minimum is not None and role.at_least(minimum)


def _build_object(model: type[models.Model], project: Project, owner) -> models.Model:
    """Unsaved objects are enough: permission checks only follow foreign keys."""
    ingest = ExperimentIngest(project=project, created_by=owner)
    experiment = Experiment(project=project, created_by=owner, code="EXP", type=ExposureType.ACUTE)
    builders: dict[type[models.Model], Callable[[], models.Model]] = {
        Project: lambda: project,
        ProjectMembership: lambda: ProjectMembership(project=project, user=owner),
        ExperimentIngest: lambda: ingest,
        ExperimentIngestGroup: lambda: ExperimentIngestGroup(ingest=ingest),
        Experiment: lambda: experiment,
        Condition: lambda: Condition(experiment=experiment),
        ExperimentFile: lambda: ExperimentFile(experiment=experiment),
        NeuronalMetricsFrame: lambda: NeuronalMetricsFrame(experiment=experiment),
    }
    return builders[model]()


@pytest.fixture
def project() -> Project:
    return Project.objects.create(name="Project")


@pytest.fixture
def other_project() -> Project:
    return Project.objects.create(name="Other project")


@pytest.fixture
def make_user(django_user_model):
    counter = iter(range(1000))

    def _make(**kwargs):
        return django_user_model.objects.create_user(username=f"user{next(counter)}", **kwargs)

    return _make


@pytest.mark.parametrize("own", [False, True], ids=["others", "own"])
@pytest.mark.parametrize("role", ROLES, ids=lambda r: r or "none")
@pytest.mark.parametrize(
    ("model", "action"),
    list(EXPECTED),
    ids=[f"{action}_{model._meta.model_name}" for model, action in EXPECTED],
)
def test_object_permission_matrix(make_user, project, model, action, role, own):
    user = make_user()
    if role is not None:
        ProjectMembership.objects.create(project=project, user=user, role=role)
    owner = user if own else make_user()

    obj = _build_object(model, project, owner)
    minimum_any, minimum_own = EXPECTED[(model, action)]
    expected = _allowed(role, minimum_any) or (own and _allowed(role, minimum_own))

    assert user.has_perm(perm_name(action, model), obj) is expected


@pytest.mark.parametrize("model", [m for m, a in EXPECTED if a == Action.VIEW])
def test_manager_elsewhere_can_only_view(make_user, project, other_project, model):
    user = make_user()
    ProjectMembership.objects.create(project=other_project, user=user, role=M)
    obj = _build_object(model, project, user)

    assert user.has_perm(perm_name(Action.VIEW, model), obj)
    for action in (Action.ADD, Action.CHANGE, Action.DELETE):
        assert not user.has_perm(perm_name(action, model), obj)


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r or "none")
@pytest.mark.parametrize(
    ("model", "action"),
    list(EXPECTED),
    ids=[f"{action}_{model._meta.model_name}" for model, action in EXPECTED],
)
def test_model_level_permission_means_allowed_somewhere(make_user, project, model, action, role):
    user = make_user()
    if role is not None:
        ProjectMembership.objects.create(project=project, user=user, role=role)

    minimum_any, minimum_own = EXPECTED[(model, action)]
    expected = _allowed(role, minimum_any) or _allowed(role, minimum_own)

    assert user.has_perm(perm_name(action, model)) is expected


@pytest.fixture
def make_admin(make_user):
    def _make(**kwargs):
        user = make_user(**kwargs)
        user.groups.add(Group.objects.get(name=ADMINS_GROUP))
        return user

    return _make


@pytest.mark.parametrize("own_role", [None, E, M], ids=lambda r: r or "none")
@pytest.mark.parametrize(
    ("model", "action"),
    list(EXPECTED),
    ids=[f"{action}_{model._meta.model_name}" for model, action in EXPECTED],
)
def test_admin_acts_as_manager_whatever_their_role(
    make_user, make_admin, project, model, action, own_role
):
    admin = make_admin()
    if own_role is not None:
        ProjectMembership.objects.create(project=project, user=admin, role=own_role)
    obj = _build_object(model, project, make_user())  # created by someone else

    minimum_any, _ = EXPECTED[(model, action)]
    assert admin.has_perm(perm_name(action, model), obj) is _allowed(M, minimum_any)
    # Adding projects comes from the Admins group's (global) Django permission.
    granted_by_group = (model, action) == (Project, Action.ADD)
    assert admin.has_perm(perm_name(action, model)) is (
        _allowed(M, minimum_any) or granted_by_group
    )


def test_admin_role_in_and_querysets(make_user, make_admin, project, other_project):
    admin = make_admin()
    ProjectMembership.objects.create(project=project, user=admin, role=E)

    assert is_project_admin(admin)
    assert not is_project_admin(make_user())
    assert role_in(admin, project) == M
    assert role_in(admin, other_project) == M
    assert role_in(admin, None) is None
    # The stored membership is unchanged.
    assert project_roles(admin) == {project.pk: E}
    assert set(Project.objects.with_role(admin, M)) == set(Project.objects.all())
    assert can_in_project(admin, Action.ADD, ExperimentIngest, other_project)
    assert can_in_project(admin, Action.CHANGE, ExperimentIngest, other_project)
    assert not can_in_project(admin, Action.ADD, Condition, other_project)


def test_inactive_admin_has_no_permissions(make_admin, project):
    admin = make_admin(is_active=False)
    assert not is_project_admin(admin)
    assert not admin.has_perm("ntx.change_project", project)
    assert list(Project.objects.with_role(admin, M)) == []


def test_admins_group_holds_researcher_permissions_and_manage_all_projects(make_admin):
    group = Group.objects.get(name=ADMINS_GROUP)
    researchers = Group.objects.get(name=RESEARCHERS_GROUP)
    codenames = set(group.permissions.values_list("codename", flat=True))
    assert codenames == {
        *researchers.permissions.values_list("codename", flat=True),
        "manage_all_projects",
    }

    admin = make_admin()
    assert admin.has_perm("ntx.add_project")
    assert admin.has_perm("ntx.change_chemical")


def test_superuser_is_admin(make_user):
    assert is_project_admin(make_user(is_superuser=True))


def test_superuser_can_do_everything(make_user, project):
    user = make_user(is_superuser=True)
    for model, action in EXPECTED:
        obj = _build_object(model, project, None)
        assert user.has_perm(perm_name(action, model), obj)


@pytest.mark.parametrize("make", ["inactive", "anonymous"])
def test_inactive_and_anonymous_users_have_no_permissions(make_user, project, make):
    if make == "inactive":
        user = make_user(is_active=False)
        ProjectMembership.objects.create(project=project, user=user, role=M)
    else:
        user = AnonymousUser()

    assert not user.has_perm("ntx.view_project", project)
    assert not user.has_perm("ntx.view_project")
    assert not user.has_perm("ntx.view_chemical")
    assert not user.has_module_perms("ntx")
    assert list(Project.objects.visible_to(user)) == []


@pytest.mark.parametrize("model", [Chemical, ConcentrationUnit])
def test_global_models_viewable_by_everyone_but_changed_by_researchers(make_user, model):
    user = make_user()
    researcher = make_user()
    researcher.groups.add(Group.objects.get(name=RESEARCHERS_GROUP))

    assert user.has_perm(perm_name(Action.VIEW, model))
    for action in (Action.ADD, Action.CHANGE, Action.DELETE):
        assert not user.has_perm(perm_name(action, model))
        assert researcher.has_perm(perm_name(action, model))


def test_researchers_can_add_projects(make_user):
    user = make_user()
    researcher = make_user()
    researcher.groups.add(Group.objects.get(name=RESEARCHERS_GROUP))

    assert not user.has_perm("ntx.add_project")
    assert researcher.has_perm("ntx.add_project")


def test_researchers_group_only_holds_global_permissions():
    group = Group.objects.get(name=RESEARCHERS_GROUP)
    granted = {
        (perm.content_type.model, perm.codename.split("_", 1)[0])
        for perm in group.permissions.select_related("content_type")
    }
    assert {model for model, _ in granted} == {"project", "chemical", "concentrationunit"}
    assert ("project", "add") in granted
    assert not any(model == "project" and action != "add" for model, action in granted)


def test_every_user_can_use_the_ntx_admin_module(make_user):
    assert make_user().has_module_perms("ntx")


def test_everyone_sees_all_projects_and_with_role_filters_by_membership(
    make_user, project, other_project
):
    user = make_user()
    ProjectMembership.objects.create(project=project, user=user, role=E)
    ProjectMembership.objects.create(project=other_project, user=make_user(), role=M)
    experiment = Experiment.objects.create(project=project, code="A", type=ExposureType.ACUTE)
    other = Experiment.objects.create(project=other_project, code="B", type=ExposureType.ACUTE)

    assert set(Project.objects.visible_to(user)) == set(Project.objects.all())
    assert set(Project.objects.visible_to(make_user())) == set(Project.objects.all())
    assert set(Experiment.objects.visible_to(user)) == {experiment, other}
    assert list(project.experiments.visible_to(user)) == [experiment]
    assert list(Project.objects.with_role(user, E)) == [project]
    assert list(Project.objects.with_role(user, M)) == []


def test_superuser_sees_all_projects(make_user, project, other_project):
    user = make_user(is_superuser=True)
    assert set(Project.objects.visible_to(user)) == set(Project.objects.all())
    assert {project, other_project} <= set(Project.objects.visible_to(user))


def test_can_in_project_ignores_ownership(make_user, project):
    editor = make_user()
    ProjectMembership.objects.create(project=project, user=editor, role=E)

    assert can_in_project(editor, Action.ADD, ExperimentIngest, project)
    assert not can_in_project(editor, Action.CHANGE, ExperimentIngest, project)
    assert not can_in_project(editor, Action.ADD, ExperimentIngest, None)


def test_role_cache_is_per_user_object_and_can_be_cleared(make_user, project):
    user = make_user()
    assert project_roles(user) == {}

    ProjectMembership.objects.create(project=project, user=user, role=E)
    assert project_roles(user) == {}

    clear_role_cache(user)
    assert project_roles(user) == {project.pk: E}
