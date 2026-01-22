"""Module to read MEA layout files and parse layout information."""
import re
from datetime import datetime

import pandas as pd


def parse_group_name(name: str) -> dict:
    """
    Parse a group name into compound, dosage, and unit.
    """
    name = str(name).strip()
    parts = name.split()
    compound, dosage, unit = None, None, None

    pattern = re.compile(r"([\d\.]+)\s*([a-zA-Z/%µμ]*)")

    for i, part in enumerate(parts):
        match = pattern.fullmatch(part)
        if match:
            try:
                dosage = float(match.group(1))
            except ValueError:
                dosage = None

            unit = match.group(2)
            if not unit and i + 1 < len(parts):
                unit = parts[i + 1]

            compound = " ".join(parts[:i]) if i > 0 else None
            break

    if dosage is None:
        for i, part in enumerate(parts[::-1]):
            if part.replace(".", "", 1).isdigit():
                dosage = float(part)
                compound = " ".join(parts[:-(i + 1)]) if (i + 1) < len(parts) else None
                break

    if compound is None and dosage is None:
        compound = name

    return {"compound": compound if compound else None, "dosage": dosage, "unit": unit}


def read_layout(file_path: str) -> dict:
    """
    Read an Excel layout file and extract experiment metadata.

    Returns
    -------
    dict: {'date': str, 'wells': int, 'control_group': str, 'groups': list[dict]}
    """
    df = pd.read_excel(file_path, header=None)

    parsed_date = None
    date_label = str(df.iloc[0, 0]).strip().lower()
    if "date" in date_label:
        try:
            date_value = df.iloc[0, 1]
            if isinstance(date_value, str):
                parsed_date = datetime.strptime(date_value.strip(), "%d/%m/%Y").date()
            elif isinstance(date_value, pd.Timestamp):
                parsed_date = date_value.date()
        except Exception:
            parsed_date = None

    parsed_wells = None
    wells_label = str(df.iloc[1, 0]).strip().lower()
    if "well" in wells_label:
        try:
            wells_value = df.iloc[1, 1]
            parsed_wells = int(str(wells_value))
        except Exception:
            parsed_wells = None

    try:
        group_start = df.index[df.iloc[:, 0] == "Groups"][0] + 1
        groups_df = (
            df.iloc[group_start:]
            .dropna(how="all", axis=1)
            .dropna(subset=[0, 1])
        )
    except Exception:
        groups_df = pd.DataFrame()

    groups: list[dict] = []
    control_group = None
    first_group_name = None

    for i, (_, row) in enumerate(groups_df.iterrows()):
        group_name = str(row.iloc[0]).strip()
        wells_str = str(row.iloc[1]).strip()

        if i == 0:
            first_group_name = group_name

        parsed_group_name = parse_group_name(group_name)

        if control_group is None and any(
            keyword in group_name.lower() for keyword in ["control", "dmso"]
        ):
            control_group = group_name

        groups.append(
            {
                "name": group_name,
                "compound": parsed_group_name["compound"],
                "dosage": parsed_group_name["dosage"],
                "unit": parsed_group_name["unit"],
                "wells": wells_str,
            }
        )

    if control_group is None and first_group_name is not None:
        control_group = first_group_name

    return {
        "date": str(parsed_date) if parsed_date else None,
        "wells": parsed_wells,
        "control_group": control_group,
        "groups": groups,
    }
