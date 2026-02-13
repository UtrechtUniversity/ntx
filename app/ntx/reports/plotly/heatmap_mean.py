from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go

from ntx.analysis.dtos import AnalysisPipelineResult, ConditionInfo

from .contracts import PlotlyCard, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme


def build_heatmap_card(
    result: AnalysisPipelineResult,
    *,
    params: Sequence[str],
) -> list[PlotlyCard]:
    if not params or not result.labels.params:
        return []

    param_lookup = {param.key: param for param in result.labels.params}
    unknown_params = [param for param in params if param not in param_lookup]
    if unknown_params:
        raise ValueError(f"Unknown parameter(s) requested for heatmap: {', '.join(unknown_params)}")

    fig = _build_param_condition_heatmap(result, params)
    figure_json = serialize_figure(fig)

    return [
        PlotlyCard(
            id="heatmap:params_vs_concentration",
            title="Parameter response heatmap",
            figure=PlotlyFigure(**figure_json),
            config=dict(DEFAULT_PLOTLY_CONFIG),
            meta={
                "plot_type": "heatmap",
                "params": list(params),
                "card_order": 0,
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
    count_matrix: list[list[int | None]] = []

    for condition in conditions:
        row: list[float | None] = []
        count_row: list[int | None] = []

        for param_key in params:
            record = aggregates.get((condition.label, param_key))
            value = record.mean * 100 if record and record.mean is not None else None
            count = record.n if record else None
            row.append(value)
            count_row.append(count)
        z_matrix.append(row)
        count_matrix.append(count_row)

    fig.add_trace(
        go.Heatmap(
            x=x_params,
            y=y_conditions,
            z=z_matrix,
            text=count_matrix,
            texttemplate="%{text}",
            textfont=dict(color="black"),
            colorbar=dict(title="Response (%)"),
            hovertemplate=("Condition: %{y}<br>Param: %{x}<br>Value: %{z:.2f}%<extra></extra>"),
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


def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
