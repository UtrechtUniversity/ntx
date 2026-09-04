from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from django.db.models import Prefetch

from ntx.analysis.dtos import ParamInfo
from ntx.analysis.pipeline import (
    _condition_display_label,
    build_param_infos,
    run_experiment_analysis,
)
from ntx.metrics_store import fetch_experiment_metrics_frames
from ntx.models import Condition, Experiment, OutlierMethod, Project, _normalize_well
from ntx.reports.plotly.builders import PlotlyBuildContext, select_plot_builder
from ntx.reports.plotly.contracts import (
    PlotlyCard,
    PlotlyParamOption,
    PlotlyWellOption,
    ProjectReportExperimentMetadataPayload,
    ProjectReportPayload,
)

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PARAMS: Sequence[str] = (
    "number_of_spikes",
    "number_of_bursts",
    "burst_duration",
    "spikes_per_burst",
    "inter_burst_interval",
    "number_of_network_bursts",
    "network_burst_duration",
    "spikes_per_network_burst",
    "mean_isi_within_network_burst",
    "area_under_normalized_cross_correlation",
)


def build_project_report_experiment_metadata_payload(
    project: Project,
    *,
    experiment: int,
) -> dict[str, Any]:
    """Build parameter and well metadata for one project-owned experiment."""
    condition_queryset = Condition.objects.select_related(
        "chemical__canonical",
        "unit__canonical",
    )
    selected_experiment = (
        project.experiments.prefetch_related(Prefetch("conditions", queryset=condition_queryset))
        .filter(id=experiment)
        .first()
    )
    if selected_experiment is None:
        raise ValueError("Selected experiment does not belong to this project.")

    frames = fetch_experiment_metrics_frames([selected_experiment.id])
    param_keys = sorted({param for frame in frames for param in frame.metrics.params})
    available_params = _build_available_params(build_param_infos(param_keys))
    payload = ProjectReportExperimentMetadataPayload(
        selected_experiment=selected_experiment.id,
        available_params=available_params,
        available_wells=_build_available_wells(selected_experiment),
    )
    return payload.model_dump(mode="json")


