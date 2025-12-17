from __future__ import annotations

from .ingest.electrode_correction import apply_electrode_correction
from .ingest.parsers import AxionCSV, AxionSettings


def test_electrode_correction_zero_selected_electrodes_sum_vs_mean():
    wells = ["A1"]
    electrodes = ["A1_11"]

    baseline_csv = AxionCSV(
        wells=wells,
        metrics={},
        settings=AxionSettings(active_electrode_criterion=6.0),
        electrodes=electrodes,
        electrode_metrics={
            "Mean Firing Rate (Hz)": [0.0],
            "Burst Frequency (Hz)": [0.0],
            "Number of Spikes": [123.0],
            "Number of Bursts": [4.0],
        },
        electrode_metric_categories={
            "Mean Firing Rate (Hz)": "Activity Metrics",
            "Number of Spikes": "Activity Metrics",
            "Burst Frequency (Hz)": "Electrode Burst Metrics",
            "Number of Bursts": "Electrode Burst Metrics",
        },
    )

    exposure_csv = AxionCSV(
        wells=wells,
        metrics={},
        settings=AxionSettings(active_electrode_criterion=6.0),
        electrodes=electrodes,
        electrode_metrics={
            "Mean Firing Rate (Hz)": [0.0],
            "Burst Frequency (Hz)": [0.0],
            "Number of Spikes": [456.0],
            "Number of Bursts": [9.0],
        },
        electrode_metric_categories=baseline_csv.electrode_metric_categories,
    )

    result = apply_electrode_correction(
        baseline_csv=baseline_csv,
        exposure_csv=exposure_csv,
        baseline_map={},
        exposure_map={},
        wells=wells,
        active_electrode_criterion=6.0,
        burst_frequency_threshold=0.3,
    )

    assert result.number_of_active_electrodes["A1"] == 0
    assert result.number_of_bursting_electrodes["A1"] == 0

    assert result.baseline_map["number_of_spikes"]["A1"] == 0
    assert result.baseline_map["number_of_bursts"]["A1"] == 0

    assert result.baseline_map["mean_firing_rate"]["A1"] is None
    assert result.baseline_map["burst_frequency"]["A1"] is None
