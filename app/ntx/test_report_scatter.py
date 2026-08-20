from __future__ import annotations

from collections.abc import Sequence

import pytest
from django.db.models import Prefetch
from django.urls import reverse

from ntx.analysis.dtos import (
    AggregateRecord,
    AnalysisLabels,
    AnalysisPipelineResult,
    ConditionInfo,
    Observation,
    ParamInfo,
)
from ntx.exposure_types import ExposureType
from ntx.models import (
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    NeuronalMetricsFrame,
    Project,
)
from ntx.reports.plotly.correlation_scatter import build_correlation_scatter_card
from ntx.reports.service import _build_available_wells

pytestmark = pytest.mark.django_db


def _observation(condition: str, param: str, well: str, value: float | None) -> Observation:
    return Observation(
        experiment_id=1,
        div=0,
        condition_label=condition,
        param=param,
        well=well,
        value=value,
        is_control=condition == "Control",
        is_knockout=False,
        is_inactive=False,
        is_excluded=False,
    )


def _aggregate(condition: str, param: str, mean: float) -> AggregateRecord:
    return AggregateRecord(
        div=0,
        condition_label=condition,
        param=param,
        n=2,
        mean=mean,
        sem=None,
        std=None,
        q1=None,
        median=None,
        q3=None,
    )


@pytest.fixture
def scatter_result() -> AnalysisPipelineResult:
    labels = AnalysisLabels(
        params=[
            ParamInfo("x", "Burst Duration - Avg (sec)", "Network"),
            ParamInfo("y", "Number of Spikes per Burst - Avg", "Network"),
        ],
        conditions=[
            ConditionInfo("Control", "DMSO", None, None, None, True, None),
            ConditionInfo("Treatment", "Chemical", 1.0, "1", "uM", False, None),
        ],
        control_map={"Control": "Control", "Treatment": "Control"},
    )
    observations = [
        _observation("Control", "x", "A1", 1.0),
        _observation("Control", "y", "A1", 2.0),
        _observation("Control", "x", "A2", 3.0),
        _observation("Control", "y", "A2", None),
        _observation("Treatment", "x", "B1", 4.0),
        _observation("Treatment", "y", "B1", 5.0),
        _observation("Treatment", "x", "B2", None),
        _observation("Treatment", "y", "B2", 7.0),
    ]
    return AnalysisPipelineResult(
        labels=labels,
        pre_outlier=observations,
        post_outlier=observations,
        fences=[],
        aggregates=[
            _aggregate("Control", "x", 2.0),
            _aggregate("Control", "y", 2.0),
            _aggregate("Treatment", "x", 4.0),
            _aggregate("Treatment", "y", 6.0),
        ],
        outliers=[],
    )


def test_scatter_selected_well_means_each_axis_from_available_values(scatter_result):
    card = build_correlation_scatter_card(
        scatter_result,
        x_axis="x",
        y_axis="y",
        selected_wells=["A1", "A2", "B1", "B2"],
        selected_wells_mode="mean",
    )[0]
    figure = card.figure
    assert figure is not None

    assert [trace["x"] for trace in figure.data] == [[200.0], [400.0]]
    assert [trace["y"] for trace in figure.data] == [[200.0], [600.0]]


def test_scatter_one_well_uses_its_paired_values(scatter_result):
    card = build_correlation_scatter_card(
        scatter_result,
        x_axis="x",
        y_axis="y",
        selected_wells=["A1"],
        selected_wells_mode="mean",
    )[0]
    figure = card.figure
    assert figure is not None

    assert len(figure.data) == 1
    assert figure.data[0]["x"] == [100.0]
    assert figure.data[0]["y"] == [200.0]


def test_scatter_individual_wells_are_paired_and_grouped_by_condition(scatter_result):
    card = build_correlation_scatter_card(
        scatter_result,
        x_axis="x",
        y_axis="y",
        selected_wells=["A1", "A2", "B1", "B2"],
        selected_wells_mode="individual",
    )[0]
    figure = card.figure
    assert figure is not None

    assert len(figure.data) == 2
    assert [trace["name"] for trace in figure.data] == ["Control", "Treatment"]
    assert [trace["customdata"] for trace in figure.data] == [["A1"], ["B1"]]
    assert [trace["x"] for trace in figure.data] == [[100.0], [400.0]]
    assert [trace["y"] for trace in figure.data] == [[200.0], [500.0]]
    assert "Well: %{customdata}" in figure.data[0]["hovertemplate"]


def _metrics_payload(
    *, wells: list[str], params: list[str], ratios: Sequence[Sequence[float | None]]
) -> dict[str, object]:
    return {
        "params": params,
        "wells": wells,
        "baseline": [[1.0 for _ in wells] for _ in params],
        "exposure": [list(row) for row in ratios],
        "ratio": [list(row) for row in ratios],
    }


