"""Ingestion service for Axion MEA exports."""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from ntx.metrics_metadata import AXION_METRICS_MAP, METRIC_SECTIONS, QC_BASELINE_METRICS
from ntx.metrics_schema import MetricsPayload
from ntx.models import (
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentStatus,
    NeuronalMetricsFrame,
    Project,
    Sex,
)
from ntx.utils import sanitize_numeric_json

from .discovery import ExperimentFolder, parse_filename_metadata
from .electrode_correction import ElectrodeCorrectionError, apply_electrode_correction
from .layout import ExperimentLayout, parse_layout_xlsx
from .parsers import parse_axion_csv, translate_metrics


class IngestionError(Exception):
    """Raised when ingestion cannot continue."""


@dataclass
class MetricsBundle:
    payload: dict
    qc: dict
    knockout_stats: dict


logger = logging.getLogger(__name__)


def create_experiment_from_files(
    folder: ExperimentFolder,
    *,
    project: Project | None = None,
    chemical: Chemical | None = None,
    control_chemical: Chemical | None = None,
    concentration_unit: ConcentrationUnit | None = None,
    default_unit_symbol: str = "uM",
    overwrite: bool = False,
    allow_missing_mask_metrics: bool = False,
    layout: ExperimentLayout | None = None,
) -> Experiment:
    """
    Transactionally create an experiment from Axion export files.
    """
    metadata = folder.metadata or parse_filename_metadata(folder.baseline_csv)
    if layout is None:
        layout = parse_layout_xlsx(folder.layout_file)

    project = project or _get_default_project()

    with transaction.atomic():
        existing = Experiment.objects.filter(project=project, code=metadata.code).first()
        if existing:
            if not overwrite:
                experiment = existing
            else:
                _delete_existing_experiment(existing)
                experiment = None
        else:
            experiment = None

        exposure_chemical = chemical or _get_or_create_chemical(metadata.chemical or "Unknown")
        resolved_control_chemical = control_chemical or _get_or_create_chemical("DMSO")
        unit_obj = concentration_unit or _get_or_create_unit(default_unit_symbol)

        if experiment is None:
            experiment = _create_experiment(project, metadata, layout, unit_obj)
            _create_experiment_files(experiment, folder, div=metadata.div, include_layout=True)

            conditions, wells = _create_conditions_and_layout(
                experiment,
                layout,
                exposure_chemical,
                resolved_control_chemical,
                unit_obj,
            )
            experiment.well_count = len(wells)
            experiment.condition_count = len(conditions)
        else:
            wells = _sort_wells(
                [
                    well
                    for condition in experiment.conditions.all()
                    for well in (condition.wells if isinstance(condition.wells, list) else [])
                ]
            )
            _assert_layout_compatible(experiment, layout, wells=wells)
            _create_experiment_files(experiment, folder, div=metadata.div, include_layout=False)

        frame_div = _metrics_frame_div(metadata)
        if NeuronalMetricsFrame.objects.filter(experiment=experiment, div=frame_div).exists():
            raise IngestionError(
                f"Metrics for experiment '{experiment.code}' already exist for DIV {frame_div}"
            )

        metrics_bundle = _process_metrics(
            experiment=experiment,
            baseline_path=folder.baseline_csv,
            exposure_path=folder.exposure_csv,
            wells=wells,
            allow_missing_mask_metrics=allow_missing_mask_metrics,
        )

        NeuronalMetricsFrame.objects.create(
            experiment=experiment,
            div=frame_div,
            metrics_json=metrics_bundle.payload,
            qc_json=metrics_bundle.qc,
        )

        experiment.knockout_stats = _compute_experiment_knockout_stats(experiment)
        experiment.status = ExperimentStatus.INGESTED
        experiment.parsed_at = timezone.now()
        experiment.save()

    return experiment


def _get_default_project() -> Project:
    try:
        return Project.objects.get(slug="default-project")
    except Project.DoesNotExist as exc:  # pragma: no cover - migration should seed this
        raise IngestionError("Default project (slug=default-project) is missing") from exc


