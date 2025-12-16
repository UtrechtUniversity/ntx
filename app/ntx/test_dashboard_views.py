from __future__ import annotations

import pytest
from django.urls import reverse

from .models import Chemical, Condition, Experiment, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def project() -> Project:
    return Project.objects.create(name="My Project")


@pytest.fixture
def experiment(project: Project) -> Experiment:
    return Experiment.objects.create(project=project, code="EXP-001")


@pytest.fixture
def experiment_with_conditions(experiment: Experiment) -> Experiment:
    control = Chemical.objects.create(name="ControlChem")
    treatment = Chemical.objects.create(name="TreatmentChem")

    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        is_control=True,
        wells=["A1"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="Treatment",
        chemical=treatment,
        is_control=False,
        wells=["A2"],
    )
    return experiment


def test_projects_overview_is_home(client, project: Project, experiment: Experiment):
    response = client.get(reverse("ntx:home"))
    assert response.status_code == 200

    projects = list(response.context["projects"])
    ctx_project = next(item for item in projects if item.pk == project.pk)
    assert ctx_project.experiments_count == 1

    content = response.content.decode()
    assert "Neurotoxicology" in content
    assert f'href="{reverse("ntx:home")}"' in content
    assert f'href="{reverse("ntx:projects")}"' in content
    assert f'href="{reverse("ntx:experiments")}"' in content

    assert project.name in content
    assert reverse("ntx:project_detail", kwargs={"slug": project.slug}) in content


def test_project_detail_lists_experiments(client, project: Project, experiment: Experiment):
    response = client.get(reverse("ntx:project_detail", kwargs={"slug": project.slug}))
    assert response.status_code == 200
    assert response.context["project"].pk == project.pk

    experiments = list(response.context["experiments"])
    assert [exp.pk for exp in experiments] == [experiment.pk]
    assert experiment.code in response.content.decode()
    assert (
        reverse("ntx:experiment_detail", kwargs={"pk": experiment.pk})
        in response.content.decode()
    )


def test_experiments_list_shows_chemicals_and_control(
    client, experiment_with_conditions: Experiment
):
    response = client.get(reverse("ntx:experiments"))
    assert response.status_code == 200

    rows = response.context["experiment_rows"]
    row = next(item for item in rows if item["experiment"].pk == experiment_with_conditions.pk)
    assert row["chemicals"] == ["TreatmentChem"]
    assert row["control_chemicals"] == ["ControlChem"]

    content = response.content.decode()
    assert "TreatmentChem" in content
    assert "ControlChem" in content


def test_experiment_detail_lists_conditions(client, experiment_with_conditions: Experiment):
    response = client.get(
        reverse("ntx:experiment_detail", kwargs={"pk": experiment_with_conditions.pk})
    )
    assert response.status_code == 200
    assert response.context["experiment"].pk == experiment_with_conditions.pk

    conditions = list(response.context["conditions"])
    assert {condition.chemical.name for condition in conditions} == {"ControlChem", "TreatmentChem"}
    content = response.content.decode()
    assert "ControlChem" in content
    assert "TreatmentChem" in content
