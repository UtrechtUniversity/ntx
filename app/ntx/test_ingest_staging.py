from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MethodType

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.test import RequestFactory
from openpyxl import load_workbook

from .admin import ExperimentIngestAdmin
from .exposure_types import ExposureType
from .ingest.discovery import discover_experiment_files
from .models import ConcentrationUnit, Experiment, ExperimentIngest, ExperimentIngestGroup, Project

pytestmark = pytest.mark.django_db


def _stored_name(path: Path, media_root: Path) -> str:
    return str(path.relative_to(media_root))


def _create_invalid_parsed_ingest(
    *,
    stored_data_dir: Path,
    media_root: Path,
    code: str = "STAGED-INVALID",
    exposure_type: str = ExposureType.ACUTE,
) -> ExperimentIngest:
    folder = discover_experiment_files(stored_data_dir)
    project = Project.objects.get(slug="default-project")

    ingest = ExperimentIngest.objects.create(
        project=project,
        status=ExperimentIngest.Status.PARSED,
        layout_file=_stored_name(folder.layout_file, media_root),
        baseline_csv=_stored_name(folder.baseline_csv, media_root),
        exposure_csv=_stored_name(folder.exposure_csv, media_root),
        code=code,
        div=folder.metadata.div if folder.metadata else 10,
        chemical=folder.metadata.chemical if folder.metadata else "Lindane",
        cell_line=folder.metadata.cell_line if folder.metadata else "rcortex",
        date=date(2020, 10, 12),
        exposure_type=exposure_type,
        layout_date=date(2020, 10, 12),
        layout_wells=48,
    )
    ExperimentIngestGroup.objects.create(
        ingest=ingest,
        chemical="Control",
        is_control=True,
        wells="A1",
    )
    ExperimentIngestGroup.objects.create(
        ingest=ingest,
        chemical="Lindane",
        concentration=Decimal("0.1"),
        unit=ConcentrationUnit.objects.get_or_create(
            symbol="uM",
            defaults={"name": "uM", "slug": "um"},
        )[0],
        is_control=False,
        wells="A1",
    )
    return ingest


def test_execute_ingest_requires_defined_exposure_type(
    stored_data_dir: Path,
    media_root: Path,
):
    ingest = _create_invalid_parsed_ingest(
        stored_data_dir=stored_data_dir,
        media_root=media_root,
        code="STAGED-UNDEFINED",
        exposure_type=ExposureType.UNDEFINED,
    )

    with pytest.raises(ValidationError) as excinfo:
        ingest.execute_ingest()

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.ERROR
    assert "Exposure type must be set" in str(excinfo.value)
    assert "Exposure type must be set" in ingest.error_message
    assert not Experiment.objects.filter(code=ingest.code).exists()


def test_parse_files_defaults_missing_exposure_type_to_undefined(
    stored_data_dir: Path,
    media_root: Path,
):
    folder = discover_experiment_files(stored_data_dir)
    project = Project.objects.get(slug="default-project")
    ingest = ExperimentIngest.objects.create(
        project=project,
        layout_file=_stored_name(folder.layout_file, media_root),
        baseline_csv=_stored_name(folder.baseline_csv, media_root),
        exposure_csv=_stored_name(folder.exposure_csv, media_root),
    )

    ingest.parse_files()

    assert ingest.status == ExperimentIngest.Status.PARSED
    assert ingest.exposure_type == ExposureType.UNDEFINED


def test_execute_ingest_marks_staged_validation_failure_as_error(
    stored_data_dir: Path,
    media_root: Path,
):
    ingest = _create_invalid_parsed_ingest(
        stored_data_dir=stored_data_dir,
        media_root=media_root,
    )

    with pytest.raises(ValidationError):
        ingest.execute_ingest()

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.ERROR
    assert "Duplicate well 'A1'" in ingest.error_message
    assert not Experiment.objects.filter(code=ingest.code).exists()


def test_admin_promotion_reports_attempted_failures_separately(
    stored_data_dir: Path,
    media_root: Path,
):
    failed_ingest = _create_invalid_parsed_ingest(
        stored_data_dir=stored_data_dir,
        media_root=media_root,
    )
    skipped_ingest = _create_invalid_parsed_ingest(
        stored_data_dir=stored_data_dir,
        media_root=media_root,
        code="STAGED-SKIPPED",
    )
    skipped_ingest.status = ExperimentIngest.Status.PENDING
    skipped_ingest.save(update_fields=["status", "updated_at"])

    request = RequestFactory().post("/admin/ntx/experimentingest/")
    model_admin = ExperimentIngestAdmin(ExperimentIngest, AdminSite())
    captured: list[tuple[object, int | str]] = []

    def capture_message(
        self: ExperimentIngestAdmin,
        request: HttpRequest,
        message: object,
        level: int | str = messages.INFO,
        extra_tags: str = "",
        fail_silently: bool = False,
    ) -> None:
        captured.append((message, level))

    model_admin.message_user = MethodType(capture_message, model_admin)

    model_admin._promote_to_experiment(
        request,
        ExperimentIngest.objects.filter(pk__in=[failed_ingest.pk, skipped_ingest.pk]),
        replace_existing=False,
    )

    failed_ingest.refresh_from_db()
    assert failed_ingest.status == ExperimentIngest.Status.ERROR
    assert captured == [("0 experiments created, 1 failed, 1 skipped.", messages.WARNING)]
