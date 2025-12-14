from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ParamInfo:
    key: str
    label: str
    section: str


@dataclass(frozen=True, slots=True)
class ConditionInfo:
    label: str
    chemical: str
    concentration: float | None
    concentration_label: str | None
    unit_symbol: str | None
    is_control: bool
    sex_prefix: str | None


@dataclass(frozen=True, slots=True)
class AnalysisLabels:
    params: list[ParamInfo]
    conditions: list[ConditionInfo]
    control_map: dict[str, str]


@dataclass(frozen=True, slots=True)
class Observation:
    experiment_id: int
    div: int
    condition_label: str
    param: str
    well: str
    value: float | None
    is_control: bool
    is_knockout: bool
    is_inactive: bool
    is_excluded: bool


@dataclass(frozen=True, slots=True)
class FenceRecord:
    div: int
    condition_label: str
    param: str
    method: Literal["BOXPLOT", "ZSCORE"]
    n: int
    q1: float | None
    median: float | None
    q3: float | None
    lower: float | None
    upper: float | None


@dataclass(frozen=True, slots=True)
class AggregateRecord:
    div: int
    condition_label: str
    param: str
    n: int
    mean: float | None
    sem: float | None
    std: float | None
    q1: float | None
    median: float | None
    q3: float | None


@dataclass(frozen=True, slots=True)
class OutlierPoint:
    div: int
    condition_label: str
    param: str
    experiment_id: int
    well: str
    value: float


@dataclass(frozen=True, slots=True)
class AnalysisPipelineResult:
    labels: AnalysisLabels
    pre_outlier: list[Observation]
    post_outlier: list[Observation]
    fences: list[FenceRecord]
    aggregates: list[AggregateRecord]
    outliers: list[OutlierPoint]