def build_project_report_payload(
    project: Project,
    *,
    plot: str,
    params: Sequence[str] | None = None,
    x_axis: str | None = None,
    y_axis: str | None = None,
    experiment: int | None = None,
    selected_wells: list[str] | None = None,
    selected_wells_mode: str | None = None,
    outlier_method: str | OutlierMethod | None = None,
) -> dict[str, Any]:
    """Build and serialize a Plotly report payload for a project.

    The payload contains fully formed Plotly card JSON and parameter-selection metadata. Scatter
    reports use "x_axis" and "y_axis" and require one experiment belonging to the project.
    Their well keys are normalized, deduplicated, and validated against that experiment, and
    "selected_wells_mode" defaults to "mean". Scatter payloads also include the selected
    experiment, its available wells, the normalized well selection, and the effective display mode.

    Non-scatter reports use "params" and analyze either the selected project-owned experiment or
    all experiments in the project when no experiment is selected. Invalid plot, experiment,
    parameter, axis, well, display-mode, or outlier-method selections raise "ValueError".
    """
    builder = select_plot_builder(plot)
    scatter_requested = builder.key == "scatter"
    param_selection_mode = builder.param_selection_mode.value

    experiments_queryset = project.experiments.all()
    if scatter_requested and experiment is not None:
        condition_queryset = Condition.objects.filter(experiment_id=experiment).select_related(
            "chemical__canonical",
            "unit__canonical",
        )
        experiments_queryset = experiments_queryset.prefetch_related(
            Prefetch("conditions", queryset=condition_queryset)
        )
    experiments = list(experiments_queryset)
    experiment_by_id = {item.id: item for item in experiments}
    selected_experiment = experiment_by_id.get(experiment) if experiment is not None else None
    scatter_experiment: Experiment | None = None

    if scatter_requested:
        if experiment is None:
            raise ValueError("Scatter plot requires an experiment.")
        if selected_experiment is None:
            raise ValueError("Selected experiment does not belong to this project.")
        scatter_experiment = selected_experiment
        experiment_ids = [selected_experiment.id]
    elif experiment is not None:
        if selected_experiment is None:
            raise ValueError("Selected experiment does not belong to this project.")
        experiment_ids = [selected_experiment.id]
    else:
        experiment_ids = list(experiment_by_id)

    normalized_selected_wells: list[str] | None = None
    normalized_selected_wells_mode: Literal["mean", "individual"] | None = None
    if scatter_requested:
        if scatter_experiment is None:
            raise ValueError("Selected experiment does not belong to this project.")
        normalized_selected_wells = _normalize_well_keys(selected_wells)
        available_well_keys = {
            well
            for condition in scatter_experiment.conditions.all()
            for well in condition.wells
            if isinstance(well, str)
        }
        unknown_wells = [
            well for well in normalized_selected_wells or [] if well not in available_well_keys
        ]
        if unknown_wells:
            raise ValueError(
                f"Unknown well(s) for selected experiment: {', '.join(unknown_wells)}."
            )

        requested_wells_mode = (selected_wells_mode or "mean").strip().lower()
        if requested_wells_mode not in {"mean", "individual"}:
            raise ValueError("selected_wells_mode must be 'mean' or 'individual'.")
        normalized_selected_wells_mode = (
            "individual" if requested_wells_mode == "individual" else "mean"
        )

    selected_outlier_method = OutlierMethod(outlier_method or project.outlier_method)
    result = run_experiment_analysis(experiment_ids, outlier_method=selected_outlier_method)

    available_params = _build_available_params(result.labels.params)
    available_keys = [param.key for param in available_params]
    available_key_set = set(available_keys)
    default_selected_params = [key for key in DEFAULT_REPORT_PARAMS if key in available_key_set]
    selected_params = _resolve_selected_params(
        requested_params=params,
        available_keys=available_keys,
        default_selected_params=default_selected_params,
    )

    if param_selection_mode == "xy_axes":
        context = PlotlyBuildContext(
            x_axis=x_axis,
            y_axis=y_axis,
            selected_wells=normalized_selected_wells,
            selected_wells_mode=normalized_selected_wells_mode,
        )
    else:
        context = PlotlyBuildContext(params=selected_params)

    cards: list[PlotlyCard] = builder.build(result, context)

    available_experiments = [
        {"id": item.id, "label": f"{item.code} ({item.pk})"} for item in experiments
    ]
    available_wells = (
        _build_available_wells(scatter_experiment) if scatter_experiment is not None else []
    )
    payload = ProjectReportPayload(
        cards=cards,
        available_params=available_params,
        default_selected_params=default_selected_params,
        selected_params=selected_params,
        param_selection_mode=param_selection_mode,
        x_axis=x_axis,
        y_axis=y_axis,
        available_experiments=available_experiments,
        selected_experiment=(selected_experiment.id if selected_experiment is not None else None),
        available_wells=available_wells,
        selected_wells=(normalized_selected_wells if param_selection_mode == "xy_axes" else None),
        selected_wells_mode=(
            normalized_selected_wells_mode if param_selection_mode == "xy_axes" else None
        ),
    )
    return payload.model_dump(mode="json", exclude_none=True)


def _resolve_selected_params(
    *,
    requested_params: Sequence[str] | None,
    available_keys: Sequence[str],
    default_selected_params: Sequence[str],
) -> list[str]:
    if not available_keys:
        return []

    available = set(available_keys)
    normalized_requested = _normalize_param_keys(requested_params)
    if not normalized_requested:
        if default_selected_params:
            return list(default_selected_params)
        return list(available_keys)

    unknown = [key for key in normalized_requested if key not in available]
    if unknown:
        logger.error("Unknown report parameter keys requested: %s", ", ".join(unknown))
        raise ValueError(f"Unknown parameter(s) requested: {', '.join(unknown)}.")

    return normalized_requested


def _normalize_param_keys(params: Sequence[str] | None) -> list[str]:
    if not params:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in params:
        key = raw.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def _normalize_well_keys(wells: Sequence[str] | None) -> list[str] | None:
    if not wells:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in wells:
        token = raw.strip()
        if not token:
            continue
        key = _normalize_well(token)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized or None


def _build_available_params(params: Sequence[ParamInfo]) -> list[PlotlyParamOption]:
    return [
        PlotlyParamOption(key=param.key, label=param.label, section=param.section)
        for param in params
    ]


def _build_available_wells(experiment: Experiment) -> list[PlotlyWellOption]:
    available_wells: list[PlotlyWellOption] = []
    for condition in experiment.conditions.all():
        condition_label = _condition_display_label(condition)
        for well in condition.wells:
            available_wells.append(
                PlotlyWellOption(
                    key=well,
                    label=f"{well} ({condition_label})",
                )
            )
    return available_wells
