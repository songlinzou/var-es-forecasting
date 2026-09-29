"""Data-quality checks for the raw OHLC snapshot.

These checks flag problems; they never fix them. Every finding has a severity:

- "error":  the data contradicts itself (e.g. high below low) and must be
            resolved (corrected, dropped, or explicitly justified) before modelling.
- "review": the data is plausible but unusual (e.g. a very large daily move)
            and should be checked against known market events.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")
REPORT_COLUMNS = ["date", "check", "severity", "detail"]

Finding = tuple[pd.Timestamp, str, str, str]


def quality_report(
    df: pd.DataFrame,
    *,
    trading_days: pd.DatetimeIndex | None = None,
    large_move_threshold: float = 0.07,
    adjustment_tolerance: float = 1e-5,
) -> pd.DataFrame:
    """Run all checks and return one row per finding, sorted by date.

    Args:
        df: snapshot with a DatetimeIndex and at least open/high/low/close.
        trading_days: the official exchange calendar for the sample. If given,
            the snapshot's dates are compared against it.
        large_move_threshold: absolute daily log return above which a day is
            flagged for review (0.07 = 7%).
        adjustment_tolerance: relative change in the adjustment factor that
            counts as a real change rather than floating-point noise.
    """
    findings: list[Finding] = []
    findings += _check_duplicate_dates(df)
    findings += _check_missing_values(df)
    findings += _check_non_positive_prices(df)
    findings += _check_ohlc_consistency(df)
    findings += _check_flat_bars(df)
    findings += _check_zero_volume(df)
    findings += _check_large_moves(df, large_move_threshold)
    findings += _check_adjustments(df, adjustment_tolerance)
    if trading_days is not None:
        findings += _check_calendar(df.index, trading_days)

    report = pd.DataFrame(findings, columns=REPORT_COLUMNS)
    return report.sort_values(["date", "check"], kind="stable").reset_index(drop=True)


def nyse_trading_days(start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DatetimeIndex:
    """Official NYSE trading days between start and end, inclusive."""
    import pandas_market_calendars as mcal  # imported here so the checks work without it

    schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return pd.DatetimeIndex(schedule.index).normalize()


def check_sample_fits(dates: pd.DatetimeIndex, cfg: dict) -> dict[str, int]:
    """Count trading days in each sample period and check the estimation window fits.

    Raises ValueError if fewer trading days precede the development period
    than the estimation window needs, or if a period contains no data.
    """
    split = cfg["sample_split"]
    dev_start, dev_end = (pd.Timestamp(d) for d in split["development"])
    test_start, test_end = (pd.Timestamp(d) for d in split["locked_test"])
    window = split["estimation_window"]

    counts = {
        "before_development": int((dates < dev_start).sum()),
        "development": int(((dates >= dev_start) & (dates <= dev_end)).sum()),
        "locked_test": int(((dates >= test_start) & (dates <= test_end)).sum()),
    }

    if counts["before_development"] < window:
        raise ValueError(
            f"Only {counts['before_development']} trading days precede the development "
            f"period, but the estimation window needs {window}."
        )
    empty = [name for name in ("development", "locked_test") if counts[name] == 0]
    if empty:
        raise ValueError(f"No data in sample period(s): {empty}")
    return counts


# --- Individual checks -------------------------------------------------------------------


def _check_duplicate_dates(df: pd.DataFrame) -> list[Finding]:
    duplicated = df.index[df.index.duplicated(keep="first")]
    return [(d, "duplicate_date", "error", "date appears more than once") for d in duplicated]


def _check_missing_values(df: pd.DataFrame) -> list[Finding]:
    findings = []
    for col in PRICE_COLUMNS:
        for d in df.index[df[col].isna()]:
            findings.append((d, "missing_value", "error", f"{col} is missing"))
    return findings


def _check_non_positive_prices(df: pd.DataFrame) -> list[Finding]:
    findings = []
    for col in PRICE_COLUMNS:
        for d in df.index[df[col] <= 0]:
            findings.append((d, "non_positive_price", "error", f"{col} is zero or negative"))
    return findings


def _check_ohlc_consistency(df: pd.DataFrame) -> list[Finding]:
    o, h, l, c = (df[col] for col in PRICE_COLUMNS)
    rules = [
        (h < l, "high is below low"),
        (h < np.maximum(o, c), "high is below open or close"),
        (l > np.minimum(o, c), "low is above open or close"),
    ]
    return [
        (d, "ohlc_inconsistent", "error", message)
        for mask, message in rules
        for d in df.index[mask.fillna(False)]
    ]


def _check_flat_bars(df: pd.DataFrame) -> list[Finding]:
    o, h, l, c = (df[col] for col in PRICE_COLUMNS)
    flat = (o == h) & (h == l) & (l == c)
    detail = "open, high, low and close are identical (possible placeholder data)"
    return [(d, "flat_bar", "review", detail) for d in df.index[flat]]


def _check_zero_volume(df: pd.DataFrame) -> list[Finding]:
    if "volume" not in df.columns:
        return []
    return [(d, "zero_volume", "review", "volume is zero") for d in df.index[df["volume"] == 0]]


def _check_large_moves(df: pd.DataFrame, threshold: float) -> list[Finding]:
    # Use adjusted prices if available, so ex-dividend drops don't count as moves.
    prices = df["adj_close"] if "adj_close" in df.columns else df["close"]
    log_returns = np.log(prices).diff()
    large = log_returns.abs() > threshold
    return [
        (d, "large_move", "review", f"daily log return of {log_returns[d]:+.2%}")
        for d in df.index[large.fillna(False)]
    ]


def _check_adjustments(df: pd.DataFrame, tolerance: float) -> list[Finding]:
    """The adjustment factor (adj_close / close) should change only on days
    with a dividend or split, and should change on every such day."""
    needed = {"adj_close", "div_cash", "split_factor"}
    if not needed <= set(df.columns):
        return []

    factor = df["adj_close"] / df["close"]
    factor_changed = (factor / factor.shift(1) - 1).abs() > tolerance
    corporate_action = (df["div_cash"] > 0) | (df["split_factor"] != 1)

    # The first row has no previous day to compare with, so it is skipped.
    comparable = factor.shift(1).notna()
    unexplained = factor_changed & ~corporate_action & comparable
    unapplied = corporate_action & ~factor_changed & comparable

    return [
        (d, "adjustment_unexplained", "error", "adjustment factor changes with no dividend or split")
        for d in df.index[unexplained]
    ] + [
        (d, "adjustment_missing", "error", "dividend or split with no change in adjustment factor")
        for d in df.index[unapplied]
    ]


def _check_calendar(dates: pd.DatetimeIndex, trading_days: pd.DatetimeIndex) -> list[Finding]:
    dates = pd.DatetimeIndex(dates).unique()
    missing = trading_days.difference(dates)
    extra = dates.difference(trading_days)
    return [
        (d, "missing_trading_day", "error", "exchange was open but the date is missing")
        for d in missing
    ] + [(d, "not_a_trading_day", "error", "date is not an exchange trading day") for d in extra]