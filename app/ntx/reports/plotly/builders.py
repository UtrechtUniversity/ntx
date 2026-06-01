from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ntx.analysis.dtos import AnalysisPipelineResult

from .activity_comparison import build_activity_comparison_cards
from .concentration_response import build_concentration_response_cards
from .contracts import PlotlyCard
from .heatmap_mean import build_heatmap_card


# Parameters shared by all plot builders
@dataclass(frozen=True, slots=True)
class PlotlyBuildContext:
    params: Sequence[str]


# Registry entry describing a plot type and how to build its cards.
@dataclass(frozen=True, slots=True)
class PlotlyCardBuilder:
    key: str
    title: str
    description: str
    build: Callable[[AnalysisPipelineResult, PlotlyBuildContext], list[PlotlyCard]]


def _build_activity_comparison(
    result: AnalysisPipelineResult, ctx: PlotlyBuildContext
) -> list[PlotlyCard]:
    # Use the existing activity comparison builder.
    return build_activity_comparison_cards(result, params=ctx.params)


def _build_heatmap(result: AnalysisPipelineResult, ctx: PlotlyBuildContext) -> list[PlotlyCard]:
    # Use the heatmap builder with the same param selection.
    return build_heatmap_card(result, params=ctx.params)


def _build_concentration_response(
    result: AnalysisPipelineResult, ctx: PlotlyBuildContext
) -> list[PlotlyCard]:
    # Use the existing concentration response builder.
    return build_concentration_response_cards(result, params=ctx.params)


# Ordered list controls option ordering in the UI.
DEFAULT_PLOTLY_BUILDERS: list[PlotlyCardBuilder] = [
    PlotlyCardBuilder(
        key="activity_comparison",
        title="Activity comparison",
        description="Treatment response (%) by condition (mean +/- SEM)",
        build=_build_activity_comparison,
    ),
    PlotlyCardBuilder(
        key="heatmap",
        title="Heatmap",
        description="Mean response (%) for selected parameters across conditions.",
        build=_build_heatmap,
    ),
    PlotlyCardBuilder(
        key="concentration_response",
        title="Concentration-response curves",
        description="Treatment response (%) by condition (mean +/- SEM)",
        build=_build_concentration_response,
    ),
]

# Quick lookup for API validation and option generation.
PLOTLY_BUILDERS_BY_KEY = {builder.key: builder for builder in DEFAULT_PLOTLY_BUILDERS}


def select_plot_builders(plot: str | None) -> list[PlotlyCardBuilder]:
    # Support comma-separated plot keys so multiple plot types can be requested.
    if plot is None or not plot.strip():
        raise ValueError("Plot selection is required.")

    plot_keys = [token.strip() for token in plot.split(",") if token.strip()]
    if not plot_keys:
        raise ValueError("Plot selection is required.")

    # Enforce strict plot keys so callers must request known builders.
    unknown_keys = [key for key in plot_keys if key not in PLOTLY_BUILDERS_BY_KEY]
    if unknown_keys:
        raise ValueError(f"Unknown plot type(s): {', '.join(unknown_keys)}.")

    builders: list[PlotlyCardBuilder] = []
    for key in plot_keys:
        builder = PLOTLY_BUILDERS_BY_KEY.get(key)
        if builder and builder not in builders:
            builders.append(builder)

    return builders


def build_plot_options() -> list[dict[str, str]]:
    # Lightweight option objects for Alpine to render the selector.
    return [
        {
            "value": builder.key,
            "label": builder.title,
            "description": builder.description,
        }
        for builder in DEFAULT_PLOTLY_BUILDERS
    ]
