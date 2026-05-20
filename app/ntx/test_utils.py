from __future__ import annotations

from decimal import Decimal

from .utils import normalize_decimals


def test_normalize_decimals():
    assert normalize_decimals(Decimal("100.000000")) == "100"
    assert normalize_decimals(Decimal("0.100000")) == "0.1"
    assert normalize_decimals(Decimal("10.010000")) == "10.01"
    assert normalize_decimals(Decimal("100")) == "100"
    assert normalize_decimals(Decimal("0.000000")) == "0"
