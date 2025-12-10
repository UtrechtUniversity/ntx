from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Iterable, TypeGuard

import pytest

from .analysis.control_normalization import normalize_ratios_to_control
from .ingest.bma import (
    load_bma_condition_normalized_to_control,
    load_bma_condition_ratios,
    load_bma_metrics,
)
from .ingest.discovery import discover_experiment_files
from .ingest.service import create_experiment_from_files
from .metrics_metadata import QC_BASELINE_METRICS
from .metrics_schema import Matrix, MetricsPayload, Numeric

pytestmark = pytest.mark.django_db

# This row is empty in the current BMA file
BMA_EXCLUDED_PARAMS = {"weighted_mean_firing_rate"}

BMA_CONDITION_SHEETS = ["Control", "0.01", "0.1", "1", "10", "100"]
BMA_SHEET_BY_CONCENTRATION = {
    Decimal("0.01"): "0.01",
    Decimal("0.1"): "0.1",
    Decimal("1"): "1",
    Decimal("10"): "10",
    Decimal("100"): "100",
}


def test_ingested_metrics_match_bma_well_averages(stored_data_dir: Path):
    folder = discover_experiment_files(stored_data_dir)
    experiment = create_experiment_from_files(folder, overwrite=True)

    bma_files = sorted(stored_data_dir.glob("*_BMA.xlsx"))
    assert bma_files, f"No *_BMA.xlsx found in {stored_data_dir}"
    assert len(bma_files) == 1, f"Expected exactly one *_BMA.xlsx, found: {bma_files}"
    bma_path = bma_files[0]

    expected = load_bma_metrics(bma_path)

    frame = experiment.neuronal_metrics_frames.get(div=0)
    payload = MetricsPayload.model_validate(frame.metrics_json)

    _assert_same_wells("metrics_json", payload.wells, "bma", expected.wells)

    actual_baseline = _matrix_to_param_map(payload.params, payload.wells, payload.baseline)
    actual_exposure = _matrix_to_param_map(payload.params, payload.wells, payload.exposure)
    actual_ratio = _matrix_to_param_map(payload.params, payload.wells, payload.ratio)

    condition_wells_by_sheet = _condition_wells_by_sheet(experiment)
    missing_sheets = sorted(set(BMA_CONDITION_SHEETS) - set(condition_wells_by_sheet))
    assert not missing_sheets, f"Missing condition layouts for sheets: {missing_sheets}"

    control_wells = condition_wells_by_sheet["Control"]
    normalized = normalize_ratios_to_control(payload, control_wells=control_wells)
    actual_control_normalized = _matrix_to_param_map(
        normalized.params, normalized.wells, normalized.normalized
    )

    assert BMA_EXCLUDED_PARAMS.issubset(set(actual_baseline)), (
        "Expected excluded params to be present in metrics_json, but they were missing: "
        f"{sorted(BMA_EXCLUDED_PARAMS - set(actual_baseline))}"
    )
    actual_baseline = {k: v for k, v in actual_baseline.items() if k not in BMA_EXCLUDED_PARAMS}
    actual_exposure = {k: v for k, v in actual_exposure.items() if k not in BMA_EXCLUDED_PARAMS}
    actual_ratio = {k: v for k, v in actual_ratio.items() if k not in BMA_EXCLUDED_PARAMS}
    actual_control_normalized = {
        k: v for k, v in actual_control_normalized.items() if k not in BMA_EXCLUDED_PARAMS
    }

    mismatches: list[str] = []
    mismatches.extend(
        _compare_param_maps(
            expected.baseline,
            actual_baseline,
            wells=payload.wells,
            frame="baseline",
        )
    )
    mismatches.extend(
        _compare_param_maps(
            expected.exposure,
            actual_exposure,
            wells=payload.wells,
            frame="exposure",
        )
    )

    for sheet_name in BMA_CONDITION_SHEETS:
        expected_condition = load_bma_condition_ratios(bma_path, sheet_name)
        wells = condition_wells_by_sheet[sheet_name]
        _assert_same_wells(
            f"layout:{sheet_name}", wells, f"bma:{sheet_name}", expected_condition.wells
        )
        mismatches.extend(
            _compare_param_maps(
                expected_condition.ratios,
                actual_ratio,
                wells=wells,
                frame=f"ratio:{sheet_name}",
            )
        )
        if sheet_name == "Control":
            continue

        expected_norm = load_bma_condition_normalized_to_control(bma_path, sheet_name)
        _assert_same_wells(
            f"layout:{sheet_name}", wells, f"bma_norm:{sheet_name}", expected_norm.wells
        )
        mismatches.extend(
            _compare_param_maps(
                expected_norm.normalized,
                actual_control_normalized,
                wells=wells,
                frame=f"control_norm:{sheet_name}",
            )
        )
        mismatches.extend(
            _compare_norm_aggregates(
                expected_norm,
                actual_control_normalized,
                wells=wells,
                frame=f"control_norm:{sheet_name}",
            )
        )

    qc_wells, qc_map = _qc_json_to_param_map(frame.qc_json)
    _assert_same_wells("qc_json", qc_wells, "bma", expected.wells)

    expected_qc = {
        "number_of_active_electrodes": expected.baseline.get("number_of_active_electrodes", {}),
        "number_of_bursting_electrodes": expected.baseline.get("number_of_bursting_electrodes", {}),
        "number_network_bursts_baseline": expected.baseline.get("number_of_network_bursts", {}),
    }
    actual_qc = {key: qc_map.get(key, {}) for key in QC_BASELINE_METRICS}
    mismatches.extend(
        _compare_param_maps(
            expected_qc,
            actual_qc,
            wells=expected.wells,
            frame="qc",
        )
    )

    assert not mismatches, _format_mismatch_report(bma_path, mismatches)


