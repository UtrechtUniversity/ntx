from __future__ import annotations

import copy
import io
import json
import zipfile
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.db.models import Count
from django.forms.models import BaseInlineFormSet, model_to_dict
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

from .metrics_metadata import METRIC_SECTIONS
from .metrics_schema import MetricsPayload
from .models import (
    AnyUser,
    Chemical,
    ConcentrationUnit,
    Condition,
    Experiment,
    ExperimentFile,
    ExperimentIngest,
    ExperimentIngestGroup,
    NeuronalMetricsFrame,
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectScopedQuerySet,
)
from .permissions import NOBODY, PROJECT_RULES, Action, clear_role_cache, user_can
from .utils import normalize_decimals

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec

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


class ProjectListFilter(admin.SimpleListFilter):
    """
    Filter by project, listing only the projects the user can view.
    """

    title = "project"
    parameter_name = "project"

    def __init__(self, request, params, model, model_admin) -> None:
        self._project_path: str = getattr(model, "project_path")
        super().__init__(request, params, model, model_admin)

    def lookups(self, request, model_admin) -> list[tuple[str, str]]:
        projects = Project.objects.visible_to(request.user).order_by("name")
        return [(str(project.pk), project.name) for project in projects]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if not value.isdigit():
            raise IncorrectLookupParameters(f"Invalid project id {value!r}")
        lookup = f"{self._project_path}__pk" if self._project_path else "pk"
        return queryset.filter(**{lookup: int(value)})


class MyProjectsListFilter(admin.SimpleListFilter):
    """
    Limit the list to projects where the user is a collaborator.
    """

    title = "collaboration"
    parameter_name = "mine"

    def lookups(self, request, model_admin) -> list[tuple[str, str]]:
        return [("1", "My projects")]

    def queryset(self, request, queryset):
        if self.value() != "1":
            return queryset
        return cast(ProjectScopedQuerySet, queryset).with_collaborator(request.user)