def _get_or_create_chemical(name: str) -> Chemical:
    chemical = Chemical.objects.filter(name__iexact=name).first()
    if chemical:
        return chemical
    return Chemical.objects.create(name=name)


def _delete_existing_experiment(experiment: Experiment) -> None:
    """
    Remove an existing experiment and its related ingestion artifacts.
    """
    NeuronalMetricsFrame.objects.filter(experiment=experiment).delete()
    ExperimentFile.objects.filter(experiment=experiment).delete()
    Condition.objects.filter(experiment=experiment).delete()
    experiment.delete()


def _get_or_create_unit(symbol: str | None) -> ConcentrationUnit | None:
    if symbol is None:
        return None
    unit = (
        ConcentrationUnit.objects.filter(symbol__iexact=symbol).first()
        or ConcentrationUnit.objects.filter(name__iexact=symbol).first()
    )
    if unit:
        return unit
    return ConcentrationUnit.objects.create(name=symbol, symbol=symbol, slug=slugify(symbol))


def _create_experiment(
    project: Project,
    metadata,
    layout: ExperimentLayout,
    unit: ConcentrationUnit | None,
) -> Experiment:
    sex_value = _map_sex(metadata.sex)
    experiment = Experiment(
        project=project,
        code=metadata.code,
        sex=sex_value,
        researcher=(metadata.raw.get("mea:experimenter") or "") if hasattr(metadata, "raw") else "",
        date=layout.date,
        cell_line=metadata.cell_line or "",
        type=(metadata.raw.get("mea:type_of_exposure") or "") if hasattr(metadata, "raw") else "",
        manufacturer="axion",
        default_concentration_unit=unit,
    )
    experiment.save()
    return experiment


def _create_experiment_files(
    experiment: Experiment, folder: ExperimentFolder, div: int | None, *, include_layout: bool
) -> None:
    layout_name = _storage_name(folder.layout_file)
    baseline_name = _storage_name(folder.baseline_csv)
    exposure_name = _storage_name(folder.exposure_csv)

    if include_layout:
        ExperimentFile.objects.create(
            experiment=experiment,
            file=str(layout_name),
            kind=ExperimentFile.FileKind.LAYOUT,
        )
    ExperimentFile.objects.create(
        experiment=experiment,
        file=str(baseline_name),
        kind=ExperimentFile.FileKind.AXION_BASELINE,
        div=div,
    )
    ExperimentFile.objects.create(
        experiment=experiment,
        file=str(exposure_name),
        kind=ExperimentFile.FileKind.AXION_EXPOSURE,
        div=div,
    )


def _storage_name(path: str | Path) -> str:
    """
    Make sure the path lives under the default storage root and return its storage-relative
    name.
    """
    storage_root = getattr(default_storage, "location", None)
    if not storage_root:
        raise IngestionError("Default file storage must expose a filesystem location for ingestion")

    resolved = Path(path).resolve()
    if not resolved.exists():
        raise IngestionError(f"Ingested file does not exist: {resolved}")

    try:
        relative = resolved.relative_to(Path(storage_root).resolve())
    except ValueError as exc:
        raise IngestionError(
            f"Ingested file {resolved} must be located under storage root {storage_root}"
        ) from exc

    return str(relative)


def _create_conditions_and_layout(
    experiment: Experiment,
    layout: ExperimentLayout,
    exposure_chemical: Chemical,
    control_chemical: Chemical,
    unit: ConcentrationUnit | None,
) -> tuple[list[Condition], list[str]]:
    should_prefix = _should_prefix_sex(experiment.project, experiment.sex)
    prefix = _sex_prefix(experiment.sex) if should_prefix else ""

    conditions: list[Condition] = []
    all_wells: list[str] = []

    for cond_layout in layout.conditions:
        name = _format_condition_name(cond_layout, unit)
        if prefix:
            name = f"{prefix}{name}"

        condition_chemical = control_chemical if cond_layout.is_control else exposure_chemical
        condition = Condition(
            experiment=experiment,
            name=name,
            chemical=condition_chemical,
            concentration=cond_layout.concentration,
            unit=unit,
            is_control=cond_layout.is_control,
            wells=_sort_wells(cond_layout.wells),
        )
        condition.full_clean()
        condition.save()
        conditions.append(condition)
        all_wells.extend(condition.wells)

    wells = _sort_wells(all_wells)
    return conditions, wells


