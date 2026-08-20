from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import plotly.graph_objects as go

from ntx.analysis.dtos import AnalysisPipelineResult, ConditionInfo, ParamInfo

from .contracts import PlotlyCard, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme

WellDisplayMode = Literal["mean", "individual"]

COLORS = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)


def build_correlation_scatter_card(
    result: AnalysisPipelineResult,
    *,
    x_axis: str,
    y_axis: str,
    selected_wells: list[str] | None = None,
    selected_wells_mode: WellDisplayMode | None = None,
) -> list[PlotlyCard]:
    """Compare two parameters for conditions in one analyzed experiment."""
    if not result.labels.params:
        return []

    param_lookup = {param.key: param for param in result.labels.params}
    if x_axis not in param_lookup:
        raise ValueError(f"Unknown x_axis parameter: {x_axis}")
    if y_axis not in param_lookup:
        raise ValueError(f"Unknown y_axis parameter: {y_axis}")

    mode = selected_wells_mode or "mean"
    fig = _build_xy_scatter(
        result,
        x_axis=x_axis,
        y_axis=y_axis,
        param_lookup=param_lookup,
        selected_wells=selected_wells or [],
        selected_wells_mode=mode,
    )
    meta: dict[str, object] = {
        "plot_type": "scatter",
        "x_axis": x_axis,
        "y_axis": y_axis,
        "selected_wells_mode": mode,
        "card_order": 0,
    }
    if selected_wells:
        meta["selected_wells"] = selected_wells

    return [
        PlotlyCard(
            id="scatter:xy_comparison",
            title=f"{param_lookup[x_axis].label} vs {param_lookup[y_axis].label}",
            figure=PlotlyFigure(**serialize_figure(fig)),
            config=dict(DEFAULT_PLOTLY_CONFIG),
            meta=meta,
        )
    ]


def _build_xy_scatter(
    result: AnalysisPipelineResult,
    *,
    x_axis: str,
    y_axis: str,
    param_lookup: dict[str, ParamInfo],
    selected_wells: Sequence[str],
    selected_wells_mode: WellDisplayMode,
) -> go.Figure:
    fig = go.Figure()
    apply_theme(fig)

    conditions = sorted(result.labels.conditions, key=_condition_sort_key)
    if not selected_wells:
        _add_all_well_mean_traces(
            fig,
            result=result,
            conditions=conditions,
            x_axis=x_axis,
            y_axis=y_axis,
            param_lookup=param_lookup,
        )
    elif selected_wells_mode == "individual":
        _add_individual_well_traces(
            fig,
            result=result,
            conditions=conditions,
            selected_wells=set(selected_wells),
            x_axis=x_axis,
            y_axis=y_axis,
            param_lookup=param_lookup,
        )
    else:
        _add_selected_well_mean_traces(
            fig,
            result=result,
            conditions=conditions,
            selected_wells=set(selected_wells),
            x_axis=x_axis,
            y_axis=y_axis,
            param_lookup=param_lookup,
        )

    fig.update_layout(
        xaxis=dict(
            title=escape_plot_text(f"{param_lookup[x_axis].label} (% of control)"),
            showgrid=True,
            zeroline=False,
        ),
        yaxis=dict(
            title=escape_plot_text(f"{param_lookup[y_axis].label} (% of control)"),
            showgrid=True,
            zeroline=False,
        ),
        hovermode="closest",
        showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99),
    )
    return fig


def _add_all_well_mean_traces(
    fig: go.Figure,
    *,
    result: AnalysisPipelineResult,
    conditions: Sequence[ConditionInfo],
    x_axis: str,
    y_axis: str,
    param_lookup: dict[str, ParamInfo],
) -> None:
    aggregates = {
        (record.condition_label, record.param): record
        for record in result.aggregates
        if record.div == 0
    }
    for index, condition in enumerate(conditions):
        x_record = aggregates.get((condition.label, x_axis))
        y_record = aggregates.get((condition.label, y_axis))
        if x_record is None or y_record is None or x_record.mean is None or y_record.mean is None:
            continue
        _add_condition_trace(
            fig,
            condition=condition,
            color=COLORS[index % len(COLORS)],
            x=[x_record.mean * 100],
            y=[y_record.mean * 100],
            param_lookup=param_lookup,
            x_axis=x_axis,
            y_axis=y_axis,
            hover_heading=escape_plot_text(condition.label),
        )


