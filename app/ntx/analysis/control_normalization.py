from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, TypeGuard

from ntx.metrics_schema import Matrix, MetricsPayload, Numeric


class ControlNormalizationError(ValueError):
    """Raised when control normalization cannot be computed."""


@dataclass(frozen=True)
class ControlNormalizedResult:
    params: list[str]
    wells: list[str]
    control_wells: list[str]
    control_means: list[float | None]
    normalized: Matrix


def normalize_ratios_to_control(
    payload: MetricsPayload, *, control_wells: Iterable[str]
) -> ControlNormalizedResult:
    """
    Normalize per-well ratios to the mean of the Control group.

    The stored `metrics_json.ratio` values are unitless ratios (Exposure/Baseline).
    This function returns unitless values normalized to control mean:

        normalized = ratio / mean(control_ratios)

    Missing values (`null`) and knockouts (`-1`) are treated as missing and ignored
    when computing the control mean.
    """
    wells = payload.wells
    well_index = {well: idx for idx, well in enumerate(wells)}
    control_well_list = list(control_wells)
    if not control_well_list:
        raise ControlNormalizationError("No control wells provided")

    try:
        control_indices = [well_index[well] for well in control_well_list]
    except KeyError as exc:
        missing_well = str(exc.args[0])
        raise ControlNormalizationError(
            f"Control well '{missing_well}' is not present in metrics payload"
        ) from exc

    control_means: list[float | None] = []
    normalized: Matrix = []

    for row in payload.ratio:
        values = row

        mean = _mean_of_indices(values, control_indices)
        control_means.append(mean)

        if mean is None or mean == 0:
            normalized.append([None for _ in values])
            continue

        normalized.append(
            [_safe_divide(value, mean) if _is_valid_ratio(value) else None for value in values]
        )

    return ControlNormalizedResult(
        params=list(payload.params),
        wells=list(wells),
        control_wells=control_well_list,
        control_means=control_means,
        normalized=normalized,
    )


def _is_valid_ratio(value: Numeric) -> TypeGuard[int | float]:
    # Returns True for ratio values we can normalize; `None` and `-1` (KO sentinel) are
    # treated as missing, and all other values are guaranteed finite by payload validation.
    return value is not None and value != -1


def _mean_of_indices(values: list[Numeric], indices: list[int]) -> float | None:
    total = 0.0
    count = 0
    for idx in indices:
        if idx >= len(values):
            continue
        value = values[idx]
        if not _is_valid_ratio(value):
            continue
        total += float(value)
        count += 1
    if count == 0:
        return None
    return total / count


def _safe_divide(value: Numeric, denom: float) -> float | None:
    if not _is_valid_ratio(value):
        return None
    result = float(value) / denom
    if not math.isfinite(result):
        return None
    return result
