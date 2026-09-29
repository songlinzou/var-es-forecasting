"""Small helpers for writing Markdown reports."""

from __future__ import annotations

import numpy as np
import pandas as pd


def fmt(value) -> str:
    """Format a number for a report table; other values are converted with str()."""
    if isinstance(value, (bool, np.bool_)):
        return str(value)
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, (float, np.floating)):
        if np.isnan(value):
            return "n/a"
        if value == 0:
            return "0"
        if abs(value) < 1e-4:
            return f"{value:.1e}"
        return f"{value:,.4f}" if abs(value) < 10 else f"{value:,.1f}"
    return str(value)


def md_table(df: pd.DataFrame, index_label: str = "") -> str:
    """Render a DataFrame as a Markdown table."""
    header = "| " + " | ".join([index_label] + [str(c) for c in df.columns]) + " |"
    divider = "|" + "---|" * (len(df.columns) + 1)
    rows = [
        "| " + " | ".join([str(idx)] + [fmt(v) for v in row]) + " |"
        for idx, row in zip(df.index, df.itertuples(index=False))
    ]
    return "\n".join([header, divider] + rows)