from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import ErrorDetails

from .metrics_schema import MetricsPayload, MetricsQcPayload
from .utils import sanitize_numeric_json

User = get_user_model()

WELL_RE = re.compile(r"^[A-Za-z](\d+)$")


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
        abstract = False
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
        """
        Enforce that aliases point directly to a canonical record.
        """
        canonical = getattr(self, "canonical", None)
        if canonical is None:
            return
        if getattr(canonical, "canonical_id", None) is not None:
            raise ValidationError(
                {"canonical": "Canonical must reference a canonical record (canonical is None)."}
            )

    def _validate_canonical_chain(self):
        """
        Ensure the canonical relationship does not form a cycle.
        """
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
        abstract = False
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
        abstract = False
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
    code = models.CharField(max_length=128)
    sex = models.CharField(
        max_length=1,
        choices=Sex.choices,
        default=Sex.UNKNOWN,
    )
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
        max_length=16,
        choices=ExperimentStatus.choices,
        default=ExperimentStatus.CREATED,
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        ordering = ["-date", "code"]
        indexes = [
            models.Index(fields=["project", "date"], name="experiment_project_date_idx"),
        ]
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
    kind = models.CharField(
        max_length=32,
        choices=FileKind.choices,
        default=FileKind.OTHER,
    )
    div = models.PositiveIntegerField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        ordering = ["experiment_id", "id"]

    def __str__(self) -> str:
        return self.file.name


class Condition(TimeStampedModel):
    if TYPE_CHECKING:
        experiment_id: int

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="conditions")
    name = models.CharField(max_length=255)
    chemical = models.ForeignKey(Chemical, on_delete=models.PROTECT, related_name="conditions")
    concentration = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
    )
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
        abstract = False
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "experiment",
                    "name",
                    "concentration",
                    "unit",
                ],
                name="condition_unique_per_experiment",
                nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(
                fields=["experiment", "is_control"],
                name="cond_exp_ctrl_idx",
            )
        ]

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
        abstract = False
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
        
class ExperimentIngest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARSED = "PARSED", "Parsed"
        INGESTED = "INGESTED", "Ingested"
        ERROR = "ERROR", "Error"

    class SubmissionMethod(models.TextChoices):
        UPLOAD = "UPLOAD", "Upload"
        DISCOVERED = "DISCOVERED", "Discovered"

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    submission_method = models.CharField(
        max_length=16,
        choices=SubmissionMethod.choices,
        default=SubmissionMethod.UPLOAD,
    )
    error_message = models.TextField(blank=True, default="")

    layout_file = models.FileField(upload_to="ingest/layouts/")
    baseline_csv = models.FileField(upload_to="ingest/baselines/")
    exposure_csv = models.FileField(upload_to="ingest/exposures/")

    def __str__(self) -> str:
        return f"ExperimentIngest #{self.pk or 'new'} ({self.status})"


