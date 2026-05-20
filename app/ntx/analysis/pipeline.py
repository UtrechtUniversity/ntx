from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Literal, Sequence, cast

import polars as pl
from django.db.models import Prefetch

from ntx.metrics_metadata import AXION_METRICS_MAP, METRIC_SECTIONS
from ntx.metrics_store import fetch_experiment_metrics_frames, metrics_frame_to_records
from ntx.models import Condition, Experiment, OutlierMethod
from ntx.utils import normalize_decimals

from .dtos import (
    AggregateRecord,
    AnalysisLabels,
    AnalysisPipelineResult,
    ConditionInfo,
    FenceRecord,
    Observation,
    OutlierPoint,
    ParamInfo,
)
from .outliers import compute_outlier_summary


class AnalysisPipelineError(ValueError):
    """Raised when the analytical pipeline cannot run."""


@dataclass(frozen=True, slots=True)
class _ExperimentMeta:
    experiment_id: int
    project_id: int
    network_burst_threshold: int
    excluded_wells: list[str]
    outlier_method: OutlierMethod


@dataclass(frozen=True, slots=True)
class _ConditionRow:
    experiment_id: int
    condition_id: int
    well: str
    condition_name: str
    condition_label: str
    sex_prefix: str | None
    chemical_label: str
    concentration: float | None
    concentration_label: str | None
    unit_symbol: str | None
    is_control: bool


def run_experiment_analysis(
    experiment_ids: Iterable[int],
    *,
    ignore_exclusions: bool = False,
    outlier_method: OutlierMethod | None = None,
) -> AnalysisPipelineResult:
    """
    Run the Polars-based analytical pipeline for a set of experiments.

    Output value semantics:
    - Stored JSON uses `null` for missing, `-1` for knockouts.
    - In-memory we treat knockouts as missing for stats (value becomes null) while keeping
      an `is_knockout` mask for reporting.
    """
    ids = list(dict.fromkeys(int(value) for value in experiment_ids))
    if not ids:
        return AnalysisPipelineResult(
            labels=AnalysisLabels(params=[], conditions=[], control_map={}),
            pre_outlier=[],
            post_outlier=[],
            fences=[],
            aggregates=[],
            outliers=[],
        )

    experiments = (
        Experiment.objects.filter(id__in=ids)
        .select_related("project")
        .prefetch_related(
            Prefetch(
                "conditions",
                queryset=Condition.objects.select_related(
                    "chemical__canonical",
                    "unit__canonical",
                ).only(
                    "id",
                    "experiment_id",
                    "name",
                    "chemical_id",
                    "chemical__name",
                    "chemical__canonical_id",
                    "chemical__canonical__name",
                    "concentration",
                    "unit_id",
                    "unit__symbol",
                    "unit__name",
                    "unit__canonical_id",
                    "unit__canonical__symbol",
                    "unit__canonical__name",
                    "is_control",
                    "wells",
                ),
            )
        )
    )

    if not experiments:
        raise AnalysisPipelineError(f"No experiments found for ids={sorted(ids)}")

    metas = [_build_experiment_meta(experiment) for experiment in experiments]

    resolved_outlier_method = outlier_method or _resolve_outlier_method(metas)

    condition_rows, control_map, condition_infos = _build_condition_rows(experiments)

    labels = AnalysisLabels(
        params=[],
        conditions=sorted(condition_infos.values(), key=lambda info: info.label),
        control_map=control_map,
    )

    frames = fetch_experiment_metrics_frames(ids)
    if not frames:
        return AnalysisPipelineResult(
            labels=labels,
            pre_outlier=[],
            post_outlier=[],
            fences=[],
            aggregates=[],
            outliers=[],
        )

    df = _load_frames(frames)
    df = _join_metadata(df, metas=metas, condition_rows=condition_rows)
    df = _apply_masks(df, ignore_exclusions=ignore_exclusions)
    df = _normalize_controls(df, method=resolved_outlier_method)
    df, fences, outliers = _remove_outliers(df, method=resolved_outlier_method)
    aggregates = _aggregate(df)

    param_infos = _build_param_infos(sorted(df.get_column("param").unique().to_list()))
    labels = AnalysisLabels(
        params=param_infos,
        conditions=sorted(condition_infos.values(), key=lambda info: info.label),
        control_map=control_map,
    )

    pre = _observations_from_frame(df, value_col="value_pre")
    post = _observations_from_frame(df, value_col="value_post")

    return AnalysisPipelineResult(
        labels=labels,
        pre_outlier=pre,
        post_outlier=post,
        fences=fences,
        aggregates=aggregates,
        outliers=outliers,
    )


