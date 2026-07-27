from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.db.models import Count
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe
from django.http import HttpResponse
from django.forms.models import model_to_dict
import io
import zipfile
import json

from .metrics_metadata import METRIC_SECTIONS
from .metrics_schema import MetricsPayload
from .models import (
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentIngest,
    ExperimentIngestGroup,
    NeuronalMetricsFrame,
    Project,
)
from .utils import normalize_decimals

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


class ExperimentIngestGroupInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        value = self.instance.concentration
        if value is not None and not self.is_bound:
            self.initial["concentration"] = normalize_decimals(value)

    class Meta:
        model = ExperimentIngestGroup
        fields = ("chemical", "concentration", "unit", "is_control", "wells")
        widgets = {
            "concentration": forms.TextInput(attrs={"class": "ntx-ingest-concentration-input"}),
            "wells": forms.TextInput(attrs={"class": "ntx-ingest-wells-input"}),
        }


class ExperimentIngestGroupInline(admin.TabularInline):
    model = ExperimentIngestGroup
    form = ExperimentIngestGroupInlineForm
    extra = 0
    fields = ("chemical", "concentration", "unit", "is_control", "wells")
    autocomplete_fields = ("unit",)
    ordering = ("id",)

    class Media:
        css = {"all": ("ntx/admin_ingest.css",)}


@admin.register(ExperimentIngest)
class ExperimentIngestAdmin(admin.ModelAdmin):
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
    readonly_fields = ("status", "error_message", "created_at", "updated_at")

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
                    "exposure_type",
                ),
            },
        ),
        ("Layout summary (editable)", {"fields": ("layout_date", "layout_wells")}),
    )
    actions = [
        "parse_selected_uploads",
        "promote_to_experiment",
        "promote_to_experiment_replacing_existing",
        "download_parsed_metadata",
    ]

    def get_fieldsets(self, request, obj=None):  # type: ignore[override]
        # obj is None → add view; obj is not None → change view
        return self.add_fieldsets if obj is None else self.change_fieldsets

    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        return (ExperimentIngestGroupInline,)

    @admin.action(description="Parse/reparse selected uploads")
    def parse_selected_uploads(self, request, queryset):
        parsed = 0
        failed = 0

        for ingest in queryset:
            try:
                ingest.parse_files()
                parsed += 1
            except Exception:
                failed += 1

        self.message_user(
            request,
            f"{parsed} uploads parsed, {failed} failed.",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )

    @admin.action(description="Promote selected ingests to Experiments")
    def promote_to_experiment(self, request, queryset):
        self._promote_to_experiment(request, queryset, replace_existing=False)

    @admin.action(description="Promote selected ingests, replacing existing Experiments")
    def promote_to_experiment_replacing_existing(self, request, queryset):
        self._promote_to_experiment(request, queryset, replace_existing=True)

    def _promote_to_experiment(self, request, queryset, *, replace_existing: bool):
        created = 0
        skipped = 0
        failed = 0

        for ingest in queryset:
            if ingest.status != ExperimentIngest.Status.PARSED:
                skipped += 1
                continue

            try:
                ingest.execute_ingest(replace_existing=replace_existing)
                created += 1
            except ValidationError:
                failed += 1

        level = messages.SUCCESS if failed == 0 else messages.WARNING
        self.message_user(
            request,
            f"{created} experiments created, {failed} failed, {skipped} skipped.",
            level=level,
        )

    @admin.action(description="Download ingest records as JSON for selected ingests")
    def download_parsed_metadata(self, request, queryset):
        """Create a zip containing the ExperimentIngest row data (all table fields) as JSON.

        Each selected ingest produces a file named `<code_or_pk>_ingest.json`.
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            added = 0
            for ingest in queryset:
                try:
                    data = model_to_dict(ingest)
                    filename = f"{ingest.code or ingest.pk}_ingest.json"
                    zf.writestr(filename, json.dumps(data, indent=2, default=str))
                    added += 1
                except Exception:
                    continue

        if added == 0:
            self.message_user(
                request,
                "No ingest records exported for selected ingests.",
                level=messages.WARNING,
            )
            return None

        buffer.seek(0)
        resp = HttpResponse(buffer.getvalue(), content_type="application/zip")
        resp["Content-Disposition"] = "attachment; filename=ingest_records.zip"
        return resp

    def save_model(self, request, obj, form, change):
        """
        Control when parsing happens:

        - ADD view: save uploaded files, then parse once.
        - CHANGE view: reparse only when one of the uploaded files changes.
        """
        upload_fields = {"layout_file", "baseline_csv", "exposure_csv"}
        should_parse = not change or any(field in request.FILES for field in upload_fields)

        if "baseline_csv" in request.FILES:
            obj.original_baseline_filename = cast(UploadedFile, request.FILES["baseline_csv"]).name
        if "exposure_csv" in request.FILES:
            obj.original_exposure_filename = cast(UploadedFile, request.FILES["exposure_csv"]).name

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
        return normalize_decimals(value)

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
