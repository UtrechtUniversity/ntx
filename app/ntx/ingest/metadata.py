from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import re
import logging

from .discovery import parse_filename_metadata
from .layout import parse_layout_xlsx

logger = logging.getLogger(__name__)


def _normalize_code(raw_code: str | None) -> str | None:
    if raw_code is None:
        return None
    matches = re.findall(r"(?<!\d)(\d{6})(?!\d)", raw_code)
    if matches:
        return matches[-1]
    return raw_code


def collect_experiment_metadata_from_files(
    *,
    layout_file: str | Path,
    baseline_file: str | Path,
    exposure_file: str | Path,
) -> Dict[str, Any]:
    """
    Returns a dict with metadata extracted from filenames and layout files.

    - Parses filenames via ntx.ingest.discovery.parse_filename_metadata
      (baseline + exposure, then merged).
    - Parses layout via ntx.ingest.layout.parse_layout_xlsx.
    - Builds a merged dict that admin/models expect, with keys:
        experiment_id, code, sex, div, compound, type_of_cells,
        experimenter, plate_number, date, layout_meta,
        baseline_filename_meta, exposure_filename_meta.
    """

    layout_path = Path(layout_file)
    baseline_path = Path(baseline_file)
    exposure_path = Path(exposure_file)

    if not layout_path.exists():
        raise FileNotFoundError(f"Layout file not found: {layout_path}")
    if not baseline_path.exists():
        raise FileNotFoundError(f"Baseline file not found: {baseline_path}")
    if not exposure_path.exists():
        raise FileNotFoundError(f"Exposure file not found: {exposure_path}")

    # 1) Filename metadata
    baseline_meta = parse_filename_metadata(baseline_path)
    exposure_meta = parse_filename_metadata(exposure_path)
    merged_meta = baseline_meta.merge(exposure_meta)

    # 2) Layout metadata
    layout = parse_layout_xlsx(layout_path)

    # Build layout_meta
    groups: list[dict[str, Any]] = []
    control_group_name: str | None = None

    for cond in layout.conditions:
        if cond.is_control:
            name = "Control"
            control_group_name = name
            dosage = None
        else:
            dosage = float(cond.concentration) if cond.concentration is not None else None
            name = f"{dosage}" if dosage is not None else ""

        groups.append(
            {
                "name": name,
                "compound": merged_meta.chemical or cond.chemical or "",
                "dosage": dosage,
                "unit": cond.unit,
                "wells": " ".join(cond.wells),
            }
        )

    layout_meta: dict[str, Any] = {
        "date": layout.date.isoformat(),
        "wells": layout.plate_wells,
        "control_group": control_group_name or "",
        "groups": groups,
    }

    experiment_number = merged_meta.raw.get("mea:experiment_number")

    if experiment_number:
        normalized_code = experiment_number
    else:
        normalized_code = _normalize_code(merged_meta.code)

    merged: dict[str, Any] = {
        "experiment_id": normalized_code,
        "code": normalized_code,

        "sex": merged_meta.sex,

        "div": f"DIV {merged_meta.div}" if merged_meta.div is not None else None,

        "compound": merged_meta.chemical,
        "type_of_cells": merged_meta.cell_line,
        "experimenter": merged_meta.raw.get("mea:experimenter"),
        "plate_number": merged_meta.raw.get("mea:plate_number"),

        "date": layout.date.isoformat(),

        "layout_meta": layout_meta,
        "baseline_filename_meta": baseline_meta.raw,
        "exposure_filename_meta": exposure_meta.raw,
    }

    if not merged.get("compound"):
        logger.warning("Compound could not be determined from either layout or filenames.")

    return merged
