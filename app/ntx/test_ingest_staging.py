from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MethodType

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.http import HttpRequest
from django.test import RequestFactory

from .admin import ExperimentIngestAdmin, ExperimentIngestGroupInlineForm
from .exposure_types import ExposureType
from .ingest.discovery import discover_experiment_files
from .ingest.layout import ConditionLayout, ExperimentLayout
from .models import (
    ConcentrationUnit,
    Experiment,
    ExperimentIngest,
    ExperimentIngestGroup,
    Project,
    Sex,
)

pytestmark = pytest.mark.django_db


def _stored_name(path: Path, media_root: Path) -> str:
    return str(path.relative_to(media_root))


def _create_minimal_ingest(project: Project) -> ExperimentIngest:
    return ExperimentIngest.objects.create(
        project=project,
        layout_file="ingest/layouts/layout.xlsx",
        baseline_csv="ingest/baselines/baseline.csv",
        exposure_csv="ingest/exposures/exposure.csv",
    )


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
    assert ingest.error_stage == ExperimentIngest.ErrorStage.PROMOTE
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


def test_sync_groups_from_layout_clears_fractional_trailing_zeroes():
    project = Project.objects.get(slug="default-project")
    ingest = _create_minimal_ingest(project)
    ingest.chemical = "Lindane"
    layout = ExperimentLayout(
        date=date(2020, 10, 12),
        plate_wells=48,
        conditions=[
            ConditionLayout(
                concentration=None,
                wells=["A1"],
                is_control=True,
                chemical="Control",
            ),
            ConditionLayout(
                concentration=Decimal("100.000000"),
                wells=["A2"],
                is_control=False,
                unit="uM",
            ),
            ConditionLayout(
                concentration=Decimal("0.100000"),
                wells=["A3"],
                is_control=False,
                unit="uM",
            ),
        ],
    )

    ingest.sync_groups_from_layout(layout)

    groups = list(ingest.ingest_groups.order_by("id"))
    assert groups[1].concentration == Decimal("100")
    assert groups[2].concentration == Decimal("0.1")


def test_ingest_group_inline_form_formats_concentration_initial_value():
    project = Project.objects.get(slug="default-project")
    ingest = _create_minimal_ingest(project)
    group = ExperimentIngestGroup.objects.create(
        ingest=ingest,
        chemical="Lindane",
        concentration=Decimal("100.000000"),
        wells="A1",
    )

    form = ExperimentIngestGroupInlineForm(instance=group)

    assert form.initial["concentration"] == "100"


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
    assert ingest.error_stage == ExperimentIngest.ErrorStage.PROMOTE
    assert "Duplicate well 'A1'" in ingest.error_message
    assert not Experiment.objects.filter(code=ingest.code).exists()