def _build_experiment_meta(experiment: Experiment) -> _ExperimentMeta:
    excluded = experiment.excluded_wells
    if not isinstance(excluded, list):
        excluded = []

    outlier_raw = getattr(experiment.project, "outlier_method", None)
    outlier_method = OutlierMethod(outlier_raw) if outlier_raw else OutlierMethod.BOXPLOT

    return _ExperimentMeta(
        experiment_id=experiment.id,
        project_id=experiment.project.id,
        network_burst_threshold=int(experiment.network_burst_threshold or 20),
        excluded_wells=[str(well) for well in excluded if isinstance(well, str)],
        outlier_method=outlier_method,
    )


def _resolve_outlier_method(metas: Sequence[_ExperimentMeta]) -> OutlierMethod:
    methods = {meta.outlier_method for meta in metas}
    if len(methods) == 1:
        return next(iter(methods))
    raise AnalysisPipelineError(
        "Multiple outlier methods present; pass outlier_method explicitly. "
        f"methods={sorted(method.value for method in methods)}"
    )


def _parse_sex_prefix(condition_name: str) -> tuple[str | None, str]:
    stripped = condition_name.strip()
    if len(stripped) >= 2 and stripped[1] == " " and stripped[0] in {"F", "M", "X"}:
        return stripped[:2], stripped[2:].lstrip()
    return None, stripped


def _format_decimal(value: Decimal) -> str:
    """Format decimal values for labels: strip fractional zeros but keep full integers."""
    if value == 0:
        raise AnalysisPipelineError("Concentration value must be non-zero for labels.")
    return normalize_decimals(value)


def _build_condition_rows(
    experiments: Iterable[Experiment],
) -> tuple[list[_ConditionRow], dict[str, str], dict[str, ConditionInfo]]:
    rows: list[_ConditionRow] = []
    control_map: dict[str, str] = {}
    infos: dict[str, ConditionInfo] = {}

    for experiment in experiments:
        conditions = list(experiment.conditions.all())
        controls = [condition for condition in conditions if condition.is_control]
        if len(controls) != 1:
            raise AnalysisPipelineError(
                "Each experiment must have exactly one control condition. "
                f"experiment_id={experiment.id} controls={len(controls)}"
            )
        control = controls[0]
        control_label = _condition_display_label(control)

        for condition in conditions:
            condition_label = _condition_display_label(condition)
            control_map.setdefault(condition_label, control_label)

            condition_name = condition.name or ""
            sex_prefix, _ = _parse_sex_prefix(condition_name)

            chemical = condition.chemical.canonical or condition.chemical
            unit = (
                condition.unit.canonical
                if condition.unit and condition.unit.canonical
                else condition.unit
            )

            concentration_value = (
                float(condition.concentration) if condition.concentration is not None else None
            )
            concentration_label = (
                _format_decimal(condition.concentration)
                if isinstance(condition.concentration, Decimal)
                else (str(condition.concentration) if condition.concentration is not None else None)
            )
            unit_symbol = None
            if unit is not None:
                unit_symbol = unit.symbol or unit.name

            infos.setdefault(
                condition_label,
                ConditionInfo(
                    label=condition_label,
                    chemical=chemical.name,
                    concentration=concentration_value,
                    concentration_label=concentration_label,
                    unit_symbol=unit_symbol,
                    is_control=bool(condition.is_control),
                    sex_prefix=sex_prefix,
                ),
            )

            wells = condition.wells
            if not isinstance(wells, list) or not all(isinstance(well, str) for well in wells):
                raise AnalysisPipelineError(
                    "Condition.wells must be a list[str]. "
                    f"condition_id={condition.id} experiment_id={experiment.id}"
                )

            for well in wells:
                rows.append(
                    _ConditionRow(
                        experiment_id=experiment.id,
                        condition_id=condition.id,
                        well=well,
                        condition_name=condition_name,
                        condition_label=condition_label,
                        sex_prefix=sex_prefix,
                        chemical_label=chemical.name,
                        concentration=concentration_value,
                        concentration_label=concentration_label,
                        unit_symbol=unit_symbol,
                        is_control=bool(condition.is_control),
                    )
                )

    return rows, control_map, infos


