from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import Group
from django.test import RequestFactory
from django.urls import reverse

from .admin import ExperimentIngestAdmin
from .exposure_types import ExposureType
from .groups import ADMINS_GROUP, RESEARCHERS_GROUP
from .models import (
    Chemical,
    Condition,
    Experiment,
    ExperimentIngest,
    ExperimentIngestGroup,
    Project,
    ProjectMembership,
    ProjectRole,
)

pytestmark = pytest.mark.django_db

E, M = ProjectRole.EDITOR, ProjectRole.MANAGER


@pytest.fixture
def project() -> Project:
    return Project.objects.create(name="My project")


@pytest.fixture
def other_project() -> Project:
    return Project.objects.create(name="Other project")


@pytest.fixture
def make_member(django_user_model):
    """Create a user with the given (project, role) memberships; a None role is skipped."""

    def _make(username: str, *roles: tuple[Project, ProjectRole | None]):
        user = django_user_model.objects.create_user(username=username)
        for project, role in roles:
            if role is not None:
                ProjectMembership.objects.create(project=project, user=user, role=role)
        return user

    return _make


def _experiment(project: Project, code: str, created_by=None) -> Experiment:
    experiment = Experiment.objects.create(
        project=project, code=code, type=ExposureType.ACUTE, created_by=created_by
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=Chemical.objects.get_or_create(name="DMSO")[0],
        is_control=True,
        wells=["A1"],
    )
    return experiment


def _ingest(project: Project, created_by=None, code: str | None = None) -> ExperimentIngest:
    # bulk_create skips full_clean, so no files are needed.
    (ingest,) = ExperimentIngest.objects.bulk_create(
        [ExperimentIngest(project=project, created_by=created_by, code=code)]
    )
    return ingest


class TestVisibility:
    def test_changelists_show_all_projects(self, client, make_member, project, other_project):
        client.force_login(make_member("editor", (project, E)))
        mine = _experiment(project, "MINE-1")
        other = _experiment(other_project, "OTH-1")

        response = client.get(reverse("admin:ntx_experiment_changelist"))
        assert response.status_code == 200
        assert {e.pk for e in response.context["cl"].result_list} == {mine.pk, other.pk}
        content = response.content.decode()
        assert "My project" in content
        assert "Other project" in content

    @pytest.mark.parametrize(
        "url_name",
        [
            "admin:ntx_project_changelist",
            "admin:ntx_experimentingest_changelist",
            "admin:ntx_condition_changelist",
        ],
    )
    def test_non_member_sees_other_changelists(self, client, make_member, other_project, url_name):
        client.force_login(make_member("outsider"))
        _experiment(other_project, "OTH-1")
        _ingest(other_project, code="OTH-INGEST")

        response = client.get(reverse(url_name))
        assert response.status_code == 200
        assert "Other project" in response.content.decode()

    def test_non_member_sees_objects_read_only(self, client, make_member, other_project):
        client.force_login(make_member("outsider"))
        experiment = _experiment(other_project, "OTH-1")
        ingest = _ingest(other_project, code="OTH-INGEST")

        for url in [
            reverse("admin:ntx_project_change", args=[other_project.pk]),
            reverse("admin:ntx_experiment_change", args=[experiment.pk]),
            reverse("admin:ntx_experimentingest_change", args=[ingest.pk]),
        ]:
            response = client.get(url)
            assert response.status_code == 200, url
            assert response.context["has_change_permission"] is False, url
            assert response.context["has_delete_permission"] is False, url

    @pytest.mark.parametrize(
        ("url_name", "make_object"),
        [
            ("admin:ntx_project_changelist", lambda project: project),
            (
                "admin:ntx_experiment_changelist",
                lambda project: _experiment(project, f"EXP-{project.pk}"),
            ),
            ("admin:ntx_experimentingest_changelist", lambda project: _ingest(project)),
        ],
    )
    def test_my_projects_filter(
        self, client, make_member, project, other_project, url_name, make_object
    ):
        client.force_login(make_member("editor", (project, E)))
        mine = make_object(project)
        make_object(other_project)

        response = client.get(reverse(url_name), {"mine": "1"})
        assert response.status_code == 200
        assert [obj.pk for obj in response.context["cl"].result_list] == [mine.pk]

    def test_project_filter_ignores_invalid_values(self, client, make_member):
        client.force_login(make_member("outsider"))
        response = client.get(reverse("admin:ntx_experiment_changelist"), {"project": "x"})
        assert response.status_code == 302
        assert "e=1" in response["Location"]

    def test_unit_autocomplete_is_available_to_editors(self, client, make_member, project):
        client.force_login(make_member("editor", (project, E)))
        response = client.get(
            reverse("admin:autocomplete"),
            {"app_label": "ntx", "model_name": "experimentingestgroup", "field_name": "unit"},
        )
        assert response.status_code == 200


