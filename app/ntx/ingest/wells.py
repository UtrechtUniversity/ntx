"""Well string parsing and validation utilities."""

from __future__ import annotations

import re
from typing import Iterable

WELL_RE = re.compile(r"^[A-Za-z](\d+)$")


def parse_well_string(well_string: str) -> list[str]:
    """
    Parse a well string into individual well names.

    Supports:
    - Single wells: ``A1``
    - Space-separated wells: ``A1 A2 B3``
    - Ranges: ``A1-A4`` -> ``["A1", "A2", "A3", "A4"]``
    - 2D ranges: ``A1-B4`` -> all wells from A1 through B4
    """
    wells: list[str] = []
    for part in well_string.replace(",", " ").split():
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            wells.extend(_expand_range(token))
        else:
            wells.append(_normalize_well(token))
    return wells


def _normalize_well(token: str) -> str:
    match = WELL_RE.match(token)
    if not match:
        raise ValueError(f"Invalid well token '{token}'")
    row = token[0].upper()
    col = int(match.group(1))
    if col <= 0:
        raise ValueError(f"Well number must be positive in '{token}'")
    return f"{row}{col}"


def _expand_range(range_str: str) -> list[str]:
    """
    Expand a well range like ``A1-A4`` or ``A1-B4``.
    """
    if range_str.count("-") != 1:
        raise ValueError(f"Invalid range '{range_str}'")

    start_token, end_token = range_str.split("-", 1)
    start = _normalize_well(start_token)
    end = _normalize_well(end_token)

    start_row, start_col = start[0], int(start[1:])
    end_row, end_col = end[0], int(end[1:])

    if (start_row > end_row) or (start_row == end_row and start_col > end_col):
        raise ValueError(f"Range start must precede end in '{range_str}'")
    if end_col < start_col:
        raise ValueError(f"Range columns must increase in '{range_str}'")

    rows: Iterable[int]
    rows = range(ord(start_row), ord(end_row) + 1)
    cols: Iterable[int]
    cols = range(start_col, end_col + 1)

    wells: list[str] = []
    for row_code in rows:
        for col in cols:
            wells.append(f"{chr(row_code)}{col}")
    return wells
