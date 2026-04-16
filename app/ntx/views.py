from __future__ import annotations

from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .analysis.pipeline import AnalysisPipelineError
from .models import Condition, Experiment, Project
from .reports.plotly.builders import build_plot_options
from .reports.service import build_project_report_payload


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
    plot_options = build_plot_options()
    return render(
        request,
        "ntx/project_report.html",
        {
            "project": project,
            "plot_options": plot_options,
        },
    )


@require_GET
def project_report_api(request, slug: str):
    project = get_object_or_404(Project, slug=slug)

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

    try:
        payload = build_project_report_payload(
            project,
            plot=plot,
            params=params,
            x_axis=x_axis,
            y_axis=y_axis,
        )
    except (AnalysisPipelineError, ValueError) as exc:
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