class ProjectScopedAdmin(admin.ModelAdmin):
    """
    Admin for models that belong to a project. Every logged-in user can view all of them;
    other permissions come from the user's role in the object's project (see ntx.permissions).
    """

    # Models deleted together with this admin's objects, without needing their own delete
    # permission (they are read-only in their own admins).
    cascade_delete_models: tuple[type[models.Model], ...] = ()

    def get_queryset(self, request) -> models.QuerySet:
        queryset = super().get_queryset(request)
        return cast(ProjectScopedQuerySet, queryset).visible_to(request.user)

    def has_view_permission(self, request, obj=None) -> bool:
        return user_can(request.user, Action.VIEW, self.model, obj)

    def has_add_permission(self, request) -> bool:
        return user_can(request.user, Action.ADD, self.model)

    def has_change_permission(self, request, obj=None) -> bool:
        return user_can(request.user, Action.CHANGE, self.model, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        return user_can(request.user, Action.DELETE, self.model, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "project":
            # Only offer projects where the user may add this kind of object.
            minimum = PROJECT_RULES[self.model].get(Action.ADD, NOBODY).minimum_role()
            kwargs["queryset"] = (
                Project.objects.with_role(request.user, minimum)
                if minimum is not None
                else Project.objects.none()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change, **kwargs)
        if "project" not in form_class.base_fields:
            return form_class
        return _with_project_permission_check(form_class, request.user, self.model)

    def get_deleted_objects(self, objs, request):
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        implied = {str(model._meta.verbose_name) for model in self.cascade_delete_models}
        perms_needed = {name for name in perms_needed if str(name) not in implied}
        return to_delete, model_count, perms_needed, protected


def _with_project_permission_check(
    form_class: type[forms.ModelForm], user: AnyUser, model: type[models.Model]
) -> type[forms.ModelForm]:
    """
    Reject saving the object into a project where the user may not add it (new objects) or
    change it (existing objects, e.g. when moving another user's ingest).
    """

    def clean(form: forms.ModelForm) -> dict[str, Any]:
        form_class.clean(form)
        cleaned_data = form.cleaned_data
        project = cleaned_data.get("project")
        if project is not None:
            candidate = copy.copy(form.instance)
            setattr(candidate, "project", project)
            action = Action.CHANGE if form.instance.pk else Action.ADD
            if not user_can(user, action, model, candidate):
                form.add_error(
                    "project",
                    f"You do not have permission to {action} "
                    f"{model._meta.verbose_name_plural} in this project.",
                )
        return cleaned_data

    return type(form_class.__name__, (form_class,), {"clean": clean})


class ReadOnlyAdminMixin:
    """
    Disable editing in the admin for ingestion-backed models.
    """

    actions = None

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None) -> list[str]:
        model_cls = cast(type[models.Model] | None, getattr(self, "model", None))
        if model_cls is None:
            return []
        fields = [field.name for field in model_cls._meta.fields]
        fields.extend(field.name for field in model_cls._meta.many_to_many)
        return fields


class ProjectMembershipFormSet(BaseInlineFormSet):
    def clean(self) -> None:
        super().clean()
        if self.instance.pk is None:
            # A new project's creator becomes its manager when saved.
            return
        has_manager = any(
            form.cleaned_data.get("role") == ProjectRole.MANAGER
            and not form.cleaned_data.get("DELETE")
            for form in self.forms
            if getattr(form, "cleaned_data", None)
        )
        if not has_manager:
            raise ValidationError("A project needs at least one manager.")


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    formset = ProjectMembershipFormSet
    extra = 0
    fields = ("user", "role")
    verbose_name = "collaborator"
    verbose_name_plural = "collaborators"

    def _can_manage(self, request, obj: Project | None) -> bool:
        if obj is None:
            return user_can(request.user, Action.ADD, Project)
        return user_can(request.user, Action.CHANGE, Project, obj)

    def has_view_permission(self, request, obj=None) -> bool:
        return user_can(request.user, Action.VIEW, Project, obj)

    def has_add_permission(self, request, obj) -> bool:
        return self._can_manage(request, obj)

    def has_change_permission(self, request, obj=None) -> bool:
        return self._can_manage(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        return self._can_manage(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = (
                get_user_model().objects.filter(is_active=True).order_by("username")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Project)
class ProjectAdmin(ProjectScopedAdmin):
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
    list_filter = (MyProjectsListFilter, "outlier_method")
    readonly_fields = ("created_by", "created_at", "updated_at")
    inlines = (ProjectMembershipInline,)
    cascade_delete_models = (Condition, ExperimentFile, NeuronalMetricsFrame)
    # No bulk delete: deleting goes through the delete view, which lists affected experiments.
    actions = None

    def get_queryset(self, request) -> models.QuerySet[Project]:
        qs = super().get_queryset(request)
        return qs.annotate(
            _experiments_count=Count("experiments", distinct=True),
            _collaborators_count=Count("collaborators", distinct=True),
        )

    def save_model(self, request, obj: Project, form, change: bool) -> None:
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change: bool) -> None:
        super().save_related(request, form, formsets, change)
        project = form.instance
        if not change and project.created_by_id is not None:
            # Saved after the inline so the creator always ends up as manager.
            ProjectMembership.objects.update_or_create(
                project=project,
                user_id=project.created_by_id,
                defaults={"role": ProjectRole.MANAGER},
            )
        clear_role_cache(request.user)

    def delete_view(self, request, object_id, extra_context=None):
        extra_context = dict(extra_context or {})
        project = self.get_object(request, unquote(object_id))
        if project is not None:
            extra_context["experiments_to_delete"] = project.experiments.select_related(
                "created_by"
            ).order_by("code")
            extra_context["ingests_to_delete_count"] = project.experiment_ingests.count()
        return super().delete_view(request, object_id, extra_context)

    @admin.display(description="Experiments")
    def experiments_count(self, obj) -> int:
        return getattr(obj, "_experiments_count", 0)

    @admin.display(description="Collaborators")
    def collaborators_count(self, obj) -> int:
        return getattr(obj, "_collaborators_count", 0)


@admin.register(Chemical)
class ChemicalAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "canonical", "experiments_count", "conditions_count")
    search_fields = ("name", "slug", "description")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request) -> models.QuerySet[Chemical]:
        qs = super().get_queryset(request)
        return qs.annotate(
            _conditions_count=Count("conditions", distinct=True),
            _experiments_count=Count("conditions__experiment", distinct=True),
        )

    @admin.display(description="Conditions")
    def conditions_count(self, obj) -> int:
        return getattr(obj, "_conditions_count", 0)

    @admin.display(description="Experiments")
    def experiments_count(self, obj) -> int:
        return getattr(obj, "_experiments_count", 0)


