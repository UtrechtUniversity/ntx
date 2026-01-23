from __future__ import annotations

import csv
from pathlib import Path
from typing import cast

import pytest

from .ingest.discovery import ExperimentFolder, discover_experiment_files
from .ingest.layout import parse_layout_xlsx
from .ingest.service import IngestionError, create_experiment_from_files
from .metrics_schema import MetricsPayload
from .models import (
    Chemical,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentStatus,
    NeuronalMetricsFrame,
)

pytestmark = pytest.mark.django_db


def _measurement_stub(wells: list[str]) -> str:
    electrodes = [f"{well}_11" for well in wells]
    values = ",".join("0" for _ in electrodes)
    return (
        "Measurement," + ",".join(electrodes) + ",\n"
        "Activity Metrics\n"
        "Mean Firing Rate (Hz)," + values + ",\n"
        "Electrode Burst Metrics\n"
        "Burst Frequency (Hz)," + values + ",\n"
    )


def _write_csv_with_well_header(
    source: Path,
    dest: Path,
    *,
    drop_well: str | None = None,
    add_well: str | None = None,
) -> None:
    rows: list[list[str]] = []
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row and (row[0] or "").strip().lower() == "well averages":
                wells = [cell for cell in row[1:] if (cell or "").strip()]
                if drop_well:
                    wells = [well for well in wells if well != drop_well]
                if add_well and add_well not in wells:
                    wells.append(add_well)
                row = [row[0], *wells]
            rows.append(row)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_create_experiment_from_real_folder(stored_data_dir: Path):
    folder = discover_experiment_files(stored_data_dir)
    experiment = create_experiment_from_files(folder)

    assert experiment.code == "201210_LvM_256135_1294-67"
    assert experiment.status == ExperimentStatus.INGESTED
    assert experiment.parsed_at is not None
    assert experiment.well_count == 48
    assert experiment.condition_count == 6

    all_wells = [well for condition in experiment.conditions.all() for well in condition.wells]
    assert len(all_wells) == experiment.well_count
    assert len(set(all_wells)) == experiment.well_count

    assert Condition.objects.filter(experiment=experiment, is_control=True).exists()
    control_condition = Condition.objects.get(experiment=experiment, is_control=True)
    assert control_condition.chemical.name == "DMSO"
    expected_exposure_chemical = (
        folder.metadata.chemical if folder.metadata and folder.metadata.chemical else "Unknown"
    )
    non_control_chemicals = set(
        Condition.objects.filter(experiment=experiment, is_control=False).values_list(
            "chemical__name",
            flat=True,
        )
    )
    assert non_control_chemicals == {expected_exposure_chemical}
    assert experiment.files.count() == 3
    assert experiment.files.filter(kind=ExperimentFile.FileKind.LAYOUT).exists()
    assert experiment.files.filter(kind=ExperimentFile.FileKind.AXION_BASELINE).exists()
    assert experiment.files.filter(kind=ExperimentFile.FileKind.AXION_EXPOSURE).exists()
    for exp_file in experiment.files.all():
        assert exp_file.file.name
        assert exp_file.file.name.startswith("axion/")

    frame = NeuronalMetricsFrame.objects.get(experiment=experiment, div=0)
    payload = MetricsPayload.model_validate(frame.metrics_json)
    assert len(payload.wells) == experiment.well_count
    assert set(payload.wells) == set(all_wells)

    baseline_matrix = cast(list[list[float | int | None]], payload.baseline)
    ratio_matrix = cast(list[list[float | int | None]], payload.ratio)

    baseline_values = [value for row in baseline_matrix for value in row]
    assert any(value is not None for value in baseline_values)

    ratio_values = [value for row in ratio_matrix for value in row]
    assert any(value not in (None, -1) for value in ratio_values)

    assert experiment.knockout_stats
    for section, stats in experiment.knockout_stats.items():
        assert section
        assert "count" in stats
        assert "percent" in stats
        assert 0 <= stats["percent"] <= 100

    qc_json = frame.qc_json
    assert "number_network_bursts_baseline" in qc_json
    assert len(qc_json["wells"]) == experiment.well_count


