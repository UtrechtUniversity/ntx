from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ntx.analysis.pipeline import run_experiment_analysis
from ntx.models import Experiment, Project
from ntx.reports.plotly.builders import PlotlyBuildContext, select_plot_builders
from ntx.reports.plotly.contracts import (
    PlotlyCard,
    PlotlyParamOption,
    ProjectReportPayload,
)

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PARAMS: Sequence[str] = (
    "number_of_spikes",
    "isi_coefficient_of_variation",
)


def build_project_report_payload(
    project: Project,
    *,
    plot: str,
    params: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Build a Plotly-first report payload for a project.

    - Payload: {"version": 1, "cards": [...]} with parameter metadata.
    - Cards wrap fully-formed Plotly figure JSON.
    """
    experiment_ids = list(Experiment.objects.filter(project=project).values_list("id", flat=True))
    result = run_experiment_analysis(experiment_ids)

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

    context = PlotlyBuildContext(params=selected_params)
    for builder in select_plot_builders(plot):
        # Fail fast on unexpected builder errors (developer-facing).
        cards.extend(builder.build(result, context))

    payload = ProjectReportPayload(
        cards=cards,
        available_params=available_params,
        default_selected_params=default_selected_params,
        selected_params=selected_params,
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
