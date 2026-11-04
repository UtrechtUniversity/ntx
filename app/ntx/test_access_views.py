from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from .exposure_types import ExposureType
from .groups import ADMINS_GROUP, RESEARCHERS_GROUP
from .models import Experiment, Project, ProjectMembership, ProjectRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def project() -> Project:
    return Project.objects.create(name="Mine")


@pytest.fixture
def other_project() -> Project:
    return Project.objects.create(name="Other")


@pytest.fixture
def experiment(project: Project) -> Experiment:
    return Experiment.objects.create(project=project, code="MINE-1", type=ExposureType.ACUTE)


@pytest.fixture
def other_experiment(other_project: Project) -> Experiment:
    return Experiment.objects.create(project=other_project, code="OTH-1", type=ExposureType.ACUTE)


@pytest.fixture
def login_as(client, django_user_model, project: Project):
    def _login(role: ProjectRole | None, *, username: str = "member"):
        user = django_user_model.objects.create_user(username=username)
        if role is not None:
            ProjectMembership.objects.create(project=project, user=user, role=role)
        client.force_login(user)
        return user

    return _login


def _project_urls(project: Project) -> list[str]:
    return [
        reverse("ntx:project_detail", kwargs={"slug": project.slug}),
        reverse("ntx:project_report", kwargs={"slug": project.slug}),
        reverse("ntx:project_report_api", kwargs={"slug": project.slug}),
        reverse("ntx:project_report_metadata_api", kwargs={"slug": project.slug}),
    ]


class TestAnonymous:
    @pytest.mark.parametrize("url_name", ["ntx:home", "ntx:projects", "ntx:experiments"])
    def test_pages_redirect_to_login(self, client, url_name):
        url = reverse(url_name)
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('login')}?next={url}"

    def test_admin_redirects_to_its_login(self, client):
        response = client.get(reverse("admin:index"))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("admin:login"))

    def test_project_pages_redirect_to_login(self, client, project, experiment):
        for url in [
            reverse("ntx:project_detail", kwargs={"slug": project.slug}),
            reverse("ntx:project_report", kwargs={"slug": project.slug}),
            reverse("ntx:experiment_detail", kwargs={"pk": experiment.pk}),
        ]:
            response = client.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith(reverse("login"))

    @pytest.mark.parametrize(
        "url_name", ["ntx:project_report_api", "ntx:project_report_metadata_api"]
    )
    def test_json_api_returns_401(self, client, project, url_name):
        response = client.get(reverse(url_name, kwargs={"slug": project.slug}))
        assert response.status_code == 401
        assert response.json() == {"error": "Authentication required."}

    @pytest.mark.parametrize("url_name", ["login", "password_reset", "admin:login"])
    def test_auth_pages_are_public(self, client, url_name):
        assert client.get(reverse(url_name)).status_code == 200

    def test_favicon_is_public(self, client):
        response = client.get("/favicon.ico")
        assert response.status_code == 301
        assert "favicon.svg" in response["Location"]


class TestNonMember:
    def test_can_view_every_project(self, client, login_as, other_project, other_experiment):
        login_as(None)
        for url in _project_urls(other_project):
            # The report APIs answer 400 without query parameters, but do not hide the project.
            assert client.get(url).status_code in (200, 400), url
        url = reverse("ntx:experiment_detail", kwargs={"pk": other_experiment.pk})
        assert client.get(url).status_code == 200

    def test_lists_show_all_projects(
        self, client, login_as, project, other_project, experiment, other_experiment
    ):
        login_as(ProjectRole.EDITOR)

        projects = client.get(reverse("ntx:projects")).context["projects"]
        assert {p.pk for p in projects} == set(Project.objects.values_list("pk", flat=True))
        assert {project.pk, other_project.pk} <= {p.pk for p in projects}

        rows = client.get(reverse("ntx:experiments")).context["experiment_rows"]
        assert {row["experiment"].pk for row in rows} == {experiment.pk, other_experiment.pk}

    def test_unknown_project_returns_404(self, client, login_as):
        login_as(None)
        url = reverse("ntx:project_detail", kwargs={"slug": "does-not-exist"})
        assert client.get(url).status_code == 404

    def test_report_api_cannot_reach_other_project_experiment(
        self, client, login_as, project, other_experiment
    ):
        login_as(None)
        url = reverse("ntx:project_report_metadata_api", kwargs={"slug": project.slug})
        response = client.get(url, {"experiment": other_experiment.pk})
        assert response.status_code == 400

    def test_sees_no_edit_links(self, client, login_as, project):
        login_as(None)
        response = client.get(reverse("ntx:project_detail", kwargs={"slug": project.slug}))
        content = response.content.decode()

        assert response.context["role_label"] == ""
        assert reverse("admin:ntx_project_change", args=[project.pk]) not in content
        assert reverse("admin:ntx_project_delete", args=[project.pk]) not in content
        assert reverse("admin:ntx_experimentingest_add") not in content