def test_create_experiment_allows_control_chemical_override(stored_data_dir: Path):
    folder = discover_experiment_files(stored_data_dir)
    water, _ = Chemical.objects.get_or_create(name="Water")
    experiment = create_experiment_from_files(folder, control_chemical=water, overwrite=True)

    control_condition = Condition.objects.get(experiment=experiment, is_control=True)
    assert control_condition.chemical == water


def test_overwrite_existing_experiment(stored_data_dir: Path):
    folder = discover_experiment_files(stored_data_dir)
    first = create_experiment_from_files(folder)
    first_id = first.id

    # Re-import with overwrite should replace the existing experiment (same code).
    second = create_experiment_from_files(folder, overwrite=True)
    assert first_id != second.id
    assert not Experiment.objects.filter(id=first_id).exists()
    assert Experiment.objects.filter(project=second.project, code=second.code).count() == 1

    frame = second.neuronal_metrics_frames.get(div=0)
    MetricsPayload.model_validate(frame.metrics_json)


def test_ratio_contains_values_for_common_metric(stored_data_dir: Path):
    folder = discover_experiment_files(stored_data_dir)
    experiment = create_experiment_from_files(folder, overwrite=True)
    payload = experiment.neuronal_metrics_frames.get(div=0).metrics_json
    burst_idx = payload["params"].index("burst_frequency")
    ratio_row = payload["ratio"][burst_idx]
    assert sum(1 for value in ratio_row if value not in (None, -1)) > (experiment.well_count / 2)


def test_overwrite_rolls_back_on_failure(stored_data_dir: Path, media_root: Path):
    folder = discover_experiment_files(stored_data_dir)
    original = create_experiment_from_files(folder)
    original_id = original.id

    tmp_dir = media_root / "axion" / "tmp_failure"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    baseline_path.write_text("This is not a valid Axion CSV export\n")
    exposure_path = tmp_dir / "exposure_neuralMetrics.csv"
    exposure_path.write_text("This is not a valid Axion CSV export\n")

    bad_folder = ExperimentFolder(
        path=baseline_path.parent,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=exposure_path,
        metadata=folder.metadata,
    )

    with pytest.raises(ValueError):
        create_experiment_from_files(bad_folder, overwrite=True)

    assert Experiment.objects.filter(id=original_id).exists()
    assert NeuronalMetricsFrame.objects.filter(experiment_id=original_id).exists()


def test_ingest_fails_on_missing_wells(stored_data_dir: Path, media_root: Path):
    folder = discover_experiment_files(stored_data_dir)
    layout = parse_layout_xlsx(folder.layout_file)
    layout_wells = [well for condition in layout.conditions for well in condition.wells]
    assert len(layout_wells) > 0

    missing_well = layout_wells[-1]

    tmp_dir = media_root / "axion" / "tmp_mismatch"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    exposure_path = tmp_dir / "exposure_neuralMetrics.csv"
    _write_csv_with_well_header(folder.baseline_csv, baseline_path, drop_well=missing_well)
    _write_csv_with_well_header(folder.exposure_csv, exposure_path, drop_well=missing_well)

    bad_folder = ExperimentFolder(
        path=baseline_path.parent,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=exposure_path,
        metadata=folder.metadata,
    )

    before_count = Experiment.objects.count()
    with pytest.raises(IngestionError) as excinfo:
        create_experiment_from_files(bad_folder, overwrite=True)
    assert missing_well in str(excinfo.value)
    assert Experiment.objects.count() == before_count


def test_ingest_succeeds_with_extra_csv_wells(stored_data_dir: Path, media_root: Path):
    folder = discover_experiment_files(stored_data_dir)
    layout = parse_layout_xlsx(folder.layout_file)
    layout_wells = [well for condition in layout.conditions for well in condition.wells]

    tmp_dir = media_root / "axion" / "tmp_extra"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # To be safe, adding a new well that definitely isn't in layout.
    extra_well = "Z99"
    assert extra_well not in layout_wells

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    exposure_path = tmp_dir / "exposure_neuralMetrics.csv"
    _write_csv_with_well_header(folder.baseline_csv, baseline_path, add_well=extra_well)
    _write_csv_with_well_header(folder.exposure_csv, exposure_path, add_well=extra_well)

    new_folder = ExperimentFolder(
        path=baseline_path.parent,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=exposure_path,
        metadata=folder.metadata,
    )

    # Should succeed
    experiment = create_experiment_from_files(new_folder, overwrite=True)
    assert experiment.status == ExperimentStatus.INGESTED
    # Ensure well count matches LAYOUT, not CSV
    assert experiment.well_count == len(layout_wells)
    frame = experiment.neuronal_metrics_frames.get(div=0)
    assert extra_well not in frame.metrics_json["wells"]


