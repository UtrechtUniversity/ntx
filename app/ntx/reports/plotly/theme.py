from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

DEFAULT_PLOTLY_CONFIG: dict[str, Any] = {
    "responsive": True,
    "displayModeBar": False,
    "displaylogo": False,
}


def apply_theme(fig: go.Figure) -> None:
    fig.update_layout(
        template=None,
        autosize=True,
        font={
            "family": (
                "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "
                "Apple Color Emoji, Segoe UI Emoji"
            ),
            "size": 12,
            "color": "#0f172a",
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 60, "r": 20, "t": 20, "b": 80},
        hoverlabel={"bgcolor": "#ffffff", "font": {"size": 12, "color": "#0f172a"}},
        colorway=[
            "#2563eb",
            "#16a34a",
            "#f97316",
            "#a855f7",
            "#dc2626",
            "#0ea5e9",
            "#84cc16",
            "#f59e0b",
        ],
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.35)",
        zeroline=False,
        automargin=True,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, automargin=True)
