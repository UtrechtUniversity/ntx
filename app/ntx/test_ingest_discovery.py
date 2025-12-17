from __future__ import annotations

from pathlib import Path

from .ingest.discovery import (
    ScanResult,
    discover_experiment_files,
    parse_filename_metadata,
    scan_folder,
)


def test_parse_filename_metadata(data_dir: Path):
    baseline_csv = (
        data_dir / "201210_LvM_256135_1294-67_MEA_rCortex_Lindane_baseline_female_DIV10(000)_"
        "Spike Detector (7 x STD)(000)_neuralMetrics.csv"
    )
    meta = parse_filename_metadata(baseline_csv)
    assert meta.code == "201210_LvM_256135_1294-67"
    assert meta.chemical == "Lindane"
    assert meta.measurement == "baseline"
    assert meta.sex == "female"
    assert meta.div == 10
    assert meta.cell_line == "rcortex"


def test_discover_experiment_files(data_dir: Path):
    folder = discover_experiment_files(data_dir)
    assert folder.path == data_dir
    assert folder.layout_file.name.endswith("_LO.xlsx")
    assert "baseline" in folder.baseline_csv.name.lower()
    assert "exposure" in folder.exposure_csv.name.lower()
    assert folder.metadata is not None
    assert folder.metadata.code == "201210_LvM_256135_1294-67"


def test_scan_folder_single_experiment(data_dir: Path):
    result = scan_folder(data_dir)
    assert isinstance(result, ScanResult)
    assert len(result.experiments) == 1
    assert result.errors == []
    assert result.experiments[0].path == data_dir


def test_scan_folder_no_layout_returns_error(tmp_path: Path):
    result = scan_folder(tmp_path)
    assert result.experiments == []
    assert len(result.errors) == 1
    assert result.errors[0].folder == tmp_path
    assert "No layout files found" in str(result.errors[0])
