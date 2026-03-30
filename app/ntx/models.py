from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import ErrorDetails

from ntx.ingest.metadata import collect_experiment_metadata_from_files

from .metrics_schema import MetricsPayload, MetricsQcPayload
from .utils import sanitize_numeric_json

User = get_user_model()

WELL_RE = re.compile(r"^[A-Za-z](\d+)$")
DIV_NUM_RE = re.compile(r"DIV\s*(\d+)", re.IGNORECASE)


def _normalize_well(well: str) -> str:
    match = WELL_RE.match(well)
    if not match:
        raise ValueError(f"Invalid well label '{well}'")
    row = well[0].upper()
    col = int(match.group(1))
    if col <= 0:
        raise ValueError(f"Well number must be positive in '{well}'")
    return f"{row}{col}"


def _sort_wells(wells: list[str]) -> list[str]:
    def _well_key(well: str) -> tuple[str, int]:
        row = well[0].upper()
        try:
            col = int(well[1:])
        except ValueError:
            col = 0
        return (row, col)

    return sorted(set(wells), key=_well_key)


class TimeStampedModel(models.Model):
    if TYPE_CHECKING:
        id: int

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """
        Validate models on save.
        """
        self.full_clean()
        super().save(*args, **kwargs)


def _generate_unique_slug(
    instance: models.Model, value: str, *, slug_field_name: str = "slug"
) -> str:
    """
    Generate a unique slug for the given instance.

    Suffixes are appended in case of a collision.
    """
    base_slug = slugify(value) or "item"
    slug_value = base_slug
    suffix = 2
    ModelClass = instance.__class__
    while (
        ModelClass._default_manager.filter(**{slug_field_name: slug_value})
        .exclude(pk=instance.pk)
        .exists()
    ):
        slug_value = f"{base_slug}-{suffix}"
        suffix += 1
    return slug_value


class OutlierMethod(models.TextChoices):
    BOXPLOT = "BOXPLOT", "Boxplot"
    ZSCORE = "ZSCORE", "Z-Score"


class Project(TimeStampedModel):
    if TYPE_CHECKING:
        experiments: models.Manager["Experiment"]

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="projects_created",
        null=True,
        blank=True,
    )
    outlier_method = models.CharField(
        max_length=20,
        choices=OutlierMethod.choices,
        default=OutlierMethod.BOXPLOT,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    collaborators = models.ManyToManyField(
        User,
        related_name="collaborating_projects",
        blank=True,
    )

    class Meta(TimeStampedModel.Meta):
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = _generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)


class CanonicalMixin(models.Model):
    canonical = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="aliases",
        null=True,
        blank=True,
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        self._validate_canonical_root()
        self._validate_canonical_chain()

    def _validate_canonical_root(self):
        canonical = getattr(self, "canonical", None)
        if canonical is None:
            return
        if getattr(canonical, "canonical_id", None) is not None:
            raise ValidationError(
                {"canonical": "Canonical must reference a canonical record (canonical is None)."}
            )

    def _validate_canonical_chain(self):
        current: "CanonicalMixin | None" = getattr(self, "canonical", None)
        if current is None:
            return

        seen: set[int] = set()
        while current is not None:
            if current is self:
                raise ValidationError("Canonical reference creates a cycle.")
            if current.pk:
                if current.pk in seen:
                    raise ValidationError("Canonical reference creates a cycle.")
                seen.add(current.pk)
            current = getattr(current, "canonical", None)

    def canonical_or_self(self):
        return self.canonical or self


class Chemical(CanonicalMixin, TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta(CanonicalMixin.Meta, TimeStampedModel.Meta):
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(canonical__isnull=True) | ~Q(canonical=models.F("id")),
                name="chemical_canonical_not_self",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = _generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)


class ConcentrationUnit(CanonicalMixin, TimeStampedModel):
    name = models.CharField(max_length=64)
    slug = models.SlugField(unique=True)
    symbol = models.CharField(max_length=16, blank=True)

    class Meta(CanonicalMixin.Meta, TimeStampedModel.Meta):
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(canonical__isnull=True) | ~Q(canonical=models.F("id")),
                name="concentration_unit_canonical_not_self",
            )
        ]

    def __str__(self) -> str:
        return self.symbol or self.name

    def save(self, *args, **kwargs):
        if not self.slug and (self.symbol or self.name):
            self.slug = _generate_unique_slug(self, self.symbol or self.name)
        super().save(*args, **kwargs)


