# ntx/metadata_utils/extract_metadata.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ntx.metadata_utils.parse_filename import extract_mea_filename_metadata
from ntx.metadata_utils.read_layout import read_layout

logger = logging.getLogger(__name__)


def collect_experiment_metadata_from_files(
    *,
    layout_file: str | Path,
    baseline_file: str | Path,
    exposure_file: str | Path,
) -> dict[str, Any]:
    """
    Parse metadata from:
      - layout Excel (.xlsx)
      - baseline CSV
      - exposure CSV

    and merge them into a single dict.
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

    # 1) Parse filename metadata
    baseline_meta = extract_mea_filename_metadata(baseline_path)
    exposure_meta = extract_mea_filename_metadata(exposure_path)

    # 2) Parse layout
    layout_meta = read_layout(str(layout_path))

    def pick(*values):
        for v in values:
            if v not in [None, "", {}, []]:
                return v
        return None

    experiment_id = pick(
        baseline_meta.get("mea:experiment_number"),
        exposure_meta.get("mea:experiment_number"),
    )

    # Optional: sanity-check experiment_id if both exist
    b_id = baseline_meta.get("mea:experiment_number")
    e_id = exposure_meta.get("mea:experiment_number")
    if b_id and e_id and b_id != e_id:
        raise ValueError(f"Experiment number mismatch: baseline={b_id}, exposure={e_id}")

    merged: dict[str, Any] = {
        "experiment_id": experiment_id,
        "date": pick(
            layout_meta.get("date"),
            baseline_meta.get("mea:date"),
            exposure_meta.get("mea:date")
            ),
        "experimenter": pick(
            baseline_meta.get("mea:experimenter"),
            exposure_meta.get("mea:experimenter")
            ),
        "plate_number": pick(
            baseline_meta.get("mea:plate_number"),
            exposure_meta.get("mea:plate_number")
            ),
        "type_of_cells": pick(
            baseline_meta.get("mea:type_of_cells"),
            exposure_meta.get("mea:type_of_cells")
            ),
        "compound": pick(baseline_meta.get("mea:compound"), exposure_meta.get("mea:compound")),
        "type_of_exposure": pick(
            baseline_meta.get("mea:type_of_exposure"),
            exposure_meta.get("mea:type_of_exposure")
            ),
        "type_of_experiment": pick(
            baseline_meta.get("mea:type_of_experiment"),
            exposure_meta.get("mea:type_of_experiment")
            ),
        "sex": pick(baseline_meta.get("mea:sex"), exposure_meta.get("mea:sex")),
        "div": pick(baseline_meta.get("mea:div"), exposure_meta.get("mea:div")),
        "control_group": layout_meta.get("control_group"),
        "num_wells": layout_meta.get("wells"),
        "groups": layout_meta.get("groups"),
        "exposures": [
            {
                "date": exposure_meta.get("mea:date"),
                "div": exposure_meta.get("mea:div"),
                "exposure_duration": exposure_meta.get("mea:exposure_duration"),
            }
        ],
        "baseline_filename_meta": baseline_meta,
        "exposure_filename_meta": exposure_meta,
        "layout_meta": layout_meta,
    }

    if not merged.get("compound"):
        logger.warning("Compound could not be determined from either layout or filenames.")

    return merged
