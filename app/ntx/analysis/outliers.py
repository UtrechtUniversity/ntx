from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

import numpy as np
from statsmodels.stats.stattools import medcouple

from ntx.models import OutlierMethod


@dataclass(frozen=True, slots=True)
class OutlierSummary:
    method: OutlierMethod
    n: int
    q1: float | None
    median: float | None
    q3: float | None
    mean: float | None
    std: float | None
    lower_fence: float | None
    upper_fence: float | None


def finite_floats(values: Iterable[object]) -> list[float]:
    """
    Coerce a sequence to finite floats, dropping null/NaN/Inf/bools.
    """
    cleaned: list[float] = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, Decimal):
            numeric = float(value)
        elif isinstance(value, numbers.Real):
            numeric = float(value)
        else:
            continue
        if not math.isfinite(numeric):
            continue
        cleaned.append(numeric)
    return cleaned


def compute_outlier_summary(values: Sequence[object], *, method: OutlierMethod) -> OutlierSummary:
    """
    Compute outlier fences:

    - Skip outlier removal when n < 4.
    - BOXPLOT uses the adjusted boxplot (medcouple) fences with midpoint quantiles,
      using statsmodels' exact O(N**2) algorithm (use_fast=False).
    - ZSCORE uses mean ± 2*std (sample std, ddof=1) when n >= 4.
    """
    cleaned = finite_floats(values)
    n = len(cleaned)
    if n == 0:
        return OutlierSummary(
            method=method,
            n=0,
            q1=None,
            median=None,
            q3=None,
            mean=None,
            std=None,
            lower_fence=None,
            upper_fence=None,
        )

    arr = np.asarray(cleaned, dtype=float)
    q1 = float(np.quantile(arr, 0.25, method="midpoint"))
    median = float(np.median(arr))
    q3 = float(np.quantile(arr, 0.75, method="midpoint"))
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n >= 2 else None

    if n < 4:
        return OutlierSummary(
            method=method,
            n=n,
            q1=q1,
            median=median,
            q3=q3,
            mean=mean,
            std=std,
            lower_fence=None,
            upper_fence=None,
        )

    if method == OutlierMethod.ZSCORE:
        if std is None or not math.isfinite(std):
            return OutlierSummary(
                method=method,
                n=n,
                q1=q1,
                median=median,
                q3=q3,
                mean=mean,
                std=std,
                lower_fence=None,
                upper_fence=None,
            )
        return OutlierSummary(
            method=method,
            n=n,
            q1=q1,
            median=median,
            q3=q3,
            mean=mean,
            std=std,
            lower_fence=mean - 2 * std,
            upper_fence=mean + 2 * std,
        )

    iqr = q3 - q1
    if iqr == 0:
        return OutlierSummary(
            method=method,
            n=n,
            q1=q1,
            median=median,
            q3=q3,
            mean=mean,
            std=std,
            lower_fence=q1,
            upper_fence=q3,
        )

    try:
        mc = float(medcouple(arr, use_fast=False))
    except Exception:  # noqa: BLE001 - statsmodels may raise on degenerate data
        mc = float("nan")

    base = 1.5 * iqr
    if not math.isfinite(mc):
        lower = q1 - base
        upper = q3 + base
    elif mc >= 0:
        lower = q1 - base * math.exp(-4 * mc)
        upper = q3 + base * math.exp(3 * mc)
    else:
        lower = q1 - base * math.exp(-3 * mc)
        upper = q3 + base * math.exp(4 * mc)

    if not math.isfinite(lower):
        lower = None
    if not math.isfinite(upper):
        upper = None

    return OutlierSummary(
        method=method,
        n=n,
        q1=q1,
        median=median,
        q3=q3,
        mean=mean,
        std=std,
        lower_fence=lower,
        upper_fence=upper,
    )