def _condition_wells_by_sheet(experiment) -> dict[str, list[str]]:
    wells_by_sheet: dict[str, list[str]] = {}
    for condition in experiment.conditions.all():
        if condition.is_control:
            sheet_name = "Control"
        else:
            if condition.concentration is None:
                raise AssertionError("Non-control conditions must have a concentration")
            try:
                sheet_name = BMA_SHEET_BY_CONCENTRATION[condition.concentration]
            except KeyError as exc:
                raise AssertionError(
                    "Unexpected concentration for BMA condition sheet mapping: "
                    f"{condition.concentration}"
                ) from exc
        wells = condition.wells
        if not isinstance(wells, list):
            raise AssertionError("Condition.wells must be a list[str]")
        wells_by_sheet[sheet_name] = _sort_wells(wells)
    return wells_by_sheet


def _sort_wells(wells: Iterable[str]) -> list[str]:
    def _well_key(well: str) -> tuple[str, int]:
        row = well[0].upper()
        try:
            col = int(well[1:])
        except ValueError:
            col = 0
        return (row, col)

    return sorted({well for well in wells}, key=_well_key)


def _matrix_to_param_map(
    params: list[str], wells: list[str], matrix: Matrix
) -> dict[str, dict[str, Numeric]]:
    per_param: dict[str, dict[str, Numeric]] = {}
    for param, row in zip(params, matrix):
        per_param[param] = {well: value for well, value in zip(wells, row)}
    return per_param


def _qc_json_to_param_map(qc_json: object) -> tuple[list[str], dict[str, dict[str, Numeric]]]:
    if not isinstance(qc_json, dict):
        raise AssertionError("qc_json must be a dict")

    wells = qc_json.get("wells")
    if not isinstance(wells, list) or not all(isinstance(well, str) for well in wells):
        raise AssertionError("qc_json['wells'] must be a list[str]")

    per_param: dict[str, dict[str, Numeric]] = {}
    for key, values in qc_json.items():
        if key == "wells":
            continue
        if not isinstance(values, list):
            continue
        per_param[key] = {
            well: values[idx] if idx < len(values) else None for idx, well in enumerate(wells)
        }
    return wells, per_param


def _assert_same_wells(
    left_label: str, left: list[str], right_label: str, right: list[str]
) -> None:
    if left == right:
        return

    left_set = set(left)
    right_set = set(right)
    missing = sorted(right_set - left_set)
    extra = sorted(left_set - right_set)
    raise AssertionError(
        "Well header mismatch. "
        f"{left_label}_count={len(left)} {right_label}_count={len(right)} "
        f"missing_from_{left_label}={missing or 'none'} "
        f"extra_in_{left_label}={extra or 'none'}"
    )