class TestMyProjects:
    def test_my_projects_lists_only_collaborations(self, client, login_as, project, other_project):
        login_as(ProjectRole.EDITOR)
        response = client.get(reverse("ntx:projects"), {"show": "mine"})

        assert response.context["show_mine"] is True
        assert [p.pk for p in response.context["projects"]] == [project.pk]
        assert response.context["projects"][0].role_label == "Editor"

    def test_admin_status_does_not_add_to_my_projects(self, client, login_as):
        user = login_as(None)
        user.groups.add(Group.objects.get(name=ADMINS_GROUP))
        response = client.get(reverse("ntx:projects"), {"show": "mine"})

        assert list(response.context["projects"]) == []
        assert "You are not a collaborator in any project yet." in response.content.decode()

    def test_all_projects_is_the_default(self, client, login_as, project, other_project):
        login_as(ProjectRole.EDITOR)
        response = client.get(reverse("ntx:projects"))

        assert response.context["show_mine"] is False
        assert {project.pk, other_project.pk} <= {p.pk for p in response.context["projects"]}
        assert "?show=mine" in response.content.decode()


class TestMemberLinks:
    def test_editor_can_upload(self, client, login_as, project):
        login_as(ProjectRole.EDITOR)
        response = client.get(reverse("ntx:project_detail", kwargs={"slug": project.slug}))
        content = response.content.decode()

        assert response.context["role_label"] == "Editor"

        assert f"{reverse('admin:ntx_experimentingest_add')}?project={project.pk}" in content
        assert reverse("admin:ntx_project_change", args=[project.pk]) not in content

    def test_manager_can_edit_and_delete(self, client, login_as, project):
        login_as(ProjectRole.MANAGER)
        response = client.get(reverse("ntx:project_detail", kwargs={"slug": project.slug}))
        content = response.content.decode()

        assert reverse("admin:ntx_project_change", args=[project.pk]) in content
        assert reverse("admin:ntx_project_delete", args=[project.pk]) in content

    @pytest.mark.parametrize(
        ("role", "label"), [(None, "Admin"), (ProjectRole.EDITOR, "Editor (Admin)")]
    )
    def test_admin_can_edit_and_delete_any_project(self, client, login_as, project, role, label):
        user = login_as(role)
        user.groups.add(Group.objects.get(name=ADMINS_GROUP))
        response = client.get(reverse("ntx:project_detail", kwargs={"slug": project.slug}))
        content = response.content.decode()

        assert response.context["role_label"] == label
        assert reverse("admin:ntx_project_change", args=[project.pk]) in content
        assert reverse("admin:ntx_project_delete", args=[project.pk]) in content
        assert f"{reverse('admin:ntx_experimentingest_add')}?project={project.pk}" in content
        assert (
            reverse("admin:ntx_project_add") in client.get(reverse("ntx:projects")).content.decode()
        )

    def test_only_researchers_see_new_project_link(self, client, login_as):
        user = login_as(None)
        add_url = reverse("admin:ntx_project_add")
        assert add_url not in client.get(reverse("ntx:projects")).content.decode()

        user.groups.add(Group.objects.get(name=RESEARCHERS_GROUP))
        assert add_url in client.get(reverse("ntx:projects")).content.decode()

    @pytest.mark.parametrize("own", [True, False])
    def test_editor_can_delete_only_own_experiment(self, client, login_as, project, own):
        user = login_as(ProjectRole.EDITOR)
        experiment = Experiment.objects.create(
            project=project,
            code="OWN-1",
            type=ExposureType.ACUTE,
            created_by=user if own else None,
        )
        response = client.get(reverse("ntx:experiment_detail", kwargs={"pk": experiment.pk}))
        delete_url = reverse("admin:ntx_experiment_delete", args=[experiment.pk])

        assert (delete_url in response.content.decode()) is own