def _add_selected_well_mean_traces(
    fig: go.Figure,
    *,
    result: AnalysisPipelineResult,
    conditions: Sequence[ConditionInfo],
    selected_wells: set[str],
    x_axis: str,
    y_axis: str,
    param_lookup: dict[str, ParamInfo],
) -> None:
    values: dict[tuple[str, str], list[float]] = {}
    for record in result.post_outlier:
        if (
            record.div == 0
            and record.well in selected_wells
            and record.value is not None
            and record.param in {x_axis, y_axis}
        ):
            values.setdefault((record.condition_label, record.param), []).append(record.value)

    for index, condition in enumerate(conditions):
        x_values = values.get((condition.label, x_axis), [])
        y_values = values.get((condition.label, y_axis), [])
        if not x_values or not y_values:
            continue
        _add_condition_trace(
            fig,
            condition=condition,
            color=COLORS[index % len(COLORS)],
            x=[sum(x_values) / len(x_values) * 100],
            y=[sum(y_values) / len(y_values) * 100],
            param_lookup=param_lookup,
            x_axis=x_axis,
            y_axis=y_axis,
            hover_heading=f"{escape_plot_text(condition.label)} (selected-well mean)",
        )


def _add_individual_well_traces(
    fig: go.Figure,
    *,
    result: AnalysisPipelineResult,
    conditions: Sequence[ConditionInfo],
    selected_wells: set[str],
    x_axis: str,
    y_axis: str,
    param_lookup: dict[str, ParamInfo],
) -> None:
    observations: dict[tuple[str, str], dict[str, float]] = {}
    for record in result.post_outlier:
        if (
            record.div == 0
            and record.well in selected_wells
            and record.value is not None
            and record.param in {x_axis, y_axis}
        ):
            observations.setdefault((record.condition_label, record.well), {})[record.param] = (
                record.value
            )

    for index, condition in enumerate(conditions):
        paired = [
            (well, values[x_axis], values[y_axis])
            for (condition_label, well), values in observations.items()
            if condition_label == condition.label and x_axis in values and y_axis in values
        ]
        paired.sort(key=lambda item: _well_sort_key(item[0]))
        if not paired:
            continue
        _add_condition_trace(
            fig,
            condition=condition,
            color=COLORS[index % len(COLORS)],
            x=[x_value * 100 for _, x_value, _ in paired],
            y=[y_value * 100 for _, _, y_value in paired],
            customdata=[well for well, _, _ in paired],
            param_lookup=param_lookup,
            x_axis=x_axis,
            y_axis=y_axis,
            hover_heading=(f"{escape_plot_text(condition.label)}<br>Well: %{{customdata}}"),
        )


def _add_condition_trace(
    fig: go.Figure,
    *,
    condition: ConditionInfo,
    color: str,
    x: list[float],
    y: list[float],
    param_lookup: dict[str, ParamInfo],
    x_axis: str,
    y_axis: str,
    hover_heading: str,
    customdata: list[str] | None = None,
) -> None:
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            customdata=customdata,
            mode="markers",
            name=escape_plot_text(condition.label),
            marker=dict(
                size=10,
                color=color,
                opacity=0.7,
                line=dict(width=1, color="white"),
            ),
            hovertemplate=(
                f"<b>{hover_heading}</b><br>"
                f"{escape_plot_text(param_lookup[x_axis].label)}: %{{x:.2f}}%<br>"
                f"{escape_plot_text(param_lookup[y_axis].label)}: %{{y:.2f}}%"
                "<extra></extra>"
            ),
        )
    )


def _well_sort_key(well: str) -> tuple[str, int, str]:
    row = well[:1].upper()
    try:
        column = int(well[1:])
    except ValueError:
        column = 0
    return row, column, well


def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    return (
        0 if info.is_control else 1,
        info.sex_prefix or "",
        info.chemical,
        concentration,
        info.label,
    )
