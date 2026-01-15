from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

import numpy as np


def sanitize_plotly_json(value: Any) -> Any:
    """
    Convert a Plotly figure dict into strict JSON-safe types.

    - Converts numpy scalars/arrays into Python scalars/lists.
    - Converts Decimal into float.
    - Converts datetime/date into ISO strings.
    - Replaces NaN/Infinity with None (JSON null).
    """
    if value is None:
        return None

    if isinstance(value, (str, bool)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, Decimal):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None

    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()

    if isinstance(value, np.generic):
        return sanitize_plotly_json(value.item())

    if isinstance(value, np.ndarray):
        return sanitize_plotly_json(value.tolist())

    if isinstance(value, dict):
        return {str(key): sanitize_plotly_json(val) for key, val in value.items()}

    if isinstance(value, (list, tuple)):
        return [sanitize_plotly_json(item) for item in value]

    raise TypeError(f"Unsupported type in Plotly JSON: {type(value)!r}")


def strip_template(figure: dict[str, Any]) -> dict[str, Any]:
    layout = figure.get("layout")
    if isinstance(layout, dict):
        layout.pop("template", None)
    return figure


def enforce_responsive_layout(figure: dict[str, Any]) -> dict[str, Any]:
    layout = figure.get("layout")
    if not isinstance(layout, dict):
        return figure
    layout["autosize"] = True
    layout.pop("width", None)
    layout.pop("height", None)
    return figure