def _process_metrics(
    *,
    experiment: Experiment,
    baseline_path,
    exposure_path,
    wells: list[str],
    allow_missing_mask_metrics: bool = False,
) -> MetricsBundle:
    baseline_csv = parse_axion_csv(baseline_path)
    exposure_csv = parse_axion_csv(exposure_path)

    _assert_well_alignment(
        layout_wells=wells,
        baseline_wells=baseline_csv.wells,
        exposure_wells=exposure_csv.wells,
        baseline_path=baseline_path,
        exposure_path=exposure_path,
    )
    excluded_wells = experiment.excluded_wells

    if baseline_csv.settings.active_electrode_criterion is not None:
        experiment.active_electrode_criterion = baseline_csv.settings.active_electrode_criterion
    experiment.save(update_fields=["active_electrode_criterion"])

    baseline_map = translate_metrics(baseline_csv)
    exposure_map = translate_metrics(exposure_csv)

    try:
        correction = apply_electrode_correction(
            baseline_csv=baseline_csv,
            exposure_csv=exposure_csv,
            baseline_map=baseline_map,
            exposure_map=exposure_map,
            wells=wells,
            active_electrode_criterion=experiment.active_electrode_criterion,
            burst_frequency_threshold=experiment.burst_frequency_threshold,
        )
    except ElectrodeCorrectionError as exc:
        raise IngestionError(f"Electrode correction failed: {exc}") from exc

    baseline_map = correction.baseline_map
    exposure_map = correction.exposure_map

    _assert_qc_metrics_available(
        baseline_map=baseline_map,
        wells=wells,
        baseline_path=baseline_path,
        allow_missing_qc_metrics=allow_missing_mask_metrics,
        excluded_wells=excluded_wells,
    )

    params = [
        param
        for param in _collect_params(baseline_map, exposure_map)
        if param not in QC_BASELINE_METRICS
    ]

    baseline_matrix: list[list[float | int | None]] = []
    exposure_matrix: list[list[float | int | None]] = []
    ratio_matrix: list[list[float | int | None]] = []

    for param in params:
        baseline_row: list[float | int | None] = []
        exposure_row: list[float | int | None] = []
        ratio_row: list[float | int | None] = []
        for well in wells:
            baseline_val = baseline_map.get(param, {}).get(well)
            exposure_val = exposure_map.get(param, {}).get(well)

            ratio_val = _compute_ratio(baseline_val, exposure_val)

            baseline_row.append(baseline_val)
            exposure_row.append(exposure_val)
            ratio_row.append(ratio_val)

        baseline_matrix.append(baseline_row)
        exposure_matrix.append(exposure_row)
        ratio_matrix.append(ratio_row)

    qc_json = _build_qc_json(
        baseline_map,
        wells,
        number_of_active_electrodes=correction.number_of_active_electrodes,
        number_of_bursting_electrodes=correction.number_of_bursting_electrodes,
    )

    payload = {
        "params": params,
        "wells": wells,
        "baseline": baseline_matrix,
        "exposure": exposure_matrix,
        "ratio": ratio_matrix,
    }

    MetricsPayload.model_validate(payload)
    knockout_stats = _compute_knockout_stats(params, ratio_matrix)

    # Sanitize non-finite values before storing.
    sanitized_payload = sanitize_numeric_json(payload)
    sanitized_qc = sanitize_numeric_json(qc_json)

    return MetricsBundle(payload=sanitized_payload, qc=sanitized_qc, knockout_stats=knockout_stats)


