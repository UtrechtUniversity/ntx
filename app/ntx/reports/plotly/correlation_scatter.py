from __future__ import annotations

import plotly.graph_objects as go

from ntx.analysis.dtos import AnalysisPipelineResult

from .contracts import PlotlyCard, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme


def build_correlation_scatter_card(
    result: AnalysisPipelineResult,
    *,
    x_axis: str,
    y_axis: str,
) -> list[PlotlyCard]:
    """Build scatter plot comparing two parameters across all conditions."""
    if not result.labels.params:
        return []

    param_lookup = {param.key: param for param in result.labels.params}
    
    if x_axis not in param_lookup:
        raise ValueError(f"Unknown x_axis parameter: {x_axis}")
    if y_axis not in param_lookup:
        raise ValueError(f"Unknown y_axis parameter: {y_axis}")

    fig = _build_xy_scatter(result, x_axis, y_axis, param_lookup)
    figure_json = serialize_figure(fig)

    return [
        PlotlyCard(
            id="scatter:xy_comparison",
            title=f"{param_lookup[x_axis].label} vs {param_lookup[y_axis].label}",
            figure=PlotlyFigure(**figure_json),
            config=dict(DEFAULT_PLOTLY_CONFIG),
            meta={
                "plot_type": "scatter",
                "x_axis": x_axis,
                "y_axis": y_axis,
                "card_order": 0,
            },
        )
    ]


def _build_xy_scatter(
    result: AnalysisPipelineResult,
    x_axis: str,
    y_axis: str,
    param_lookup: dict,
) -> go.Figure:
    """Create scatter plot with condition/chemical grouping."""
    fig = go.Figure()
    apply_theme(fig)

    conditions = sorted(result.labels.conditions, key=_condition_sort_key)
    
    aggregates = {
        (record.condition_label, record.param): record
        for record in result.aggregates
        if record.div == 0  # Use baseline only
    }

    # Color palette for conditions
    colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ]

    for idx, condition in enumerate(conditions):
        x_record = aggregates.get((condition.label, x_axis))
        y_record = aggregates.get((condition.label, y_axis))
        
        if not x_record or not y_record:
            continue

        x_val = x_record.mean * 100 if x_record.mean is not None else None
        y_val = y_record.mean * 100 if y_record.mean is not None else None
        
        if x_val is None or y_val is None:
            continue

        fig.add_trace(
            go.Scatter(
                x=[x_val],
                y=[y_val],
                mode="markers",
                name=escape_plot_text(condition.label),
                marker=dict(
                    size=10,
                    color=colors[idx % len(colors)],
                    opacity=0.7,
                    line=dict(width=1, color="white"),
                ),
                hovertemplate=(
                    f"<b>{escape_plot_text(condition.label)}</b><br>"
                    f"{param_lookup[x_axis].label}: %{{x:.2f}}%<br>"
                    f"{param_lookup[y_axis].label}: %{{y:.2f}}%<extra></extra>"
                ),
            )
        )

    x_label = escape_plot_text(f"{param_lookup[x_axis].label}  (% of control)")
    y_label = escape_plot_text(f"{param_lookup[y_axis].label} (% of control)")

    fig.update_layout(
        xaxis=dict(
            title=x_label,
            showgrid=True,
            zeroline=False,
        ),
        yaxis=dict(
            title=y_label,
            showgrid=True,
            zeroline=False,
        ),
        hovermode="closest",
        showlegend=True,
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="right",
            x=0.99,
        ),
    )

    return fig


def _condition_sort_key(info) -> tuple[int, str, str, float, str]:
    """Sort key for conditions: controls first, then by chemical and concentration."""
    from ntx.analysis.dtos import ConditionInfo
    
    if not isinstance(info, ConditionInfo):
        # Fallback for conditions that don't have these attributes
        return (0, "", "", 0.0, str(info.label))
    
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
