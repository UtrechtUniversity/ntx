"""
Schema validation for `NeuronalMetricsFrame.metrics_json`.

The payload is columnar: parameter-first arrays with wells as the second dimension:
- First dimension: Parameters
- Second dimension: Wells

Notes:
- Store missing/unusable values as JSON null. JSON/JSONB cannot encode NaN/Inf.
- `ratio` may include `-1` to represent a knockout (one side missing). Treat `-1` as
  missing for statistical calculations, but count it separately for knockout stats.
"""

from __future__ import annotations

import math
import numbers
from typing import Sequence, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

Numeric = int | float | None
# Single param row: per-well values (e.g., [0.5, 1.2, ...]).
Matrix = list[list[Numeric]]


def _validate_numeric_row(values: Sequence[Numeric], expected_length: int, label: str):
    if len(values) != expected_length:
        raise ValueError(f"{label} must contain {expected_length} values")

    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError(f"{label} must contain only numeric values or nulls")
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} must not contain NaN or infinite values")


class MetricsPayload(BaseModel):
    """
    Validation for metrics payloads.
    """

    # Reject unexpected keys in metrics_json.
    model_config = ConfigDict(extra="forbid")

    # Ordered parameter names (e.g., number_of_spikes); rows in each matrix follow this order.
    params: list[str] = Field(default_factory=list)
    # Ordered well labels (e.g., A1, B2); columns in each matrix follow this order.
    wells: list[str] = Field(default_factory=list)
    # Per-param x per-well values for each frame.
    baseline: Matrix
    exposure: Matrix
    ratio: Matrix

    # Field-level check: params/wells lists must not be empty.
    @field_validator("params", "wells")
    @classmethod
    def _ensure_non_empty(cls, value: list[str], info: ValidationInfo):
        if not value:
            raise ValueError(f"{info.field_name} must not be empty")
        return value

    # Field-level normalization: trim whitespace from each entry.
    @field_validator("params", "wells")
    @classmethod
    def _strip_values(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value]

    # Model-level check: enforce matrix shapes match params x wells,
    # after the field-level checks.
    @model_validator(mode="after")
    def _validate_shapes(self):
        params_count = len(self.params)
        wells_count = len(self.wells)

        for label, matrix in (
            ("baseline", self.baseline),
            ("exposure", self.exposure),
            ("ratio", self.ratio),
        ):
            self._validate_matrix(matrix, params_count, wells_count, label)

        return self

    @staticmethod
    def _validate_matrix(
        matrix: Matrix,
        params_count: int,
        wells_count: int,
        label: str,
    ):
        if len(matrix) != params_count:
            raise ValueError(f"{label} must have {params_count} rows (one per param)")

        for row in matrix:
            numeric_row = cast(Sequence[Numeric], row)
            _validate_numeric_row(numeric_row, wells_count, label)


class MetricsQcPayload(BaseModel):
    """
    Validation for `NeuronalMetricsFrame.qc_json`.

    This payload stores per-well QC values aligned to the `wells` header.
    """

    model_config = ConfigDict(extra="allow")

    wells: list[str]
    number_of_active_electrodes: list[Numeric]
    number_of_bursting_electrodes: list[Numeric]
    number_network_bursts_baseline: list[Numeric]

    @field_validator("wells")
    @classmethod
    def _ensure_non_empty(cls, value: list[str], info: ValidationInfo):
        if not value:
            raise ValueError(f"{info.field_name} must not be empty")
        return value

    @field_validator("wells")
    @classmethod
    def _strip_values(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value]

    @model_validator(mode="after")
    def _validate_shapes(self):
        wells_count = len(self.wells)
        for label, values in (
            ("number_of_active_electrodes", self.number_of_active_electrodes),
            ("number_of_bursting_electrodes", self.number_of_bursting_electrodes),
            ("number_network_bursts_baseline", self.number_network_bursts_baseline),
        ):
            numeric_row = cast(Sequence[Numeric], values)
            _validate_numeric_row(numeric_row, wells_count, label)

        for key, values in (self.model_extra or {}).items():
            if not isinstance(key, str):
                raise ValueError("qc_json keys must be strings")
            if not isinstance(values, list):
                raise ValueError(f"{key} must be a list")
            numeric_row = cast(Sequence[Numeric], values)
            _validate_numeric_row(numeric_row, wells_count, key)

        return self