def _metrics_frame_div(metadata) -> int:
    """
    Determine the DIV index for a stored metrics frame.

    Acute experiments are stored as a single slice with div=0. Chronic/subchronic
    experiments use the parsed DIV value.
    """
    exposure_type = ""
    raw = getattr(metadata, "raw", None)
    if isinstance(raw, dict):
        exposure_type = str(raw.get("mea:type_of_exposure") or "").strip().lower()

    if exposure_type in {"chronic", "subchronic"}:
        div = getattr(metadata, "div", None)
        if div is None:
            raise IngestionError("Chronic experiment ingestion requires a DIV in the filename")
        return int(div)

    return 0


def _assert_layout_compatible(
    experiment: Experiment, layout: ExperimentLayout, *, wells: list[str]
):
    existing_wells = set(wells)
    layout_wells = {well for condition in layout.conditions for well in condition.wells}
    if existing_wells != layout_wells:
        raise IngestionError(
            "Layout wells differ from existing experiment; refusing to ingest metrics frame. "
            f"experiment={experiment.code} existing_wells={len(existing_wells)} "
            f"layout_wells={len(layout_wells)}"
        )


def _compute_experiment_knockout_stats(experiment: Experiment) -> dict:
    """
    Aggregate knockout stats across all ingested metrics frames for an experiment.
    """
    section_map: dict[str, str] = {}
    for section, section_params in METRIC_SECTIONS.items():
        for param in section_params:
            section_map[param] = section

    totals: dict[str, int] = {}
    knockouts: dict[str, int] = {}

    for frame in experiment.neuronal_metrics_frames.all():
        payload = frame.metrics_json
        if not isinstance(payload, dict):
            continue

        params = payload.get("params")
        ratio = payload.get("ratio")
        if not isinstance(params, list) or not isinstance(ratio, list):
            continue

        for param, ratios in zip(params, ratio):
            if not isinstance(param, str):
                continue
            if not isinstance(ratios, list):
                continue

            section = section_map.get(param, "Other")
            totals[section] = totals.get(section, 0) + len(ratios)
            knockouts[section] = knockouts.get(section, 0) + sum(
                1 for value in ratios if value == -1
            )

    stats: dict[str, dict[str, float | int]] = {}
    for section, total in totals.items():
        knockout_count = knockouts.get(section, 0)
        percent = (knockout_count / total * 100) if total else 0
        stats[section] = {"count": knockout_count, "percent": round(percent, 2)}

    return stats


def _collect_params(
    baseline_map: dict[str, dict[str, float | int | None]],
    exposure_map: dict[str, dict[str, float | int | None]],
) -> list[str]:
    seen = set()
    params: list[str] = []
    available = set(baseline_map) | set(exposure_map)

    for param in AXION_ORDER:
        if param in available and param not in seen:
            params.append(param)
            seen.add(param)

    for param in available:
        if param not in seen:
            params.append(param)
            seen.add(param)
    return params


AXION_ORDER = list(dict.fromkeys(AXION_METRICS_MAP.values()))


def _format_wells(wells: Iterable[str], *, max_items: int = 20) -> str:
    values = list(wells)
    if len(values) > max_items:
        return f"{values[:max_items]} (+{len(values) - max_items} more)"
    return str(values)


