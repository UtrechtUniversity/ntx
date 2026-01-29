from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ntx.analysis.pipeline import run_experiment_analysis
from ntx.models import Experiment, Project
from ntx.reports.plotly.activity_comparison import build_activity_comparison_cards
from ntx.reports.plotly.heatmap_mean import build_heatmap_card
from ntx.reports.plotly.contracts import (
    PlotlyCard,
    PlotlyCardError,
    ProjectReportPayload,
)

DEFAULT_ACTIVITY_COMPARISON_PARAMS: Sequence[str] = (
    "number_of_spikes",
    "isi_coefficient_of_variation",
)


def build_project_report_payload(
    project: Project,
    *,
    plot: str = "activity",
    params: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Build a Plotly-first report payload for a project.

    - Payload: {"version": 1, "cards": [...], "warnings": [...]}.
    - Cards wrap fully-formed Plotly figure JSON.
    """
    experiment_ids = list(Experiment.objects.filter(project=project).values_list("id", flat=True))
    result = run_experiment_analysis(experiment_ids)

    warnings: list[str] = []
    cards: list[PlotlyCard] = []
    print('plot', plot)

    try:
        if plot == "heatmap":
            cards = build_heatmap_card(
                result,
                params=list(params) if params is not None else list(DEFAULT_ACTIVITY_COMPARISON_PARAMS),
            )
        else:
            cards = build_activity_comparison_cards(
                result,
                params=list(params) if params is not None else list(DEFAULT_ACTIVITY_COMPARISON_PARAMS),
            )

    except Exception as exc:
        warnings.append(str(exc))

        if plot == "heatmap":
            cards = [
                PlotlyCard(
                    id="heatmap:error",
                    title="Heatmap",
                    status="error",
                    error=PlotlyCardError(
                        code="CARDS_BUILD_FAILED",
                        message=str(exc),
                    ),
                    meta={"plot_type": "heatmap"},
                )
            ]
        else:
            cards = [
                PlotlyCard(
                    id="activity_comparison:error",
                    title="Activity comparison",
                    status="error",
                    error=PlotlyCardError(
                        code="CARDS_BUILD_FAILED",
                        message=str(exc),
                    ),
                    meta={"plot_type": "activity_comparison"},
                )
            ]

    payload = ProjectReportPayload(cards=cards, warnings=warnings)
    return payload.model_dump(mode="json", exclude_none=True)
