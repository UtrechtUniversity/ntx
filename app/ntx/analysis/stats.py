from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence, cast

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from statsmodels.formula.api import ols
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd

from .dtos import Observation


@dataclass(frozen=True, slots=True)
class AnovaResult:
    param: str
    p_value: float | None


@dataclass(frozen=True, slots=True)
class TukeyPairwiseResult:
    param: str
    control: str
    condition: str
    p_value: float | None
    stars: str


def p_value_to_stars(p_value: float | None) -> str:
    if p_value is None or not math.isfinite(p_value):
        return ""
    if p_value <= 0.001:
        return "***"
    if p_value <= 0.01:
        return "**"
    if p_value <= 0.05:
        return "*"
    return ""


def run_anova(
    observations: Iterable[Observation],
    *,
    control_label: str | None = None,
    include_control: bool = True,
) -> list[AnovaResult]:
    """
    One-way ANOVA per parameter.

    This operates on per-well values (after outlier removal).
    When `include_control=False`, control wells are excluded from the analysis
    (for flexibility, usage currently not planned).
    """
    rows: list[dict[str, object]] = []
    for obs in observations:
        if obs.value is None:
            continue
        if not include_control and obs.is_control:
            continue
        if (
            control_label is not None
            and obs.condition_label == control_label
            and not include_control
        ):
            continue
        rows.append(
            {
                "param": obs.param,
                "condition": obs.condition_label,
                "value": float(obs.value),
            }
        )

    if not rows:
        return []

    df = pd.DataFrame.from_records(rows)
    results: list[AnovaResult] = []
    for param, group_df in cast(Any, df).groupby("param", sort=True):
        try:
            if len(set(group_df["condition"])) < 2:
                results.append(AnovaResult(param=str(param), p_value=None))
                continue
            model = ols("value ~ C(condition)", data=group_df).fit()
            table = anova_lm(model, typ=2)
            table_any = cast(Any, table)
            p_value_raw = table_any.loc["C(condition)", "PR(>F)"]
            results.append(AnovaResult(param=str(param), p_value=float(p_value_raw)))
        except Exception:  # noqa: BLE001
            results.append(AnovaResult(param=str(param), p_value=None))
    return results


def run_tukey_pairwise_against_control(
    observations: Iterable[Observation],
    *,
    control_label: str,
) -> list[TukeyPairwiseResult]:
    """
    Compute Tukey-Kramer HSD p-values per (param, control vs condition).

    This runs Tukey on pairwise subsets consisting of (control + one condition),
    like in NeurotoxMEA, rather than a single multi-group Tukey run.
    """
    rows: list[dict[str, object]] = []
    for obs in observations:
        if obs.value is None:
            continue
        rows.append(
            {
                "param": obs.param,
                "condition": obs.condition_label,
                "value": float(obs.value),
            }
        )

    if not rows:
        return []

    df = pd.DataFrame.from_records(rows)
    if control_label not in set(df["condition"]):
        raise ValueError(f"control_label '{control_label}' is not present in the observations")

    results: list[TukeyPairwiseResult] = []
    for param, group_df in cast(Any, df).groupby("param", sort=True):
        control_values = group_df[group_df["condition"] == control_label]["value"]
        if control_values.size < 2:
            continue

        conditions = sorted(set(group_df["condition"]) - {control_label})
        for condition in conditions:
            subset = group_df[group_df["condition"].isin([control_label, condition])]
            if len(set(subset["condition"])) < 2:
                continue
            if subset[subset["condition"] == condition]["value"].size < 2:
                continue

            try:
                tukey = pairwise_tukeyhsd(endog=subset["value"], groups=subset["condition"])
                pvalues = cast(Any, getattr(tukey, "pvalues", None))
                p_value = float(pvalues[0]) if pvalues is not None and len(pvalues) else None
            except Exception:  # noqa: BLE001
                p_value = None

            results.append(
                TukeyPairwiseResult(
                    param=str(param),
                    control=control_label,
                    condition=str(condition),
                    p_value=p_value,
                    stars=p_value_to_stars(p_value),
                )
            )

    return results


@dataclass(frozen=True, slots=True)
class DoseResponseFit:
    model: str
    params: dict[str, float]
    adj_r2: float | None
    rmse: float | None
    x_fit: list[float]
    y_fit: list[float]