def _assert_well_alignment(
    *,
    layout_wells: list[str],
    baseline_wells: list[str],
    exposure_wells: list[str],
    baseline_path,
    exposure_path,
) -> None:
    layout_set = set(layout_wells)
    baseline_set = set(baseline_wells)
    exposure_set = set(exposure_wells)

    baseline_dupes = sorted([well for well, count in Counter(baseline_wells).items() if count > 1])
    exposure_dupes = sorted([well for well, count in Counter(exposure_wells).items() if count > 1])
    if baseline_dupes or exposure_dupes:
        raise IngestionError(
            "Duplicate well labels found in Axion CSV headers. "
            f"baseline={baseline_dupes or 'none'} exposure={exposure_dupes or 'none'}"
        )

    errors: list[str] = []
    if baseline_set != exposure_set:
        baseline_only = _sort_wells(baseline_set - exposure_set)
        exposure_only = _sort_wells(exposure_set - baseline_set)
        errors.append(
            "Baseline/exposure CSV well headers differ. "
            f"baseline_only={_format_wells(baseline_only)} "
            f"exposure_only={_format_wells(exposure_only)}"
        )

    missing_from_baseline = _sort_wells(layout_set - baseline_set)
    extra_in_baseline = _sort_wells(baseline_set - layout_set)
    if missing_from_baseline:
        errors.append(
            "Layout contains wells missing from baseline CSV. "
            f"missing_from_baseline={_format_wells(missing_from_baseline)}"
        )
    if extra_in_baseline:
        logger.info(
            "Baseline CSV contains extra wells not in Layout (ignoring). "
            f"extra_in_baseline={_format_wells(extra_in_baseline)}"
        )

    missing_from_exposure = _sort_wells(layout_set - exposure_set)
    extra_in_exposure = _sort_wells(exposure_set - layout_set)
    if missing_from_exposure:
        errors.append(
            "Layout contains wells missing from exposure CSV. "
            f"missing_from_exposure={_format_wells(missing_from_exposure)}"
        )
    if extra_in_exposure:
        logger.info(
            "Exposure CSV contains extra wells not in Layout (ignoring). "
            f"extra_in_exposure={_format_wells(extra_in_exposure)}"
        )

    if errors:
        baseline_name = Path(baseline_path).name
        exposure_name = Path(exposure_path).name
        raise IngestionError(
            "Well mismatch detected. "
            f"Files: baseline={baseline_name} exposure={exposure_name}. " + " ".join(errors)
        )
    return None


def _assert_qc_metrics_available(
    *,
    baseline_map: dict[str, dict[str, float | int | None]],
    wells: list[str],
    baseline_path,
    allow_missing_qc_metrics: bool = False,
    excluded_wells: Iterable[str] | None = None,
) -> None:
    """
    Ensure baseline QC inputs exist.

    These are needed for well masking (inactive wells) and QC views.

    - `number_of_active_electrodes` and `number_of_bursting_electrodes` are derived from baseline
      electrode-level data (Measurement section) and stored in `qc_json`.
    - `number_of_network_bursts` baseline values are persisted as
      `number_network_bursts_baseline` in `qc_json`.
    """

    def _axion_label(param_name: str) -> str:
        for raw_label, internal in AXION_METRICS_MAP.items():
            if internal == param_name:
                return raw_label
        return param_name

    required_params = ("number_of_network_bursts",)

    missing_metric_rows: list[str] = []
    missing_values: dict[str, set[str]] = {}

    excluded = {well for well in (excluded_wells or []) if isinstance(well, str)}

    for param_name in required_params:
        values = baseline_map.get(param_name)
        if values is None:
            missing_metric_rows.append(param_name)
            continue

        missing_wells = {
            well for well in wells if well not in excluded and values.get(well) is None
        }
        if missing_wells:
            if not allow_missing_qc_metrics:
                raise IngestionError(
                    "Baseline CSV is missing required per-well values for "
                    f"'{param_name}' ({_axion_label(param_name)}), "
                    "so QC masking cannot be applied. "
                    f"Missing wells: {_format_wells(_sort_wells(missing_wells))}. "
                    f"File: {Path(baseline_path).name}"
                )
            missing_values[param_name] = missing_wells

    if missing_metric_rows:
        human = ", ".join(f"{name} ({_axion_label(name)})" for name in sorted(missing_metric_rows))
        raise IngestionError(
            "Baseline CSV is missing required QC metric rows needed for masking; "
            "refusing to ingest. "
            f"Missing metrics: {human}. "
            f"File: {Path(baseline_path).name}"
        )

    if missing_values:
        logger.warning(
            "QC metrics missing per-well values; analysis will treat affected wells as inactive. "
            "missing=%s file=%s",
            {name: _format_wells(_sort_wells(missing)) for name, missing in missing_values.items()},
            Path(baseline_path).name,
        )


