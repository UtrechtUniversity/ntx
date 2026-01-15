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


def build_activity_comparison_cards(
    result: AnalysisPipelineResult,
    *,
    params: Sequence[str],
) -> list[PlotlyCard]:
    if not params:
        raise ValueError("No parameters requested for activity comparison.")
    if not result.labels.params:
        raise ValueError("No parameters available for activity comparison.")

    param_lookup = {param.key: param for param in result.labels.params}
    conditions = sorted(result.labels.conditions, key=_condition_sort_key)
    aggregates = {
        (record.condition_label, record.param): record
        for record in result.aggregates
        if record.div == 0
    }

    condition_labels = [escape_plot_text(condition.label) for condition in conditions]
    cards: list[PlotlyCard] = []
    for order, param_key in enumerate(params):
        param = param_lookup.get(param_key)
        title = escape_plot_text(param.label) if param else _format_param_label(param_key)
        if not param:
            cards.append(
                PlotlyCard(
                    id=f"activity_comparison:{param_key}:error",
                    title=title,
                    status="error",
                    error=PlotlyCardError(
                        code="UNKNOWN_PARAM",
                        message=f"Unknown parameter '{param_key}'.",
                    ),
                    config=dict(DEFAULT_PLOTLY_CONFIG),
                    meta={
                        "plot_type": "activity_comparison",
                        "card_order": order,
                        "param_key": param_key,
                    },
                )
            )
            continue

        try:
            fig = _build_param_figure(param, conditions, aggregates)
            figure_json = serialize_figure(fig)
            cards.append(
                PlotlyCard(
                    id=f"activity_comparison:{param.key}",
                    title=title,
                    status="ok",
                    figure=PlotlyFigure(**figure_json),
                    config=dict(DEFAULT_PLOTLY_CONFIG),
                    meta={
                        "plot_type": "activity_comparison",
                        "card_order": order,
                        "param_key": param.key,
                        "condition_labels": condition_labels,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - card-level error isolation.
            cards.append(
                PlotlyCard(
                    id=f"activity_comparison:{param.key}:error",
                    title=title,
                    status="error",
                    error=PlotlyCardError(code="FIGURE_BUILD_FAILED", message=str(exc)),
                    config=dict(DEFAULT_PLOTLY_CONFIG),
                    meta={
                        "plot_type": "activity_comparison",
                        "card_order": order,
                        "param_key": param.key,
                    },
                )
            )
    return cards


def _build_param_figure(
    param: ParamInfo,
    conditions: list[ConditionInfo],
    aggregates: dict[tuple[str, str], Any],
) -> go.Figure:
    fig = go.Figure()
    apply_theme(fig)

    x_labels: list[str] = []
    for condition in conditions:
        condition_label = escape_plot_text(condition.label)
        record = aggregates.get((condition.label, param.key))

        mean = record.mean * 100 if record and record.mean is not None else None
        sem = record.sem * 100 if record and record.sem is not None else None

        x_labels.append(condition_label)
        fig.add_trace(
            go.Bar(
                name=condition_label,
                x=[condition_label],
                y=[mean],
                error_y={"type": "data", "array": [sem], "visible": True},
                hovertemplate=HOVER_TEMPLATE,
            )
        )

    fig.update_layout(barmode="group", showlegend=False)
    fig.update_xaxes(categoryorder="array", categoryarray=x_labels, tickangle=-30, automargin=True)
    fig.update_yaxes(title_text="Treatment response (%)", rangemode="tozero", automargin=True)
    return fig


def _format_param_label(param_key: str) -> str:
    return escape_plot_text(param_key.replace("_", " ").replace("  ", " ").strip().title())


def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
