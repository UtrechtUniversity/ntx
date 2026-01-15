from __future__ import annotations

from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .analysis.pipeline import AnalysisPipelineError
from .models import Condition, Experiment, Project
from .reports.service import DEFAULT_ACTIVITY_COMPARISON_PARAMS, build_project_report_payload


def projects_overview(request):
    projects = Project.objects.annotate(experiments_count=Count("experiments", distinct=True))
    return render(request, "ntx/projects_list.html", {"projects": projects})


def project_detail(request, slug: str):
    project = get_object_or_404(Project, slug=slug)
    experiments = project.experiments.all()
    return render(
        request,
        "ntx/project_detail.html",
        {"project": project, "experiments": experiments},
    )


def project_report(request, slug: str):
    project = get_object_or_404(Project, slug=slug)
    return render(request, "ntx/project_report.html", {"project": project})


@require_GET
def project_report_api(request, slug: str):
    project = get_object_or_404(Project, slug=slug)

    raw_params = request.GET.getlist("params")
    params: list[str] = []
    if len(raw_params) == 1:
        params = [item.strip() for item in raw_params[0].split(",") if item.strip()]
    elif raw_params:
        params = [item.strip() for item in raw_params if item.strip()]
    if not params:
        params = list(DEFAULT_ACTIVITY_COMPARISON_PARAMS)

    try:
        payload = build_project_report_payload(
            project,
            params=params,
        )
    except AnalysisPipelineError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(payload)


def experiments_list(request):
    experiments = (
        Experiment.objects.select_related("project")
        .prefetch_related(
            Prefetch(
                "conditions",
                queryset=Condition.objects.select_related("chemical"),
            )
        )
        .all()
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


def experiment_detail(request, pk: int):
    experiment = get_object_or_404(
        Experiment.objects.select_related("project").prefetch_related(
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
        {"experiment": experiment, "conditions": conditions},
    )