class TestProjects:
    def test_researcher_creates_project_and_becomes_manager(self, client, make_member):
        user = make_member("researcher")
        user.groups.add(Group.objects.get(name=RESEARCHERS_GROUP))
        client.force_login(user)

        assert client.get(reverse("admin:ntx_project_changelist")).status_code == 200
        response = client.post(
            reverse("admin:ntx_project_add"),
            {
                "name": "New project",
                "slug": "new-project",
                "outlier_method": "BOXPLOT",
                "memberships-TOTAL_FORMS": "0",
                "memberships-INITIAL_FORMS": "0",
            },
        )
        assert response.status_code == 302, response.context and response.context.get("errors")

        project = Project.objects.get(slug="new-project")
        assert project.created_by == user
        assert ProjectMembership.objects.get(project=project, user=user).role == M
        assert response["Location"] == reverse("admin:ntx_project_changelist")

    def test_creator_is_manager_even_if_inline_says_otherwise(self, client, make_member):
        user = make_member("researcher")
        user.groups.add(Group.objects.get(name=RESEARCHERS_GROUP))
        client.force_login(user)

        client.post(
            reverse("admin:ntx_project_add"),
            {
                "name": "New project",
                "slug": "new-project",
                "outlier_method": "BOXPLOT",
                "memberships-TOTAL_FORMS": "1",
                "memberships-INITIAL_FORMS": "0",
                "memberships-0-user": str(user.pk),
                "memberships-0-role": E,
            },
        )
        project = Project.objects.get(slug="new-project")
        assert ProjectMembership.objects.get(project=project, user=user).role == M

    def test_non_researcher_cannot_add_projects(self, client, make_member):
        client.force_login(make_member("member"))
        assert client.get(reverse("admin:ntx_project_add")).status_code == 403

    @pytest.mark.parametrize("role", [None, E])
    def test_only_managers_can_change_project(self, client, make_member, project, role):
        client.force_login(make_member("member", (project, role)))
        url = reverse("admin:ntx_project_change", args=[project.pk])

        response = client.get(url)
        assert response.status_code == 200
        assert response.context["has_change_permission"] is False

        response = client.post(url, {"name": "Renamed"})
        assert response.status_code == 403

    def test_manager_manages_collaborators(self, client, make_member, project):
        manager = make_member("manager", (project, M))
        newcomer = make_member("newcomer")
        client.force_login(manager)
        membership = ProjectMembership.objects.get(user=manager)

        response = client.post(
            reverse("admin:ntx_project_change", args=[project.pk]),
            {
                "name": project.name,
                "slug": project.slug,
                "outlier_method": project.outlier_method,
                "memberships-TOTAL_FORMS": "2",
                "memberships-INITIAL_FORMS": "1",
                "memberships-0-id": str(membership.pk),
                "memberships-0-project": str(project.pk),
                "memberships-0-user": str(manager.pk),
                "memberships-0-role": M,
                "memberships-1-project": str(project.pk),
                "memberships-1-user": str(newcomer.pk),
                "memberships-1-role": E,
            },
        )
        assert response.status_code == 302
        assert ProjectMembership.objects.get(project=project, user=newcomer).role == E

    def test_last_manager_cannot_be_removed(self, client, make_member, project):
        manager = make_member("manager", (project, M))
        client.force_login(manager)
        membership = ProjectMembership.objects.get(user=manager)

        response = client.post(
            reverse("admin:ntx_project_change", args=[project.pk]),
            {
                "name": project.name,
                "slug": project.slug,
                "outlier_method": project.outlier_method,
                "memberships-TOTAL_FORMS": "1",
                "memberships-INITIAL_FORMS": "1",
                "memberships-0-id": str(membership.pk),
                "memberships-0-project": str(project.pk),
                "memberships-0-user": str(manager.pk),
                "memberships-0-role": E,
            },
        )
        assert response.status_code == 200
        assert "A project needs at least one manager." in response.content.decode()
        membership.refresh_from_db()
        assert membership.role == M

    def test_manager_sees_experiments_before_deleting_project(self, client, make_member, project):
        client.force_login(make_member("manager", (project, M)))
        _experiment(project, "EXP-A")
        _experiment(project, "EXP-B")
        _ingest(project)
        url = reverse("admin:ntx_project_delete", args=[project.pk])

        response = client.get(url)
        assert response.status_code == 200
        assert not response.context["perms_lacking"]
        content = response.content.decode()
        assert "Warning: this permanently deletes the project" in content
        assert 'id="experiments-to-delete"' in content
        assert "EXP-A" in content and "EXP-B" in content
        assert "2 experiments" in content
        assert "1 upload" in content

        response = client.post(url, {"post": "yes"})
        assert response.status_code == 302
        assert not Project.objects.filter(pk=project.pk).exists()
        assert not Experiment.objects.filter(code__in=["EXP-A", "EXP-B"]).exists()
        assert not ExperimentIngest.objects.filter(project_id=project.pk).exists()

    @pytest.mark.parametrize("role", [None, E])
    def test_non_managers_cannot_delete_project(self, client, make_member, project, role):
        client.force_login(make_member("member", (project, role)))
        url = reverse("admin:ntx_project_delete", args=[project.pk])
        assert client.get(url).status_code == 403
        assert client.post(url, {"post": "yes"}).status_code == 403
        assert Project.objects.filter(pk=project.pk).exists()