def fit_dose_response(
    concentrations: Sequence[float],
    responses: Sequence[float],
    sigma: Sequence[float] | None = None,
    *,
    models: Sequence[str] = ("linear", "4pl"),
    fit_points: int = 100,
) -> DoseResponseFit:
    """
    Fit a dose-response curve and select a winner.

    Supported models:
    - "linear": y = m*x + c (polyfit). Uses Weighted Least Squares if sigma provided (w=1/sigma).
    - "4pl": four-parameter logistic (Hill/log-logistic). Uses weighted curve_fit if sigma provided.
    """
    x = np.asarray(concentrations, dtype=float)
    y = np.asarray(responses, dtype=float)
    sigma_arr = np.asarray(sigma, dtype=float) if sigma is not None else None

    # Filter invalid data points
    if sigma_arr is not None:
        mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(sigma_arr) & (x > 0) & (sigma_arr > 0)
        sigma_arr = sigma_arr[mask]
    else:
        mask = np.isfinite(x) & np.isfinite(y) & (x > 0)

    x = x[mask]
    y = y[mask]

    if x.size < 2:
        raise ValueError("Need at least two valid (concentration, response) points to fit a curve")

    order = np.argsort(x)
    x = x[order]
    y = y[order]
    if sigma_arr is not None:
        sigma_arr = sigma_arr[order]

    candidates: list[DoseResponseFit] = []
    for model in models:
        if model == "linear":
            candidates.append(_fit_linear(x, y, sigma=sigma_arr, fit_points=fit_points))
        elif model == "4pl":
            try:
                candidates.append(_fit_4pl(x, y, sigma=sigma_arr, fit_points=fit_points))
            except Exception:  # noqa: BLE001
                # Curve fitting often fails with optimal parameters not found
                continue
        else:
            raise ValueError(f"Unsupported model '{model}'")

    if not candidates:
        return _fit_linear(x, y, sigma=sigma_arr, fit_points=fit_points)

    def score(item: DoseResponseFit) -> tuple[float, float]:
        adj = item.adj_r2 if item.adj_r2 is not None and math.isfinite(item.adj_r2) else -1e9
        rmse = item.rmse if item.rmse is not None and math.isfinite(item.rmse) else 1e9
        return (adj, -rmse)

    return max(candidates, key=score)


def _fit_linear(
    x: np.ndarray, y: np.ndarray, *, sigma: np.ndarray | None, fit_points: int
) -> DoseResponseFit:
    weights = 1.0 / sigma if sigma is not None else None

    # Check for empty weights or inf weights after division (though sigma>0 is checked above)
    if weights is not None:
        weights = np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0)

    m, c = np.polyfit(x, y, 1, w=weights)
    y_hat = m * x + c
    adj_r2, rmse = _fit_metrics(y, y_hat, p=2, weights=weights)

    x_fit = np.linspace(float(x.min()), float(x.max()), fit_points)
    y_fit = (m * x_fit + c).tolist()
    return DoseResponseFit(
        model="linear",
        params={"m": float(m), "c": float(c)},
        adj_r2=adj_r2,
        rmse=rmse,
        x_fit=x_fit.tolist(),
        y_fit=y_fit,
    )


def _four_param_logistic(x: np.ndarray, a: float, b: float, c: float, d: float) -> np.ndarray:
    # y = d + (a - d) / (1 + (x / c)**b)
    # Using float64 explicitly to avoid overflow warnings in exp
    return d + (a - d) / (1.0 + np.power(x / c, b))


def _fit_4pl(
    x: np.ndarray, y: np.ndarray, *, sigma: np.ndarray | None, fit_points: int
) -> DoseResponseFit:
    # Initial guesses: top/bottom from endpoints; IC50 around median x; slope = 1.
    a0 = float(y.max())
    d0 = float(y.min())
    c0 = float(np.median(x))
    b0 = 1.0

    bounds = (
        [0.0, -10.0, float(x.min()) * 1e-6, 0.0],
        [float(y.max()) * 10.0, 10.0, float(x.max()) * 1e6, float(y.max()) * 10.0],
    )

    popt, _ = curve_fit(
        _four_param_logistic,
        x,
        y,
        p0=[a0, b0, c0, d0],
        bounds=bounds,
        maxfev=50_000,
        sigma=sigma,
        absolute_sigma=True if sigma is not None else False,
    )

    y_hat = _four_param_logistic(x, *popt)

    weights = 1.0 / sigma if sigma is not None else None
    if weights is not None:
        weights = np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0)

    adj_r2, rmse = _fit_metrics(y, y_hat, p=4, weights=weights)

    x_fit = np.linspace(float(x.min()), float(x.max()), fit_points)
    y_fit = _four_param_logistic(x_fit, *popt).tolist()
    return DoseResponseFit(
        model="4pl",
        params={"a": float(popt[0]), "b": float(popt[1]), "c": float(popt[2]), "d": float(popt[3])},
        adj_r2=adj_r2,
        rmse=rmse,
        x_fit=x_fit.tolist(),
        y_fit=y_fit,
    )


def _fit_metrics(
    y: np.ndarray, y_hat: np.ndarray, *, p: int, weights: np.ndarray | None = None
) -> tuple[float | None, float | None]:
    if y.size == 0:
        return None, None

    resid = y - y_hat

    if weights is not None:
        sse = float(np.sum(weights * resid * resid))
        y_mean = np.average(y, weights=weights)
        sst = float(np.sum(weights * (y - float(y_mean)) ** 2))
    else:
        sse = float(np.sum(resid * resid))
        sst = float(np.sum((y - float(np.mean(y))) ** 2))

    rmse = math.sqrt(sse / y.size) if y.size else None

    if sst == 0 or y.size <= p + 1:
        return None, rmse

    r2 = 1.0 - sse / sst
    adj_r2 = 1.0 - (1.0 - r2) * (y.size - 1) / (y.size - p - 1)
    return adj_r2, rmse
