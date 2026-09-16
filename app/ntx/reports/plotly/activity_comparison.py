from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

import plotly.graph_objects as go

from ntx.analysis.dtos import AnalysisPipelineResult, ConditionInfo, ParamInfo
from ntx.analysis.stats import p_value_to_stars, run_tukey_pairwise_against_control

from .contracts import PlotlyCard, PlotlyFigure
from .serialize import serialize_figure
from .text import escape_plot_text
from .theme import DEFAULT_PLOTLY_CONFIG, apply_theme

HOVER_TEMPLATE = "%{x}<br>%{y:.2f}%<extra></extra>"

# Fixed palette for coloring individual experiments' dots when
# color_by_experiment is on. Assigned cyclically by sorted experiment_id, so
# a given experiment gets the same color across every card in the report.
EXPERIMENT_COLOR_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def build_activity_comparison_cards(
    result: AnalysisPipelineResult,
    *,
    params: Sequence[str],
    activity_comparison_mode: Literal["bar", "jitter"] = "bar",
    color_by_experiment: bool = False,
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
    observation_values: dict[tuple[str, str], list[tuple[float, int]]] = {
        (obs.condition_label, obs.param): []
        for obs in result.post_outlier
        if obs.value is not None and obs.param in {param.key for param in result.labels.params}
    }
    for obs in result.post_outlier:
        if obs.value is not None:
            observation_values.setdefault((obs.condition_label, obs.param), []).append(
                (obs.value, obs.experiment_id)
            )

    experiment_color_map: dict[int, str] = {}
    experiment_legend: list[dict[str, Any]] = []
    if color_by_experiment:
        experiment_ids = sorted({obs.experiment_id for obs in result.post_outlier})
        experiment_color_map = {
            experiment_id: EXPERIMENT_COLOR_PALETTE[index % len(EXPERIMENT_COLOR_PALETTE)]
            for index, experiment_id in enumerate(experiment_ids)
        }
        # Same legend data is attached to every jitter card below - the
        # experiment set doesn't vary per card, so the frontend renders one
        # shared legend outside the plot frames rather than repeating it
        # inside each individual chart.
        experiment_legend = [
            {"experiment_id": experiment_id, "label": f"Experiment {experiment_id}", "color": color}
            for experiment_id, color in sorted(experiment_color_map.items())
        ]

    # # One panel per chemical keeps each chart to a short, readable dose series
    # # instead of flattening every chemical's conditions onto one x-axis. The
    # # shared control conditions are repeated in every chemical's group so each
    # # panel still has its own reference line/band.
    chemical_groups = _group_conditions_by_chemical(conditions)

    cards: list[PlotlyCard] = []
    card_order = 0
    for param_key in params:
        param = param_lookup.get(param_key)
        if param is None:
            raise ValueError(f"Unknown parameter requested for activity comparison: {param_key}")

        # One Tukey run per param, across every condition - not per
        # (param, chemical). Every chemical panel shares the same control
        # conditions (see _group_conditions_by_chemical above), so a
        # condition's stars vs. control come out identical whether Tukey
        # sees just that chemical's rows or every chemical's rows at once -
        # the per-condition subset happens inside run_tukey_pairwise_against_control
        # either way. Running it once per param and filtering the results
        # per chemical below avoids repeating the same DataFrame-construction
        # and pairwise_tukeyhsd overhead once per chemical for no benefit.
        significance_stars_by_condition: dict[str, str] = {}
        if activity_comparison_mode == "jitter":
            param_observations = [obs for obs in result.post_outlier if obs.param == param.key]
            significance_stars_by_condition = _build_condition_significance_stars(
                param_observations, conditions, param.key
            )

        for chemical, chemical_conditions in chemical_groups:
            chemical_condition_labels = {c.label for c in chemical_conditions}
            significance_stars = {
                condition_label: stars
                for condition_label, stars in significance_stars_by_condition.items()
                if condition_label in chemical_condition_labels
            }

            fig = _build_param_figure(
                param,
                chemical_conditions,
                aggregates,
                observation_values=observation_values,
                activity_comparison_mode=activity_comparison_mode,
                significance_stars=significance_stars,
                color_by_experiment=color_by_experiment,
                experiment_color_map=experiment_color_map,
            )
            figure_json = serialize_figure(fig)
            condition_labels = [escape_plot_text(c.label) for c in chemical_conditions]
            title = escape_plot_text(param.label)
            if chemical:
                title = f"{title} \u2014 {chemical}"
            cards.append(
                PlotlyCard(
                    id=f"activity_comparison:{param.key}:{_slugify(chemical) or 'all'}",
                    title=title,
                    figure=PlotlyFigure(**figure_json),
                    config=dict(DEFAULT_PLOTLY_CONFIG),
                    meta={
                        "plot_type": "activity_comparison",
                        "card_order": card_order,
                        "param_key": param.key,
                        "condition_labels": condition_labels,
                        "activity_comparison_mode": activity_comparison_mode,
                        "chemical": chemical,
                        "experiment_legend": experiment_legend,
                    },
                )
            )
            card_order += 1
    return cards


def _group_conditions_by_chemical(
    conditions: list[ConditionInfo],
) -> list[tuple[str, list[ConditionInfo]]]:
    """Group conditions by chemical for per-chemical panels.

    Control conditions are shared - included in every chemical's group,
    sorted alongside that chemical's own conditions - so each panel keeps
    its own control reference rather than needing a separate control-only
    panel. If there are no non-control conditions at all (e.g. a project
    with only control data selected), everything is returned as one
    unlabeled group rather than producing zero cards.
    """
    control_conditions = [c for c in conditions if c.is_control]
    treatment_conditions = [c for c in conditions if not c.is_control]

    chemicals_in_order: list[str] = []
    by_chemical: dict[str, list[ConditionInfo]] = {}
    for condition in treatment_conditions:
        chemical = condition.chemical
        if chemical not in by_chemical:
            chemicals_in_order.append(chemical)
            by_chemical[chemical] = []
        by_chemical[chemical].append(condition)

    if not chemicals_in_order:
        return [("", sorted(control_conditions, key=_condition_sort_key))]

    groups: list[tuple[str, list[ConditionInfo]]] = []
    for chemical in chemicals_in_order:
        group_conditions = sorted(
            control_conditions + by_chemical[chemical], key=_condition_sort_key
        )
        groups.append((chemical, group_conditions))
    return groups


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug


def _build_condition_significance_stars(
    observations: list[Any],
    conditions: list[ConditionInfo],
    param_key: str,
) -> dict[str, str]:
    control_labels = [condition.label for condition in conditions if condition.is_control]
    if not control_labels:
        return {}
    control_label = control_labels[0]
    try:
        results = run_tukey_pairwise_against_control(observations, control_label=control_label)
    except ValueError:
        return {}

    stars_by_condition: dict[str, str] = {}
    for result in results:
        if result.param != param_key:
            continue
        if result.stars:
            stars_by_condition[result.condition] = result.stars
    return stars_by_condition


def _build_param_figure(
    param: ParamInfo,
    conditions: list[ConditionInfo],
    aggregates: dict[tuple[str, str], Any],
    *,
    observation_values: dict[tuple[str, str], list[tuple[float, int]]],
    activity_comparison_mode: Literal["bar", "jitter"] = "bar",
    significance_stars: dict[str, str] | None = None,
    color_by_experiment: bool = False,
    experiment_color_map: dict[int, str] | None = None,
) -> go.Figure:
    fig = go.Figure()
    apply_theme(fig)

    if activity_comparison_mode == "jitter":
        return _build_jitter_figure(
            param,
            conditions,
            aggregates,
            observation_values,
            significance_stars=significance_stars or {},
            color_by_experiment=color_by_experiment,
            experiment_color_map=experiment_color_map or {},
        )

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


def _build_jitter_figure(
    param: ParamInfo,
    conditions: list[ConditionInfo],
    aggregates: dict[tuple[str, str], Any],
    observation_values: dict[tuple[str, str], list[tuple[float, int]]],
    *,
    significance_stars: dict[str, str],
    color_by_experiment: bool = False,
    experiment_color_map: dict[int, str] | None = None,
) -> go.Figure:
    fig = go.Figure()
    apply_theme(fig)

    DOT_COLOR = "rgba(100, 100, 100, 0.6)"
    DOT_SIZE = 3
    MEAN_COLOR = "#e2231a"
    MEAN_HALF_WIDTH = 0.22
    REF_LINE_COLOR = "rgba(30, 30, 30, 0.55)"
    REF_BAND_COLOR = "rgba(30, 30, 30, 0.10)"

    control_label = next((c.label for c in conditions if c.is_control), None)

    # Control reference: real control mean (dotted line) +/- SEM (shaded band),
    # drawn first so the dots and mean bars sit on top of it.
    if control_label is not None:
        control_record = aggregates.get((control_label, param.key))
        if control_record and control_record.mean is not None:
            control_mean = control_record.mean * 100
            control_sem = (control_record.sem or 0) * 100
            fig.add_hline(
                y=control_mean,
                line_dash="dot",
                line_color=REF_LINE_COLOR,
                line_width=1,
            )
            if control_sem:
                fig.add_hrect(
                    y0=control_mean - control_sem,
                    y1=control_mean + control_sem,
                    fillcolor=REF_BAND_COLOR,
                    line_width=0,
                    layer="below",
                )

    labels: list[str] = []
    max_y = 0.0
    experiment_color_map = experiment_color_map or {}
    for idx, condition in enumerate(conditions):
        label = escape_plot_text(condition.label)
        labels.append(label)
        values = observation_values.get((condition.label, param.key), [])

        if values and color_by_experiment:
            # One trace per experiment within this condition, each colored
            # from the shared experiment_color_map so a given experiment's
            # color stays consistent across every condition/panel.
            values_by_experiment: dict[int, list[float]] = {}
            for value, experiment_id in values:
                values_by_experiment.setdefault(experiment_id, []).append(value * 100)
            for experiment_id in sorted(values_by_experiment):
                y_values_for_experiment = values_by_experiment[experiment_id]
                fig.add_trace(
                    go.Box(
                        x=[label] * len(y_values_for_experiment),
                        y=y_values_for_experiment,
                        boxpoints="all",
                        jitter=0.5,
                        pointpos=0,
                        width=0.6,
                        marker={
                            "size": DOT_SIZE,
                            "color": experiment_color_map.get(experiment_id, DOT_COLOR),
                            "line": {"width": 0},
                        },
                        line={"color": "rgba(0,0,0,0)"},
                        fillcolor="rgba(0,0,0,0)",
                        showlegend=False,
                        offsetgroup=str(experiment_id),
                        hoveron="points",
                        hovertemplate=HOVER_TEMPLATE,
                    )
                )
            max_y = max(max_y, max(v for vs in values_by_experiment.values() for v in vs))
            y_values = [v for vs in values_by_experiment.values() for v in vs]
        elif values:
            y_values = [value * 100 for value, _experiment_id in values]
            fig.add_trace(
                go.Box(
                    x=[label] * len(y_values),
                    y=y_values,
                    boxpoints="all",
                    jitter=0.5,
                    pointpos=0,
                    width=0.6,
                    marker={"size": DOT_SIZE, "color": DOT_COLOR, "line": {"width": 0}},
                    line={"color": "rgba(0,0,0,0)"},
                    fillcolor="rgba(0,0,0,0)",
                    showlegend=False,
                    hoveron="points",
                    hovertemplate=HOVER_TEMPLATE,
                )
            )
            max_y = max(max_y, max(y_values))
        else:
            # No data for this condition - add an invisible, data-less trace
            # anyway so the category still appears on the axis (mirrors how
            # bar mode always adds a trace per condition, even with a null
            # mean). Without this, categories with no observations would
            # simply be missing from the axis rather than showing empty.
            fig.add_trace(
                go.Scatter(
                    x=[label],
                    y=[None],
                    mode="markers",
                    marker={"opacity": 0},
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
            y_values = []

        record = aggregates.get((condition.label, param.key))
        mean = record.mean * 100 if record and record.mean is not None else None
        if mean is not None:
            # Red mean bar, matching the reference figure, instead of a marker.
            # x0/x1 use the loop index as a numeric offset around the category:
            # with categoryorder="array" + categoryarray=labels below, category
            # positions are pinned to this same index order, so idx +/- half
            # width lands exactly on either side of that category's column.
            fig.add_shape(
                type="line",
                x0=idx - MEAN_HALF_WIDTH,
                x1=idx + MEAN_HALF_WIDTH,
                y0=mean,
                y1=mean,
                line={"color": MEAN_COLOR, "width": 3},
            )
            max_y = max(max_y, mean)

        star_text = significance_stars.get(condition.label)
        if star_text and not condition.is_control:
            bracket_y = max(y_values) * 1.06 if y_values else max_y * 1.06
            fig.add_shape(
                type="line",
                x0=idx - MEAN_HALF_WIDTH,
                x1=idx + MEAN_HALF_WIDTH,
                y0=bracket_y,
                y1=bracket_y,
                line={"color": "#334155", "width": 1},
            )
            fig.add_annotation(
                x=idx,
                y=bracket_y,
                text=star_text,
                showarrow=False,
                yshift=8,
                font={"size": 11, "color": "#0f172a"},
                xanchor="center",
                yanchor="bottom",
            )
            max_y = max(max_y, bracket_y * 1.12)

    fig.update_layout(
        showlegend=False,
        margin={"l": 60, "r": 20, "t": 20, "b": 110},
        font={"size": 11},
    )
    fig.update_xaxes(
        title_text="Condition",
        # Categories now come directly from each trace's x-values (the
        # condition labels themselves), same as bar mode - no separate
        # tickvals/ticktext array to fall out of sync with the data when
        # the number of conditions changes between renders (e.g. switching
        # between "All experiments" and one experiment). categoryarray only
        # pins the display order to match the idx used for the mean-bar and
        # significance-bracket shapes above.
        categoryorder="array",
        categoryarray=labels,
        tickangle=-30,
        tickfont={"size": 11},
        title_font={"size": 12},
        automargin=True,
    )
    fig.update_yaxes(
        title_text="Treatment response (%)",
        rangemode="tozero",
        tickfont={"size": 11},
        title_font={"size": 12},
        range=[0, max_y * 1.15 if max_y else None],
        automargin=True,
    )
    return fig


def _condition_sort_key(info: ConditionInfo) -> tuple[int, str, str, float, str]:
    concentration = float(info.concentration) if info.concentration is not None else float("inf")
    sex_prefix = info.sex_prefix or ""
    return (0 if info.is_control else 1, sex_prefix, info.chemical, concentration, info.label)