class ExperimentStatus(models.TextChoices):
    CREATED = "CREATED", "Created"
    INGESTED = "INGESTED", "Ingested"
    FAILED = "FAILED", "Failed"


class Sex(models.TextChoices):
    UNKNOWN = "U", "Unknown"
    FEMALE = "F", "Female"
    MALE = "M", "Male"
    MIXED = "X", "Mixed"


class Experiment(TimeStampedModel):
    if TYPE_CHECKING:
        conditions: models.Manager["Condition"]
        files: models.Manager["ExperimentFile"]
        neuronal_metrics_frames: models.Manager["NeuronalMetricsFrame"]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="experiments")
    code = models.CharField(max_length=128, blank=True, null=True, unique=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    researcher = models.CharField(max_length=255, blank=True)
    date = models.DateField(null=True, blank=True)
    cell_line = models.CharField(max_length=128, blank=True)
    type = models.CharField(max_length=64, blank=True)
    manufacturer = models.CharField(max_length=64, blank=True)
    default_concentration_unit = models.ForeignKey(
        ConcentrationUnit,
        on_delete=models.PROTECT,
        related_name="experiments",
        null=True,
        blank=True,
    )
    active_electrode_criterion = models.FloatField(default=6.0)
    network_burst_threshold = models.IntegerField(default=20)
    burst_frequency_threshold = models.FloatField(default=0.3)
    parsed_at = models.DateTimeField(null=True, blank=True)
    excluded_wells = models.JSONField(default=list, blank=True)
    well_count = models.PositiveIntegerField(default=0)
    condition_count = models.PositiveIntegerField(default=0)
    knockout_stats = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=ExperimentStatus.choices, default=ExperimentStatus.CREATED
    )
    yoda_exported_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-date", "code"]
        indexes = [models.Index(fields=["project", "date"], name="experiment_project_date_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"], name="experiment_project_code_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.project}: {self.code}"


class ExperimentFile(TimeStampedModel):
    class FileKind(models.TextChoices):
        AXION_BASELINE = "AXION_BASELINE", "Axion Baseline"
        AXION_EXPOSURE = "AXION_EXPOSURE", "Axion Exposure"
        LAYOUT = "LAYOUT", "Plate Layout"
        OTHER = "OTHER", "Other"

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(max_length=500, upload_to="axion/")
    kind = models.CharField(max_length=32, choices=FileKind.choices, default=FileKind.OTHER)
    div = models.PositiveIntegerField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["experiment_id", "id"]

    def __str__(self) -> str:
        return self.file.name


class Condition(TimeStampedModel):
    if TYPE_CHECKING:
        experiment_id: int

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="conditions")
    name = models.CharField(max_length=255)
    chemical = models.ForeignKey(Chemical, on_delete=models.PROTECT, related_name="conditions")
    concentration = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    unit = models.ForeignKey(
        ConcentrationUnit,
        on_delete=models.PROTECT,
        related_name="conditions",
        null=True,
        blank=True,
    )
    is_control = models.BooleanField(default=False)
    wells = models.JSONField(default=list, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "name", "concentration", "unit"],
                name="condition_unique_per_experiment",
                nulls_distinct=False,
            )
        ]
        indexes = [models.Index(fields=["experiment", "is_control"], name="cond_exp_ctrl_idx")]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()

        duplicate_qs = Condition.objects.filter(
            experiment=self.experiment,
            name=self.name,
            concentration=self.concentration,
            unit=self.unit,
        )
        if self.pk:
            duplicate_qs = duplicate_qs.exclude(pk=self.pk)
        if duplicate_qs.exists():
            raise ValidationError(
                "Condition with this experiment, name, concentration, and unit already exists."
            )

        if not isinstance(self.wells, list):
            raise ValidationError({"wells": "wells must be a list of well labels."})

        normalized: list[str] = []
        for entry in self.wells:
            if not isinstance(entry, str):
                raise ValidationError({"wells": "wells must contain only strings."})
            token = entry.strip()
            if not token:
                raise ValidationError({"wells": "wells cannot contain empty strings."})
            try:
                normalized.append(_normalize_well(token))
            except ValueError as exc:
                raise ValidationError({"wells": str(exc)}) from exc

        if not normalized:
            raise ValidationError({"wells": "Condition must contain at least one well."})

        normalized = _sort_wells(normalized)
        self.wells = normalized

        if not self.experiment_id:
            return

        new_wells = set(normalized)
        other_conditions = Condition.objects.filter(experiment_id=self.experiment_id)
        if self.pk:
            other_conditions = other_conditions.exclude(pk=self.pk)

        for other in other_conditions.only("id", "name", "wells"):
            other_wells = other.wells
            if not isinstance(other_wells, list):
                continue
            overlap = new_wells.intersection(other_wells)
            if overlap:
                raise ValidationError(
                    {
                        "wells": (
                            "Wells must be unique within an experiment. "
                            f"Overlap with '{other.name}': {sorted(overlap)}"
                        )
                    }
                )