class TestAdmins:
    @pytest.fixture
    def admin_user(self, make_member, project):
        # An editor in `project`, without any role in other projects.
        user = make_member("admin", (project, E))
        user.groups.add(Group.objects.get(name=ADMINS_GROUP))
        return user

    def test_admin_manages_any_project(self, client, admin_user, make_member, other_project):
        manager = make_member("manager", (other_project, M))
        newcomer = make_member("newcomer")
        client.force_login(admin_user)
        membership = ProjectMembership.objects.get(user=manager)

        response = client.post(
            reverse("admin:ntx_project_change", args=[other_project.pk]),
            {
                "name": "Renamed",
                "slug": other_project.slug,
                "outlier_method": other_project.outlier_method,
                "memberships-TOTAL_FORMS": "2",
                "memberships-INITIAL_FORMS": "1",
                "memberships-0-id": str(membership.pk),
                "memberships-0-project": str(other_project.pk),
                "memberships-0-user": str(manager.pk),
                "memberships-0-role": M,
                "memberships-1-project": str(other_project.pk),
                "memberships-1-user": str(newcomer.pk),
                "memberships-1-role": E,
            },
        )
        assert response.status_code == 302
        other_project.refresh_from_db()
        assert other_project.name == "Renamed"
        assert ProjectMembership.objects.get(project=other_project, user=newcomer).role == E
        # Admin status does not create a membership.
        assert not ProjectMembership.objects.filter(project=other_project, user=admin_user)

    def test_admin_edits_and_deletes_others_objects(self, client, admin_user, other_project):
        client.force_login(admin_user)
        ingest = _ingest(other_project, code="OTH-INGEST")
        experiment = _experiment(other_project, "OTH-1")

        response = client.get(reverse("admin:ntx_experimentingest_change", args=[ingest.pk]))
        assert response.context["has_change_permission"] is True

        url = reverse("admin:ntx_experiment_delete", args=[experiment.pk])
        assert client.post(url, {"post": "yes"}).status_code == 302
        assert not Experiment.objects.filter(pk=experiment.pk).exists()

        url = reverse("admin:ntx_project_delete", args=[other_project.pk])
        assert client.post(url, {"post": "yes"}).status_code == 302
        assert not Project.objects.filter(pk=other_project.pk).exists()

    def test_admin_can_upload_to_any_project(self, client, admin_user, project, other_project):
        client.force_login(admin_user)
        response = client.get(reverse("admin:ntx_experimentingest_add"))
        choices = set(response.context["adminform"].form.fields["project"].queryset)
        assert {project, other_project} <= choices

    def test_admin_can_add_projects(self, client, admin_user):
        client.force_login(admin_user)
        assert client.get(reverse("admin:ntx_project_add")).status_code == 200


