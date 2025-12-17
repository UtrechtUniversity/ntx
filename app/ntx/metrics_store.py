from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .metrics_schema import MetricsPayload, MetricsQcPayload
from .models import NeuronalMetricsFrame


@dataclass(frozen=True)
class MetricsFrameRecord:
    experiment_id: int
    div: int
    metrics: MetricsPayload
    qc: MetricsQcPayload


def fetch_experiment_metrics_frames(experiment_ids: Iterable[int]) -> list[MetricsFrameRecord]:
    """
    Fetch all stored metrics frames for the given experiments.

    Returns one record per (experiment_id, div).
    """
    ids = list(experiment_ids)
    if not ids:
        return []

    frames = (
        NeuronalMetricsFrame.objects.filter(experiment_id__in=ids)
        .order_by("experiment_id", "div", "id")
        .only("experiment_id", "div", "metrics_json", "qc_json")
    )

    records: list[MetricsFrameRecord] = []
    for frame in frames:
        if not isinstance(frame.metrics_json, dict):
            raise TypeError("metrics_json must be a dict")
        if not isinstance(frame.qc_json, dict):
            raise TypeError("qc_json must be a dict")

        records.append(
            MetricsFrameRecord(
                experiment_id=frame.experiment_id,
                div=frame.div,
                metrics=MetricsPayload.model_construct(**frame.metrics_json),
                qc=MetricsQcPayload.model_construct(**frame.qc_json),
            )
        )

    return records


def metrics_frame_to_records(frame: MetricsFrameRecord) -> list[dict[str, Any]]:
    """
    Convert a columnar MetricsPayload to records suitable for Polars dataframes.

    Each output row represents one (param, well) observation with baseline/exposure/ratio values
    and the enclosing (experiment_id, div).
    """
    payload = frame.metrics
    records: list[dict[str, Any]] = []

    for param_idx, param in enumerate(payload.params):
        baseline_row = payload.baseline[param_idx]
        exposure_row = payload.exposure[param_idx]
        ratio_row = payload.ratio[param_idx]

        for well_idx, well in enumerate(payload.wells):
            records.append(
                {
                    "experiment_id": frame.experiment_id,
                    "div": frame.div,
                    "param": param,
                    "well": well,
                    "baseline": baseline_row[well_idx],
                    "exposure": exposure_row[well_idx],
                    "ratio": ratio_row[well_idx],
                }
            )

    return records
