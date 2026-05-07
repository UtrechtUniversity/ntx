from __future__ import annotations

from django.db import models


class ExposureType(models.TextChoices):
    ACUTE = "ACUTE", "Acute"
    CHRONIC = "CHRONIC", "Chronic"
    SUBCHRONIC = "SUBCHRONIC", "Subchronic"
    UNDEFINED = "UNDEFINED", "Undefined"


FINAL_EXPOSURE_TYPE_CHOICES = tuple(
    choice for choice in ExposureType.choices if choice[0] != ExposureType.UNDEFINED
)
