from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from django.db.models import Prefetch

from ntx.analysis.dtos import AnalysisLabels, AnalysisPipelineResult, ParamInfo
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
from ntx.utils import normalize_decimals

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

    result = run_experiment_analysis(
        [selected_experiment.id],
        outlier_method=OutlierMethod(project.outlier_method),
    )
    available_wells = _build_active_well_options(selected_experiment, result)

    payload = ProjectReportExperimentMetadataPayload(
        selected_experiment=selected_experiment.id,
        available_params=available_params,
        available_wells=available_wells,
    )
    return payload.model_dump(mode="json")


def build_project_report_payload(
    project: Project,
    *,
    plot: str | None,
    params: Sequence[str] | None = None,
    x_axis: str | None = None,
    y_axis: str | None = None,
    experiment: int | None = None,
    experiments: Sequence[int] | None = None,
    chemicals: Sequence[str] | None = None,
    concentrations: Sequence[str] | None = None,
    selected_wells: list[str] | None = None,
    selected_wells_mode: str | None = None,
    outlier_method: str | OutlierMethod | None = None,
    activity_comparison_mode: str | None = None,
    color_by_experiment: bool = False,
) -> dict[str, Any]:
    """Build and serialize a Plotly report payload for a project.

    The payload contains fully formed Plotly card JSON and parameter-selection metadata. Scatter
    reports use "x_axis" and "y_axis" and require one experiment belonging to the project.
    Their well keys are normalized, deduplicated, and validated against that experiment, and
    "selected_wells_mode" defaults to "mean". Scatter payloads also include the selected
    experiment, its available wells, the normalized well selection, and the effective display mode.

    Non-scatter reports use "params" and analyze the selected project-owned experiments
    ("experiment" and/or "experiments"), or all experiments in the project when none is selected.
    "chemicals", "concentrations" and "experiments" cascade in that order: each one only accepts
    values that exist together with the selections before it (empty = all); other values are
    dropped. Invalid plot, experiment,
    parameter, axis, well, display-mode, or outlier-method selections raise "ValueError".
    """
    builder = select_plot_builder(plot)
    scatter_requested = builder.key == "scatter"
    activity_comparison_requested = builder.key == "activity_comparison"
    param_selection_mode = builder.param_selection_mode.value

    normalized_activity_comparison_mode: Literal["bar", "jitter"] = "bar"
    if activity_comparison_mode is not None:
        candidate = activity_comparison_mode.strip().lower()
        if candidate not in {"bar", "jitter"}:
            raise ValueError("activity_comparison_mode must be 'bar' or 'jitter'.")
        normalized_activity_comparison_mode = candidate  # type: ignore[assignment]

    # Merge the single "experiment" (scatter, legacy callers) with the
    # multi-select "experiments" list, keeping the user's order, no duplicates.
    requested_experiment_ids: list[int] = []
    for candidate_id in [*([experiment] if experiment is not None else []), *(experiments or [])]:
        if candidate_id not in requested_experiment_ids:
            requested_experiment_ids.append(candidate_id)
    if scatter_requested and len(requested_experiment_ids) > 1:
        raise ValueError("Scatter plot requires exactly one experiment.")

    experiments_queryset = project.experiments.all()
    if scatter_requested and requested_experiment_ids:
        condition_queryset = Condition.objects.filter(
            experiment_id=requested_experiment_ids[0]
        ).select_related(
            "chemical__canonical",
            "unit__canonical",
        )
        experiments_queryset = experiments_queryset.prefetch_related(
            Prefetch("conditions", queryset=condition_queryset)
        )
    project_experiments = list(experiments_queryset)
    experiment_by_id = {item.id: item for item in project_experiments}
    if any(item_id not in experiment_by_id for item_id in requested_experiment_ids):
        raise ValueError("Selected experiment does not belong to this project.")
    selected_experiments = [experiment_by_id[item_id] for item_id in requested_experiment_ids]
    # Echoed as "selected_experiment" only when exactly one experiment is in scope.
    selected_experiment = selected_experiments[0] if len(selected_experiments) == 1 else None
    scatter_experiment: Experiment | None = None

    filters = _ConditionFilterSelection()
    if scatter_requested:
        if selected_experiment is None:
            raise ValueError("Scatter plot requires an experiment.")
        scatter_experiment = selected_experiment
        experiment_ids = [selected_experiment.id]
    else:
        # Cascading filters, top to bottom: chemicals -> concentrations ->
        # experiments. Each list only offers values that exist together with
        # the selections above it. Selections that are no longer valid are
        # dropped, not rejected. An empty selection means "all".
        filters = _resolve_condition_filters(
            project,
            [item.id for item in project_experiments],
            chemicals=chemicals,
            concentrations=concentrations,
        )
        available_experiment_ids = set(filters.available_experiment_ids)
        selected_experiments = [
            item for item in selected_experiments if item.id in available_experiment_ids
        ]
        selected_experiment = selected_experiments[0] if len(selected_experiments) == 1 else None
        # No experiment ticked: analyze every experiment that matches the
        # chemical/concentration selection (all experiments when unfiltered).
        experiment_ids = [item.id for item in selected_experiments] or list(
            filters.available_experiment_ids
        )

    selected_outlier_method = OutlierMethod(outlier_method or project.outlier_method)
    result = run_experiment_analysis(experiment_ids, outlier_method=selected_outlier_method)

    if filters.selected_chemicals or filters.selected_concentrations:
        result = _filter_result_for_condition_selection(
            result,
            chemicals=filters.selected_chemicals,
            concentrations=filters.selected_concentrations,
        )

    selected_outlier_method = OutlierMethod(outlier_method or project.outlier_method)
    result = run_experiment_analysis(experiment_ids, outlier_method=selected_outlier_method)

    normalized_selected_wells: list[str] | None = None
    normalized_selected_wells_mode: Literal["mean", "individual"] | None = None
    active_wells: set[str] = set()
    if scatter_requested:
        if scatter_experiment is None:
            raise ValueError("Selected experiment does not belong to this project.")
        active_wells = _build_active_well_keys(result)
        if selected_wells is not None:
            normalized_selected_wells = _normalize_well_keys(selected_wells)
        elif active_wells:
            normalized_selected_wells = sorted(active_wells)
        else:
            normalized_selected_wells = None
        unknown_wells = [
            well for well in normalized_selected_wells or [] if well not in active_wells
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
        context = PlotlyBuildContext(
            params=selected_params,
            activity_comparison_mode=normalized_activity_comparison_mode,
            color_by_experiment=bool(color_by_experiment),
        )

    cards: list[PlotlyCard] = builder.build(result, context)

    available_experiments = [
        {"id": item.id, "label": f"{item.code} ({item.pk})"} for item in project_experiments
    ]
    available_wells = (
        _build_active_well_options(scatter_experiment, result)
        if scatter_experiment is not None
        else []
    )
    payload = ProjectReportPayload(
        cards=cards,
        available_params=available_params,
        default_selected_params=default_selected_params,
        selected_params=selected_params,
        param_selection_mode=param_selection_mode,
        activity_comparison_mode=(
            normalized_activity_comparison_mode if activity_comparison_requested else None
        ),
        color_by_experiment=(bool(color_by_experiment) if activity_comparison_requested else None),
        x_axis=x_axis,
        y_axis=y_axis,
        available_experiments=available_experiments,
        selected_experiment=(selected_experiment.id if selected_experiment is not None else None),
        selected_experiments=[item.id for item in selected_experiments] or None,
        selected_chemicals=filters.selected_chemicals or None,
        selected_concentrations=filters.selected_concentrations or None,
        available_chemicals=filters.available_chemicals,
        available_concentrations=filters.available_concentrations,
        available_experiment_ids=filters.available_experiment_ids,
        available_wells=available_wells,
        selected_wells=(normalized_selected_wells if param_selection_mode == "xy_axes" else None),
        selected_wells_mode=(
            normalized_selected_wells_mode if param_selection_mode == "xy_axes" else None
        ),
    )
    return payload.model_dump(mode="json", exclude_none=True)


@dataclass(slots=True)
class _ConditionFilterSelection:
    """Cascading chemical -> concentration -> experiment filter state."""

    available_chemicals: list[str] = field(default_factory=list)
    selected_chemicals: list[str] = field(default_factory=list)
    available_concentrations: list[str] = field(default_factory=list)
    selected_concentrations: list[str] = field(default_factory=list)
    available_experiment_ids: list[int] = field(default_factory=list)


def _resolve_condition_filters(
    project: Project,
    project_experiment_ids: Sequence[int],
    *,
    chemicals: Sequence[str] | None,
    concentrations: Sequence[str] | None,
) -> _ConditionFilterSelection:
    """Resolve the cascading filters from the project's non-control conditions.

    - Chemicals: every non-control chemical in the project (canonical name,
      the same name the analysis labels use).
    - Concentrations: those of the selected chemicals (all chemicals if none).
    - Experiments: those with a condition that matches both selections (all
      project experiments if nothing is selected).

    Requested values that are not available are dropped.
    """
    rows: list[tuple[int, str, Decimal | None]] = [
        (
            condition.experiment_id,
            (condition.chemical.canonical or condition.chemical).name,
            condition.concentration,
        )
        for condition in Condition.objects.filter(
            experiment__project=project,
            is_control=False,
        ).select_related("chemical__canonical")
    ]

    available_chemicals = sorted({name for _, name, _ in rows})
    selected_chemicals = [
        name for name in _normalize_text_values(chemicals) if name in available_chemicals
    ]
    if selected_chemicals:
        chemical_set = set(selected_chemicals)
        rows = [row for row in rows if row[1] in chemical_set]

    # Decimal keys compare by value, so 1.000000 and 1 are one option.
    labels_by_value: dict[Decimal, str] = {}
    for _, _, value in rows:
        if value is not None:
            labels_by_value.setdefault(value, normalize_decimals(value))
    available_concentrations = [labels_by_value[value] for value in sorted(labels_by_value)]
    requested_concentrations = _normalize_text_values(concentrations)
    selected_concentrations = [
        option
        for option in available_concentrations
        if any(
            _concentration_matches(float(option), candidate)
            for candidate in requested_concentrations
        )
    ]
    if selected_concentrations:
        rows = [
            row
            for row in rows
            if row[2] is not None
            and any(
                _concentration_matches(float(row[2]), candidate)
                for candidate in selected_concentrations
            )
        ]

    if selected_chemicals or selected_concentrations:
        matching_ids = {experiment_id for experiment_id, _, _ in rows}
        available_experiment_ids = [
            item_id for item_id in project_experiment_ids if item_id in matching_ids
        ]
    else:
        available_experiment_ids = list(project_experiment_ids)

    return _ConditionFilterSelection(
        available_chemicals=available_chemicals,
        selected_chemicals=selected_chemicals,
        available_concentrations=available_concentrations,
        selected_concentrations=selected_concentrations,
        available_experiment_ids=available_experiment_ids,
    )


def _normalize_text_values(values: Sequence[str] | None) -> list[str]:
    """Strip, drop empty values and deduplicate while keeping order."""
    normalized: list[str] = []
    for value in values or []:
        item = value.strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _filter_result_for_condition_selection(
    result: AnalysisPipelineResult,
    *,
    chemicals: Sequence[str],
    concentrations: Sequence[str],
) -> AnalysisPipelineResult:
    """Keep conditions whose chemical AND concentration are selected (empty = any),
    plus controls."""
    if not chemicals and not concentrations:
        return result

    chemical_set = set(chemicals)
    selected_labels: set[str] = set()
    matched_non_control = False
    for condition in result.labels.conditions:
        if condition.is_control:
            continue
        if chemical_set and condition.chemical not in chemical_set:
            continue
        if concentrations and not any(
            _concentration_matches(condition.concentration, candidate)
            for candidate in concentrations
        ):
            continue
        selected_labels.add(condition.label)
        matched_non_control = True

    if not matched_non_control:
        raise ValueError("No conditions match the requested chemical/concentration selection.")

    for condition in result.labels.conditions:
        if condition.is_control and condition.label in result.labels.control_map:
            selected_labels.add(condition.label)

    filtered_conditions = [
        condition for condition in result.labels.conditions if condition.label in selected_labels
    ]
    filtered_control_map = {
        key: value
        for key, value in result.labels.control_map.items()
        if key in selected_labels or value in selected_labels
    }

    return AnalysisPipelineResult(
        labels=AnalysisLabels(
            params=result.labels.params,
            conditions=filtered_conditions,
            control_map=filtered_control_map,
        ),
        pre_outlier=[
            record for record in result.pre_outlier if record.condition_label in selected_labels
        ],
        post_outlier=[
            record for record in result.post_outlier if record.condition_label in selected_labels
        ],
        fences=[record for record in result.fences if record.condition_label in selected_labels],
        aggregates=[
            record for record in result.aggregates if record.condition_label in selected_labels
        ],
        outliers=[
            record for record in result.outliers if record.condition_label in selected_labels
        ],
    )


def _concentration_matches(value: float | None, candidate: str) -> bool:
    if value is None:
        return False
    try:
        target = float(candidate)
    except ValueError:
        return str(value) == candidate or str(value).rstrip("0").rstrip(".") == candidate
    return abs(value - target) < 1e-9


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


def _build_active_well_keys(result: AnalysisPipelineResult) -> set[str]:
    active_wells: set[str] = set()
    for record in result.pre_outlier:
        if record.div == 0 and not record.is_inactive and not record.is_excluded:
            active_wells.add(record.well)
    return active_wells


def _build_active_well_options(
    experiment: Experiment,
    result: AnalysisPipelineResult,
) -> list[PlotlyWellOption]:
    active_wells = _build_active_well_keys(result)
    if not active_wells:
        return []

    by_condition: dict[str, str] = {}
    for condition in experiment.conditions.all():
        condition_label = _condition_display_label(condition)
        for well in condition.wells:
            if isinstance(well, str):
                by_condition[well] = condition_label

    available_wells: list[PlotlyWellOption] = []
    for well in sorted(active_wells):
        condition_label = by_condition.get(well, "Unknown")
        available_wells.append(
            PlotlyWellOption(
                key=well,
                label=f"{well} ({condition_label})",
            )
        )
    return available_wells


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
