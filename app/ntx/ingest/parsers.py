"""CSV parsing utilities for Axion neural metrics exports."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ntx.metrics_metadata import AXION_METRICS_MAP, IGNORED_METRICS


@dataclass
class AxionSettings:
    active_electrode_criterion: float | None = None
    analysis_start: float | None = None
    analysis_end: float | None = None
    synchrony_window: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class AxionCSV:
    wells: list[str]
    metrics: dict[str, list[float | None]]
    settings: AxionSettings
    electrodes: list[str] = field(default_factory=list)
    electrode_metrics: dict[str, list[float | None]] = field(default_factory=dict)
    electrode_metric_categories: dict[str, str] = field(default_factory=dict)


def parse_axion_csv(path: str | Path) -> AxionCSV:
    """
    Parse an Axion neural metrics CSV file.
    """
    rows = _read_rows(path)
    return parse_axion_rows(rows, source=path)


def parse_axion_rows(rows: list[list[str]], *, source: str | Path = "<rows>") -> AxionCSV:
    """
    Parse Axion neural metrics exports from CSV-like rows.
    """
    well_row_idx = _find_row(rows, "well averages")
    if well_row_idx is None:
        raise ValueError(f"No 'Well Averages' section found in {source}")

    measurement_row_idx = None
    for idx in range(well_row_idx + 1, len(rows)):
        row = rows[idx]
        if row and (row[0] or "").strip().lower().startswith("measurement"):
            measurement_row_idx = idx
            break

    settings = _parse_settings(rows[:well_row_idx])
    wells = _parse_well_header(rows[well_row_idx])

    metrics: dict[str, list[float | None]] = {}
    end_idx = measurement_row_idx if measurement_row_idx is not None else len(rows)
    for row in rows[well_row_idx + 1 : end_idx]:
        if not row:
            continue

        label = (row[0] or "").strip()
        if not label:
            continue
        if label in IGNORED_METRICS:
            continue
        if _is_category_row(row):
            continue

        values = _parse_metric_row(row[1:], len(wells))
        metrics[label] = values

    electrode_names: list[str] = []
    electrode_metrics: dict[str, list[float | None]] = {}
    electrode_categories: dict[str, str] = {}

    if measurement_row_idx is not None:
        electrode_names = _parse_electrode_header(rows[measurement_row_idx])
        current_category = ""
        for row in rows[measurement_row_idx + 1 :]:
            if not row:
                continue

            label = (row[0] or "").strip()
            if not label:
                continue
            if _is_category_row(row):
                current_category = label
                continue
            if label in IGNORED_METRICS:
                continue

            values = _parse_metric_row(row[1:], len(electrode_names))
            electrode_metrics[label] = values
            if current_category:
                electrode_categories[label] = current_category

    return AxionCSV(
        wells=wells,
        metrics=metrics,
        settings=settings,
        electrodes=electrode_names,
        electrode_metrics=electrode_metrics,
        electrode_metric_categories=electrode_categories,
    )


def translate_metrics(axion_csv: AxionCSV) -> dict[str, dict[str, float | int | None]]:
    """
    Translate raw metric labels to internal parameter names.
    """
    translated: dict[str, dict[str, float | int | None]] = {}
    for raw_name, values in axion_csv.metrics.items():
        internal = AXION_METRICS_MAP.get(raw_name)
        if internal is None:
            continue
        per_well: dict[str, float | None] = {}
        for well, raw_value in zip(axion_csv.wells, values):
            per_well[well] = _coerce_value(raw_value, internal)
        translated[internal] = per_well
    return translated


def _read_rows(path: str | Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [row for row in csv.reader(handle)]


def _find_row(rows: list[list[str]], needle: str) -> int | None:
    needle_lower = needle.lower()
    for idx, row in enumerate(rows):
        if row and (row[0] or "").strip().lower() == needle_lower:
            return idx
    return None


def _parse_settings(rows: list[list[str]]) -> AxionSettings:
    settings = AxionSettings()
    for row in rows:
        if not row:
            continue
        key = (row[0] or "").strip()
        value = row[1] if len(row) > 1 else None

        if key.startswith("Active Electrode Criterion"):
            settings.active_electrode_criterion = _to_float(value)
        elif key.startswith("Analysis Start"):
            settings.analysis_start = _to_float(value)
        elif key.startswith("Analysis End"):
            settings.analysis_end = _to_float(value)
        elif key.startswith("Synchrony Window"):
            settings.synchrony_window = _to_float(value)
        elif key:
            settings.extras[key] = value
    return settings


def _parse_well_header(row: list[str]) -> list[str]:
    wells = [(cell or "").strip() for cell in row[1:] if (cell or "").strip()]
    if not wells:
        raise ValueError("Well Averages row does not list any wells")
    return wells


def _parse_electrode_header(row: list[str]) -> list[str]:
    electrodes = [(cell or "").strip() for cell in row[1:] if (cell or "").strip()]
    if not electrodes:
        raise ValueError("Measurement row does not list any electrodes")
    return electrodes


def _is_category_row(row: list[str]) -> bool:
    if len(row) == 1:
        return True
    if all(not (cell or "").strip() for cell in row[1:]):
        return True
    return False


def _parse_metric_row(values: list[str], expected_len: int) -> list[float | None]:
    parsed: list[float | None] = []
    for raw in values[:expected_len]:
        text = (raw or "").strip()
        if text == "" or text.lower() == "nan":
            parsed.append(None)
            continue
        parsed.append(_to_float(text))

    # Pad missing trailing cells (Axion can sometimes have a trailing comma).
    while len(parsed) < expected_len:
        parsed.append(None)
    return parsed


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _coerce_value(value: Any, param_name: str | None = None) -> float | int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None

    should_cast_int = param_name is not None and param_name.startswith("number_")
    if should_cast_int and float(numeric).is_integer():
        return int(numeric)

    return numeric
