"""Tests for var_es.data.quality and var_es.data.load.

Each test starts from a clean synthetic snapshot on real NYSE dates,
injects one specific defect, and checks that exactly that defect is flagged.
"""

import numpy as np
import pandas as pd
import pytest

from var_es.data.load import load_raw_snapshot
from var_es.data.quality import check_sample_fits, nyse_trading_days, quality_report

START, END = "2020-01-01", "2020-12-31"


@pytest.fixture(scope="module")
def trading_days():
    return nyse_trading_days(START, END)


def _build_snapshot(trading_days: pd.DatetimeIndex, div_cash: np.ndarray) -> pd.DataFrame:
    """Synthetic snapshot whose raw prices drop on ex-dividend days, like real data.

    The adjusted close follows a smooth random walk (the total-return path).
    The adjustment factor is then built backwards from the dividends, and the
    raw close is adjusted close / factor.
    """
    n = len(trading_days)
    rng = np.random.default_rng(42)
    adj_close = 300 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))

    factor = np.ones(n)
    for t in range(n - 1, 0, -1):
        factor[t - 1] = factor[t] / (1 + div_cash[t] * factor[t] / adj_close[t - 1])
    close = adj_close / factor

    open_ = close * (1 + rng.normal(0, 0.002, n))
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.004,
            "low": np.minimum(open_, close) * 0.996,
            "close": close,
            "volume": np.full(n, 5_000_000),
            "adj_close": adj_close,
            "div_cash": div_cash,
            "split_factor": np.ones(n),
        },
        index=pd.DatetimeIndex(trading_days, name="date"),
    )


def _quarterly_dividends(n: int, amount: float = 1.5) -> np.ndarray:
    div_cash = np.zeros(n)
    div_cash[[60, 120, 180, 240]] = amount
    return div_cash


@pytest.fixture
def clean(trading_days):
    """A defect-free snapshot with quarterly dividends applied consistently."""
    return _build_snapshot(trading_days, _quarterly_dividends(len(trading_days)))


def _checks(report: pd.DataFrame) -> set[str]:
    return set(report["check"])


# --- A clean snapshot produces no findings --------------------------------------------


def test_clean_snapshot_has_no_findings(clean, trading_days):
    report = quality_report(clean, trading_days=trading_days)
    assert report.empty, report.to_string()


def test_report_has_expected_columns(clean):
    assert list(quality_report(clean).columns) == ["date", "check", "severity", "detail"]


# --- Each defect is flagged --------------------------------------------------------------


def test_high_below_low_is_an_error(clean):
    day = clean.index[10]
    clean.loc[day, "high"] = clean.loc[day, "low"] - 1

    report = quality_report(clean)
    flagged = report[report["check"] == "ohlc_inconsistent"]
    assert day in set(flagged["date"])
    assert set(flagged["severity"]) == {"error"}


def test_low_above_close_is_an_error(clean):
    day = clean.index[20]
    clean.loc[day, "low"] = clean.loc[day, "close"] + 0.5
    clean.loc[day, "high"] = clean.loc[day, "low"] + 1

    report = quality_report(clean)
    assert "low is above open or close" in set(report["detail"])


def test_missing_value_is_an_error(clean):
    clean.loc[clean.index[5], "open"] = np.nan
    report = quality_report(clean)
    assert "missing_value" in _checks(report)


def test_non_positive_price_is_an_error(clean):
    clean.loc[clean.index[5], "low"] = 0.0
    report = quality_report(clean)
    assert "non_positive_price" in _checks(report)


def test_flat_bar_is_flagged_for_review(clean):
    day = clean.index[30]
    clean.loc[day, ["open", "high", "low"]] = clean.loc[day, "close"]

    report = quality_report(clean)
    flagged = report[report["check"] == "flat_bar"]
    assert list(flagged["date"]) == [day]
    assert list(flagged["severity"]) == ["review"]


def test_zero_volume_is_flagged_for_review(clean):
    clean.loc[clean.index[30], "volume"] = 0
    assert "zero_volume" in _checks(quality_report(clean))


