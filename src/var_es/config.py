"""Load and validate the project configuration.

Convention: every risk level in the config is a *confidence level*
(for example 0.99). The tail probability used in calculations is
alpha = 1 - confidence_level.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

REQUIRED_SECTIONS = ("data", "sample_split", "risk", "returns")

# A VaR confidence level below 50% is almost certainly a tail probability
# (e.g. 0.01) entered by mistake, so treat it as an error.
MIN_CONFIDENCE_LEVEL = 0.5


def load_config(path: str | Path) -> dict[str, Any]:
    """Read a YAML config file, validate it, and return it as a dict.

    Raises FileNotFoundError if the file does not exist, and ValueError
    with a clear message if the contents fail validation.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    validate_config(cfg)
    return cfg


def validate_config(cfg: Any) -> None:
    """Raise ValueError if the config is incomplete or internally inconsistent.

    All problems are collected and reported together, so a broken config
    can be fixed in one pass instead of one error at a time.
    """
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a YAML mapping, but the file is empty or malformed.")

    missing = [s for s in REQUIRED_SECTIONS if not isinstance(cfg.get(s), dict)]
    if missing:
        raise ValueError(f"Config is missing required sections: {missing}")

    errors: list[str] = []
    data, split, risk, returns = (cfg[s] for s in REQUIRED_SECTIONS)

    # --- Dates and periods ---------------------------------------------------
    start = _parse_date(data.get("start"), "data.start", errors)
    end = _parse_date(data.get("end"), "data.end", errors)
    dev = _parse_period(split.get("development"), "sample_split.development", errors)
    test = _parse_period(split.get("locked_test"), "sample_split.locked_test", errors)

    if start is not None and end is not None and start >= end:
        errors.append(f"data.start ({start.date()}) must be before data.end ({end.date()}).")
    if dev is not None and test is not None and dev[1] >= test[0]:
        errors.append(
            "The development period must end before the locked test starts "
            f"(development ends {dev[1].date()}, locked test starts {test[0].date()})."
        )
    if dev is not None and start is not None and dev[0] < start:
        errors.append("sample_split.development starts before data.start.")
    if test is not None and end is not None and test[1] > end:
        errors.append("sample_split.locked_test ends after data.end.")

    # --- Integer settings ----------------------------------------------------
    _check_positive_int(split.get("estimation_window"), "sample_split.estimation_window", errors)
    _check_positive_int(risk.get("horizon_days"), "risk.horizon_days", errors)

    # --- Confidence levels ---------------------------------------------------
    for key in ("confidence_levels_var", "confidence_levels_es"):
        _check_confidence_levels(risk.get(key), f"risk.{key}", errors)

    # --- Return scaling ------------------------------------------------------
    scale = returns.get("scale")
    if not _is_number(scale) or scale <= 0:
        errors.append(f"returns.scale must be a positive number, got {scale!r}.")

    if errors:
        raise ValueError("Invalid config:\n  - " + "\n  - ".join(errors))


# --- Helpers -----------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for finite ints and floats. Booleans are excluded on purpose,
    because in Python True == 1 and would otherwise pass as a number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_date(value: Any, name: str, errors: list[str]) -> pd.Timestamp | None:
    """Convert a string or date to a Timestamp, or record an error and return None.

    Accepts both quoted YAML dates (strings) and unquoted ones, which
    PyYAML turns into datetime.date objects.
    """
    if value is None:
        errors.append(f"{name} is missing.")
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        errors.append(f"{name} is not a valid date: {value!r}.")
        return None
    if pd.isna(ts):
        errors.append(f"{name} is not a valid date: {value!r}.")
        return None
    return ts


def _parse_period(
    value: Any, name: str, errors: list[str]
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Parse a [start, end] pair, or record an error and return None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        errors.append(f"{name} must be a list of two dates [start, end], got {value!r}.")
        return None

    first = _parse_date(value[0], f"{name} start", errors)
    last = _parse_date(value[1], f"{name} end", errors)
    if first is None or last is None:
        return None
    if first >= last:
        errors.append(f"{name} start ({first.date()}) must be before its end ({last.date()}).")
        return None
    return first, last


def _check_positive_int(value: Any, name: str, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{name} must be a positive integer, got {value!r}.")


def _check_confidence_levels(value: Any, name: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{name} must be a non-empty list, got {value!r}.")
        return

    for level in value:
        if not _is_number(level) or not 0 < level < 1:
            errors.append(
                f"{name} contains {level!r}; confidence levels must be strictly between 0 and 1."
            )
        elif level < MIN_CONFIDENCE_LEVEL:
            errors.append(
                f"{name} contains {level!r}. Did you enter a tail probability "
                f"(e.g. 0.01) instead of a confidence level (e.g. 0.99)?"
            )