def _qc_payload(wells: list[str]) -> dict[str, object]:
    return {
        "wells": wells,
        "number_of_active_electrodes": [5 for _ in wells],
        "number_of_bursting_electrodes": [0 for _ in wells],
        "number_network_bursts_baseline": [21 for _ in wells],
    }


def _create_experiment(
    project: Project,
    *,
    code: str,
    wells: list[str],
    value: float,
) -> Experiment:
    unit, _ = ConcentrationUnit.objects.get_or_create(
        slug="um", defaults={"name": "uM", "symbol": "uM"}
    )
    control = Chemical.objects.create(name=f"DMSO {code}")
    treatment = Chemical.objects.create(name=f"Chemical {code}")
    experiment = Experiment.objects.create(
        project=project,
        code=code,
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    midpoint = len(wells) // 2
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        is_control=True,
        wells=wells[:midpoint],
    )
    Condition.objects.create(
        experiment=experiment,
        name="Treatment",
        chemical=treatment,
        concentration="1",
        unit=unit,
        is_control=False,
        wells=wells[midpoint:],
    )
    ratios = [
        [1.0 if index < midpoint else value for index in range(len(wells))],
        [1.0 if index < midpoint else value + 1 for index in range(len(wells))],
    ]
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=wells,
            params=["burst_duration", "spikes_per_burst"],
            ratios=ratios,
        ),
        qc_json=_qc_payload(wells),
    )
    return experiment


@pytest.fixture
def scatter_api_data():
    project = Project.objects.create(name="Scatter API Project")
    selected = _create_experiment(
        project,
        code="SCATTER-SELECTED",
        wells=["A1", "A2", "B1", "B2"],
        value=2.0,
    )
    other = _create_experiment(
        project,
        code="SCATTER-OTHER",
        wells=["C1", "C2", "D1", "D2"],
        value=99.0,
    )
    foreign_project = Project.objects.create(name="Foreign Scatter Project")
    foreign = _create_experiment(
        foreign_project,
        code="SCATTER-FOREIGN",
        wells=["E1", "E2", "F1", "F2"],
        value=50.0,
    )
    return project, selected, other, foreign


def _scatter_params(experiment: Experiment) -> dict[str, object]:
    return {
        "plot": "scatter",
        "experiment": experiment.id,
        "x_axis": "burst_duration",
        "y_axis": "spikes_per_burst",
    }


def test_scatter_api_requires_project_owned_experiment(client, scatter_api_data):
    project, selected, _, foreign = scatter_api_data
    url = reverse("ntx:project_report_api", kwargs={"slug": project.slug})

    assert client.get(url, {"plot": "scatter"}).status_code == 400
    params = _scatter_params(selected)
    params["experiment"] = foreign.id
    assert client.get(url, params).status_code == 400
    params["experiment"] = "not-an-id"
    assert client.get(url, params).status_code == 400


def test_scatter_api_validates_wells_and_mode(client, scatter_api_data):
    project, selected, _, _ = scatter_api_data
    url = reverse("ntx:project_report_api", kwargs={"slug": project.slug})
    params = _scatter_params(selected)

    invalid_well = client.get(url, {**params, "wells": "Z99"})
    assert invalid_well.status_code == 400
    invalid_mode = client.get(url, {**params, "selected_wells_mode": "points"})
    assert invalid_mode.status_code == 400

    response = client.get(url, {**params, "wells": " a1,A1,A2 "})
    assert response.status_code == 200
    assert response.json()["selected_wells"] == ["A1", "A2"]
    assert response.json()["selected_wells_mode"] == "mean"

    repeated = client.get(
        f"{url}?plot=scatter&experiment={selected.id}"
        "&x_axis=burst_duration&y_axis=spikes_per_burst&wells=A1,A2&wells=B1"
    )
    assert repeated.status_code == 200
    assert repeated.json()["selected_wells"] == ["A1", "A2", "B1"]

    legacy = client.get(url, {**params, "well": "A1"})
    assert legacy.status_code == 200
    assert legacy.json()["selected_wells"] == ["A1"]


def test_scatter_api_metadata_and_analysis_are_experiment_scoped(client, scatter_api_data):
    project, selected, _, _ = scatter_api_data
    url = reverse("ntx:project_report_api", kwargs={"slug": project.slug})
    response = client.get(url, _scatter_params(selected))
    assert response.status_code == 200
    payload = response.json()

    assert payload["selected_experiment"] == selected.id
    assert {item["key"] for item in payload["available_wells"]} == {
        "A1",
        "A2",
        "B1",
        "B2",
    }
    traces = payload["cards"][0]["figure"]["data"]
    assert len(traces) == 2
    assert max(value for trace in traces for value in trace["x"]) < 1_000
