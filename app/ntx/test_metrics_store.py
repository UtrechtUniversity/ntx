from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from .exposure_types import ExposureType
from .metrics_store import fetch_experiment_metrics_frames
from .models import Experiment, NeuronalMetricsFrame, Project

pytestmark = pytest.mark.django_db


def _payload():
    return {
        "params": ["mean_firing_rate"],
        "wells": ["A1", "A2"],
        "baseline": [[1.0, 2.0]],
        "exposure": [[2.0, 4.0]],
        "ratio": [[2.0, 2.0]],
    }


def _qc_payload():
    return {
        "wells": ["A1", "A2"],
        "number_of_active_electrodes": [0, 0],
        "number_of_bursting_electrodes": [0, 0],
        "number_network_bursts_baseline": [None, None],
    }


def test_fetch_experiment_metrics_frames_returns_one_record_per_div():
    project = Project.objects.create(name="Metrics Store Project")
    experiment = Experiment.objects.create(project=project, code="EXP-1", type=ExposureType.ACUTE)

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_payload(),
        qc_json=_qc_payload(),
    )
    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=7,
        metrics_json=_payload(),
        qc_json=_qc_payload(),
    )

    frames = fetch_experiment_metrics_frames([experiment.id])
    assert [(frame.experiment_id, frame.div) for frame in frames] == [
        (experiment.id, 0),
        (experiment.id, 7),
    ]
    assert frames[0].metrics.params == ["mean_firing_rate"]
    assert frames[0].qc.wells == ["A1", "A2"]


def test_metrics_frame_unique_constraint_enforced():
    project = Project.objects.create(name="Metrics Store Unique Project")
    experiment = Experiment.objects.create(project=project, code="EXP-2", type=ExposureType.ACUTE)

    NeuronalMetricsFrame.objects.create(
        experiment=experiment,
        div=0,
        metrics_json=_payload(),
        qc_json=_qc_payload(),
    )

    duplicate = NeuronalMetricsFrame(
        experiment=experiment,
        div=0,
        metrics_json=_payload(),
        qc_json=_qc_payload(),
    )
    with pytest.raises(ValidationError):
        duplicate.save()


def test_experiment_rejects_undefined_exposure_type():
    project = Project.objects.create(name="Invalid Exposure Project")
    experiment = Experiment(
        project=project,
        code="EXP-UNDEFINED",
        type=ExposureType.UNDEFINED,
    )

    with pytest.raises(ValidationError):
        experiment.save()
