"""Tests for the GJR-GARCH-MIDAS model in var_es.models.midas."""

import numpy as np
import pandas as pd
import pytest

from var_es.models.distributions import SkewT
from var_es.models.garch import GarchSpec, backcast, fit_garch
from var_es.models.garch import log_likelihood_terms as gjr_terms
from var_es.models.midas import (
    MidasSpec,
    beta_weights,
    components,
    daily_macro,
    fit_midas,
    log_likelihood_terms,
    one_step_variance,
)

K = 12


def simulate_midas(n_months=240, theta=0.5, w=4.0, alpha=0.0, gamma=0.15, beta=0.88,
                   m0=0.0, mu=0.03, shape=(7.0, -0.15), rho=0.95, seed=0):
    """GJR-GARCH-MIDAS data with a persistent monthly AR(1) macro variable.

    Returns (daily returns, monthly lag table in the format of available_lags).
    """
    rng = np.random.default_rng(seed)
    n_total = n_months + K
    x = np.zeros(n_total)
    for t in range(1, n_total):
        x[t] = rho * x[t - 1] + rng.normal(0, np.sqrt(1 - rho**2))
    months = pd.period_range("1998-01", periods=n_total, freq="M")[K:]
    lag_rows = np.array([x[i - K : i][::-1] for i in range(K, n_total)])  # X_{m-1}, ..., X_{m-K}
    lags = pd.DataFrame(lag_rows, index=pd.PeriodIndex(months, name="month"),
                        columns=[f"lag_{k}" for k in range(1, K + 1)])

    dates = pd.bdate_range(months[0].start_time, months[-1].end_time.normalize())
    month_pos = np.asarray(months.get_indexer(dates.to_period("M")))
    tau = np.exp(m0 + theta * lag_rows @ beta_weights(w, K))[month_pos]

    z = SkewT.ppf(rng.uniform(size=len(dates)), list(shape))
    persistence = alpha + gamma / 2 + beta
    g, eps = 1.0, np.empty(len(dates))
    for t in range(len(dates)):
        if t > 0:
            neg = eps[t - 1] < 0
            g = (1 - persistence) + (alpha + gamma * neg) * eps[t - 1] ** 2 / tau[t - 1] + beta * g
        eps[t] = np.sqrt(tau[t] * g) * z[t]
    return pd.Series(mu + eps, index=pd.DatetimeIndex(dates, name="date")), lags


@pytest.fixture(scope="module")
def with_macro():
    return simulate_midas(n_months=360, theta=0.5, seed=0)


@pytest.fixture(scope="module")
def no_macro():
    return simulate_midas(n_months=360, theta=0.0, seed=2)


# --- Building blocks ---------------------------------------------------------------------------


def test_beta_weights():
    flat = beta_weights(1.0, K)
    np.testing.assert_allclose(flat, np.full(K, 1 / K))
    declining = beta_weights(5.0, K)
    assert declining.sum() == pytest.approx(1.0)
    assert np.all(np.diff(declining) < 0)  # more weight on recent months


def test_zero_macro_effect_reproduces_gjr_garch_exactly(no_macro):
    returns, lags = no_macro
    arr = returns.to_numpy()
    x = daily_macro(returns.index, lags)
    start = backcast(arr - arr.mean())

    omega, alpha, gamma, beta = 0.02, 0.01, 0.14, 0.88
    tau = omega / (1 - (alpha + gamma / 2 + beta))
    gjr = gjr_terms(np.array([0.03, omega, alpha, gamma, beta, 7.0, -0.15]), arr,
                    GarchSpec(asymmetric=True, dist="skewt"), start)
    midas = log_likelihood_terms(np.array([0.03, alpha, gamma, beta, np.log(tau), 0.0, 3.0, 7.0, -0.15]),
                                 arr, x, MidasSpec("skewt"), start)
    np.testing.assert_allclose(midas, gjr, rtol=1e-12)


