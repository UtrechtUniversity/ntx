from __future__ import annotations

from .ingest.service import _compute_ratio


def test_compute_ratio_knockout_only_when_one_side_missing():
    assert _compute_ratio(None, None) is None
    assert _compute_ratio(None, 1.0) == -1
    assert _compute_ratio(1.0, None) == -1


def test_compute_ratio_returns_none_when_baseline_zero():
    assert _compute_ratio(0, 0) is None
    assert _compute_ratio(0, 1.0) is None


def test_compute_ratio_computes_finite_ratios():
    assert _compute_ratio(2, 4) == 2.0
    assert _compute_ratio(2, 0) == 0.0
