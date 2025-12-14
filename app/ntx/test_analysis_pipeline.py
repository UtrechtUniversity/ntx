from __future__ import annotations

from typing import Sequence

import pytest

from .analysis.pipeline import run_experiment_analysis
from .models import (
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    NeuronalMetricsFrame,
    Project,
)

pytestmark = pytest.mark.django_db


def _metrics_payload(*, wells: list[str], ratios: Sequence[float | int | None]) -> dict:
    baseline = [1.0 for _ in wells]
    exposure: list[float | int | None] = []
    for ratio in ratios:
        if ratio is None or ratio == -1:
            exposure.append(None)
        else:
            exposure.append(ratio)

    return {
        "params": ["mean_firing_rate"],
        "wells": wells,
        "baseline": [baseline],
        "exposure": [exposure],
        "ratio": [ratios],
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


def test_pipeline_normalizes_per_experiment():
    project = Project.objects.create(name="Analysis Normalization Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    exp1 = Experiment.objects.create(project=project, code="EXP-1", default_concentration_unit=unit)
    Condition.objects.create(
        experiment=exp1,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=exp1,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=exp1,
        div=0,
        metrics_json=_metrics_payload(wells=["A1", "A2", "A3", "A4"], ratios=[1, 1, 2, 2]),
        qc_json=_qc_payload(wells=["A1", "A2", "A3", "A4"]),
    )

    exp2 = Experiment.objects.create(project=project, code="EXP-2", default_concentration_unit=unit)
    Condition.objects.create(
        experiment=exp2,
        name="Control",
        chemical=control,
        concentration=None,
        unit=unit,
        is_control=True,
        wells=["A1", "A2"],
    )
    Condition.objects.create(
        experiment=exp2,
        name="0.1 uM",
        chemical=chemical,
        concentration="0.1",
        unit=unit,
        is_control=False,
        wells=["A3", "A4"],
    )
    NeuronalMetricsFrame.objects.create(
        experiment=exp2,
        div=0,
        metrics_json=_metrics_payload(wells=["A1", "A2", "A3", "A4"], ratios=[2, 2, 2, 2]),
        qc_json=_qc_payload(wells=["A1", "A2", "A3", "A4"]),
    )

    analysis = run_experiment_analysis([exp1.id, exp2.id], ignore_exclusions=False)

    values: dict[tuple[int, str], float | None] = {}
    for obs in analysis.pre_outlier:
        if obs.param != "mean_firing_rate" or obs.div != 0:
            continue
        values[(obs.experiment_id, obs.well)] = obs.value

    assert values[(exp1.id, "A1")] == pytest.approx(1.0)
    assert values[(exp1.id, "A2")] == pytest.approx(1.0)
    assert values[(exp1.id, "A3")] == pytest.approx(2.0)
    assert values[(exp1.id, "A4")] == pytest.approx(2.0)

    assert values[(exp2.id, "A1")] == pytest.approx(1.0)
    assert values[(exp2.id, "A2")] == pytest.approx(1.0)
    assert values[(exp2.id, "A3")] == pytest.approx(1.0)
    assert values[(exp2.id, "A4")] == pytest.approx(1.0)


def test_pipeline_masks_excluded_inactive_and_knockout():
    project = Project.objects.create(name="Analysis Masking Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project,
        code="EXP-MASK",
        default_concentration_unit=unit,
        excluded_wells=["A3"],
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

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(wells=["A1", "A2", "A3", "A4"], ratios=[1, 1, 2, -1]),
        qc_json=_qc_payload(
            wells=["A1", "A2", "A3", "A4"],
            active_electrodes=[5, 4, 5, 5],  # A2 inactive (<=4)
            network_bursts=[21, 21, 21, 21],
        ),
    )

    analysis = run_experiment_analysis([experiment.id], ignore_exclusions=False)

    obs_by_well: dict[str, list] = {}
    for obs in analysis.pre_outlier:
        if obs.param != "mean_firing_rate":
            continue
        obs_by_well.setdefault(obs.well, []).append(obs)

    a1 = obs_by_well["A1"][0]
    a2 = obs_by_well["A2"][0]
    a3 = obs_by_well["A3"][0]
    a4 = obs_by_well["A4"][0]

    assert a1.value == pytest.approx(1.0)
    assert a1.is_inactive is False
    assert a1.is_excluded is False
    assert a1.is_knockout is False

    assert a2.value is None
    assert a2.is_inactive is True

    assert a3.value is None
    assert a3.is_excluded is True

    assert a4.value is None
    assert a4.is_knockout is True


def test_pipeline_masks_outliers_for_non_control_conditions():
    project = Project.objects.create(name="Analysis Outlier Project")
    unit = ConcentrationUnit.objects.create(name="uM", symbol="uM", slug="um")
    chemical = Chemical.objects.create(name="ChemA")
    control = Chemical.objects.create(name="DMSO")

    experiment = Experiment.objects.create(
        project=project, code="EXP-OUTLIER", default_concentration_unit=unit
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
        wells=["A3", "A4", "A5", "A6", "A7"],
    )

    ratios = [1, 1, 1, 1, 1, 1, 100]
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_metrics_payload(
            wells=["A1", "A2", "A3", "A4", "A5", "A6", "A7"], ratios=ratios
        ),
        qc_json=_qc_payload(wells=["A1", "A2", "A3", "A4", "A5", "A6", "A7"]),
    )

    analysis = run_experiment_analysis([experiment.id], ignore_exclusions=False)

    outliers = [o for o in analysis.outliers if o.param == "mean_firing_rate" and o.well == "A7"]
    assert len(outliers) == 1
    assert outliers[0].value == pytest.approx(100.0)

    pre_value = next(
        obs.value
        for obs in analysis.pre_outlier
        if obs.param == "mean_firing_rate" and obs.well == "A7"
    )
    post_value = next(
        obs.value
        for obs in analysis.post_outlier
        if obs.param == "mean_firing_rate" and obs.well == "A7"
    )

    assert pre_value == pytest.approx(100.0)
    assert post_value is None
