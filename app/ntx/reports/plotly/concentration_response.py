from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ntx.analysis.dtos import AnalysisPipelineResult, ConditionInfo, ParamInfo
from ntx.analysis.stats import fit_dose_response

from .contracts import PlotlyCard, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme

HOVER_TEMPLATE = "%{x}<br>%{y:.2f}%<extra></extra>"


def build_concentration_response_cards(
    result: AnalysisPipelineResult,
    *,
    params: Sequence[str],
) -> list[PlotlyCard]:
    if not params or not result.labels.params:
        return []

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
        if param is None:
            raise ValueError(f"Unknown parameter requested for concentration response curve: {param_key}")
        fig = _build_param_figure(param, conditions, aggregates)
        figure_json = serialize_figure(fig)
        cards.append(
            PlotlyCard(
                id=f"concentration_response:{param.key}",
                title=escape_plot_text(param.label),
                figure=PlotlyFigure(**figure_json),
                config=dict(DEFAULT_PLOTLY_CONFIG),
                meta={
                    "plot_type": "concentration_response",
                    "card_order": order,
                    "param_key": param.key,
                    "condition_labels": condition_labels,
                },
            )
        )
    return cards


def _build_param_figure(
    param: ParamInfo,
    conditions: list[ConditionInfo],
    aggregates: dict[tuple[str, str], Any],
) -> go.Figure:
    param_key = param.key
    # Group conditions by compound/chemical
    compounds: dict[str, list[ConditionInfo]] = {}
    for condition in conditions:
        chemical = condition.chemical or "Unknown"
        if chemical not in compounds:
            compounds[chemical] = []
        compounds[chemical].append(condition)

    # Store fit results for table
    fit_results: list[dict[str, Any]] = []
    x_vals_by_compound: dict[str, list[float]] = {}
    y_vals_by_compound: dict[str, list[float | None]] = {}

    # For each compound, collect data and fit
    for compound in sorted(compounds.keys()):
        compound_conditions = compounds[compound]
        # Sort by concentration (controls with None concentration go to the end)
        compound_conditions_sorted = sorted(
            compound_conditions,
            key=lambda c: float(c.concentration) if c.concentration is not None else float("inf"),
        )

        x_vals: list[float] = []
        y_vals: list[float | None] = []
        error_y_vals: list[float] = []

        for condition in compound_conditions_sorted:
            if condition.concentration is None:
                continue

            record = aggregates.get((condition.label, param_key))

            x_vals.append(float(condition.concentration))

            mean = record.mean * 100 if record and record.mean is not None else None
            sem = record.sem * 100 if record and record.sem is not None else None

            y_vals.append(mean)
            error_y_vals.append(sem if sem is not None else 0)

        if x_vals:
            # Fit dose-response curve
            try:
                y_vals_filtered = [y for y in y_vals if y is not None]
                if len(x_vals) >= 2 and len(y_vals_filtered) >= 2:
                    fit = fit_dose_response(
                        x_vals,
                        y_vals_filtered,
                        sigma=[error_y_vals[i] for i, y in enumerate(y_vals) if y is not None] or None,
                    )

                    x_vals_by_compound[compound] = fit.x_fit
                    y_vals_by_compound[compound] = fit.y_fit

                    # Extract IC50/EC50 from params
                    ic50_ec50 = fit.params.get("c")
                    if ic50_ec50 is not None:
                        ic50_ec50 = f"{ic50_ec50:.4f}"
                    else:
                        ic50_ec50 = "N/A"

                    # Store fit results for table
                    fit_results.append(
                        {
                            "Compound": compound,
                            "adj_r_squared": f"{fit.adj_r2:.4f}" if fit.adj_r2 is not None else "N/A",
                            "IC50/EC50": ic50_ec50,
                            "Model": fit.model,
                            "RMSE": f"{fit.rmse:.4f}" if fit.rmse is not None else "N/A",
                        }
                    )
            except Exception:  # noqa: BLE001
                # If fit fails, skip this compound
                pass

    # Create figure with subplots: graph on top, table on bottom
    fig = make_subplots(
        rows=2,
        cols=1,
        row_heights=[0.45, 0.5],
        specs=[[{"secondary_y": False}], [{"type": "table"}]],
        vertical_spacing=0.22,
    )

    apply_theme(fig)

    # Add scatter traces for the graph
    for compound in sorted(x_vals_by_compound.keys()):
        fig.add_trace(
            go.Scatter(
                name=escape_plot_text(compound),
                x=x_vals_by_compound[compound],
                y=y_vals_by_compound[compound],
                mode="lines",
                hovertemplate=HOVER_TEMPLATE,
                showlegend=True,
            ),
            row=1,
            col=1,
        )

    # Add table with fit results
    if fit_results:
        table_data = {
            "Compound": [row["Compound"] for row in fit_results],
            "adj_r_squared": [row["adj_r_squared"] for row in fit_results],
            "IC50/EC50": [row["IC50/EC50"] for row in fit_results],
            "Model": [row["Model"] for row in fit_results],
            "RMSE": [row["RMSE"] for row in fit_results],
        }

        fig.add_trace(
        go.Table(
            columnwidth=[3, 2.5, 2.5, 1.5, 2],
            header={
                "values": list(table_data.keys()),
                "align": "center",
                "font": {"size": 8, "color": "white"},
                "fill_color": "#1f77b4",
                "height": 22,
            },
            cells={
                "values": list(table_data.values()),
                "align": "center",
                "font": {"size": 8},
                "height": 20,
                "fill_color": "white",
            },
        ),
        row=2,
        col=1,
)

    fig.update_layout(
        showlegend=True,
        margin=dict(t=40, b=40, l=80, r=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0
        )

    )   
    fig.update_xaxes(title_text="Concentration", type="log", title_standoff=5, automargin=True, row=1, col=1)
    fig.update_yaxes(title_text="Treatment response (%)", rangemode="tozero", automargin=True, row=1, col=1)
    return fig

def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
