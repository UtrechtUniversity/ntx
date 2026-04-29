from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.db.models import Count
from django.forms import Textarea
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

from ntx.ingest.metadata import collect_experiment_metadata_from_files

from .metrics_metadata import METRIC_SECTIONS
from .metrics_schema import MetricsPayload
from .models import (
    DIV_NUM_RE,
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentIngest,
    ExperimentIngestGroup,
    NeuronalMetricsFrame,
    Project,
    Sex,
    _first_present,
)

Numeric = float | int | None


def _format_metrics_value(value: Numeric, *, is_ratio: bool) -> str:
    if value is None:
        return ""
    if is_ratio and value == -1:
        return "KO"
    if isinstance(value, int):
        return str(value)
    # Format with 4 significant digits (for now)
    return format(float(value), ".4g")


def _render_metrics_table(
    *,
    params: Sequence[str],
    wells: Sequence[str],
    rows: Sequence[Sequence[Numeric]],
    is_ratio: bool,
) -> SafeString:
    table_rows = _build_table_rows(params=params, wells=wells, rows=rows, is_ratio=is_ratio)

    return mark_safe(
        render_to_string(
            "admin/ntx/neuronalmetrics/metrics_table.html",
            {"wells": list(wells), "rows": table_rows},
        )
    )


def _build_table_rows(
    *,
    params: Sequence[str],
    wells: Sequence[str],
    rows: Sequence[Sequence[Numeric]],
    is_ratio: bool,
) -> list[dict[str, str | list[str]]]:
    table_rows: list[dict[str, str | list[str]]] = []
    for param, row in zip(params, rows):
        cells = [
            _format_metrics_value(row[idx] if idx < len(row) else None, is_ratio=is_ratio)
            for idx in range(len(wells))
        ]
        table_rows.append({"param": param, "cells": cells})
    return table_rows


def _render_admin_message(message: str) -> SafeString:
    return mark_safe(
        render_to_string(
            "admin/ntx/neuronalmetrics/message.html",
            {"message": message},
        )
    )


def _render_metrics_payload_table(payload: MetricsPayload, *, section: str) -> SafeString:
    matrix = getattr(payload, section)
    is_ratio = section == "ratio"

    return _render_metrics_table(
        params=payload.params,
        wells=payload.wells,
        rows=matrix,
        is_ratio=is_ratio,
    )


def _render_qc_json_table(qc_json: object) -> SafeString:
    if not qc_json:
        return _render_admin_message("No qc_json payload stored.")

    qc = cast(dict[str, Any], qc_json)
    wells = cast(list[str], qc["wells"])

    params = list(METRIC_SECTIONS.get("QC", []))
    extras = sorted(key for key in qc.keys() if key not in {"wells"} and key not in params)
    params.extend(extras)

    rows = [cast(list[Numeric], qc[param]) for param in params]
    return _render_metrics_table(params=params, wells=wells, rows=rows, is_ratio=False)


class ReadOnlyAdminMixin:
    """
    Disable editing in the admin for ingestion-backed models.
    """

    actions = None

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def get_readonly_fields(self, request, obj=None) -> list[str]:
        model_cls = cast(type[models.Model] | None, getattr(self, "model", None))
        if model_cls is None:
            return []
        fields = [field.name for field in model_cls._meta.fields]
        fields.extend(field.name for field in model_cls._meta.many_to_many)
        return fields


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "outlier_method",
        "start_date",
        "end_date",
        "experiments_count",
        "collaborators_count",
    )
    search_fields = ("name", "slug", "description")
    list_filter = ("outlier_method",)
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("collaborators",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _experiments_count=Count("experiments", distinct=True),
            _collaborators_count=Count("collaborators", distinct=True),
        )

    @admin.display(description="Experiments")
    def experiments_count(self, obj):
        return getattr(obj, "_experiments_count", 0)

    @admin.display(description="Collaborators")
    def collaborators_count(self, obj):
        return getattr(obj, "_collaborators_count", 0)


@admin.register(Chemical)
class ChemicalAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "canonical", "experiments_count", "conditions_count")
    search_fields = ("name", "slug", "description")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _conditions_count=Count("conditions", distinct=True),
            _experiments_count=Count("conditions__experiment", distinct=True),
        )

    @admin.display(description="Conditions")
    def conditions_count(self, obj):
        return getattr(obj, "_conditions_count", 0)

    @admin.display(description="Experiments")
    def experiments_count(self, obj):
        return getattr(obj, "_experiments_count", 0)