class NeuronalMetricsFrame(TimeStampedModel):
    if TYPE_CHECKING:
        experiment_id: int

    experiment = models.ForeignKey(
        Experiment, on_delete=models.CASCADE, related_name="neuronal_metrics_frames"
    )
    div = models.PositiveIntegerField()
    metrics_json = models.JSONField()
    qc_json = models.JSONField(default=dict, blank=True)

    class Meta(TimeStampedModel.Meta):
        verbose_name = "Neuronal metrics frame"
        verbose_name_plural = "Neuronal metrics frames"
        ordering = ["experiment_id", "div"]
        indexes = [models.Index(fields=["experiment", "div"], name="neur_metrics_exp_div_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "div"], name="neur_metrics_frame_exp_div_unique"
            )
        ]

    def __str__(self) -> str:
        return f"Metrics for {self.experiment.code} (DIV {self.div})"

    def clean(self):
        super().clean()
        # Ensure metrics_json and qc_json match their Pydantic contracts before saving.
        # JSON/JSONB cannot represent NaN/Inf; store missing/unusable as null.
        # Knockouts are represented separately (ratio == -1) and handled in analysis.
        # In ingestion, detect/log non-finite values *before* sanitizing for easier debugging.

        errors: dict[str, list[ErrorDetails]] = {}

        sanitized_metrics = sanitize_numeric_json(self.metrics_json)
        self.metrics_json = sanitized_metrics
        try:
            metrics_payload = MetricsPayload.model_validate(sanitized_metrics)
        except PydanticValidationError as exc:
            metrics_payload = None
            errors["metrics_json"] = exc.errors(include_url=False)

        sanitized_qc = sanitize_numeric_json(self.qc_json)
        self.qc_json = sanitized_qc
        try:
            qc_payload = MetricsQcPayload.model_validate(sanitized_qc)
        except PydanticValidationError as exc:
            qc_payload = None
            errors["qc_json"] = exc.errors(include_url=False)

        if errors:
            raise ValidationError(errors)

        if metrics_payload is None or qc_payload is None:
            raise ValidationError("Unexpected validation state for metrics_json/qc_json")

        if metrics_payload.wells != qc_payload.wells:
            raise ValidationError(
                {"qc_json": "qc_json wells must match metrics_json wells (order matters)."}
            )


