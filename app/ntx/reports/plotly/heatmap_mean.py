from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go

from ntx.analysis.dtos import AnalysisPipelineResult, ConditionInfo, ParamInfo

from .contracts import PlotlyCard, PlotlyCardError, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme

HOVER_TEMPLATE = "%{x}<br>%{y:.2f}%<extra></extra>"


def build_heatmap_card(
    result: AnalysisPipelineResult,
    *,
    params: Sequence[str],
) -> list[PlotlyCard]:
    fig = _build_param_condition_heatmap(result, params)
    figure_json = serialize_figure(fig)

    return [
        PlotlyCard(
            id="heatmap:params_vs_concentration",
            title="Parameter response heatmap",
            status="ok",
            figure=PlotlyFigure(**figure_json),
            config=dict(DEFAULT_PLOTLY_CONFIG),
            meta={
                "plot_type": "heatmap",
                "params": list(params),
            },
        )
    ]


def _build_param_condition_heatmap(
    result: AnalysisPipelineResult,
    params: Sequence[str],
) -> go.Figure:
    fig = go.Figure()
    apply_theme(fig)

    # lookup tables
    param_lookup = {p.key: p for p in result.labels.params}
    conditions = sorted(result.labels.conditions, key=_condition_sort_key)

    aggregates = {
        (record.condition_label, record.param): record
        for record in result.aggregates
        if record.div == 0
    }

    # axes
    x_params = [escape_plot_text(param_lookup[p].label) for p in params]
    y_conditions = [escape_plot_text(c.label) for c in conditions]

    z_matrix: list[list[float | None]] = []

    for condition in conditions:
        row: list[float | None] = []
        for param_key in params:
            record = aggregates.get((condition.label, param_key))
            value = record.mean * 100 if record and record.mean is not None else None
            row.append(value)
        z_matrix.append(row)

    fig.add_trace(
        go.Heatmap(
            x=x_params,
            y=y_conditions,
            z=z_matrix,
            colorbar=dict(title="Response (%)"),
            hovertemplate=(
                "Condition: %{y}<br>"
                "Param: %{x}<br>"
                "Value: %{z:.2f}%<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        xaxis=dict(title="Parameters"),
        yaxis=dict(title="Concentration / Condition"),
    )
    # fig.update_layout(
    #     height=max(400, 40 * len(conditions)),
    #     margin=dict(l=120, r=40, t=60, b=80),
    # )

    return fig



def _format_param_label(param_key: str) -> str:
    return escape_plot_text(param_key.replace("_", " ").replace("  ", " ").strip().title())


def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
