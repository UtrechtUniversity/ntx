from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from django.db.models import Count, Prefetch
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET as django_require_GET

from .analysis.pipeline import AnalysisPipelineError
from .middleware import json_api
from .models import (
    Condition,
    Experiment,
    ExperimentIngest,
    OutlierMethod,
    Project,
    ProjectRole,
)
from .permissions import Action, can_in_project, is_project_admin, project_roles, user_can
from .reports.plotly.builders import build_plot_options
from .reports.service import (
    build_project_report_experiment_metadata_payload,
    build_project_report_payload,
)


class _ViewDecorator(Protocol):
    def __call__[F: Callable[..., HttpResponseBase]](self, view: F, /) -> F: ...


# ty 0.0.83 does not preserve the generic callable variable in django-stubs.
# Describe the same decorator with a generic method to preserve view signatures.
require_GET = cast(_ViewDecorator, django_require_GET)


def get_project_or_404(request: HttpRequest, slug: str) -> Project:
    """
    Return the project if the user may view it (every logged-in user may), or raise 404.
    """
    return get_object_or_404(Project.objects.visible_to(request.user), slug=slug)


def _role_label(role: ProjectRole | None, *, is_admin: bool) -> str:
    """
    The user's own role in a project, noting admin status (which gives manager rights).
    """
    if is_admin:
        return f"{role.label} (Admin)" if role else "Admin"
    return str(role.label) if role else ""


# Query string value of `show` that limits the projects list to the user's own projects.
MY_PROJECTS = "mine"


def projects_overview(request: HttpRequest) -> HttpResponse:
    show_mine = request.GET.get("show") == MY_PROJECTS
    queryset = (
        Project.objects.with_collaborator(request.user)
        if show_mine
        else Project.objects.visible_to(request.user)
    )
    projects = list(queryset.annotate(experiments_count=Count("experiments", distinct=True)))
    roles = project_roles(request.user)
    is_admin = is_project_admin(request.user)
    for project in projects:
        setattr(project, "role_label", _role_label(roles.get(project.pk), is_admin=is_admin))
    return render(
        request,
        "ntx/projects_list.html",
        {
            "projects": projects,
            "show_mine": show_mine,
            "my_projects": MY_PROJECTS,
            "can_add_project": user_can(request.user, Action.ADD, Project),
        },
    )


def project_detail(request: HttpRequest, slug: str) -> HttpResponse:
    project = get_project_or_404(request, slug)
    experiments = project.experiments.all()
    role = project_roles(request.user).get(project.pk)
    return render(
        request,
        "ntx/project_detail.html",
        {
            "project": project,
            "experiments": experiments,
            "role_label": _role_label(role, is_admin=is_project_admin(request.user)),
            "can_change_project": user_can(request.user, Action.CHANGE, Project, project),
            "can_delete_project": user_can(request.user, Action.DELETE, Project, project),
            "can_add_ingest": can_in_project(request.user, Action.ADD, ExperimentIngest, project),
        },
    )


def project_report(request: HttpRequest, slug: str) -> HttpResponse:
    project = get_project_or_404(request, slug)
    plot_options = build_plot_options()
    experiments = [
        {"id": exp.id, "label": f"{exp.code} ({exp.pk})"} for exp in project.experiments.all()
    ]
    return render(
        request,
        "ntx/project_report.html",
        {
            "project": project,
            "plot_options": plot_options,
            "experiments": experiments,
            "outlier_method_choices": OutlierMethod.choices,
        },
    )


@json_api
@require_GET
def project_report_api(request: HttpRequest, slug: str) -> JsonResponse:
    project = get_project_or_404(request, slug)

    params_input = request.GET.getlist("params")
    params: list[str] | None = None
    if len(params_input) == 1:
        parsed_params = [item.strip() for item in params_input[0].split(",") if item.strip()]
    elif params_input:
        parsed_params = [item.strip() for item in params_input if item.strip()]
    else:
        parsed_params = []
    if parsed_params:
        params = parsed_params

    # Extract x_axis and y_axis for scatter plot
    x_axis = request.GET.get("x_axis", "").strip() or None
    y_axis = request.GET.get("y_axis", "").strip() or None

    # Pass raw plot key through the builder registry (validated downstream).
    plot = request.GET.get("plot")
    outlier_method = request.GET.get("outlier_method")

    # Optional experiment selection (required for scatter plots).
    experiment_param = request.GET.get("experiment")

    # Support plural `wells` query param (comma-separated or multiple params)
    wells_input = request.GET.getlist("wells")
    selected_wells: list[str] | None = None
    parsed = [token.strip() for item in wells_input for token in item.split(",") if token.strip()]

    if parsed:
        selected_wells = list(dict.fromkeys(parsed))
    else:
        single = request.GET.get("well", "").strip() or None
        selected_wells = [single] if single else None

    selected_wells_mode = request.GET.get("selected_wells_mode", "").strip() or None

    try:
        experiment_id = (
            int(experiment_param) if experiment_param and experiment_param.strip() else None
        )
        payload = build_project_report_payload(
            project,
            plot=plot,
            params=params,
            x_axis=x_axis,
            y_axis=y_axis,
            experiment=experiment_id,
            selected_wells=selected_wells,
            selected_wells_mode=selected_wells_mode,
            outlier_method=outlier_method,
        )
    except (AnalysisPipelineError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(payload)


@json_api
@require_GET
def project_report_metadata_api(request: HttpRequest, slug: str) -> JsonResponse:
    project = get_project_or_404(request, slug)
    experiment_param = request.GET.get("experiment")

    try:
        if experiment_param is None or not experiment_param.strip():
            raise ValueError("Experiment selection is required.")
        payload = build_project_report_experiment_metadata_payload(
            project,
            experiment=int(experiment_param),
        )
    except (AnalysisPipelineError, TypeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(payload)


def experiments_list(request: HttpRequest) -> HttpResponse:
    experiments = (
        Experiment.objects.visible_to(request.user)
        .select_related("project")
        .prefetch_related(
            Prefetch(
                "conditions",
                queryset=Condition.objects.select_related("chemical"),
            )
        )
    )

    experiment_rows: list[dict[str, object]] = []
    for experiment in experiments:
        conditions = list(experiment.conditions.all())
        chemicals = {
            condition.chemical.name for condition in conditions if not condition.is_control
        }
        control_chemicals = {
            condition.chemical.name for condition in conditions if condition.is_control
        }
        experiment_rows.append(
            {
                "experiment": experiment,
                "chemicals": sorted(chemicals),
                "control_chemicals": sorted(control_chemicals),
            }
        )

    return render(request, "ntx/experiments_list.html", {"experiment_rows": experiment_rows})


def experiment_detail(request: HttpRequest, pk: int) -> HttpResponse:
    experiment = get_object_or_404(
        Experiment.objects.visible_to(request.user)
        .select_related("project", "created_by")
        .prefetch_related(
            Prefetch(
                "conditions",
                queryset=Condition.objects.select_related("chemical", "unit").order_by("name"),
            )
        ),
        pk=pk,
    )
    conditions = experiment.conditions.all()
    return render(
        request,
        "ntx/experiment_detail.html",
        {
            "experiment": experiment,
            "conditions": conditions,
            "can_delete_experiment": user_can(request.user, Action.DELETE, Experiment, experiment),
        },
    )
