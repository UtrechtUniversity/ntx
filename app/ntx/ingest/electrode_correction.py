"""Electrode-level correction for Axion well averages (ingestion-time only)."""

from __future__ import annotations

from dataclasses import dataclass

from ntx.metrics_metadata import AXION_METRICS_MAP

from .parsers import AxionCSV


class ElectrodeCorrectionError(ValueError):
    """Raised when electrode correction cannot be applied."""


@dataclass(frozen=True)
class ElectrodeCorrectionResult:
    baseline_map: dict[str, dict[str, float | int | None]]
    exposure_map: dict[str, dict[str, float | int | None]]
    number_of_active_electrodes: dict[str, int]
    number_of_bursting_electrodes: dict[str, int]


ELECTRODE_ACTIVITY_SECTION = "Activity Metrics"
ELECTRODE_BURST_SECTION = "Electrode Burst Metrics"

# Metrics that are aggregated via SUM (NeurotoxMEA uses np.ma.sum).
SUM_PARAMS: set[str] = {
    "number_of_spikes",
    "number_of_bursts",
    "number_of_network_bursts",
}


def apply_electrode_correction(
    *,
    baseline_csv: AxionCSV,
    exposure_csv: AxionCSV,
    baseline_map: dict[str, dict[str, float | int | None]],
    exposure_map: dict[str, dict[str, float | int | None]],
    wells: list[str],
    active_electrode_criterion: float,
    burst_frequency_threshold: float,
) -> ElectrodeCorrectionResult:
    """
    Recompute well averages from electrode-level data

    - Active electrode set: baseline Mean Firing Rate (Hz) * 60 > active_electrode_criterion.
    - Bursting electrode set: baseline Burst Frequency (Hz) * 60 > burst_frequency_threshold.
    - Exposure recompute uses the baseline electrode sets as a stable subset for ratios.
    - SUM metrics: if 0 selected electrodes -> 0; if all selected values are missing -> None.
    - MEAN metrics: if 0 selected electrodes -> None.
    """

    if not baseline_csv.electrodes:
        raise ElectrodeCorrectionError(
            "Baseline CSV is missing the Measurement (electrode) section"
        )
    if not exposure_csv.electrodes:
        raise ElectrodeCorrectionError(
            "Exposure CSV is missing the Measurement (electrode) section"
        )

    baseline_electrode_index = {name: idx for idx, name in enumerate(baseline_csv.electrodes)}
    exposure_electrode_index = {name: idx for idx, name in enumerate(exposure_csv.electrodes)}

    baseline_active, baseline_bursting = _identify_electrodes(
        baseline_csv,
        active_electrode_criterion=active_electrode_criterion,
        burst_frequency_threshold=burst_frequency_threshold,
    )

    active_counts = {well: len(baseline_active.get(well, set())) for well in wells}
    bursting_counts = {well: len(baseline_bursting.get(well, set())) for well in wells}

    corrected_baseline = {param: dict(values) for param, values in baseline_map.items()}
    corrected_exposure = {param: dict(values) for param, values in exposure_map.items()}

    for raw_label, baseline_values in baseline_csv.electrode_metrics.items():
        param_name = AXION_METRICS_MAP.get(raw_label)
        if param_name is None:
            continue

        section = baseline_csv.electrode_metric_categories.get(raw_label)
        if section == ELECTRODE_ACTIVITY_SECTION:
            electrode_set = baseline_active
        elif section == ELECTRODE_BURST_SECTION:
            electrode_set = baseline_bursting
        else:
            continue

        corrected_baseline.setdefault(param_name, {})
        corrected_exposure.setdefault(param_name, {})

        is_sum = param_name in SUM_PARAMS
        for well in wells:
            selected_names = electrode_set.get(well, set())
            baseline_indices = [
                baseline_electrode_index[name]
                for name in selected_names
                if name in baseline_electrode_index
            ]
            corrected_baseline[param_name][well] = _coerce_corrected_value(
                param_name,
                _aggregate_metric(baseline_values, baseline_indices, is_sum=is_sum),
            )

        exposure_values = exposure_csv.electrode_metrics.get(raw_label)
        if exposure_values is None:
            continue

        for well in wells:
            selected_names = electrode_set.get(well, set())
            exposure_indices = [
                exposure_electrode_index[name]
                for name in selected_names
                if name in exposure_electrode_index
            ]
            corrected_exposure[param_name][well] = _coerce_corrected_value(
                param_name,
                _aggregate_metric(exposure_values, exposure_indices, is_sum=is_sum),
            )

    return ElectrodeCorrectionResult(
        baseline_map=corrected_baseline,
        exposure_map=corrected_exposure,
        number_of_active_electrodes=active_counts,
        number_of_bursting_electrodes=bursting_counts,
    )


def _identify_electrodes(
    baseline_csv: AxionCSV,
    *,
    active_electrode_criterion: float,
    burst_frequency_threshold: float,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    mean_firing_rate = baseline_csv.electrode_metrics.get("Mean Firing Rate (Hz)")
    if mean_firing_rate is None:
        raise ElectrodeCorrectionError(
            "Baseline electrode section is missing 'Mean Firing Rate (Hz)' "
            "needed for active electrodes"
        )

    burst_frequency = _find_burst_frequency_row(baseline_csv.electrode_metrics)
    if burst_frequency is None:
        raise ElectrodeCorrectionError(
            "Baseline electrode section is missing 'Burst Frequency (Hz)' "
            "needed for bursting electrodes"
        )

    active: dict[str, set[str]] = {}
    bursting: dict[str, set[str]] = {}

    for electrode_name, value in zip(baseline_csv.electrodes, mean_firing_rate):
        if value is None:
            continue
        if value * 60 > active_electrode_criterion:
            well = electrode_name.split("_")[0]
            active.setdefault(well, set()).add(electrode_name)

    for electrode_name, value in zip(baseline_csv.electrodes, burst_frequency):
        if value is None:
            continue
        if value * 60 > burst_frequency_threshold:
            well = electrode_name.split("_")[0]
            bursting.setdefault(well, set()).add(electrode_name)

    return active, bursting


def _find_burst_frequency_row(
    electrode_metrics: dict[str, list[float | None]],
) -> list[float | None] | None:
    # TODO: double check if this shouldn't just use "Burst Frequency (Hz)"
    preferred = ("Burst Frequency (Hz)", "Burst Frequency - Avg (Hz)")
    for label in preferred:
        values = electrode_metrics.get(label)
        if values is not None:
            return values
    return None


def _aggregate_metric(
    values: list[float | None],
    selected_indices: list[int],
    *,
    is_sum: bool,
) -> float | None:
    if not selected_indices:
        return 0.0 if is_sum else None

    # Treat missing values as 0 and divide by all selected electrodes
    # in order to match BMA outputs. TODO: check correctness.
    total = 0.0
    saw_value = False
    for idx in selected_indices:
        value = values[idx]
        if value is None:
            continue
        total += float(value)
        saw_value = True

    if is_sum:
        return total if saw_value else None

    return total / len(selected_indices)


def _coerce_corrected_value(param_name: str, value: float | None) -> float | int | None:
    if value is None:
        return None
    if param_name.startswith("number_") and float(value).is_integer():
        return int(value)
    return value
