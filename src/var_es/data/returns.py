"""Daily returns and variance proxies built from the raw OHLC snapshot.

Everything uses dividend-adjusted prices, so returns are total returns and
ex-dividend days do not create artificial losses. Within a single day, the
adjustment factor cancels, so the intraday range estimators are identical
whether computed from raw or adjusted prices. The overnight term spans two
days, which is why adjusted prices are required for it.

All outputs are scaled: returns in percent (scale=100) and variances in
percent squared (scale**2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ADJUSTED_OHLC = ("adj_open", "adj_high", "adj_low", "adj_close")
RAW_OHLC = ("open", "high", "low", "close")

# 2 ln 2 - 1, the weight on the open-to-close term in Garman-Klass.
_GK_OPEN_CLOSE_WEIGHT = 2 * np.log(2) - 1


def log_returns(prices: pd.Series, scale: float = 100.0) -> pd.Series:
    """scale * ln(P_t / P_{t-1}). The first value is NaN."""
    return scale * np.log(prices / prices.shift(1))


def parkinson_variance(high: pd.Series, low: pd.Series, scale: float = 100.0) -> pd.Series:
    """Parkinson (1980) range-based variance: [scale * ln(H/L)]^2 / (4 ln 2)."""
    hl = scale * np.log(high / low)
    return hl**2 / (4 * np.log(2))


def garman_klass_variance(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, scale: float = 100.0
) -> pd.Series:
    """Garman-Klass (1980): 0.5 [ln(H/L)]^2 - (2 ln 2 - 1) [ln(C/O)]^2, scaled.

    Uses only prices from within the trading day, so it omits the overnight move.
    It is never negative when the high and low contain the open and close.
    """
    hl = scale * np.log(high / low)
    co = scale * np.log(close / open_)
    return 0.5 * hl**2 - _GK_OPEN_CLOSE_WEIGHT * co**2


def overnight_log_returns(open_: pd.Series, close: pd.Series, scale: float = 100.0) -> pd.Series:
    """scale * ln(O_t / C_{t-1}): the move from yesterday's close to today's open."""
    return scale * np.log(open_ / close.shift(1))


def build_returns_and_proxies(
    df: pd.DataFrame, scale: float = 100.0, factor_tolerance: float = 1e-6
) -> pd.DataFrame:
    """Build the processed dataset of returns and variance proxies.

    Columns:
        ret             daily log total return (percent)
        overnight_ret   close-to-open log return (percent)
        sq_ret          squared daily return (percent squared)
        parkinson       Parkinson variance (intraday only)
        garman_klass    Garman-Klass variance (intraday only)
        gk_overnight    Garman-Klass plus squared overnight return, often
                        called Garman-Klass-Yang-Zhang

    The first row is dropped because it has no previous close.
    Raises ValueError if the adjusted open, high, low and close do not share
    one adjustment factor per day, since the proxies rely on that.
    """
    _check_common_adjustment_factor(df, factor_tolerance)
    o, h, l, c = (df[col] for col in ADJUSTED_OHLC)

    out = pd.DataFrame(index=df.index)
    out["ret"] = log_returns(c, scale)
    out["overnight_ret"] = overnight_log_returns(o, c, scale)
    out["sq_ret"] = out["ret"] ** 2
    out["parkinson"] = parkinson_variance(h, l, scale)
    out["garman_klass"] = garman_klass_variance(o, h, l, c, scale)
    out["gk_overnight"] = out["overnight_ret"] ** 2 + out["garman_klass"]

    return out.iloc[1:]


def _check_common_adjustment_factor(df: pd.DataFrame, tolerance: float) -> None:
    missing = [col for col in ADJUSTED_OHLC + RAW_OHLC if col not in df.columns]
    if missing:
        raise ValueError(f"Snapshot is missing columns: {missing}")

    factors = pd.DataFrame(
        {raw: df[adj] / df[raw] for adj, raw in zip(ADJUSTED_OHLC, RAW_OHLC)}
    )
    relative_spread = (factors.max(axis=1) - factors.min(axis=1)) / factors["close"]
    bad_days = df.index[relative_spread > tolerance]
    if len(bad_days) > 0:
        raise ValueError(
            f"Adjusted open/high/low/close use different adjustment factors on "
            f"{len(bad_days)} day(s), first on {bad_days[0].date()}. The range-based "
            "proxies assume one factor per day."
        )