def test_admin_promotion_reports_attempted_failures_separately(
    stored_data_dir: Path,
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(model_admin, "message_user", MethodType(capture_message, model_admin))

    model_admin._promote_to_experiment(
        request,
        ExperimentIngest.objects.filter(pk__in=[failed_ingest.pk, skipped_ingest.pk]),
        replace_existing=False,
    )

    failed_ingest.refresh_from_db()
    assert failed_ingest.status == ExperimentIngest.Status.ERROR
    assert failed_ingest.error_stage == ExperimentIngest.ErrorStage.PROMOTE
    assert captured == [("0 experiments created, 1 failed, 1 skipped.", messages.WARNING)]


def test_corrected_promotion_error_becomes_parsed(
    stored_data_dir: Path,
    media_root: Path,
):
    ingest = _create_invalid_parsed_ingest(
        stored_data_dir=stored_data_dir,
        media_root=media_root,
        exposure_type=ExposureType.UNDEFINED,
    )

    # Fix the duplicate wells created by the helper so only exposure is invalid.
    ingest.ingest_groups.filter(is_control=False).update(wells="A2")

    with pytest.raises(ValidationError):
        ingest.execute_ingest()

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.ERROR
    assert ingest.error_stage == ExperimentIngest.ErrorStage.PROMOTE

    # Revalidating without correcting the exposure must retain the error.
    ingest.revalidate_after_edit()

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.ERROR
    assert ingest.error_stage == ExperimentIngest.ErrorStage.PROMOTE
    assert "Exposure type must be set" in ingest.error_message

    # Correct the exposure and revalidate, as the admin does after saving.
    ingest.exposure_type = ExposureType.ACUTE
    ingest.save(update_fields=["exposure_type", "updated_at"])
    ingest.revalidate_after_edit()

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.PARSED
    assert ingest.error_stage == ""
    assert ingest.error_message == ""


@pytest.mark.parametrize(
    ("selected_sex", "expected_status"),
    [
        (Sex.MALE, ExperimentIngest.Status.INGESTED),
        (Sex.MIXED, ExperimentIngest.Status.EDITED),
    ],
)
def test_admin_save_marks_only_changed_metadata_as_edited(
    selected_sex,
    expected_status,
):
    project = Project.objects.get(slug="default-project")
    ingest = _create_minimal_ingest(project)
    ingest.status = ExperimentIngest.Status.INGESTED
    ingest.sex = Sex.MALE
    ingest.save()

    form_class = modelform_factory(ExperimentIngest, fields=["sex"])
    form = form_class(data={"sex": selected_sex}, instance=ingest)
    assert form.is_valid(), form.errors

    request = RequestFactory().post("/admin/ntx/experimentingest/")
    model_admin = ExperimentIngestAdmin(ExperimentIngest, AdminSite())

    obj = form.save(commit=False)
    model_admin.save_model(request, obj, form, change=True)
    model_admin.save_related(request, form, [], change=True)

    ingest.refresh_from_db()
    assert ingest.sex == selected_sex
    assert ingest.status == expected_status


@pytest.mark.parametrize("corrected_sex", [Sex.FEMALE, Sex.MIXED])
def test_admin_replacement_uses_corrected_sex(
    stored_data_dir: Path,
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrected_sex,
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
    ingest.sex = Sex.MALE
    ingest.exposure_type = ExposureType.ACUTE
    ingest.save()

    original = ingest.execute_ingest()
    assert original.sex == Sex.MALE

    form_class = modelform_factory(ExperimentIngest, fields=["sex"])
    form = form_class(data={"sex": corrected_sex}, instance=ingest)
    assert form.is_valid(), form.errors

    request = RequestFactory().post("/admin/ntx/experimentingest/")
    model_admin = ExperimentIngestAdmin(ExperimentIngest, AdminSite())

    obj = form.save(commit=False)
    model_admin.save_model(request, obj, form, change=True)
    model_admin.save_related(request, form, [], change=True)

    ingest.refresh_from_db()
    assert ingest.status == ExperimentIngest.Status.EDITED

    # Editing the ingest must not immediately change the experiment.
    original.refresh_from_db()
    assert original.sex == Sex.MALE

    captured = []

    def capture_message(request, message, **kwargs):
        captured.append(message)

    monkeypatch.setattr(model_admin, "message_user", capture_message)

    # Ordinary promotion must skip edited records.
    model_admin.promote_to_experiment(
        request,
        ExperimentIngest.objects.filter(pk=ingest.pk),
    )
    assert captured[-1] == "0 experiments created, 0 failed, 1 skipped."

    with pytest.raises(ValidationError, match="replacement"):
        ingest.execute_ingest()

    # Explicit replacement must use the corrected sex.
    model_admin.promote_to_experiment_replacing_existing(
        request,
        ExperimentIngest.objects.filter(pk=ingest.pk),
    )
    assert captured[-1] == "1 experiments created, 0 failed, 0 skipped."

    ingest.refresh_from_db()
    replacement = Experiment.objects.get(code=ingest.code)

    assert ingest.status == ExperimentIngest.Status.INGESTED
    assert replacement.sex == corrected_sex
    assert replacement.pk != original.pk
    assert not Experiment.objects.filter(pk=original.pk).exists()
