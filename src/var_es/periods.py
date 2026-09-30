"""The two evaluation periods and the rules for using them.

development   Forecasts for the development period; returns from the locked
              test period are never loaded.
locked_test   The one-time evaluation. Forecasts for the locked test period;
              each forecast still uses only returns before its own date, but
              the returns file now extends to the end of the test period.

The locked test is meant to be run once, with settings fixed in advance. The
forecast scripts therefore refuse to overwrite locked-test outputs unless
explicitly told to (see guard_locked_test).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PERIODS = ("development", "locked_test")
LABELS = {"development": "development period", "locked_test": "locked test period"}


def check_period(period: str) -> str:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {period!r}")
    return period


def period_bounds(cfg: dict, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last forecast date of the period, from the config."""
    key = {"development": "development", "locked_test": "locked_test"}[check_period(period)]
    start, end = cfg["sample_split"][key]
    return pd.Timestamp(start), pd.Timestamp(end)


def returns_for_period(returns: pd.Series, cfg: dict, period: str) -> pd.Series:
    """The returns a forecasting run for this period is allowed to load."""
    test_start, test_end = period_bounds(cfg, "locked_test")
    if check_period(period) == "development":
        return returns[returns.index < test_start]
    return returns[returns.index <= test_end]


def period_note(period: str) -> str:
    if check_period(period) == "development":
        return "The locked test period is not used."
    return (
        "This is the one-time evaluation on the locked test period, with every setting "
        "fixed during development."
    )


def guard_locked_test(period: str, outputs: list[Path], allow_rerun: bool) -> None:
    """Refuse to overwrite locked-test outputs unless allow_rerun is set."""
    if check_period(period) != "locked_test" or allow_rerun:
        return
    existing = [str(p) for p in outputs if Path(p).exists()]
    if existing:
        raise SystemExit(
            "Locked-test outputs already exist:\n  " + "\n  ".join(existing) + "\n"
            "The locked test is meant to be run once. Rerunning it after seeing the results, "
            "for example with changed settings, would turn it into another development period. "
            "If you are rerunning with identical settings (e.g. after a crash), pass "
            "--allow-rerun and note it in the README."
        )