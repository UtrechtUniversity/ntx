from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

from .sanitize import enforce_responsive_layout, sanitize_plotly_json, strip_template


def serialize_figure(fig: go.Figure) -> dict[str, Any]:
    raw = fig.to_plotly_json()
    if not isinstance(raw, dict):
        raise TypeError("Plotly Figure serialization did not return a dict")

    strip_template(raw)
    enforce_responsive_layout(raw)

    sanitized = sanitize_plotly_json(raw)
    if not isinstance(sanitized, dict):
        raise TypeError("Sanitized Plotly figure is not a dict")

    data = sanitized.get("data")
    layout = sanitized.get("layout")
    frames = sanitized.get("frames")

    if not isinstance(data, list) or not isinstance(layout, dict):
        raise TypeError("Sanitized Plotly figure missing required keys: data/layout")

    result: dict[str, Any] = {"data": data, "layout": layout}
    if isinstance(frames, list) and frames:
        result["frames"] = frames
    return result