def _first_present(d: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


class ExperimentIngest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARSED = "PARSED", "Parsed"
        INGESTED = "INGESTED", "Ingested"
        ERROR = "ERROR", "Error"

    class SubmissionMethod(models.TextChoices):
        UPLOAD = "UPLOAD", "Upload"
        DISCOVERED = "DISCOVERED", "Discovered"

    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="experiment_ingests",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    submission_method = models.CharField(
        max_length=16, choices=SubmissionMethod.choices, default=SubmissionMethod.UPLOAD
    )
    error_message = models.TextField(blank=True, default="")

    layout_file = models.FileField(upload_to="ingest/layouts/", max_length=500)
    baseline_csv = models.FileField(upload_to="ingest/baselines/", max_length=500)
    exposure_csv = models.FileField(upload_to="ingest/exposures/", max_length=500)

    code = models.CharField(max_length=128, blank=True, null=True, unique=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    div = models.PositiveIntegerField(null=True, blank=True)
    chemical = models.CharField(max_length=255, blank=True, default="")
    cell_line = models.CharField(max_length=128, blank=True, default="")
    experimenter = models.CharField(max_length=255, blank=True, default="")
    date = models.DateField(null=True, blank=True)
    plate_number = models.CharField(max_length=64, blank=True, default="")

    layout_date = models.DateField(null=True, blank=True)
    layout_wells = models.PositiveIntegerField(null=True, blank=True)
    control_group = models.CharField(max_length=255, blank=True, default="")

    layout_groups = models.JSONField(null=True, blank=True, default=list)

    layout_input = models.JSONField(null=True, blank=True)
    parsed_meta = models.JSONField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at", "id"]

    def __str__(self) -> str:
        return f"ExperimentIngest #{self.pk or 'new'} ({self.status})"
    
    def _parse_decimal(self, value):
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def sync_groups_from_layout(self) -> None:
        groups = self.layout_groups or []
        if not isinstance(groups, list):
            return

        self.ingest_groups.all().delete()

        for g in groups:
            if not isinstance(g, dict):
                continue

            ExperimentIngestGroup.objects.create(
                ingest=self,
                chemical=(g.get("chemical") or g.get("compound") or ""),
                concentration=self._parse_decimal(
                    g.get("concentration") if "concentration" in g else g.get("dosage")
                ),
                unit=(g.get("unit") or ""),
                is_control=bool(g.get("is_control", False)),
                wells=(g.get("wells") or ""),
            )

    def parse_files(self) -> None:
        if not (self.layout_file and self.baseline_csv and self.exposure_csv):
            raise ValidationError({"layout_file": "All three files must be uploaded."})

        with transaction.atomic():
            try:
                self.populate_from_files()
                self.status = self.Status.PARSED
                self.error_message = ""
            # except Exception as e:
            #     self.status = self.Status.ERROR
            #     self.error_message = str(e)

                self.save(
                    update_fields=[
                        "status",
                        "error_message",
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
                        "updated_at",
                    ]
                )
                self.sync_groups_from_layout()

            except Exception as e:
                self.status = self.Status.ERROR
                self.error_message = str(e)
                self.save(update_fields=["status", "error_message", "updated_at"])
                raise

    def populate_from_files(self) -> None:
        """
        Parse layout + filenames and populate staging fields.
        Uses actual stored file paths (so original filenames are preserved).
        """
        if not (self.layout_file and self.baseline_csv and self.exposure_csv):
            raise ValidationError({"layout_file": "All three files must be uploaded."})

        merged = collect_experiment_metadata_from_files(
            layout_file=self.layout_file.path,
            baseline_file=self.baseline_csv.path,
            exposure_file=self.exposure_csv.path,
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
            raise ValidationError(
                {"layout_file": "Could not extract experiment_id (code) from the uploaded files."}
            )

        self.code = code

        sex_token = (merged.get("sex") or "").lower()
        if "female" in sex_token:
            self.sex = Sex.FEMALE
        elif "male" in sex_token:
            self.sex = Sex.MALE
        else:
            self.sex = Sex.UNKNOWN

        div_token = merged.get("div")
        if isinstance(div_token, str):
            m = DIV_NUM_RE.search(div_token)
            self.div = int(m.group(1)) if m else None
        else:
            self.div = None

        self.chemical = merged.get("compound") or ""
        self.cell_line = merged.get("type_of_cells") or ""
        self.experimenter = merged.get("experimenter") or ""
        self.plate_number = merged.get("plate_number") or ""

        date_str = merged.get("date")
        if isinstance(date_str, str) and date_str:
            try:
                self.date = datetime.fromisoformat(date_str).date()

            except Exception:
                self.date = None
        else:
            self.date = None

        layout_meta: dict[str, Any] = merged.get("layout_meta") or {}
        self.layout_input = layout_meta

        layout_date_str = layout_meta.get("date")
        if isinstance(layout_date_str, str) and layout_date_str:
            try:
                self.layout_date = datetime.fromisoformat(layout_date_str).date()

            except Exception:
                self.layout_date = None
        else:
            self.layout_date = None

        wells_val = layout_meta.get("wells")
        try:
            self.layout_wells = int(wells_val) if wells_val is not None else None
        except Exception:
            self.layout_wells = None

        self.control_group = layout_meta.get("control_group") or ""

        groups_list = layout_meta.get("groups") or []
        self.layout_groups = groups_list if isinstance(groups_list, list) else []

        self.parsed_meta = {
            "baseline_filename_meta": merged.get("baseline_filename_meta"),
            "exposure_filename_meta": merged.get("exposure_filename_meta"),
            "layout_meta": layout_meta,
        }

    def execute_ingest(self) -> Experiment:
        """
        Promote this parsed ingest to an Experiment.
        """
        if self.status != self.Status.PARSED:
            raise ValidationError("Only parsed ingests can be promoted to Experiment.")

        if not self.project_id:
            raise ValidationError({"project": "Project is required before promotion."})

        try:
            with transaction.atomic():
                if Experiment.objects.filter(code=self.code).exists():
                    raise ValidationError(
                        {"code": f"Experiment with code '{self.code}' already exists."}
                    )

                exp = Experiment.objects.create(
                    project=self.project,
                    code=self.code,
                    sex=self.sex,
                    date=self.date,
                    cell_line=self.cell_line,
                    researcher=self.experimenter,
                    status=ExperimentStatus.INGESTED,
                    parsed_at=timezone.now(),
                )

                all_wells: list[str] = []
                condition_count = 0

                for g in self.ingest_groups.all():
                    chemical, _ = Chemical.objects.get_or_create(
                        name=g.chemical or "Unknown",
                        defaults={"slug": slugify(g.chemical or "unknown")},
                    )

                    unit = None
                    if g.unit:
                        unit, _ = ConcentrationUnit.objects.get_or_create(
                            symbol=g.unit,
                            defaults={"name": g.unit, "slug": slugify(g.unit)},
                        )

                    wells = g.wells.split() if g.wells else []

                    if g.is_control:
                        condition_name = "Control"
                    elif g.concentration is not None:
                        value_str = format(g.concentration.normalize(), "f")
                        if "." in value_str:
                            value_str = value_str.rstrip("0").rstrip(".")
                        # value_str = (
                        #     format(g.concentration.normalize(), "f").rstrip("0").rstrip(".") or "0"
                        # )
                        condition_name = f"{value_str} {g.unit}".strip()
                    else:
                        condition_name = g.chemical or "Unknown"

                    Condition.objects.create(
                        experiment=exp,
                        name=condition_name,
                        chemical=chemical,
                        concentration=g.concentration,
                        unit=unit,
                        wells=wells,
                        is_control=g.is_control,
                    )

                    all_wells.extend(wells)
                    condition_count += 1

                exp.well_count = len(set(all_wells))
                exp.condition_count = condition_count
                exp.save(update_fields=["well_count", "condition_count", "updated_at"])

                self.status = self.Status.INGESTED
                self.error_message = ""
                self.save(update_fields=["status", "error_message", "updated_at"])

            return exp

        except Exception as e:
            self.status = self.Status.ERROR
            self.error_message = str(e)
            self.save(update_fields=["status", "error_message", "updated_at"])
            raise


class ExperimentIngestGroup(TimeStampedModel):
    ingest = models.ForeignKey(
        ExperimentIngest,
        on_delete=models.CASCADE,
        related_name="ingest_groups",
    )
    # name = models.CharField(max_length=255)
    chemical = models.CharField(max_length=255, blank=True, default="")
    concentration = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
    )
    unit = models.CharField(max_length=32, blank=True, default="")
    is_control = models.BooleanField(default=False)
    wells = models.TextField(
        blank=True, default="", help_text="Space-separated wells, e.g. A1 A2 A3"
    )

    class Meta(TimeStampedModel.Meta):
        # ordering = ["name"]
        ordering = ["id"]
        # constraints = [
        #     models.UniqueConstraint(fields=["ingest", "name"], name="unique_ingest_group_name")
        # ]

    def __str__(self) -> str:
        label = "Control" if self.is_control else (self.chemical or "Unknown")
        return f"{self.ingest.code or self.ingest.pk}: {label}"
        # return f"{self.ingest.code or self.ingest.pk}: {self.name}"