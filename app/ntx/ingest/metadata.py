from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .discovery import FilenameMetadata, parse_filename_metadata
from .layout import ExperimentLayout, parse_layout_xlsx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedExperimentUpload:
    metadata: FilenameMetadata
    layout: ExperimentLayout


def collect_experiment_metadata_from_files(
    *,
    layout_file: str | Path,
    baseline_file: str | Path,
    exposure_file: str | Path,
    baseline_filename: str | None = None,
    exposure_filename: str | None = None,
) -> ParsedExperimentUpload:
    """
    Parse uploaded experiment files into the canonical typed ingestion DTOs.

    Uploaded files are stored under generated storage names, so callers can
    provide the original baseline/exposure filenames for metadata parsing.
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

    baseline_meta = parse_filename_metadata(baseline_filename or baseline_path)
    exposure_meta = parse_filename_metadata(exposure_filename or exposure_path)

    if baseline_meta.code != exposure_meta.code:
        raise ValueError(
            "Baseline and exposure filenames do not match "
            f"({baseline_meta.code} vs {exposure_meta.code})"
        )

    merged_meta = baseline_meta.merge(exposure_meta)
    layout = parse_layout_xlsx(layout_path)

    if not merged_meta.chemical:
        logger.warning("Compound could not be determined from either layout or filenames.")

    return ParsedExperimentUpload(metadata=merged_meta, layout=layout)