class TestExperiments:
    @pytest.mark.parametrize(("own", "expected_status"), [(True, 302), (False, 403)])
    def test_editor_deletes_only_own_experiments(
        self, client, make_member, project, own, expected_status
    ):
        editor = make_member("editor", (project, E))
        client.force_login(editor)
        experiment = _experiment(project, "EXP-1", created_by=editor if own else None)

        response = client.post(
            reverse("admin:ntx_experiment_delete", args=[experiment.pk]), {"post": "yes"}
        )
        assert response.status_code == expected_status
        assert Experiment.objects.filter(pk=experiment.pk).exists() is not own

    def test_manager_deletes_any_experiment_in_bulk(self, client, make_member, project):
        client.force_login(make_member("manager", (project, M)))
        first = _experiment(project, "EXP-1")
        second = _experiment(project, "EXP-2")

        response = client.post(
            reverse("admin:ntx_experiment_changelist"),
            {"action": "delete_selected", "_selected_action": [first.pk, second.pk], "post": "yes"},
        )
        assert response.status_code == 302
        assert not Experiment.objects.filter(pk__in=[first.pk, second.pk]).exists()

    def test_bulk_delete_refused_if_any_experiment_is_not_deletable(
        self, client, make_member, project
    ):
        editor = make_member("editor", (project, E))
        client.force_login(editor)
        own = _experiment(project, "EXP-1", created_by=editor)
        other = _experiment(project, "EXP-2")

        response = client.post(
            reverse("admin:ntx_experiment_changelist"),
            {"action": "delete_selected", "_selected_action": [own.pk, other.pk], "post": "yes"},
        )
        assert response.status_code == 403
        assert Experiment.objects.filter(pk__in=[own.pk, other.pk]).count() == 2

    def test_non_member_cannot_delete_experiments(self, client, make_member, project):
        client.force_login(make_member("outsider"))
        experiment = _experiment(project, "EXP-1")
        url = reverse("admin:ntx_experiment_delete", args=[experiment.pk])
        assert client.get(url).status_code == 403


