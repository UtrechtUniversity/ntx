"""
This is a validation/helper utility used for BMA comparisons. It calculates
control group average with the arithmetic mean without removing outliers.
(The main pipeline instead first identifies outliers within the control group
(using Boxplot/Z-Score) and excludes them, then calculates the control mean
from the remaining wells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import polars as pl

from ntx.metrics_schema import Matrix, MetricsPayload


class ControlNormalizationError(ValueError):
    """Raised when control normalization cannot be computed."""


@dataclass(frozen=True, slots=True)
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
    This helper returns unitless values normalized to the per-param control mean:

        normalized = ratio / mean(control_ratios)

    Missing values (`null`) and knockouts (`-1`) are treated as missing and ignored
    when computing the control mean.
    """
    control_well_list = list(control_wells)
    if not control_well_list:
        raise ControlNormalizationError("No control wells provided")

    # Load Data into Polars
    # Create DataFrame with params as a column, and wells as columns
    try:
        df = pl.DataFrame(payload.ratio, schema=payload.wells, orient="row")
    except Exception as exc:
        raise ControlNormalizationError(
            "Failed to construct DataFrame from payload ratios"
        ) from exc

    df = df.with_columns(param=pl.Series(payload.params))

    # Melt to Long Format (param, well, ratio)
    melted = df.unpivot(index=["param"], variable_name="well", value_name="raw_ratio")

    # Clean Data (Handle -1 as Null)
    # Cast explicitly to Float64 first to ensure we can put None in
    melted = melted.with_columns(
        ratio=pl.when(pl.col("raw_ratio") == -1)
        .then(None)
        .otherwise(pl.col("raw_ratio"))
        .cast(pl.Float64)
    )

    # Calculate Control Means
    # Filter for control wells -> group by param -> mean
    control_means_df = (
        melted.filter(pl.col("well").is_in(control_well_list))
        .group_by("param")
        .agg(pl.col("ratio").mean().alias("control_mean"))
    )

    # Join Means Back and Normalize
    joined = melted.join(control_means_df, on="param", how="left")

    # Handle division by zero/null by making result null
    joined = joined.with_columns(
        normalized=pl.when(pl.col("control_mean").is_null() | (pl.col("control_mean") == 0))
        .then(None)
        .otherwise(pl.col("ratio") / pl.col("control_mean"))
    )

    # Pivot back to Matrix format
    # payload.params and payload.wells must be in original order
    pivoted = joined.pivot(
        on="well", index="param", values="normalized", aggregate_function="first"
    )

    # Reorder parameters: Join with original param list to preserve order
    param_order = pl.DataFrame({"param": payload.params, "param_idx": range(len(payload.params))})
    pivoted = pivoted.join(param_order, on="param").sort("param_idx").drop("param_idx")

    # Extract Matrix (list of lists) with columns in original well order
    try:
        matrix_df = pivoted.select(payload.wells)
    except Exception as exc:
        raise ControlNormalizationError(f"Error selecting wells during pivot: {exc}") from exc

    normalized_matrix = matrix_df.rows()

    # Extract Control Means in param order
    # We need to ensure we get means for every param in payload.params
    # control_means_df might be missing params if control wells had all-nulls?
    # Left join ensures all params exist.
    ordered_means_df = (
        pl.DataFrame({"param": payload.params, "idx": range(len(payload.params))})
        .join(control_means_df, on="param", how="left")
        .sort("idx")
    )
    control_means_list = ordered_means_df["control_mean"].to_list()

    return ControlNormalizedResult(
        params=list(payload.params),
        wells=list(payload.wells),
        control_wells=control_well_list,
        control_means=control_means_list,
        normalized=[list(row) for row in normalized_matrix],
    )
