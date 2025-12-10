from __future__ import annotations

import math
import numbers
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


def sanitize_numeric_json(value: Any) -> Any:
    """
    Recursively sanitize JSON-like payloads containing numeric values.

    - Coerces numpy/polars/etc numeric scalars to built-in int/float.
    - Replaces NaN/Inf with None (valid JSON null), because JSON/JSONB cannot encode
      non-finite numbers. This intentionally collapses NaN and Inf into the same
      stored representation.
    """
    if isinstance(value, Mapping):
        return {key: sanitize_numeric_json(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [sanitize_numeric_json(item) for item in value]

    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, Decimal):
        value = float(value)

    if isinstance(value, numbers.Real):
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            return None
        if isinstance(value, int):
            return int(value)
        return numeric_value

    return value