def test_ingest_fails_when_mask_metrics_missing(stored_data_dir: Path, media_root: Path):
    folder = discover_experiment_files(stored_data_dir)
    layout = parse_layout_xlsx(folder.layout_file)
    layout_wells = [well for condition in layout.conditions for well in condition.wells]

    # Minimal CSV with correct wells, but without the QC/mask metrics rows.
    tmp_dir = media_root / "axion" / "tmp_missing_mask"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    baseline_path.write_text(
        "Well Averages," + ",".join(layout_wells) + ",\n" + _measurement_stub(layout_wells)
    )

    bad_folder = ExperimentFolder(
        path=folder.path,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=folder.exposure_csv,
        metadata=folder.metadata,
    )

    before_count = Experiment.objects.count()
    with pytest.raises(IngestionError):
        create_experiment_from_files(bad_folder, overwrite=True)
    assert Experiment.objects.count() == before_count


def test_ingest_lenient_missing_mask_metrics_marks_inactive(
    stored_data_dir: Path, media_root: Path
):
    folder = discover_experiment_files(stored_data_dir)
    layout = parse_layout_xlsx(folder.layout_file)
    layout_wells = [well for condition in layout.conditions for well in condition.wells]

    # QC metric rows exist but per-well values are NaN; lenient mode accepts this and
    # analysis will treat affected wells as inactive.
    tmp_dir = media_root / "axion" / "tmp_lenient_mask"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    baseline_path.write_text(
        "".join(
            [
                "Well Averages," + ",".join(layout_wells) + ",\n",
                "Number of Active Electrodes," + ",".join("NaN" for _ in layout_wells) + ",\n",
                "Number of Network Bursts," + ",".join("NaN" for _ in layout_wells) + ",\n",
                _measurement_stub(layout_wells),
            ]
        )
    )

    bad_folder = ExperimentFolder(
        path=folder.path,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=folder.exposure_csv,
        metadata=folder.metadata,
    )

    experiment = create_experiment_from_files(
        bad_folder, overwrite=True, allow_missing_mask_metrics=True
    )
    qc_json = experiment.neuronal_metrics_frames.get(div=0).qc_json
    assert all(value == 0 for value in qc_json["number_of_active_electrodes"])
    assert all(value == 0 for value in qc_json["number_of_bursting_electrodes"])
    assert all(value is None for value in qc_json["number_network_bursts_baseline"])


def test_ingest_strict_missing_per_well_mask_metrics_raises(
    stored_data_dir: Path, media_root: Path
):
    folder = discover_experiment_files(stored_data_dir)
    layout = parse_layout_xlsx(folder.layout_file)
    layout_wells = [well for condition in layout.conditions for well in condition.wells]

    tmp_dir = media_root / "axion" / "tmp_strict_mask"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = tmp_dir / "baseline_neuralMetrics.csv"
    baseline_path.write_text(
        "".join(
            [
                "Well Averages," + ",".join(layout_wells) + ",\n",
                "Number of Active Electrodes," + ",".join("NaN" for _ in layout_wells) + ",\n",
                "Number of Network Bursts," + ",".join("NaN" for _ in layout_wells) + ",\n",
                _measurement_stub(layout_wells),
            ]
        )
    )

    bad_folder = ExperimentFolder(
        path=folder.path,
        layout_file=folder.layout_file,
        baseline_csv=baseline_path,
        exposure_csv=folder.exposure_csv,
        metadata=folder.metadata,
    )

    before_count = Experiment.objects.count()
    with pytest.raises(IngestionError):
        create_experiment_from_files(bad_folder, overwrite=True)
    assert Experiment.objects.count() == before_count