def _compare_param_maps(
    expected: dict[str, dict[str, Numeric]],
    actual: dict[str, dict[str, Numeric]],
    *,
    wells: Iterable[str],
    frame: str,
    rel_tol: float = 1e-6,
    abs_tol: float = 1e-9,
    max_mismatches_display: int = 50,
) -> list[str]:
    mismatches: list[str] = []

    missing_params = sorted(set(actual) - set(expected))
    for param in missing_params:
        mismatches.append(f"{frame}: missing param in BMA: {param}")

    for param in sorted(actual):
        expected_param = expected.get(param)
        if expected_param is None:
            continue
        actual_param = actual[param]
        for well in wells:
            expected_value = expected_param.get(well)
            actual_value = actual_param.get(well)
            if _values_equal(param, expected_value, actual_value, rel_tol=rel_tol, abs_tol=abs_tol):
                continue

            suffix = ""
            if _is_real_number(expected_value) and _is_real_number(actual_value):
                suffix = f" Δ={float(actual_value) - float(expected_value):+.6g}"
            mismatches.append(
                f"{frame} {param} {well}: expected {_fmt(expected_value)} "
                f"got {_fmt(actual_value)}{suffix}"
            )
            if len(mismatches) >= max_mismatches_display:
                mismatches.append(
                    f"{frame}: mismatches truncated at {max_mismatches_display} entries"
                )
                return mismatches
    return mismatches


def _compare_norm_aggregates(
    expected_norm,
    actual_norm: dict[str, dict[str, Numeric]],
    *,
    wells: list[str],
    frame: str,
    rel_tol: float = 1e-6,
    abs_tol: float = 1e-9,
) -> list[str]:
    mismatches: list[str] = []

    for param, expected_mean in expected_norm.mean.items():
        expected_stdev = expected_norm.stdev.get(param)
        actual_param = actual_norm.get(param)
        if actual_param is None:
            continue

        values = [actual_param.get(well) for well in wells]
        actual_mean = _mean(values)
        actual_stdev = _sample_stdev(values, mean=actual_mean)

        if not _values_equal(param, expected_mean, actual_mean, rel_tol=rel_tol, abs_tol=abs_tol):
            mismatches.append(
                f"{frame} mean {param}: expected {_fmt(expected_mean)} got {_fmt(actual_mean)}"
            )
        if not _values_equal(param, expected_stdev, actual_stdev, rel_tol=rel_tol, abs_tol=abs_tol):
            mismatches.append(
                f"{frame} stdev {param}: expected {_fmt(expected_stdev)} got {_fmt(actual_stdev)}"
            )

    return mismatches


def _mean(values: list[Numeric]) -> float | None:
    total = 0.0
    count = 0
    for value in values:
        if not _is_real_number(value):
            continue
        total += float(value)
        count += 1
    if count == 0:
        return None
    return total / count


def _sample_stdev(values: list[Numeric], *, mean: float | None) -> float | None:
    if mean is None:
        return None

    diffs: list[float] = []
    for value in values:
        if not _is_real_number(value):
            continue
        diffs.append(float(value) - mean)

    if len(diffs) < 2:
        return None

    variance = sum(diff * diff for diff in diffs) / (len(diffs) - 1)
    stdev = math.sqrt(variance)
    if not math.isfinite(stdev):
        return None
    return stdev


def _values_equal(
    param: str,
    expected: Numeric,
    actual: Numeric,
    *,
    rel_tol: float,
    abs_tol: float,
) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None

    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual

    if isinstance(expected, int) and isinstance(actual, int):
        return expected == actual

    if _is_real_number(expected) and _is_real_number(actual):
        expected_float = float(expected)
        actual_float = float(actual)
        if (
            param.startswith("number_")
            and expected_float.is_integer()
            and actual_float.is_integer()
        ):
            return int(expected_float) == int(actual_float)
        return math.isclose(expected_float, actual_float, rel_tol=rel_tol, abs_tol=abs_tol)

    return expected == actual


def _is_real_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _format_mismatch_report(bma_path: Path, mismatches: list[str]) -> str:
    lines = [
        f"BMA comparison failed for {bma_path.name}",
        f"Found {len(mismatches)} mismatches (showing up to first report limits):",
    ]
    lines.extend(f"- {line}" for line in mismatches)
    return "\n".join(lines)
