"""Tests for var_es.data.returns."""

import numpy as np
import pandas as pd
import pytest

from var_es.data.returns import (
    build_returns_and_proxies,
    garman_klass_variance,
    log_returns,
    overnight_log_returns,
    parkinson_variance,
)


def _series(values):
    return pd.Series(values, dtype=float)


def _snapshot(n: int = 300, div_day: int = 150, div_amount: float = 3.0) -> pd.DataFrame:
    """Raw OHLC with one dividend; adjusted OHLC = raw x one factor per day."""
    rng = np.random.default_rng(7)
    adj_close = 400 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))

    div_cash = np.zeros(n)
    div_cash[div_day] = div_amount
    factor = np.ones(n)
    for t in range(n - 1, 0, -1):
        factor[t - 1] = factor[t] / (1 + div_cash[t] * factor[t] / adj_close[t - 1])

    close = adj_close / factor
    open_ = close * (1 + rng.normal(0, 0.003, n))
    open_[div_day] = close[div_day - 1] - div_amount  # opens lower by the dividend
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "div_cash": div_cash},
        index=pd.bdate_range("2020-01-01", periods=n, name="date"),
    )
    for raw in ("open", "high", "low", "close"):
        df[f"adj_{raw}"] = df[raw] * factor
    return df


# --- Formulas against hand-computed values ---------------------------------------------


def test_log_returns():
    result = log_returns(_series([100, 110, 99]))

    assert np.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(9.531018, abs=1e-6)  # 100 ln(1.1)
    assert result.iloc[2] == pytest.approx(-10.536052, abs=1e-6)  # 100 ln(0.9)


def test_parkinson_variance():
    result = parkinson_variance(_series([110]), _series([100]))
    assert result.iloc[0] == pytest.approx(32.763714, abs=1e-6)


def test_garman_klass_variance():
    result = garman_klass_variance(_series([100]), _series([110]), _series([95]), _series([105]))
    assert result.iloc[0] == pytest.approx(98.267233, abs=1e-6)


def test_overnight_log_returns():
    result = overnight_log_returns(open_=_series([99, 102]), close=_series([100, 104]))
    assert result.iloc[1] == pytest.approx(100 * np.log(102 / 100))


def test_scale_changes_units():
    prices = _series([100, 110])
    assert log_returns(prices, scale=1.0).iloc[1] == pytest.approx(np.log(1.1))


# --- Properties ----------------------------------------------------------------------------


def test_garman_klass_is_never_negative_for_consistent_bars():
    rng = np.random.default_rng(0)
    open_ = _series(100 * np.exp(rng.normal(0, 0.02, 10_000)))
    close = _series(100 * np.exp(rng.normal(0, 0.02, 10_000)))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, 10_000))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, 10_000))

    assert (garman_klass_variance(open_, high, low, close) >= 0).all()


def test_intraday_proxies_are_identical_for_raw_and_adjusted_prices():
    # Your Step 1.5 answer, written as a test: the same-day factor cancels.
    df = _snapshot()
    raw = garman_klass_variance(df["open"], df["high"], df["low"], df["close"])
    adj = garman_klass_variance(df["adj_open"], df["adj_high"], df["adj_low"], df["adj_close"])

    np.testing.assert_allclose(raw, adj, rtol=1e-10)
    np.testing.assert_allclose(
        parkinson_variance(df["high"], df["low"]),
        parkinson_variance(df["adj_high"], df["adj_low"]),
        rtol=1e-10,
    )


# --- The processed dataset -----------------------------------------------------------------


def test_build_has_expected_columns_and_drops_first_row():
    df = _snapshot()
    out = build_returns_and_proxies(df)

    expected = ["ret", "overnight_ret", "sq_ret", "parkinson", "garman_klass", "gk_overnight"]
    assert list(out.columns) == expected
    assert len(out) == len(df) - 1
    assert not out.isna().any().any()


def test_ex_dividend_day_has_no_artificial_loss():
    df = _snapshot(div_day=150, div_amount=12.0)  # ~3% dividend, to make it visible
    ex_day = df.index[150]
    out = build_returns_and_proxies(df)

    raw_ret = 100 * np.log(df["close"] / df["close"].shift(1)).loc[ex_day]
    raw_overnight = 100 * np.log(df["open"] / df["close"].shift(1)).loc[ex_day]

    assert raw_ret - out.loc[ex_day, "ret"] < -2  # raw return shows a fake ~3% loss
    assert raw_overnight < -2  # so does the raw overnight return
    assert abs(out.loc[ex_day, "overnight_ret"]) < 0.5  # adjusted overnight return doesn't


def test_gk_overnight_adds_the_squared_overnight_return():
    out = build_returns_and_proxies(_snapshot())
    np.testing.assert_allclose(
        out["gk_overnight"], out["garman_klass"] + out["overnight_ret"] ** 2
    )


def test_inconsistent_adjustment_factors_are_rejected():
    df = _snapshot()
    df.loc[df.index[40], "adj_high"] *= 1.01  # high adjusted differently from the close

    with pytest.raises(ValueError, match="different adjustment factors"):
        build_returns_and_proxies(df)


def test_missing_adjusted_columns_are_rejected():
    df = _snapshot().drop(columns="adj_open")

    with pytest.raises(ValueError, match="missing columns"):
        build_returns_and_proxies(df)