def test_large_move_is_flagged_on_the_right_day(clean):
    day = clean.index[100]
    price_cols = ["open", "high", "low", "close", "adj_close"]
    clean.loc[day:, price_cols] *= 1.10  # adds a +9.5% jump to that day's return

    report = quality_report(clean)
    flagged = report[report["check"] == "large_move"]
    assert list(flagged["date"]) == [day]

    expected = np.log(clean["adj_close"]).diff().loc[day]
    assert f"{expected:+.2%}" in flagged["detail"].iloc[0]


def test_ex_dividend_drop_is_not_a_large_move(trading_days):
    # A huge dividend makes the raw close drop sharply, but the adjusted close doesn't.
    div_cash = _quarterly_dividends(len(trading_days))
    div_cash[60] = 40.0
    df = _build_snapshot(trading_days, div_cash)

    raw_drop = np.log(df["close"]).diff().iloc[60]
    assert raw_drop < -0.07, "the test needs a raw drop larger than the threshold"
    assert "large_move" not in _checks(quality_report(df))


def test_unexplained_adjustment_is_an_error(clean):
    day = clean.index[50]
    clean.loc[day, "adj_close"] *= 1.01  # factor changes with no dividend

    report = quality_report(clean)
    flagged = report[report["check"] == "adjustment_unexplained"]
    assert day in set(flagged["date"])


def test_dividend_without_adjustment_is_an_error(clean):
    day = clean.index[90]
    clean.loc[day, "div_cash"] = 1.5  # dividend recorded, but prices not adjusted

    report = quality_report(clean)
    flagged = report[report["check"] == "adjustment_missing"]
    assert list(flagged["date"]) == [day]


def test_missing_trading_day_is_an_error(clean, trading_days):
    day = clean.index[70]
    report = quality_report(clean.drop(index=day), trading_days=trading_days)

    flagged = report[report["check"] == "missing_trading_day"]
    assert list(flagged["date"]) == [day]


def test_weekend_date_is_an_error(clean, trading_days):
    saturday = pd.Timestamp("2020-06-13")
    extra = clean.iloc[[0]].set_axis(pd.DatetimeIndex([saturday], name="date"))
    df = pd.concat([clean, extra]).sort_index()

    report = quality_report(df, trading_days=trading_days)
    assert saturday in set(report.loc[report["check"] == "not_a_trading_day", "date"])


def test_duplicate_date_is_an_error(clean):
    df = pd.concat([clean, clean.iloc[[10]]]).sort_index()
    assert "duplicate_date" in _checks(quality_report(df))


# --- Exchange calendar -------------------------------------------------------------------


def test_nyse_calendar_excludes_holidays_and_special_closures():
    days = nyse_trading_days("2001-09-01", "2001-09-30")

    assert pd.Timestamp("2001-09-03") not in days  # Labor Day
    assert pd.Timestamp("2001-09-11") not in days  # market closed after 9/11
    assert pd.Timestamp("2001-09-17") in days  # reopened


# --- Sample periods -----------------------------------------------------------------------


def _split_cfg(window: int) -> dict:
    return {
        "sample_split": {
            "estimation_window": window,
            "development": ["2020-04-01", "2020-09-30"],
            "locked_test": ["2020-10-01", "2020-12-31"],
        }
    }


def test_sample_counts(clean):
    counts = check_sample_fits(clean.index, _split_cfg(window=50))

    assert counts["before_development"] == len(clean.loc[:"2020-03-31"])
    assert counts["development"] == len(clean.loc["2020-04-01":"2020-09-30"])
    assert counts["locked_test"] == len(clean.loc["2020-10-01":"2020-12-31"])


def test_estimation_window_too_long_raises(clean):
    with pytest.raises(ValueError, match="estimation window needs 100"):
        check_sample_fits(clean.index, _split_cfg(window=100))


# --- Loading the pinned snapshot ----------------------------------------------------------


def test_load_pinned_snapshot(tmp_path, clean):
    clean.to_parquet(tmp_path / "snap.parquet")
    df = load_raw_snapshot({"data": {"raw_snapshot": "snap.parquet"}}, tmp_path)
    pd.testing.assert_frame_equal(df, clean, check_freq=False)


def test_load_without_pinned_snapshot_raises(tmp_path):
    with pytest.raises(ValueError, match="No snapshot pinned"):
        load_raw_snapshot({"data": {"raw_snapshot": None}}, tmp_path)


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="run scripts/download_data.py"):
        load_raw_snapshot({"data": {"raw_snapshot": "gone.parquet"}}, tmp_path)