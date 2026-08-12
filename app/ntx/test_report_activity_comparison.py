from __future__ import annotations

import json
from typing import Sequence

import pytest
from django.urls import reverse

from .analysis.pipeline import run_experiment_analysis
from .exposure_types import ExposureType
from .models import (
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    NeuronalMetricsFrame,
    Project,
)
from .reports.plotly.activity_comparison import build_activity_comparison_cards
from .reports.plotly.correlation_scatter import build_correlation_scatter_card
from .reports.service import build_project_report_payload

pytestmark = pytest.mark.django_db


def _metrics_payload(
    *,
    wells: list[str],
    params: list[str],
    ratios: Sequence[Sequence[float | int | None]],
) -> dict:
    baseline = [[1.0 for _ in wells] for _ in params]
    exposure: list[list[float | int | None]] = []
    for ratio_row in ratios:
        exposure.append([None if value is None or value == -1 else value for value in ratio_row])

    return {
        "params": params,
        "wells": wells,
        "baseline": baseline,
        "exposure": exposure,
        "ratio": ratios,
    }


def _qc_payload(
    *,
    wells: list[str],
    active_electrodes: list[float | int | None] | None = None,
    network_bursts: list[float | int | None] | None = None,
) -> dict:
    if active_electrodes is None:
        active_electrodes = [5 for _ in wells]
    if network_bursts is None:
        network_bursts = [21 for _ in wells]

    return {
        "wells": wells,
        "number_of_active_electrodes": active_electrodes,
        "number_of_bursting_electrodes": [0 for _ in wells],
        "number_network_bursts_baseline": network_bursts,
    }


def test_build_activity_comparison_plot_orders_and_scales_values():
    project = Project.objects.create(name="Report Plot Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-REPORT",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="1 uM",
        chemical=chemical,
        concentration="1",
        unit=unit,
        is_control=False,
        wells=["A5", "A6"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="10 uM",
        chemical=chemical,
        concentration="10",
        unit=unit,
        is_control=False,
        wells=["A7", "A8"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="100 uM",
        chemical=chemical,
        concentration="100",
        unit=unit,
        is_control=False,
        wells=["A9", "A10"],
    )

    wells = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10"]
    params = ["number_of_spikes", "isi_coefficient_of_variation"]
    ratios = [
        [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],  # number_of_spikes
        [1, 1, 1.5, 1.5, 2, 2, 2.5, 2.5, 3, 3],  # isi_coefficient_of_variation
    ]

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(wells=wells, params=params, ratios=ratios),
        qc_json=_qc_payload(wells=wells),
    )

    result = run_experiment_analysis([experiment.id])
    cards = build_activity_comparison_cards(
        result,
        params=["number_of_spikes", "isi_coefficient_of_variation"],
    )
    assert len(cards) == 2

    card = next(entry for entry in cards if entry.meta.get("param_key") == "number_of_spikes")
    assert card.figure is not None

    series_names = [trace.get("name") for trace in card.figure.data]
    assert series_names == [
        "DMSO (control)",
        "ChemA 0.1 uM",
        "ChemA 1 uM",
        "ChemA 10 uM",
        "ChemA 100 uM",
    ]

    record = next(
        agg
        for agg in result.aggregates
        if agg.div == 0
        and agg.param == "number_of_spikes"
        and agg.condition_label == "ChemA 0.1 uM"
    )
    trace = next(t for t in card.figure.data if t.get("name") == "ChemA 0.1 uM")
    assert record.mean is not None
    assert record.sem is not None
    assert trace["y"][0] == pytest.approx(record.mean * 100)
    assert trace["error_y"]["array"][0] == pytest.approx(record.sem * 100)
    json.dumps(card.model_dump(mode="json", exclude_none=True))


def test_build_activity_comparison_plot_rejects_unknown_param():
    project = Project.objects.create(name="Report Plot Unknown Section Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-UNKNOWN-SECTION",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1"],
            params=["number_of_spikes"],
            ratios=[[1]],
        ),
        qc_json=_qc_payload(wells=["A1"]),
    )

    result = run_experiment_analysis([experiment.id])
    with pytest.raises(ValueError, match="Unknown parameter requested for activity comparison"):
        build_activity_comparison_cards(result, params=["not_a_param"])


def test_project_report_payload_includes_activity_comparison_plot():
    project = Project.objects.create(name="Report Service Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SERVICE",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A2"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1", "A2"],
            params=["number_of_spikes"],
            ratios=[[1, 2]],
        ),
        qc_json=_qc_payload(wells=["A1", "A2"]),
    )

    payload = build_project_report_payload(
        project,
        plot="activity_comparison",
        params=["number_of_spikes"],
    )
    assert payload["version"] == 1
    assert payload["cards"]
    assert payload["selected_params"] == ["number_of_spikes"]
    card = payload["cards"][0]
    assert card["type"] == "plotly"
    assert card["figure"]["data"]
    assert card["figure"]["layout"]


def test_project_report_payload_uses_standard_default_param_selection():
    project = Project.objects.create(name="Report Service Defaults Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SERVICE-DEFAULTS",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A2"],
    )

    default_params = [
        "number_of_spikes",
        "number_of_bursts",
        "burst_duration",
        "spikes_per_burst",
        "inter_burst_interval",
        "number_of_network_bursts",
        "network_burst_duration",
        "spikes_per_network_burst",
        "mean_isi_within_network_burst",
        "area_under_normalized_cross_correlation",
    ]
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1", "A2"],
            params=default_params,
            ratios=[[1, 2] for _ in default_params],
        ),
        qc_json=_qc_payload(wells=["A1", "A2"]),
    )

    payload = build_project_report_payload(project, plot="heatmap")
    assert payload["default_selected_params"] == default_params
    assert payload["selected_params"] == default_params