def _condition_display_label(condition: Condition) -> str:
    name = condition.name or ""
    sex_prefix, _ = _parse_sex_prefix(name)

    chemical = condition.chemical.canonical or condition.chemical

    if condition.is_control:
        base = f"{chemical.name} (control)"
    else:
        base = chemical.name
        if condition.concentration is not None:
            if isinstance(condition.concentration, Decimal):
                base = f"{base} {_format_decimal(condition.concentration)}"
            else:
                base = f"{base} {condition.concentration}"
        unit = (
            condition.unit.canonical
            if condition.unit and condition.unit.canonical
            else condition.unit
        )
        if unit is not None:
            symbol = unit.symbol or unit.name
            if symbol:
                base = f"{base} {symbol}"

    if sex_prefix:
        return f"{sex_prefix}{base}"
    return base


def _build_param_infos(params: list[str]) -> list[ParamInfo]:
    inverse: dict[str, str] = {}
    for raw, internal in AXION_METRICS_MAP.items():
        inverse.setdefault(internal, raw)

    section_by_param: dict[str, str] = {}
    for section, section_params in METRIC_SECTIONS.items():
        for param in section_params:
            section_by_param[param] = section

    ordered: list[str] = []
    seen: set[str] = set()
    for section_params in METRIC_SECTIONS.values():
        for param in section_params:
            if param in params and param not in seen:
                ordered.append(param)
                seen.add(param)
    for param in sorted(params):
        if param not in seen:
            ordered.append(param)
            seen.add(param)

    infos: list[ParamInfo] = []
    for param in ordered:
        label = inverse.get(param) or param.replace("_", " ").replace("  ", " ").strip().title()
        infos.append(
            ParamInfo(key=param, label=label, section=section_by_param.get(param, "Other"))
        )
    return infos


def _load_frames(frames) -> pl.DataFrame:
    metric_records: list[dict[str, Any]] = []
    qc_records: list[dict[str, Any]] = []

    for frame in frames:
        metric_records.extend(metrics_frame_to_records(frame))

        qc_payload = frame.qc
        qc_wells = qc_payload.wells
        for idx, well in enumerate(qc_wells):
            qc_records.append(
                {
                    "experiment_id": frame.experiment_id,
                    "div": frame.div,
                    "well": well,
                    "number_of_active_electrodes": qc_payload.number_of_active_electrodes[idx],
                    "number_of_bursting_electrodes": qc_payload.number_of_bursting_electrodes[idx],
                    "number_network_bursts_baseline": qc_payload.number_network_bursts_baseline[
                        idx
                    ],
                }
            )

    if not metric_records:
        raise AnalysisPipelineError("No metric records were produced from stored frames")

    df = pl.DataFrame(metric_records).with_columns(
        pl.col("experiment_id").cast(pl.Int64),
        pl.col("div").cast(pl.Int64),
        pl.col("param").cast(pl.Utf8),
        pl.col("well").cast(pl.Utf8),
        pl.col("baseline").cast(pl.Float64),
        pl.col("exposure").cast(pl.Float64),
        pl.col("ratio").cast(pl.Float64),
    )

    if qc_records:
        qc_df = pl.DataFrame(qc_records).with_columns(
            pl.col("experiment_id").cast(pl.Int64),
            pl.col("div").cast(pl.Int64),
            pl.col("well").cast(pl.Utf8),
            pl.col("number_of_active_electrodes").cast(pl.Float64),
            pl.col("number_of_bursting_electrodes").cast(pl.Float64),
            pl.col("number_network_bursts_baseline").cast(pl.Float64),
        )
        df = df.join(qc_df, on=["experiment_id", "div", "well"], how="left")
    else:
        df = df.with_columns(
            number_of_active_electrodes=pl.lit(None, dtype=pl.Float64),
            number_of_bursting_electrodes=pl.lit(None, dtype=pl.Float64),
            number_network_bursts_baseline=pl.lit(None, dtype=pl.Float64),
        )

    return df