def test_fast_recursion_matches_plain_loop(with_macro):
    returns, lags = with_macro
    arr, x = returns.to_numpy()[:500], daily_macro(returns.index[:500], lags)
    p = {"mu": 0.03, "alpha": 0.02, "gamma": 0.12, "beta": 0.87, "m0": 0.1, "theta": 0.4, "w": 3.0}
    start = backcast(arr - arr.mean())
    tau, g = components(p, arr, x, start)

    persistence = p["alpha"] + p["gamma"] / 2 + p["beta"]
    resid = arr - p["mu"]
    expected = np.empty(len(arr))
    expected[0] = (1 - persistence) + (p["alpha"] + p["gamma"] / 2) * start / tau[0] + p["beta"] * start / tau[0]
    for t in range(1, len(arr)):
        neg = resid[t - 1] < 0
        expected[t] = ((1 - persistence) + (p["alpha"] + p["gamma"] * neg) * resid[t - 1] ** 2 / tau[t - 1]
                       + p["beta"] * expected[t - 1])
    np.testing.assert_allclose(g, expected, rtol=1e-12)


# --- Estimation ---------------------------------------------------------------------------------


def test_recovers_the_macro_effect(with_macro):
    fit = fit_midas(*with_macro, MidasSpec("skewt"))

    assert fit.converged
    assert abs(fit.params["theta"] - 0.5) < 3 * fit.std_errors["theta"]
    assert fit.params["theta"] / fit.std_errors["theta"] > 5  # clearly detected
    assert fit.params["gamma"] == pytest.approx(0.15, abs=0.05)
    assert fit.params["lambda"] == pytest.approx(-0.15, abs=0.05)


def test_finds_no_macro_effect_when_there_is_none(no_macro):
    fit = fit_midas(*no_macro, MidasSpec("skewt"))
    assert abs(fit.params["theta"] / fit.std_errors["theta"]) < 3


def test_nested_model_fits_at_least_as_well_as_gjr(no_macro):
    returns, lags = no_macro
    midas = fit_midas(returns, lags, MidasSpec("skewt"), std_errors=False)
    gjr = fit_garch(returns, GarchSpec(asymmetric=True, dist="skewt"), std_errors=False)
    assert midas.loglik >= gjr.loglik - 1e-3


def test_forecast_equals_the_next_step(with_macro):
    returns, lags = with_macro
    r, n = returns.iloc[:800], 800
    fit = fit_midas(r, lags, MidasSpec("skewt"), std_errors=False)

    x = daily_macro(r.index, lags)
    next_day = returns.index[n]
    x_next = daily_macro(pd.DatetimeIndex([next_day]), lags)[0]
    forecast = one_step_variance(fit.params, r.to_numpy(), x, x_next)

    # Appending any return and running the recursion one day further gives the same variance.
    arr = np.append(r.to_numpy(), 0.0)
    tau, g = components(fit.params.to_dict(), arr, np.vstack([x, x_next]),
                        backcast(r.to_numpy() - r.mean()))
    assert forecast == pytest.approx(tau[-1] * g[-1], rel=1e-12)


def test_variance_ratio_is_a_share(with_macro):
    fit = fit_midas(*with_macro, MidasSpec("skewt"), std_errors=False)
    assert 0 < fit.variance_ratio < 1
    assert len(fit.weights) == K


# --- Input checks -----------------------------------------------------------------------------------


def test_missing_macro_month_is_rejected(with_macro):
    returns, lags = with_macro
    with pytest.raises(ValueError, match="No macro lags for months"):
        daily_macro(returns.index, lags.iloc[5:])


def test_nan_macro_lags_are_rejected(with_macro):
    returns, lags = with_macro
    broken = lags.copy()
    broken.iloc[3, 0] = np.nan
    with pytest.raises(ValueError, match="contain NaN"):
        daily_macro(returns.index, broken)


def test_unknown_distribution_rejected():
    with pytest.raises(ValueError, match="dist must be one of"):
        MidasSpec("laplace")


def test_estimate_does_not_depend_on_a_bad_starting_point():
    # True model: equal weights over 12 months (w = 1) and a negative macro effect.
    returns, lags = simulate_midas(n_months=300, theta=-0.6, w=1.0, seed=4)
    fresh = fit_midas(returns, lags, MidasSpec("skewt"), std_errors=False)

    # Start from the wrong peak: weight on the last month and the wrong sign.
    bad_start = fresh.params.copy()
    bad_start["w"], bad_start["theta"] = 50.0, 0.5
    warm = fit_midas(returns, lags, MidasSpec("skewt"), start_params=bad_start, std_errors=False)

    assert warm.loglik == pytest.approx(fresh.loglik, abs=1e-3)
    assert warm.params["theta"] == pytest.approx(fresh.params["theta"], abs=0.02)
    assert warm.params["theta"] < 0
    assert warm.converged