def test_project_report_api_returns_json(client):
    project = Project.objects.create(name="Report API Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-API",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A2"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1", "A2"],
            params=["number_of_spikes"],
            ratios=[[1, 2]],
        ),
        qc_json=_qc_payload(wells=["A1", "A2"]),
    )

    url = reverse("ntx:project_report_api", kwargs={"slug": project.slug})
    response = client.get(
        url,
        {
            "plot": "activity_comparison",
            "params": "number_of_spikes",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["cards"]
    assert payload["cards"][0]["type"] == "plotly"
    assert payload["cards"][0]["figure"]["data"]


def test_build_correlation_scatter_plot_uses_selected_well_values():
    project = Project.objects.create(name="Report Scatter Well Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SCATTER",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )

    wells = ["A1", "A2", "A3", "A4"]
    params = ["number_of_spikes", "isi_coefficient_of_variation"]
    ratios = [
        [1, 1, 2, 2],
        [1, 1, 1.5, 1.5],
    ]

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(wells=wells, params=params, ratios=ratios),
        qc_json=_qc_payload(wells=wells),
    )

    result = run_experiment_analysis([experiment.id])
    cards = build_correlation_scatter_card(
        result,
        x_axis="number_of_spikes",
        y_axis="isi_coefficient_of_variation",
        selected_wells=["A3"],
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.figure is not None
    assert len(card.figure.data) == 1
    trace = card.figure.data[0]
    assert trace["x"][0] == pytest.approx(200.0)
    assert trace["y"][0] == pytest.approx(150.0)
    assert card.meta.get("selected_wells") == ["A3"]


def test_project_report_payload_includes_available_wells_for_scatter():
    project = Project.objects.create(name="Report Scatter Payload Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SCATTER-PAYLOAD",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A2"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1", "A2"],
            params=["number_of_spikes"],
            ratios=[[1, 2]],
        ),
        qc_json=_qc_payload(wells=["A1", "A2"]),
    )

    payload = build_project_report_payload(
        project,
        plot="scatter",
        x_axis="number_of_spikes",
        y_axis="number_of_spikes",
        experiment=experiment.id,
    )

    assert payload["available_wells"] == [{"key": "A1", "label": "A1 (DMSO (control))"}, {"key": "A2", "label": "A2 (ChemA 0.1 uM)"}]
    assert payload.get("selected_wells") is None
    assert payload.get("selected_wells_mode") is None


def test_build_correlation_scatter_plot_uses_multi_well_individual_values():
    project = Project.objects.create(name="Report Scatter Multi-Well Individual Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SCATTER-MULTI-INDIV",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )

    wells = ["A1", "A2", "A3", "A4"]
    params = ["number_of_spikes", "isi_coefficient_of_variation"]
    ratios = [
        [1, 1, 2, 4],
        [1, 1, 1.5, 2.5],
    ]

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(wells=wells, params=params, ratios=ratios),
        qc_json=_qc_payload(wells=wells),
    )

    result = run_experiment_analysis([experiment.id])
    cards = build_correlation_scatter_card(
        result,
        x_axis="number_of_spikes",
        y_axis="isi_coefficient_of_variation",
        selected_wells=["A3", "A4"],
        selected_wells_mode="individual",
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.figure is not None
    assert len(card.figure.data) == 2
    x_values = [trace["x"][0] for trace in card.figure.data]
    y_values = [trace["y"][0] for trace in card.figure.data]
    assert sorted(x_values) == [pytest.approx(200.0), pytest.approx(400.0)]
    assert sorted(y_values) == [pytest.approx(150.0), pytest.approx(250.0)]
    assert card.meta.get("selected_wells") == ["A3", "A4"]


def test_build_correlation_scatter_plot_uses_multi_well_mean_values():
    """Test that multi-well selection calculates mean across the selected wells."""
    project = Project.objects.create(name="Report Scatter Multi-Well Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-SCATTER-MULTI",
        type=ExposureType.ACUTE,
        default_concentration_unit=unit,
    )
    Condition.objects.create(
        experiment=experiment,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=experiment,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )

    # Create data where A3=200%, A4=400% for spikes, A3=150%, A4=250% for isi_coef
    # Mean should be 300% for spikes, 200% for isi_coef
    wells = ["A1", "A2", "A3", "A4"]
    params = ["number_of_spikes", "isi_coefficient_of_variation"]
    ratios = [
        [1, 1, 2, 4],      # spikes: A1=100, A2=100, A3=200, A4=400
        [1, 1, 1.5, 2.5],  # isi_coef: A1=100, A2=100, A3=150, A4=250
    ]

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(wells=wells, params=params, ratios=ratios),
        qc_json=_qc_payload(wells=wells),
    )

    result = run_experiment_analysis([experiment.id])
    cards = build_correlation_scatter_card(
        result,
        x_axis="number_of_spikes",
        y_axis="isi_coefficient_of_variation",
        selected_wells=["A3", "A4"],
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.figure is not None
    assert len(card.figure.data) == 1
    trace = card.figure.data[0]
    # Mean of [200, 400] = 300%, Mean of [150, 250] = 200%
    assert trace["x"][0] == pytest.approx(300.0)
    assert trace["y"][0] == pytest.approx(200.0)
    assert card.meta.get("selected_wells") == ["A3", "A4"]