def _join_metadata(
    df: pl.DataFrame,
    *,
    metas: Sequence[_ExperimentMeta],
    condition_rows: Sequence[_ConditionRow],
) -> pl.DataFrame:
    meta_df = pl.DataFrame(
        [
            {
                "experiment_id": meta.experiment_id,
                "project_id": meta.project_id,
                "network_burst_threshold": meta.network_burst_threshold,
            }
            for meta in metas
        ]
    ).with_columns(
        pl.col("experiment_id").cast(pl.Int64),
        pl.col("project_id").cast(pl.Int64),
        pl.col("network_burst_threshold").cast(pl.Int64),
    )

    excluded_records: list[dict[str, Any]] = []
    for meta in metas:
        for well in meta.excluded_wells:
            excluded_records.append(
                {"experiment_id": meta.experiment_id, "well": well, "is_excluded": True}
            )

    excluded_df = (
        pl.DataFrame(excluded_records).with_columns(
            pl.col("experiment_id").cast(pl.Int64),
            pl.col("well").cast(pl.Utf8),
            pl.col("is_excluded").cast(pl.Boolean),
        )
        if excluded_records
        else pl.DataFrame(
            {
                "experiment_id": pl.Series([], dtype=pl.Int64),
                "well": pl.Series([], dtype=pl.Utf8),
                "is_excluded": pl.Series([], dtype=pl.Boolean),
            }
        )
    )

    condition_df = pl.DataFrame(
        [
            {
                "experiment_id": row.experiment_id,
                "condition_id": row.condition_id,
                "well": row.well,
                "condition_name": row.condition_name,
                "condition_label": row.condition_label,
                "sex_prefix": row.sex_prefix,
                "chemical_label": row.chemical_label,
                "concentration": row.concentration,
                "concentration_label": row.concentration_label,
                "unit_symbol": row.unit_symbol,
                "is_control": row.is_control,
            }
            for row in condition_rows
        ]
    ).with_columns(
        pl.col("experiment_id").cast(pl.Int64),
        pl.col("condition_id").cast(pl.Int64),
        pl.col("well").cast(pl.Utf8),
        pl.col("condition_name").cast(pl.Utf8),
        pl.col("condition_label").cast(pl.Utf8),
        pl.col("sex_prefix").cast(pl.Utf8),
        pl.col("chemical_label").cast(pl.Utf8),
        pl.col("concentration").cast(pl.Float64),
        pl.col("concentration_label").cast(pl.Utf8),
        pl.col("unit_symbol").cast(pl.Utf8),
        pl.col("is_control").cast(pl.Boolean),
    )

    df = df.join(meta_df, on=["experiment_id"], how="left")
    df = df.join(condition_df, on=["experiment_id", "well"], how="left")
    df = df.join(excluded_df, on=["experiment_id", "well"], how="left")
    df = df.with_columns(pl.col("is_excluded").fill_null(False))

    missing_conditions = (
        df.filter(pl.col("condition_id").is_null()).select("experiment_id", "well").unique()
    )
    if missing_conditions.height:
        sample = missing_conditions.head(10).to_dicts()
        raise AnalysisPipelineError(
            f"Missing condition mapping for some (experiment_id, well) pairs; sample={sample}"
        )

    return df


def _apply_masks(df: pl.DataFrame, *, ignore_exclusions: bool) -> pl.DataFrame:
    is_knockout = (pl.col("ratio") == -1).fill_null(False)
    ratio_value = pl.when(pl.col("ratio") == -1).then(None).otherwise(pl.col("ratio"))

    inactive_by_active_electrodes = pl.col("number_of_active_electrodes").is_null() | (
        pl.col("number_of_active_electrodes") <= 4
    )
    inactive_by_network_bursts = pl.col("number_network_bursts_baseline").is_null() | (
        pl.col("number_network_bursts_baseline") <= pl.col("network_burst_threshold")
    )

    df = df.with_columns(
        is_knockout=is_knockout,
        is_inactive=(inactive_by_active_electrodes | inactive_by_network_bursts),
        is_excluded=pl.lit(False) if ignore_exclusions else pl.col("is_excluded"),
        value_ratio=ratio_value,
    )

    df = df.with_columns(
        value_ratio=pl.when(pl.col("is_inactive") | pl.col("is_excluded"))
        .then(None)
        .otherwise(pl.col("value_ratio")),
    )

    return df


