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
    selected_wells: list[str] | None = None,
) -> list[PlotlyCard]:
    """Build scatter plot comparing two parameters across all conditions, one well, or multiple 
    wells.
    
    - No selected_wells (empty list/None): Show means for all conditions (all wells aggregated)
    - Single well: Show that well's actual values as a single point per condition
    - Multiple wells: Show mean values calculated from the subset of wells
    """
    if not result.labels.params:
        return []

    param_lookup = {param.key: param for param in result.labels.params}
    
    if x_axis not in param_lookup:
        raise ValueError(f"Unknown x_axis parameter: {x_axis}")
    if y_axis not in param_lookup:
        raise ValueError(f"Unknown y_axis parameter: {y_axis}")

    fig = _build_xy_scatter(result, x_axis, y_axis, param_lookup, selected_wells)
    figure_json = serialize_figure(fig)

    meta = {
        "plot_type": "scatter",
        "x_axis": x_axis,
        "y_axis": y_axis,
        "card_order": 0,
    }
    if selected_wells:
        meta["selected_wells"] = selected_wells

    return [
        PlotlyCard(
            id="scatter:xy_comparison",
            title=f"{param_lookup[x_axis].label} vs {param_lookup[y_axis].label}",
            figure=PlotlyFigure(**figure_json),
            config=dict(DEFAULT_PLOTLY_CONFIG),
            meta=meta,
        )
    ]


def _build_xy_scatter(
    result: AnalysisPipelineResult,
    x_axis: str,
    y_axis: str,
    param_lookup: dict,
    selected_wells: list[str] | None,
) -> go.Figure:
    """Create scatter plot with condition/chemical grouping, single well, or multi-well subset."""
    fig = go.Figure()
    apply_theme(fig)

    if isinstance(selected_wells, str):
        selected_wells = [selected_wells]

    if selected_wells:
        cleaned_wells = [well.strip() for well in selected_wells if well and well.strip()]
        cleaned_wells = list(dict.fromkeys(cleaned_wells))
    else:
        cleaned_wells = []

    if len(cleaned_wells) == 1:
        # Single well selection: show that well's actual values
        well = cleaned_wells[0]
        observations = {
            record.param: record
            for record in result.post_outlier
            if record.div == 0 and record.well == well
        }
        x_record = observations.get(x_axis)
        y_record = observations.get(y_axis)

        if x_record and y_record and x_record.value is not None and y_record.value is not None:
            x_val = x_record.value * 100
            y_val = y_record.value * 100
            fig.add_trace(
                go.Scatter(
                    x=[x_val],
                    y=[y_val],
                    mode="markers",
                    name=escape_plot_text(f"Well {well}"),
                    marker=dict(
                        size=12,
                        color="#1f77b4",
                        opacity=0.85,
                        line=dict(width=1, color="white"),
                    ),
                    hovertemplate=(
                        f"<b>Well {escape_plot_text(well)}</b><br>"
                        f"Condition: {escape_plot_text(x_record.condition_label)}<br>"
                        f"{param_lookup[x_axis].label}: %{{x:.2f}}%<br>"
                        f"{param_lookup[y_axis].label}: %{{y:.2f}}%<extra></extra>"
                    ),
                )
            )
    elif selected_wells and len(selected_wells) > 1:
        # Multi-well selection: calculate mean for the subset of wells
        well_set = set(selected_wells)
        # Group observations by condition and parameter
        aggregates_by_condition: dict[str, dict[str, list[float]]] = {}
        
        for record in result.post_outlier:
            if record.div == 0 and record.well in well_set and record.value is not None:
                if record.condition_label not in aggregates_by_condition:
                    aggregates_by_condition[record.condition_label] = {}
                if record.param not in aggregates_by_condition[record.condition_label]:
                    aggregates_by_condition[record.condition_label][record.param] = []
                aggregates_by_condition[record.condition_label][record.param].append(record.value)
        
        colors = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
        ]
        
        conditions = sorted(result.labels.conditions, key=_condition_sort_key)
        for idx, condition in enumerate(conditions):
            param_values = aggregates_by_condition.get(condition.label, {})
            x_values = param_values.get(x_axis, [])
            y_values = param_values.get(y_axis, [])
            
            # Calculate mean for the selected wells
            x_mean = sum(x_values) / len(x_values) if x_values else None
            y_mean = sum(y_values) / len(y_values) if y_values else None
            
            if x_mean is not None and y_mean is not None:
                x_val = x_mean * 100
                y_val = y_mean * 100
                
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
                            f"<b>{escape_plot_text(condition.label)}</b> (multi-well mean)<br>"
                            f"{param_lookup[x_axis].label}: %{{x:.2f}}%<br>"
                            f"{param_lookup[y_axis].label}: %{{y:.2f}}%<extra></extra>"
                        ),
                    )
                )
    else:
        # No well selection: show all wells aggregated means per condition (original behavior)
        conditions = sorted(result.labels.conditions, key=_condition_sort_key)
        aggregates = {
            (record.condition_label, record.param): record
            for record in result.aggregates
            if record.div == 0  # Use baseline only
        }

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