def _build_qc_json(
    baseline_map: dict[str, dict[str, float | int | None]],
    wells: list[str],
    *,
    number_of_active_electrodes: dict[str, int] | None = None,
    number_of_bursting_electrodes: dict[str, int] | None = None,
) -> dict[str, list[float | int | None] | list[str]]:
    qc: dict[str, list[float | int | None] | list[str]] = {"wells": wells}
    for qc_param in QC_BASELINE_METRICS:
        if qc_param == "number_of_active_electrodes" and number_of_active_electrodes is not None:
            qc[qc_param] = [number_of_active_electrodes.get(well, 0) for well in wells]
            continue
        if (
            qc_param == "number_of_bursting_electrodes"
            and number_of_bursting_electrodes is not None
        ):
            qc[qc_param] = [number_of_bursting_electrodes.get(well, 0) for well in wells]
            continue

        source_param = (
            "number_of_network_bursts" if qc_param == "number_network_bursts_baseline" else qc_param
        )
        values_for_param = baseline_map.get(source_param, {})
        qc[qc_param] = [values_for_param.get(well) for well in wells]
    return qc


def _compute_ratio(
    baseline_val: float | int | None, exposure_val: float | int | None
) -> float | int | None:
    # Knockout semantics: -1 means exactly one side is missing.
    # Case 1: Baseline Missing (ratio impossible).
    # Case 2: Exposure Missing (actual knockout, different from 0).
    if baseline_val is None and exposure_val is None:
        return None
    if baseline_val is None or exposure_val is None:
        return -1
    if baseline_val == 0:
        return None

    value = exposure_val / baseline_val
    if not math.isfinite(value):
        return None
    return value


def _compute_knockout_stats(
    params: list[str], ratio_matrix: list[list[float | int | None]]
) -> dict:
    stats: dict[str, dict[str, float | int]] = {}
    section_map: dict[str, str] = {}
    for section, section_params in METRIC_SECTIONS.items():
        for param in section_params:
            section_map[param] = section

    per_section_totals: dict[str, int] = {}
    per_section_knockouts: dict[str, int] = {}

    for param, ratios in zip(params, ratio_matrix):
        section = section_map.get(param, "Other")
        per_section_totals.setdefault(section, 0)
        per_section_knockouts.setdefault(section, 0)

        per_section_totals[section] += len(ratios)
        per_section_knockouts[section] += sum(1 for value in ratios if value == -1)

    for section, total in per_section_totals.items():
        knockouts = per_section_knockouts.get(section, 0)
        percent = (knockouts / total * 100) if total else 0
        stats[section] = {"count": knockouts, "percent": round(percent, 2)}

    return stats


def _should_prefix_sex(project: Project, new_sex: str) -> bool:
    sexes = set(
        project.experiments.exclude(sex=Sex.UNKNOWN).values_list("sex", flat=True)  # type: ignore[attr-defined]
    )
    if new_sex and new_sex != Sex.UNKNOWN:
        sexes.add(new_sex)
    return len(sexes) > 1


def _sex_prefix(sex: str) -> str:
    if sex == Sex.FEMALE:
        return "F "
    if sex == Sex.MALE:
        return "M "
    if sex == Sex.MIXED:
        return "X "
    return ""


def _format_condition_name(cond_layout, unit: ConcentrationUnit | None) -> str:
    if cond_layout.is_control:
        return "Control"

    value = cond_layout.concentration
    if isinstance(value, Decimal):
        value_str = format(value.normalize(), "f").rstrip("0").rstrip(".") or "0"
    else:
        value_str = str(value)
    symbol = unit.symbol if unit else ""
    return f"{value_str} {symbol}".strip()


def _sort_wells(wells: Iterable[str]) -> list[str]:
    def _well_key(well: str):
        row = well[0].upper()
        try:
            col = int(well[1:])
        except ValueError:
            col = 0
        return (row, col)

    return sorted({well for well in wells}, key=_well_key)


def _map_sex(value: str | None) -> str:
    if not value:
        return Sex.UNKNOWN
    lower = value.lower()
    if lower.startswith("f"):
        return Sex.FEMALE
    if lower.startswith("m"):
        return Sex.MALE
    if lower.startswith("x") or lower == "mixed":
        return Sex.MIXED
    return Sex.UNKNOWN