class TestIngests:
    def test_project_choices_are_limited_to_editor_projects(
        self, client, make_member, project, other_project
    ):
        client.force_login(make_member("editor", (project, E)))

        response = client.get(reverse("admin:ntx_experimentingest_add"))
        assert response.status_code == 200
        choices = list(response.context["adminform"].form.fields["project"].queryset)
        assert choices == [project]

        response = client.post(
            reverse("admin:ntx_experimentingest_add"), {"project": other_project.pk}
        )
        assert response.status_code == 200
        assert "project" in response.context["adminform"].form.errors

    def test_non_member_cannot_add_ingests(self, client, make_member):
        client.force_login(make_member("outsider"))
        assert client.get(reverse("admin:ntx_experimentingest_add")).status_code == 403

    @pytest.mark.parametrize(("own", "can_change"), [(True, True), (False, False)])
    def test_editor_changes_only_own_ingests(self, client, make_member, project, own, can_change):
        editor = make_member("editor", (project, E))
        client.force_login(editor)
        ingest = _ingest(project, created_by=editor if own else None)

        response = client.get(reverse("admin:ntx_experimentingest_change", args=[ingest.pk]))
        assert response.status_code == 200
        assert response.context["has_change_permission"] is can_change
        inline_formsets = response.context["inline_admin_formsets"]
        assert [formset.has_change_permission for formset in inline_formsets] == [can_change]

    def test_non_member_sees_ingests_read_only_without_actions(self, client, make_member, project):
        client.force_login(make_member("outsider"))
        ingest = _ingest(project, code="ING-1")

        response = client.get(reverse("admin:ntx_experimentingest_changelist"))
        assert response.status_code == 200
        assert "ING-1" in response.content.decode()
        action_names = [
            name for name, _ in response.context["action_form"].fields["action"].choices
        ]
        assert "promote_to_experiment" not in action_names
        assert "parse_selected_uploads" not in action_names
        assert "download_parsed_metadata" in action_names

        response = client.get(reverse("admin:ntx_experimentingest_change", args=[ingest.pk]))
        assert response.status_code == 200
        assert response.context["has_change_permission"] is False

    def test_moving_others_ingest_requires_change_permission_in_target_project(
        self, make_member, project
    ):
        target = Project.objects.create(name="Target")
        user = make_member("member", (project, M), (target, E))
        ingest = _ingest(project)  # created by someone else
        request = RequestFactory().post("/")
        request.user = user
        model_admin = ExperimentIngestAdmin(ExperimentIngest, site)

        form_class = model_admin.get_form(request, ingest, change=True)
        form = form_class(data={"project": target.pk}, instance=ingest)
        assert not form.is_valid()
        assert "You do not have permission to change" in str(form.errors["project"])

        form = form_class(data={"project": project.pk}, instance=ingest)
        form.is_valid()
        assert "project" not in form.errors

    def test_actions_skip_ingests_the_user_cannot_change(self, client, make_member, project):
        editor = make_member("editor", (project, E))
        client.force_login(editor)
        own = _ingest(project, created_by=editor, code="OWN")
        other = _ingest(project, code="OTHER")
        ExperimentIngestGroup.objects.bulk_create([ExperimentIngestGroup(ingest=other)])

        response = client.post(
            reverse("admin:ntx_experimentingest_changelist"),
            {"action": "parse_selected_uploads", "_selected_action": [own.pk, other.pk]},
            follow=True,
        )
        messages = [str(message) for message in response.context["messages"]]
        assert "1 selected uploads skipped: you do not have permission to change them." in messages
        assert "0 uploads parsed, 1 failed." in messages
        other.refresh_from_db()
        assert other.status == ExperimentIngest.Status.PENDING
        assert other.ingest_groups.count() == 1

    def test_new_ingest_records_creator(self, make_member, project, monkeypatch):
        editor = make_member("editor", (project, E))
        request = RequestFactory().post("/")
        request.user = editor
        model_admin = ExperimentIngestAdmin(ExperimentIngest, site)
        ingest = ExperimentIngest(project=project)
        monkeypatch.setattr(ExperimentIngest, "save", lambda self, *args, **kwargs: None)
        monkeypatch.setattr(ExperimentIngest, "parse_files", lambda self: None)

        model_admin.save_model(request, ingest, form=None, change=False)

        assert ingest.created_by == editor
