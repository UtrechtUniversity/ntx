from __future__ import annotations

from html import escape


def escape_plot_text(value: str) -> str:
    """
    Escape user-provided strings before putting them into Plotly text fields.

    Plotly supports HTML-like rendering in some contexts; this keeps labels and
    titles text-only.
    """

    return escape(value, quote=False)