def _compute_fences_df(
    df: pl.DataFrame,
    *,
    group_cols: list[str],
    value_col: str,
    method: OutlierMethod,
) -> pl.DataFrame:
    grouped = df.group_by(group_cols).agg(pl.col(value_col).drop_nulls().alias("_values"))

    if grouped.height == 0:
        schema: dict[str, Any] = {key: grouped.schema[key] for key in group_cols}
        schema.update(
            {
                "method": pl.Utf8,
                "n": pl.Int64,
                "q1": pl.Float64,
                "median": pl.Float64,
                "q3": pl.Float64,
                "lower": pl.Float64,
                "upper": pl.Float64,
            }
        )
        return pl.DataFrame(schema=schema)

    results: list[dict[str, Any]] = []
    for row in grouped.to_dicts():
        values = row.get("_values") or []
        summary = compute_outlier_summary(values, method=method)
        results.append(
            {
                **{key: row[key] for key in group_cols},
                "method": method.value,
                "n": summary.n,
                "q1": summary.q1,
                "median": summary.median,
                "q3": summary.q3,
                "lower": summary.lower_fence,
                "upper": summary.upper_fence,
            }
        )

    return pl.DataFrame(results)


def _normalize_controls(df: pl.DataFrame, *, method: OutlierMethod) -> pl.DataFrame:
    control_df = df.filter(pl.col("is_control"))
    fences = (
        _compute_fences_df(
            control_df,
            group_cols=["experiment_id", "param"],
            value_col="value_ratio",
            method=method,
        )
        .select(["experiment_id", "param", "n", "lower", "upper"])
        .rename({"n": "control_n", "lower": "control_lower", "upper": "control_upper"})
    )

    df = df.join(fences, on=["experiment_id", "param"], how="left")

    is_control_outlier = (
        pl.col("is_control")
        & (pl.col("control_n") >= 4)
        & pl.col("control_lower").is_not_null()
        & pl.col("control_upper").is_not_null()
        & pl.col("value_ratio").is_not_null()
        & (
            (pl.col("value_ratio") < pl.col("control_lower"))
            | (pl.col("value_ratio") > pl.col("control_upper"))
        )
    )

    df = df.with_columns(
        is_control_outlier=is_control_outlier.fill_null(False),
        value_ratio_for_norm=pl.when(is_control_outlier)
        .then(None)
        .otherwise(pl.col("value_ratio")),
    )

    control_means = (
        df.filter(pl.col("is_control"))
        .group_by(["experiment_id", "param"])
        .agg(pl.col("value_ratio_for_norm").mean().alias("control_mean"))
    )

    df = df.join(control_means, on=["experiment_id", "param"], how="left")

    df = df.with_columns(
        value_pre=pl.when(
            pl.col("control_mean").is_null()
            | (pl.col("control_mean") == 0)
            | pl.col("value_ratio_for_norm").is_null()
        )
        .then(None)
        .otherwise(pl.col("value_ratio_for_norm") / pl.col("control_mean"))
    )

    df = df.drop(["value_ratio_for_norm"])
    return df


