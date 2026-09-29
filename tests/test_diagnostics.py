"""Tests for var_es.diagnostics, using simulated data where the truth is known."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from var_es.diagnostics import (
    arch_lm,
    autocorrelations,
    distribution_summary,
    leverage_summary,
    ljung_box,
    robust_autocorrelation,
    tail_counts,
)

N = 5_000


def _iid_normal(seed: int = 1) -> pd.Series:
    return pd.Series(np.random.default_rng(seed).normal(0, 1, N))


def _garch(alpha: float = 0.08, gamma: float = 0.0, seed: int = 2) -> tuple[pd.Series, pd.Series]:
    """Simulate GJR-GARCH(1,1) returns; gamma=0 gives symmetric GARCH(1,1).

    Returns (returns, conditional variance). The variance for day t is known
    at the end of day t-1, so variance.shift(-1) is "tomorrow's variance".
    Persistence alpha + gamma/2 + beta must stay below 1 for stationarity.
    """
    omega, beta = 0.02, 0.90
    assert alpha + gamma / 2 + beta < 1, "non-stationary parameters"
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 1, N)
    var = np.empty(N)
    ret = np.empty(N)
    var[0] = omega / (1 - alpha - beta - gamma / 2)
    for t in range(N):
        if t > 0:
            shock = ret[t - 1] ** 2
            var[t] = omega + (alpha + gamma * (ret[t - 1] < 0)) * shock + beta * var[t - 1]
        ret[t] = np.sqrt(var[t]) * z[t]
    return pd.Series(ret), pd.Series(var)


# --- IID normal data: nothing should be detected ---------------------------------------------


def test_iid_normal_shows_no_dependence():
    x = _iid_normal()

    assert (ljung_box(x)["p_value"] > 0.01).all()
    assert (ljung_box(x**2)["p_value"] > 0.01).all()
    assert arch_lm(x)["p_value"] > 0.01
    assert (robust_autocorrelation(x)["robust_t"].abs() < 3).all()


def test_iid_normal_has_normal_tails():
    x = _iid_normal()
    summary = distribution_summary(x)

    assert abs(summary["excess_kurtosis"]) < 0.3
    assert summary["jarque_bera_p"] > 0.01
    assert 0.6 < tail_counts(x).loc[3, "ratio"] < 1.5


# --- GARCH data: clustering and fat tails should be detected ----------------------------------


def test_garch_shows_volatility_clustering():
    ret, _ = _garch()

    assert arch_lm(ret)["p_value"] < 1e-6
    assert (ljung_box(ret**2)["p_value"] < 1e-6).all()
    assert autocorrelations(ret**2).loc[1] > 0.05


def test_garch_has_fat_tails():
    ret, _ = _garch()
    summary = distribution_summary(ret)

    assert summary["excess_kurtosis"] > 0.3
    assert summary["jarque_bera_p"] < 1e-6


# --- Predictable returns ------------------------------------------------------------------


def test_robust_autocorrelation_detects_ar1():
    e = np.random.default_rng(3).normal(0, 1, N)
    x = np.empty(N)
    x[0] = e[0]
    for t in range(1, N):
        x[t] = 0.3 * x[t - 1] + e[t]

    result = robust_autocorrelation(pd.Series(x), lags=(1,))
    assert result.loc[1, "coefficient"] == pytest.approx(0.3, abs=0.05)
    assert result.loc[1, "robust_t"] > 10


# --- Leverage effect ------------------------------------------------------------------------


def test_leverage_uses_the_next_days_variance():
    ret = pd.Series([-1.0, 1.0, -1.0, 1.0, 0.0])
    proxy = pd.Series([0.0, 5.0, 1.0, 9.0, 1.0])
    table = leverage_summary(ret, proxy, size_threshold=0.5)

    row = table.loc["all days"]
    assert row["next_var_after_down"] == 7.0  # days after the down days: 5 and 9
    assert row["next_var_after_up"] == 1.0  # days after the up days: 1 and 1
    assert row["ratio"] == 7.0


def test_asymmetric_garch_shows_leverage_and_symmetric_does_not():
    # Both processes have the same persistence (0.98); only the asymmetry differs.
    ret, var = _garch(alpha=0.0, gamma=0.16)
    table = leverage_summary(ret, var)
    assert table.loc["all days", "ratio"] > 1.1
    assert table.loc["moves larger than 1", "ratio"] > 1.2

    ret, var = _garch(alpha=0.08, gamma=0.0)
    assert 0.93 < leverage_summary(ret, var).loc["all days", "ratio"] < 1.07


# --- Formulas -------------------------------------------------------------------------------


def test_tail_counts_expected_values():
    x = _iid_normal()
    expected = N * 2 * stats.norm.sf(4)
    assert tail_counts(x).loc[4, "expected_if_normal"] == pytest.approx(expected)


def test_mean_t_stat():
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    summary = distribution_summary(x)
    assert summary["mean_t_stat"] == pytest.approx(2.5 / (x.std(ddof=1) / 2))