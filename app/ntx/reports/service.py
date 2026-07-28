from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from ntx.analysis.pipeline import _condition_display_label, run_experiment_analysis
from ntx.models import OutlierMethod, Experiment, Project
from ntx.reports.plotly.builders import PlotlyBuildContext, select_plot_builders
from ntx.reports.plotly.contracts import (
    PlotlyCard,
    PlotlyParamOption,
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


def build_project_report_payload(
    project: Project,
    *,
    plot: str,
    params: Sequence[str] | None = None,
    x_axis: str | None = None,
    y_axis: str | None = None,
    experiment: int | None = None,
    outlier_method: str | OutlierMethod | None = None,
    selected_wells: list[str] | None = None,

) -> dict[str, Any]:
    """
    Build a Plotly-first report payload for a project.

    - Payload: {"version": 1, "cards": [...]} with parameter metadata.
    - Cards wrap fully-formed Plotly figure JSON.
    - For scatter plots: x_axis and y_axis parameters are used.
    - For other plots: params (multiple selection) are used.
    - selected_wells: list of well keys for multi-well aggregation (empty = all wells means).
    """
    # Build the list of available experiments for the project to populate the UI.
    experiments_qs = list(
        project.experiments.prefetch_related("conditions").all()
    )
    available_experiments = [
        {"id": exp.id, "label": f"{exp.code} ({exp.pk})"} for exp in experiments_qs
    ]

    # If a specific experiment is requested, validate and run analysis for that experiment only.
    if experiment is not None:
        experiment_ids = [int(experiment)]
    else:
        experiment_ids = [exp.id for exp in experiments_qs]

    selected_outlier_method = OutlierMethod(outlier_method or project.outlier_method)
    result = run_experiment_analysis(experiment_ids, outlier_method=selected_outlier_method)

    cards: list[PlotlyCard] = []

    available_params = [
        PlotlyParamOption(key=param.key, label=param.label, section=param.section)
        for param in result.labels.params
    ]
    available_keys = [param.key for param in available_params]
    available_key_set = set(available_keys)
    default_selected_params = [key for key in DEFAULT_REPORT_PARAMS if key in available_key_set]
    selected_params = _resolve_selected_params(
        requested_params=params,
        available_keys=available_keys,
        default_selected_params=default_selected_params,
    )

    # Determine parameter selection mode based on builder
    builders = select_plot_builders(plot)
    param_selection_mode = builders[0].param_selection_mode.value if builders else "multiple"

    # Create context with appropriate parameters
    normalized_selected_wells = None
    if param_selection_mode == "xy_axes":
        # normalized_selected_wells = None
        if selected_wells:
            normalized_selected_wells = [well.strip() for well in selected_wells 
                                        if well and well.strip()]
            normalized_selected_wells = list(dict.fromkeys(normalized_selected_wells))
            if not normalized_selected_wells:
                normalized_selected_wells = None

        context = PlotlyBuildContext(
            x_axis=x_axis,
            y_axis=y_axis,
            selected_wells=normalized_selected_wells,
        )
    else:
        context = PlotlyBuildContext(params=selected_params)

    for builder in builders:
        # Fail fast on unexpected builder errors (developer-facing).
        cards.extend(builder.build(result, context))

    payload = ProjectReportPayload(
        cards=cards,
        available_params=available_params,
        default_selected_params=default_selected_params,
        selected_params=selected_params,
        param_selection_mode=param_selection_mode,
        x_axis=x_axis,
        y_axis=y_axis,
        available_experiments=available_experiments,
        selected_experiment=int(experiment) if experiment is not None else None,
        available_wells=_build_available_wells(experiments_qs, experiment),
        selected_wells=normalized_selected_wells if param_selection_mode == "xy_axes" else None,
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


def _build_available_wells(
    experiments: Sequence[Experiment], selected_experiment_id: int | None
) -> list[dict[str, object]]:
    experiment = None
    if selected_experiment_id is not None:
        experiment = next((exp for exp in experiments if exp.id == selected_experiment_id), None)
    if experiment is None and experiments:
        experiment = experiments[0]
    if experiment is None:
        return []

    available_wells: list[dict[str, object]] = []
    for condition in experiment.conditions.all():
        condition_label = _condition_display_label(condition)
        for well in condition.wells:
            available_wells.append(
                {
                    "key": well,
                    "label": f"{well} ({condition_label})",
                }
            )
    return available_wells
