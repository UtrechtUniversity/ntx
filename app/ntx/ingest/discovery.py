"""File discovery and filename parsing for Axion MEA ingestion."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Filename parsing
# Logic based on internal repo neurotoxicology/src/mea/parse_filename.py
# ---------------------------------------------------------------------

COMPOUNDS = {
    "bpa",
    "dde",
    "ddt",
    "dieldrin",
    "fluetizolam",
    "flunitrazolam",
    "lindane",
    "lorazepam",
    "microplastics",
    "oxazepam",
    "pfhxs",
    "snakevenoms",
}
EXPERIMENT_TYPES = {"MEA"}
CELL_TYPES = {"rcortex"}
EXPOSURE_TYPES = {"acute", "chronic", "subchronic"}
BASELINE_EXPOSURE = {"baseline", "exposure"}

DATE_PATTERNS = [
    re.compile(r"\d{6}"),
    re.compile(r"\d{8}"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}"),
]
INITIALS_PATTERN = re.compile(r"^[A-Za-z]{2,4}$")
EXPERIMENT_NUMBER_PATTERN = re.compile(r"^\d{6}$")
PLATE_NUMBER_PATTERN = re.compile(r"^\d+-\d+$")
DIV_PATTERN = re.compile(r"DIV\d+(?:\(\d+\))?", re.IGNORECASE)
DURATION_PATTERN = re.compile(
    r"(\d+)\s*(s|sec|seconds|m|min|minutes|h|hr|hours|d|day|days)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class FieldRule:
    name: str
    matcher: Callable[[str], Optional[str]]


def _match_any(token: str, patterns: Sequence[re.Pattern[str]]) -> Optional[str]:
    for pattern in patterns:
        if pattern.fullmatch(token):
            return token
    return None


def _match_date(token: str) -> Optional[str]:
    return _match_any(token, DATE_PATTERNS)


def _match_initials(token: str) -> Optional[str]:
    return token if INITIALS_PATTERN.fullmatch(token) else None


def _match_experiment_number(token: str) -> Optional[str]:
    return token if EXPERIMENT_NUMBER_PATTERN.fullmatch(token) else None


def _match_plate_number(token: str) -> Optional[str]:
    return token if PLATE_NUMBER_PATTERN.fullmatch(token) else None


def _match_experiment_type(token: str) -> Optional[str]:
    return token if token.upper() in EXPERIMENT_TYPES else None


def _match_cell_type(token: str) -> Optional[str]:
    lowered = token.lower()
    return lowered if lowered in CELL_TYPES else None


def _match_baseline_exposure(token: str) -> Optional[str]:
    lowered = token.lower()
    return lowered if lowered in BASELINE_EXPOSURE else None


def _match_exposure_type(token: str) -> Optional[str]:
    lowered = token.lower()
    return lowered if lowered in EXPOSURE_TYPES else None


def _match_compound(token: str) -> Optional[str]:
    return token if token.lower() in COMPOUNDS else None


def _match_sex(token: str) -> Optional[str]:
    lowered = token.lower()
    if "male" in lowered or "female" in lowered or lowered.startswith("sex:"):
        return lowered
    return None


def _match_div(token: str) -> Optional[str]:
    match = DIV_PATTERN.fullmatch(token)
    if match:
        return match.group(0).upper()
    return None


def _match_duration(token: str) -> Optional[str]:
    match = DURATION_PATTERN.fullmatch(token.strip().lower())
    return match.group(0) if match else None


RULES: Sequence[FieldRule] = (
    FieldRule("mea:date", _match_date),
    FieldRule("mea:experimenter", _match_initials),
    FieldRule("mea:experiment_number", _match_experiment_number),
    FieldRule("mea:plate_number", _match_plate_number),
    FieldRule("mea:type_of_experiment", _match_experiment_type),
    FieldRule("mea:type_of_cells", _match_cell_type),
    FieldRule("mea:baseline_exposure", _match_baseline_exposure),
    FieldRule("mea:type_of_exposure", _match_exposure_type),
    FieldRule("mea:compound", _match_compound),
    FieldRule("mea:sex", _match_sex),
    FieldRule("mea:div", _match_div),
    FieldRule("mea:exposure_duration", _match_duration),
)


def tokenize(filename: str | Path) -> list[str]:
    """Return cleaned filename tokens (spaces stripped)."""
    return Path(filename).name.replace(" ", "").split("_")


def extract_metadata(filename: str | Path, check_missing: bool = False) -> dict[str, str | None]:
    metadata: dict[str, str | None] = {rule.name: None for rule in RULES}
    extras: list[str] = []

    for raw in tokenize(filename):
        token = raw.strip()
        for rule in RULES:
            if metadata[rule.name] is not None:
                continue
            value = rule.matcher(token)
            if value is not None:
                metadata[rule.name] = value
                break
        else:
            extras.append(token)

    metadata["mea:extra_tokens"] = "_".join(extras) or None

    if check_missing:
        _log_missing(metadata, filename)

    return metadata


def _log_missing(metadata: dict[str, str | None], filename: str | Path) -> None:
    missing = [key for key, value in metadata.items() if value is None]
    if missing:
        logger.debug("Missing fields for %s: %s", Path(filename).name, ", ".join(missing))


# ---------------------------------------------------------------------
# Metadata Data Transfer Objects
# ---------------------------------------------------------------------


@dataclass
class FilenameMetadata:
    code: str
    chemical: str | None
    sex: str | None
    div: int | None
    cell_line: str | None
    measurement: str | None
    raw: dict[str, str | None]
    extra_tokens: list[str]

    def merge(self, other: "FilenameMetadata") -> "FilenameMetadata":
        """Fill missing values from another parsed filename."""
        return FilenameMetadata(
            code=self.code or other.code,
            chemical=self.chemical or other.chemical,
            sex=self.sex or other.sex,
            div=self.div or other.div,
            cell_line=self.cell_line or other.cell_line,
            measurement=self.measurement or other.measurement,
            raw={**other.raw, **self.raw},
            extra_tokens=list({*self.extra_tokens, *other.extra_tokens}),
        )


def parse_filename_metadata(path: str | Path) -> FilenameMetadata:
    raw = extract_metadata(path, check_missing=True)
    tokens = tokenize(path)

    code = _compose_experiment_code(raw, tokens)
    chemical = raw.get("mea:compound")
    sex = _normalize_sex(raw.get("mea:sex"))
    div = _parse_div(raw.get("mea:div"))
    cell_line = raw.get("mea:type_of_cells")
    measurement = _normalize_measurement(raw.get("mea:baseline_exposure"))
    extra_tokens = (
        (raw.get("mea:extra_tokens") or "").split("_") if raw.get("mea:extra_tokens") else []
    )

    return FilenameMetadata(
        code=code,
        chemical=chemical,
        sex=sex,
        div=div,
        cell_line=cell_line,
        measurement=measurement,
        raw=raw,
        extra_tokens=[token for token in extra_tokens if token],
    )


def _compose_experiment_code(raw: dict[str, str | None], tokens: list[str]) -> str:
    parts: list[str] = []
    for key in ("mea:date", "mea:experimenter", "mea:experiment_number", "mea:plate_number"):
        value = raw.get(key)
        if value:
            parts.append(value)
    if parts:
        return "_".join(parts)

    # Fallback: first four underscore-separated tokens.
    return "_".join(tokens[:4]) if len(tokens) >= 4 else Path("_".join(tokens)).stem


def _normalize_sex(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if "female" in lowered or lowered.endswith("f"):
        return "female"
    if "male" in lowered or lowered.endswith("m"):
        return "male"
    if "mixed" in lowered or lowered == "x":
        return "mixed"
    return lowered


def _parse_div(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", value)
    if match:
        return int(match.group(0))
    return None


def _normalize_measurement(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if "baseline" in lowered:
        return "baseline"
    if "exposure" in lowered:
        return "exposure"
    return lowered


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------


class DiscoveryError(Exception):
    def __init__(self, folder: Path, message: str):
        self.folder = Path(folder)
        super().__init__(f"{self.folder}: {message}")


@dataclass
class ExperimentFolder:
    path: Path
    layout_file: Path
    baseline_csv: Path
    exposure_csv: Path
    metadata: FilenameMetadata | None = None


@dataclass
class ScanResult:
    experiments: list[ExperimentFolder]
    errors: list[DiscoveryError]


def scan_folder(path: str | Path) -> ScanResult:
    """
    Scan a folder (and immediate subfolders) for experiment file sets.
    """
    root = Path(path)
    if not root.exists():
        raise DiscoveryError(root, "Path does not exist")
    if not root.is_dir():
        raise DiscoveryError(root, "Path is not a directory")

    experiments: list[ExperimentFolder] = []
    errors: list[DiscoveryError] = []

    candidate_dirs = [root] if _has_layout(root) else []
    candidate_dirs.extend([p for p in root.iterdir() if p.is_dir() and _has_layout(p)])

    for candidate in candidate_dirs:
        try:
            experiments.append(discover_experiment_files(candidate))
        except DiscoveryError as exc:
            errors.append(exc)

    if not experiments and not errors:
        errors.append(DiscoveryError(root, "No layout files found in directory tree"))

    return ScanResult(experiments=experiments, errors=errors)


def discover_experiment_files(folder: str | Path) -> ExperimentFolder:
    folder = Path(folder)
    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        raise DiscoveryError(folder, "Folder contains no files")

    layout_file = _pick_single(files, _is_layout, folder, "layout workbook (*_LO.xlsx)")
    baseline_csv = _pick_single(
        files,
        lambda p: _is_neural_metrics(p) and "baseline" in p.name.lower(),
        folder,
        "baseline neural metrics",
    )
    exposure_csv = _pick_single(
        files,
        lambda p: _is_neural_metrics(p) and "exposure" in p.name.lower(),
        folder,
        "exposure neural metrics",
    )

    metadata = _validate_metadata_consistency(baseline_csv, exposure_csv)

    return ExperimentFolder(
        path=folder,
        layout_file=layout_file,
        baseline_csv=baseline_csv,
        exposure_csv=exposure_csv,
        metadata=metadata,
    )


def _has_layout(path: Path) -> bool:
    return any(_is_layout(p) for p in path.iterdir() if p.is_file())


def _is_layout(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith("_lo.xlsx") or name.endswith("_lo.xls")


def _is_neural_metrics(path: Path) -> bool:
    # We want to skip .~lock.* files
    return path.suffix.lower() == ".csv" and "neuralmetrics.csv" in path.name.lower()


def _pick_single(
    files: list[Path],
    predicate: Callable[[Path], bool],
    folder: Path,
    label: str,
) -> Path:
    matches = [p for p in files if predicate(p)]
    if not matches:
        raise DiscoveryError(folder, f"Missing {label}")
    if len(matches) > 1:
        names = ", ".join(sorted(p.name for p in matches))
        raise DiscoveryError(folder, f"Multiple {label} files found: {names}")
    return matches[0]


def _validate_metadata_consistency(
    baseline_csv: Path, exposure_csv: Path
) -> FilenameMetadata | None:
    try:
        baseline_meta = parse_filename_metadata(baseline_csv)
        exposure_meta = parse_filename_metadata(exposure_csv)
    except Exception as exc:
        logger.debug("Failed to parse filename metadata: %s", exc)
        return None

    if baseline_meta.code != exposure_meta.code:
        raise DiscoveryError(
            baseline_csv.parent,
            f"Baseline and exposure filenames do not match ("
            f"{baseline_meta.code} vs {exposure_meta.code})",
        )

    return baseline_meta.merge(exposure_meta)