@admin.register(ConcentrationUnit)
class ConcentrationUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "slug", "canonical", "conditions_count")
    search_fields = ("name", "slug", "symbol")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request) -> models.QuerySet[ConcentrationUnit]:
        qs = super().get_queryset(request)
        return qs.annotate(
            _conditions_count=Count("conditions", distinct=True),
        )

    @admin.display(description="Conditions")
    def conditions_count(self, obj) -> int:
        return getattr(obj, "_conditions_count", 0)


@admin.register(Experiment)
class ExperimentAdmin(ReadOnlyAdminMixin, ProjectScopedAdmin):
    list_display = (
        "code",
        "project",
        "status",
        "sex",
        "date",
        "condition_count",
        "well_count",
        "parsed_at",
        "created_by",
    )
    list_filter = (MyProjectsListFilter, "status", "sex", ProjectListFilter)
    search_fields = ("code", "researcher", "cell_line", "manufacturer")
    list_select_related = ("project", "created_by")
    # Experiments are read-only, but can be deleted (individually or in bulk).
    actions = ()
    cascade_delete_models = (Condition, ExperimentFile, NeuronalMetricsFrame)

    def has_delete_permission(self, request, obj=None) -> bool:
        return ProjectScopedAdmin.has_delete_permission(self, request, obj)


class ExperimentIngestGroupInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs) -> None:
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

    # Staged groups are part of their ingest: editing them requires changing the ingest.
    def _can_change_ingest(self, request, obj: ExperimentIngest | None) -> bool:
        return obj is not None and user_can(request.user, Action.CHANGE, ExperimentIngest, obj)

    def has_view_permission(self, request, obj=None) -> bool:
        return obj is not None and user_can(request.user, Action.VIEW, ExperimentIngest, obj)

    def has_add_permission(self, request, obj) -> bool:
        return self._can_change_ingest(request, obj)

    def has_change_permission(self, request, obj=None) -> bool:
        return self._can_change_ingest(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        return self._can_change_ingest(request, obj)


@admin.register(ExperimentIngest)
class ExperimentIngestAdmin(ProjectScopedAdmin):
    list_display = (
        "id",
        "status",
        "has_error",
        "submission_method",
        "code",
        "project",
        "div",
        "chemical",
        "sex",
        "created_by",
        "created_at",
    )
    list_filter = (MyProjectsListFilter, ProjectListFilter, "status", "submission_method", "sex")
    list_select_related = ("project", "created_by")
    search_fields = ("code", "chemical", "cell_line", "experimenter")
    readonly_fields = ("status", "error_message", "created_by", "created_at", "updated_at")

    class Media:
        css = {"all": ("ntx/admin_ingest.css",)}

    add_fieldsets: _FieldsetSpec = (
        (
            "Uploads",
            {"fields": ("project", "layout_file", "baseline_csv", "exposure_csv")},
        ),
    )

    change_fieldsets: _FieldsetSpec = (
        ("Logs", {"fields": ("error_message",)}),
        (
            "Status",
            {
                "fields": (
                    "status",
                    "submission_method",
                    "created_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
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

    def get_fieldsets(self, request, obj=None) -> _FieldsetSpec:
        # obj is None → add view; obj is not None → change view
        return self.add_fieldsets if obj is None else self.change_fieldsets

    def get_inlines(self, request, obj=None) -> tuple[type[ExperimentIngestGroupInline], ...]:
        if obj is None:
            return ()
        return (ExperimentIngestGroupInline,)

    @admin.display(description="Error", empty_value="")
    def has_error(self, obj: ExperimentIngest) -> str | None:
        return "Error" if obj.error_message else None

    def _allowed_ingests(
        self, request, queryset: Iterable[ExperimentIngest], action: Action
    ) -> list[ExperimentIngest]:
        """
        Keep the ingests the user may perform `action` on; report how many were left out.
        """
        ingests = list(queryset)
        allowed = [
            ingest for ingest in ingests if user_can(request.user, action, ExperimentIngest, ingest)
        ]
        denied = len(ingests) - len(allowed)
        if denied:
            self.message_user(
                request,
                f"{denied} selected uploads skipped: you do not have permission to {action} them.",
                level=messages.WARNING,
            )
        return allowed

    @admin.action(description="Parse/reparse selected uploads", permissions=["change"])
    def parse_selected_uploads(self, request, queryset) -> None:
        parsed = 0
        failed = 0

        for ingest in self._allowed_ingests(request, queryset, Action.CHANGE):
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

    @admin.action(description="Promote selected ingests to Experiments", permissions=["change"])
    def promote_to_experiment(self, request, queryset) -> None:
        self._promote_to_experiment(request, queryset, replace_existing=False)

    @admin.action(
        description="Promote selected ingests, replacing existing Experiments",
        permissions=["change"],
    )
    def promote_to_experiment_replacing_existing(self, request, queryset) -> None:
        self._promote_to_experiment(request, queryset, replace_existing=True)

    def _promote_to_experiment(self, request, queryset, *, replace_existing: bool) -> None:
        created = 0
        skipped = 0
        failed = 0

        for ingest in self._allowed_ingests(request, queryset, Action.CHANGE):
            if not ingest.can_promote(replace_existing=replace_existing):
                skipped += 1
                continue

            try:
                ingest.execute_ingest(replace_existing=replace_existing, acting_user=request.user)
                created += 1
            except ValidationError:
                failed += 1

        level = messages.SUCCESS if failed == 0 else messages.WARNING
        self.message_user(
            request,
            f"{created} experiments created, {failed} failed, {skipped} skipped.",
            level=level,
        )

    @admin.action(
        description="Download ingest records as JSON for selected ingests", permissions=["view"]
    )
    def download_parsed_metadata(self, request, queryset) -> HttpResponse | None:
        """Create a zip containing the ExperimentIngest row data (all table fields) as JSON.

        Every selected ingest must have a code. Each produces `<code>_ingest.json`.
        """
        ingests = self._allowed_ingests(request, queryset, Action.VIEW)
        missing_code_ids = sorted(ingest.pk for ingest in ingests if not ingest.code)
        if missing_code_ids:
            formatted_ids = ", ".join(str(pk) for pk in missing_code_ids)
            self.message_user(
                request,
                f"Cannot export selected ingests. Missing code for ingest IDs: {formatted_ids}.",
                level=messages.ERROR,
            )
            return None

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            added = 0
            for ingest in ingests:
                try:
                    data = model_to_dict(ingest)
                    filename = f"{ingest.code}_ingest.json"
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

    def save_model(self, request, obj, form, change) -> None:
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
        if not change:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        if should_parse:
            obj.parse_files()

    def save_related(self, request, form, formsets, change) -> None:
        super().save_related(request, form, formsets, change)

        if not change:
            return

        ingest = form.instance
        metadata_fields = {
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
            "layout_date",
            "layout_wells",
        }

        metadata_changed = bool(metadata_fields.intersection(form.changed_data))
        groups_changed = any(
            formset.has_changed() for formset in formsets if formset.model is ExperimentIngestGroup
        )

        if ingest.status == ExperimentIngest.Status.INGESTED and (
            metadata_changed or groups_changed
        ):
            ingest.status = ExperimentIngest.Status.EDITED
            ingest.save(update_fields=["status", "updated_at"])


@admin.register(ExperimentFile)
class ExperimentFileAdmin(ReadOnlyAdminMixin, ProjectScopedAdmin):
    list_display = ("experiment", "kind", "div", "file")
    list_filter = ("kind", ProjectListFilter)
    search_fields = ("file",)
    list_select_related = ("experiment",)


@admin.register(Condition)
class ConditionAdmin(ReadOnlyAdminMixin, ProjectScopedAdmin):
    list_display = (
        "name",
        "experiment",
        "chemical",
        "formatted_concentration",
        "unit",
        "is_control",
        "well_count",
    )
    list_filter = ("is_control", ProjectListFilter)
    search_fields = ("name", "chemical__name", "experiment__code")
    list_select_related = ("experiment", "chemical", "unit")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-is_control", "concentration", "name")

    @admin.display(description="Concentration", ordering="concentration")
    def formatted_concentration(self, obj) -> str:
        value = obj.concentration
        if value is None:
            return "-"
        return normalize_decimals(value)

    @admin.display(description="Wells")
    def well_count(self, obj) -> int:
        wells = getattr(obj, "wells", None)
        if not isinstance(wells, list):
            return 0
        return len(wells)


@admin.register(NeuronalMetricsFrame)
class NeuronalMetricsFrameAdmin(ReadOnlyAdminMixin, ProjectScopedAdmin):
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
