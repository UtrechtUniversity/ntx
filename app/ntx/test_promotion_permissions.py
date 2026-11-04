from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from .exposure_types import ExposureType
from .groups import ADMINS_GROUP
from .ingest.discovery import discover_experiment_files
from .ingest.service import create_experiment_from_files
from .models import Experiment, ExperimentIngest, Project, ProjectMembership, ProjectRole
from .test_ingest_staging import _create_invalid_parsed_ingest

pytestmark = pytest.mark.django_db

E, M = ProjectRole.EDITOR, ProjectRole.MANAGER


@pytest.fixture
def project() -> Project:
    return Project.objects.get(slug="default-project")


@pytest.fixture
def member(django_user_model, project):
    def _make(username: str, role: ProjectRole):
        user = django_user_model.objects.create_user(username=username)
        ProjectMembership.objects.create(project=project, user=user, role=role)
        return user

    return _make


@pytest.fixture
def make_ingest(stored_data_dir: Path, media_root: Path):
    def _make(created_by=None) -> ExperimentIngest:
        ingest = _create_invalid_parsed_ingest(
            stored_data_dir=stored_data_dir, media_root=media_root, code="PROMO-1"
        )
        # The helper assigns overlapping wells; make the ingest valid.
        ingest.ingest_groups.filter(is_control=False).update(wells="A2")
        ingest.created_by = created_by
        ingest.save(update_fields=["created_by", "updated_at"])
        return ingest

    return _make


def test_editor_promotes_own_ingest_and_owns_the_experiment(member, make_ingest):
    editor = member("editor", E)
    experiment = make_ingest(created_by=editor).execute_ingest(acting_user=editor)
    assert experiment.created_by == editor


def test_experiment_is_credited_to_the_uploader_not_the_promoting_manager(member, make_ingest):
    editor = member("editor", E)
    manager = member("manager", M)
    experiment = make_ingest(created_by=editor).execute_ingest(acting_user=manager)
    assert experiment.created_by == editor


def test_ingest_without_creator_is_credited_to_the_promoting_user(member, make_ingest):
    manager = member("manager", M)
    experiment = make_ingest().execute_ingest(acting_user=manager)
    assert experiment.created_by == manager


def test_non_member_cannot_promote(django_user_model, make_ingest):
    outsider = django_user_model.objects.create_user(username="outsider")
    ingest = make_ingest(created_by=outsider)

    with pytest.raises(ValidationError, match="permission to add experiments"):
        ingest.execute_ingest(acting_user=outsider)

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.PARSED
    assert "permission to add experiments" in ingest.error_message
    assert not Experiment.objects.filter(code="PROMO-1").exists()


@pytest.mark.parametrize(("own", "allowed"), [(True, True), (False, False)])
def test_editor_replaces_only_own_experiments(member, make_ingest, project, own, allowed):
    editor = member("editor", E)
    existing = Experiment.objects.create(
        project=project,
        code="PROMO-1",
        type=ExposureType.ACUTE,
        created_by=editor if own else None,
    )
    ingest = make_ingest(created_by=editor)

    if allowed:
        replacement = ingest.execute_ingest(replace_existing=True, acting_user=editor)
        assert replacement.pk != existing.pk
        assert not Experiment.objects.filter(pk=existing.pk).exists()
    else:
        with pytest.raises(ValidationError, match="permission to replace experiment"):
            ingest.execute_ingest(replace_existing=True, acting_user=editor)
        assert Experiment.objects.filter(pk=existing.pk).exists()


def test_manager_replaces_any_experiment_in_project(member, make_ingest, project):
    manager = member("manager", M)
    existing = Experiment.objects.create(project=project, code="PROMO-1", type=ExposureType.ACUTE)

    make_ingest().execute_ingest(replace_existing=True, acting_user=manager)

    assert not Experiment.objects.filter(pk=existing.pk).exists()


def test_admin_without_role_replaces_any_experiment(django_user_model, make_ingest, project):
    admin = django_user_model.objects.create_user(username="admin")
    admin.groups.add(Group.objects.get(name=ADMINS_GROUP))
    existing = Experiment.objects.create(project=project, code="PROMO-1", type=ExposureType.ACUTE)

    make_ingest().execute_ingest(replace_existing=True, acting_user=admin)

    assert not Experiment.objects.filter(pk=existing.pk).exists()


@pytest.mark.parametrize("acting_as", ["superuser", "system"])
def test_experiments_in_other_projects_are_never_replaced(
    django_user_model, make_ingest, acting_as
):
    other_project = Project.objects.create(name="Other project")
    existing = Experiment.objects.create(
        project=other_project, code="PROMO-1", type=ExposureType.ACUTE
    )
    acting_user = (
        django_user_model.objects.create_superuser(username="root")
        if acting_as == "superuser"
        else None
    )
    ingest = make_ingest()

    with pytest.raises(ValidationError, match="already used in another project"):
        ingest.execute_ingest(replace_existing=True, acting_user=acting_user)

    assert Experiment.objects.get(pk=existing.pk).project == other_project


def test_service_records_creator(django_user_model, stored_data_dir: Path):
    user = django_user_model.objects.create_user(username="importer")
    folder = discover_experiment_files(stored_data_dir)
    assert folder.metadata is not None
    folder.metadata.raw["mea:type_of_exposure"] = ExposureType.ACUTE

    experiment = create_experiment_from_files(folder, created_by=user)

    assert experiment.created_by == user


def test_import_command_rejects_unknown_creator(stored_data_dir: Path):
    with pytest.raises(CommandError, match="User 'nobody' does not exist"):
        call_command("import_axion_folder", str(stored_data_dir), "--created-by", "nobody")