@admin.register(ConcentrationUnit)
class ConcentrationUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "slug", "canonical", "conditions_count")
    search_fields = ("name", "slug", "symbol")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _conditions_count=Count("conditions", distinct=True),
        )

    @admin.display(description="Conditions")
    def conditions_count(self, obj):
        return getattr(obj, "_conditions_count", 0)


@admin.register(Experiment)
class ExperimentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "code",
        "project",
        "status",
        "sex",
        "date",
        "condition_count",
        "well_count",
        "parsed_at",
    )
    list_filter = ("status", "sex", "project")
    search_fields = ("code", "researcher", "cell_line", "manufacturer")
    list_select_related = ("project",)


class ExperimentIngestGroupForm(forms.ModelForm):
    class Meta:
        model = ExperimentIngestGroup
        # fields = "__all__"
        fields = ["chemical", "concentration", "unit", "is_control", "wells"]
        widgets = {
            "chemical": forms.TextInput(attrs={"size": 10, "style": "width: 10em;"}),
            "concentration": forms.TextInput(
                attrs={
                    "size": 10,
                    "style": "width: 5em;",
                }
            ),
            "unit": forms.TextInput(
                attrs={
                    "size": 10,
                    "style": "width: 5em;",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        value = getattr(self.instance, "concentration", None)
        if value is not None and "concentration" in self.fields:
            try:
                dec = value if isinstance(value, Decimal) else Decimal(str(value))
                value_str = format(dec.normalize(), "f")
                if "." in value_str:
                    value_str = value_str.rstrip("0").rstrip(".")
                self.initial["concentration"] = value_str or "0"
            except Exception:
                self.initial["concentration"] = value


class ExperimentIngestGroupInline(admin.TabularInline):
    model = ExperimentIngestGroup
    form = ExperimentIngestGroupForm
    extra = 0
    fields = ("chemical", "concentration", "unit", "is_control", "wells")
    ordering = ("id",)
    formfield_overrides = {
        models.TextField: {
            "widget": Textarea(attrs={"rows": 2, "cols": 25, "style": "resize: horizontal;"})
        }
    }


class ExperimentIngestAdminForm(forms.ModelForm):
    class Meta:
        model = ExperimentIngest
        fields = [
            "project",
            "status",
            "submission_method",
            "layout_file",
            "baseline_csv",
            "exposure_csv",
            "code",
            "sex",
            "div",
            "chemical",
            "cell_line",
            "experimenter",
            "date",
            "plate_number",
            "layout_date",
            "layout_wells",
            "control_group",
            "layout_groups",
            "layout_input",
            "parsed_meta",
        ]

    def clean(self):
        """
        On ADD:
        - Parse the uploaded files using temp copies that preserve original filenames.
        - Extract the experiment code.
        - If no code: show an error.
        - If duplicate in ExperimentIngest or Experiment: show an error.
        On CHANGE:
        - Skip parsing.
        """
        cleaned_data = super().clean() or {}

        # Only run this extra validation on ADD
        if self.instance.pk:
            return cleaned_data

        layout_file = cleaned_data.get("layout_file")
        baseline_csv = cleaned_data.get("baseline_csv")
        exposure_csv = cleaned_data.get("exposure_csv")

        if not (layout_file and baseline_csv and exposure_csv):
            return cleaned_data

        tmp_dir = tempfile.mkdtemp()

        def _write_with_original_name(uploaded):
            dst_path = os.path.join(tmp_dir, uploaded.name)
            with open(dst_path, "wb+") as f:
                for chunk in uploaded.chunks():
                    f.write(chunk)
            return dst_path

        layout_path = _write_with_original_name(layout_file)
        baseline_path = _write_with_original_name(baseline_csv)
        exposure_path = _write_with_original_name(exposure_csv)

        merged = collect_experiment_metadata_from_files(
            layout_file=layout_path,
            baseline_file=baseline_path,
            exposure_file=exposure_path,
        )

        code = _first_present(
            merged,
            "experiment_id",
            "code",
            "experiment",
            "experiment_code",
            "experimentId",
            "ExperimentID",
        )
        if not code:
            code = _first_present(
                merged.get("baseline_filename_meta") or {}, "experiment_id", "code"
            )
        if not code:
            code = _first_present(
                merged.get("exposure_filename_meta") or {}, "experiment_id", "code"
            )

        if not code:
            raise ValidationError(
                {"layout_file": "Could not extract experiment_id (code) from the uploaded files."}
            )

        ingest_exists = ExperimentIngest.objects.filter(code=code).exists()
        experiment_exists = Experiment.objects.filter(code=code).exists()

        if ingest_exists or experiment_exists:
            raise ValidationError(
                {
                    "layout_file": (
                        f"Experiment with code '{code}' already exists in the dataset; "
                        "re-upload is not allowed."
                    )
                }
            )

        sex_token = (merged.get("sex") or "").lower()
        if "female" in sex_token:
            sex = Sex.FEMALE
        elif "male" in sex_token:
            sex = Sex.MALE
        else:
            sex = Sex.UNKNOWN

        div = None
        div_token = merged.get("div")
        if isinstance(div_token, str):
            m = DIV_NUM_RE.search(div_token)
            if m:
                try:
                    div = int(m.group(1))
                except ValueError:
                    div = None

        chemical = merged.get("compound") or ""
        cell_line = merged.get("type_of_cells") or ""
        experimenter = merged.get("experimenter") or ""
        plate_number = merged.get("plate_number") or ""

        date = None
        date_str = merged.get("date")
        if isinstance(date_str, str) and date_str:
            try:
                date = datetime.fromisoformat(date_str).date()
            except Exception:
                date = None

        cleaned_data.update(
            {
                "code": code,
                "sex": sex,
                "div": div,
                "chemical": chemical,
                "cell_line": cell_line,
                "experimenter": experimenter,
                "date": date,
                "plate_number": plate_number,
            }
        )

        self.instance.code = code
        self.instance.sex = sex
        self.instance.div = div
        self.instance.chemical = chemical
        self.instance.cell_line = cell_line
        self.instance.experimenter = experimenter
        self.instance.date = date
        self.instance.plate_number = plate_number

        return cleaned_data

    def clean_code(self):
        """
        Prevent accidentally clearing `code` on existing records.
        - On ADD: we allow blank (it will be set by populate_from_files / clean()).
        - On CHANGE: if the field is left empty, keep the existing value.
        """
        code = (self.cleaned_data.get("code") or "").strip() or None

        if self.instance.pk:
            if code is None and self.instance.code:
                return self.instance.code
            return code

        return code


@admin.register(ExperimentIngest)
class ExperimentIngestAdmin(admin.ModelAdmin):
    form = ExperimentIngestAdminForm
    exclude = ("layout_groups",)
    list_display = (
        "id",
        "status",
        "submission_method",
        "code",
        "project",
        "div",
        "chemical",
        "sex",
        "created_at",
    )
    list_filter = ("project", "status", "submission_method", "sex")
    search_fields = ("code", "chemical", "cell_line", "experimenter")
    readonly_fields = ("error_message", "created_at", "updated_at")

    add_fieldsets = (
        (
            "Uploads",
            {"fields": ("project", "layout_file", "baseline_csv", "exposure_csv")},
        ),
    )

    change_fieldsets = (
        ("Logs", {"fields": ("error_message",)}),
        ("Status", {"fields": ("status", "submission_method", "created_at", "updated_at")}),
        ("Uploads", {"fields": ("layout_file", "baseline_csv", "exposure_csv")}),
        (
            "Parsed metadata (editable)",
            {
                "fields": (
                    "project",
                    "code",
                    "sex",
                    "div",
                    "chemical",
                    "cell_line",
                    "experimenter",
                    "date",
                    "plate_number",
                ),
            },
        ),
        ("Layout summary (editable)", {"fields": ("layout_date", "layout_wells", "control_group")}),
    )
    actions = ["promote_to_experiment"]

    def get_fieldsets(self, request, obj=None):  # type: ignore[override]
        # obj is None → add view; obj is not None → change view
        return self.add_fieldsets if obj is None else self.change_fieldsets

    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        return (ExperimentIngestGroupInline,)

    @admin.action(description="Promote selected ingests to Experiments")
    def promote_to_experiment(self, request, queryset):
        created = 0
        skipped = 0

        for ingest in queryset:
            if ingest.status != ExperimentIngest.Status.PARSED:
                skipped += 1
                continue

            try:
                ingest.execute_ingest()
                created += 1
            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            f"{created} experiments created, {skipped} skipped.",
            level=messages.SUCCESS,
        )

    def save_model(self, request, obj, form, change):
        """
        Control when parsing happens:

        - ADD view:
            * "_continue" (Save and continue editing)  -> parse now
            * "_save" or "_addanother"                -> don't parse yet
        - CHANGE view:
            * if status == PENDING                    -> parse now on any save
            * otherwise                               -> don't re-parse
        """
        should_parse = False

        if not change:
            if "_continue" in request.POST:
                should_parse = True

            if "layout_file" in request.FILES:
                obj.original_layout_filename = cast(
                    UploadedFile, request.FILES["layout_file"]
                ).name
            if "baseline_csv" in request.FILES:
                obj.original_baseline_filename = cast(
                    UploadedFile, request.FILES["baseline_csv"]
                ).name
            if "exposure_csv" in request.FILES:
                obj.original_exposure_filename = cast(
                    UploadedFile, request.FILES["exposure_csv"]
                ).name

        else:
            if obj.status == ExperimentIngest.Status.PENDING:
                should_parse = True

        super().save_model(request, obj, form, change)

        if should_parse:
            obj.parse_files()


@admin.register(ExperimentFile)
class ExperimentFileAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("experiment", "kind", "div", "file")
    list_filter = ("kind",)
    search_fields = ("file",)
    list_select_related = ("experiment",)


@admin.register(Condition)
class ConditionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "experiment",
        "chemical",
        "formatted_concentration",
        "unit",
        "is_control",
        "well_count",
    )
    list_filter = ("is_control", "experiment__project")
    search_fields = ("name", "chemical__name", "experiment__code")
    list_select_related = ("experiment", "chemical", "unit")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-is_control", "concentration", "name")

    @admin.display(description="Concentration", ordering="concentration")
    def formatted_concentration(self, obj):
        value = obj.concentration
        if value is None:
            return "-"
        value_str = format(value.normalize(), "f")
        if "." in value_str:
            value_str = value_str.rstrip("0").rstrip(".")
        return value_str or "0"

    @admin.display(description="Wells")
    def well_count(self, obj):
        wells = getattr(obj, "wells", None)
        if not isinstance(wells, list):
            return 0
        return len(wells)


@admin.register(NeuronalMetricsFrame)
class NeuronalMetricsFrameAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("experiment", "div", "created_at")
    search_fields = ("experiment__code",)
    list_select_related = ("experiment",)

    fieldsets = (
        (None, {"fields": ("experiment", "div", "created_at", "updated_at")}),
        (
            "Metrics (baseline)",
            {
                "fields": ("metrics_baseline_table",),
                "classes": ("collapse", "wide"),
            },
        ),
        (
            "QC (baseline)",
            {
                "fields": ("qc_table",),
                "classes": ("collapse", "wide"),
            },
        ),
        (
            "Metrics (exposure)",
            {
                "fields": ("metrics_exposure_table",),
                "classes": ("collapse", "wide"),
            },
        ),
        (
            "Metrics (ratio)",
            {
                "fields": ("metrics_ratio_table",),
                "classes": ("wide",),
            },
        ),
    )

    class Media:
        css = {"all": ("ntx/admin_metrics.css",)}

    def get_readonly_fields(self, request, obj=None) -> list[str]:
        fields = super().get_readonly_fields(request, obj)
        fields.extend(
            [
                "metrics_baseline_table",
                "metrics_exposure_table",
                "metrics_ratio_table",
                "qc_table",
            ]
        )
        return fields

    @admin.display(description="Metrics ratio")
    def metrics_ratio_table(self, obj: NeuronalMetricsFrame) -> SafeString:
        payload = MetricsPayload.model_construct(**cast(dict[str, Any], obj.metrics_json))
        return _render_metrics_payload_table(payload, section="ratio")

    @admin.display(description="Metrics baseline")
    def metrics_baseline_table(self, obj: NeuronalMetricsFrame) -> SafeString:
        payload = MetricsPayload.model_construct(**cast(dict[str, Any], obj.metrics_json))
        return _render_metrics_payload_table(payload, section="baseline")

    @admin.display(description="Metrics exposure")
    def metrics_exposure_table(self, obj: NeuronalMetricsFrame) -> SafeString:
        payload = MetricsPayload.model_construct(**cast(dict[str, Any], obj.metrics_json))
        return _render_metrics_payload_table(payload, section="exposure")

    @admin.display(description="QC baseline")
    def qc_table(self, obj: NeuronalMetricsFrame) -> SafeString:
        return _render_qc_json_table(obj.qc_json)