def _remove_outliers(
    df: pl.DataFrame, *, method: OutlierMethod
) -> tuple[pl.DataFrame, list[FenceRecord], list[OutlierPoint]]:
    non_control = df.filter(~pl.col("is_control"))

    fences_df = _compute_fences_df(
        non_control,
        group_cols=["div", "condition_label", "param"],
        value_col="value_pre",
        method=method,
    )

    df = df.join(fences_df, on=["div", "condition_label", "param"], how="left")

    is_outlier = (
        (~pl.col("is_control"))
        & (pl.col("n") >= 4)
        & pl.col("lower").is_not_null()
        & pl.col("upper").is_not_null()
        & pl.col("value_pre").is_not_null()
        & ((pl.col("value_pre") < pl.col("lower")) | (pl.col("value_pre") > pl.col("upper")))
    )

    df = df.with_columns(is_outlier=is_outlier.fill_null(False))
    df = df.with_columns(
        value_post=pl.when(pl.col("is_outlier")).then(None).otherwise(pl.col("value_pre"))
    )

    outlier_dicts = (
        df.filter(pl.col("is_outlier"))
        .select(
            pl.col("div").cast(pl.Int64),
            pl.col("condition_label"),
            pl.col("param"),
            pl.col("experiment_id").cast(pl.Int64),
            pl.col("well"),
            pl.col("value_pre").cast(pl.Float64).alias("value"),
        )
        .to_dicts()
    )
    outliers = [
        OutlierPoint(
            div=int(row["div"]),
            condition_label=str(row["condition_label"]),
            param=str(row["param"]),
            experiment_id=int(row["experiment_id"]),
            well=str(row["well"]),
            value=float(row["value"]),
        )
        for row in outlier_dicts
        if row.get("value") is not None
    ]

    fence_dicts = fences_df.to_dicts()
    fences = [
        FenceRecord(
            div=int(row["div"]),
            condition_label=str(row["condition_label"]),
            param=str(row["param"]),
            method=cast(Literal["BOXPLOT", "ZSCORE"], str(row["method"])),
            n=int(row["n"]),
            q1=float(row["q1"]) if row.get("q1") is not None else None,
            median=float(row["median"]) if row.get("median") is not None else None,
            q3=float(row["q3"]) if row.get("q3") is not None else None,
            lower=float(row["lower"]) if row.get("lower") is not None else None,
            upper=float(row["upper"]) if row.get("upper") is not None else None,
        )
        for row in fence_dicts
    ]

    return df, fences, outliers


def _aggregate(df: pl.DataFrame) -> list[AggregateRecord]:
    keys = ["div", "condition_label", "param"]

    pre_quartiles = df.group_by(keys).agg(
        pl.col("value_pre").quantile(0.25, interpolation="midpoint").alias("q1"),
        pl.col("value_pre").median().alias("median"),
        pl.col("value_pre").quantile(0.75, interpolation="midpoint").alias("q3"),
    )

    post_stats = df.group_by(keys).agg(
        pl.col("value_post").count().alias("n"),
        pl.col("value_post").mean().alias("mean"),
        pl.col("value_post").std().alias("std"),
    )

    summary = post_stats.join(pre_quartiles, on=keys, how="left").with_columns(
        sem=pl.when(pl.col("n") > 0).then(pl.col("std") / pl.col("n").sqrt()).otherwise(None),
    )

    records: list[AggregateRecord] = []
    for row in summary.to_dicts():
        records.append(
            AggregateRecord(
                div=int(row["div"]),
                condition_label=str(row["condition_label"]),
                param=str(row["param"]),
                n=int(row["n"]),
                mean=float(row["mean"]) if row.get("mean") is not None else None,
                sem=float(row["sem"]) if row.get("sem") is not None else None,
                std=float(row["std"]) if row.get("std") is not None else None,
                q1=float(row["q1"]) if row.get("q1") is not None else None,
                median=float(row["median"]) if row.get("median") is not None else None,
                q3=float(row["q3"]) if row.get("q3") is not None else None,
            )
        )
    return records


def _observations_from_frame(df: pl.DataFrame, *, value_col: str) -> list[Observation]:
    if value_col not in df.columns:
        raise AnalysisPipelineError(f"Missing value column '{value_col}' in analysis frame")

    records = df.select(
        pl.col("experiment_id").cast(pl.Int64),
        pl.col("div").cast(pl.Int64),
        pl.col("condition_label"),
        pl.col("param"),
        pl.col("well"),
        pl.col(value_col).cast(pl.Float64).alias("value"),
        pl.col("is_control").cast(pl.Boolean),
        pl.col("is_knockout").cast(pl.Boolean),
        pl.col("is_inactive").cast(pl.Boolean),
        pl.col("is_excluded").cast(pl.Boolean),
    ).to_dicts()

    return [
        Observation(
            experiment_id=int(row["experiment_id"]),
            div=int(row["div"]),
            condition_label=str(row["condition_label"]),
            param=str(row["param"]),
            well=str(row["well"]),
            value=float(row["value"]) if row.get("value") is not None else None,
            is_control=bool(row["is_control"]),
            is_knockout=bool(row["is_knockout"]),
            is_inactive=bool(row["is_inactive"]),
            is_excluded=bool(row["is_excluded"]),
        )
        for row in records
    ]
