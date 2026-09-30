"""Tests for the GARCH-MIDAS forecasting engine and expanding windows."""

import numpy as np
import pandas as pd
import pytest

from var_es.models.garch import GarchSpec, fit_garch
from var_es.models.midas import MidasSpec, beta_weights, fit_midas
from var_es.risk.forecasting import ForecastSettings, garch_forecasts
from var_es.risk.macro_forecasting import midas_forecasts

K = 6
SETTINGS = ForecastSettings(
    window=400, refit_every=7, historical_window=100, ewma_lambda=0.94,
    var_levels=(0.99, 0.975), es_levels=(0.975,),
)
N_DATES = 21


def _simulate(n_months=40, seed=0):
    """Returns with a macro-driven long-run variance, plus the monthly lag table."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n_months + K)
    for t in range(1, len(x)):
        x[t] = 0.9 * x[t - 1] + rng.normal(0, 0.4)
    months = pd.period_range("2010-01", periods=n_months, freq="M")
    lag_rows = np.array([x[i - K : i][::-1] for i in range(K, n_months + K)])
    lags = pd.DataFrame(lag_rows, index=pd.PeriodIndex(months, name="month"),
                        columns=[f"lag_{k}" for k in range(1, K + 1)])

    dates = pd.bdate_range(months[0].start_time, months[-1].end_time.normalize())
    tau = np.exp(0.6 * lag_rows @ beta_weights(3.0, K))[months.get_indexer(dates.to_period("M"))]
    z = rng.standard_t(7, len(dates)) * np.sqrt(5 / 7)
    g, r = 1.0, np.empty(len(dates))
    for t in range(len(dates)):
        if t > 0:
            e = r[t - 1] - 0.03
            g = 0.02 + 0.13 * (e < 0) * e**2 / tau[t - 1] + 0.915 * g
        r[t] = 0.03 + np.sqrt(tau[t] * g) * z[t]
    return pd.Series(r, index=pd.DatetimeIndex(dates, name="date")), lags


@pytest.fixture(scope="module")
def data():
    returns, lags = _simulate()
    dates = returns.index[SETTINGS.window : SETTINGS.window + N_DATES]
    return returns, lags, dates


def _run(returns, lags, dates, **kwargs):
    return midas_forecasts(returns, dates, lags, MidasSpec("skewt"), SETTINGS, **kwargs)


def test_forecasts_use_only_information_available_at_the_time(data):
    returns, lags, dates = data
    cutoff = dates[12]

    changed_returns = returns.copy()
    changed_returns[changed_returns.index >= cutoff] *= 4.0
    changed_lags = lags.copy()
    changed_lags.loc[changed_lags.index > cutoff.to_period("M")] += 5.0  # later months only

    before = _run(returns, lags, dates)["forecasts"]
    after = _run(changed_returns, changed_lags, dates)["forecasts"]
    cols = ["sigma", "var_0.99", "var_0.975", "es_0.975"]

    earlier = before.index <= cutoff
    pd.testing.assert_frame_equal(before.loc[earlier, cols], after.loc[earlier, cols])
    assert not before.loc[~earlier, cols].equals(after.loc[~earlier, cols])


def test_expanding_midas_refit_uses_all_earlier_returns(data):
    returns, lags, dates = data
    run = _run(returns, lags, dates, expanding=True)
    d = dates[SETTINGS.refit_every]
    i = returns.index.get_loc(d)

    direct = fit_midas(returns.iloc[:i], lags, MidasSpec("skewt"), std_errors=False)
    assert run["params"].loc[d, "theta"] == pytest.approx(direct.params["theta"], abs=1e-3)


def test_rolling_midas_uses_a_fixed_length_window(data):
    returns, lags, dates = data
    run = _run(returns, lags, dates, expanding=False)
    d = dates[SETTINGS.refit_every]
    i = returns.index.get_loc(d)

    direct = fit_midas(returns.iloc[i - SETTINGS.window : i], lags, MidasSpec("skewt"), std_errors=False)
    assert run["params"].loc[d, "theta"] == pytest.approx(direct.params["theta"], abs=1e-3)


def test_expanding_garch_refit_uses_all_earlier_returns(data):
    returns, _, dates = data
    spec = GarchSpec(asymmetric=True, dist="t")
    run = garch_forecasts(returns, dates, spec, SETTINGS, expanding=True)
    d = dates[SETTINGS.refit_every]
    i = returns.index.get_loc(d)

    direct = fit_garch(returns.iloc[:i], spec)
    assert run["forecasts"].loc[d, "sigma"] == pytest.approx(np.sqrt(direct.forecast_variance()), rel=1e-3)


def test_output_layout_and_ordering(data):
    returns, lags, dates = data
    run = _run(returns, lags, dates)
    result = run["forecasts"]

    assert list(result.columns) == ["ret", "sigma", "var_0.99", "var_0.975", "es_0.975"]
    assert (result["var_0.99"] > result["var_0.975"]).all()
    assert (result["es_0.975"] > result["var_0.975"]).all()
    assert len(run["params"]) == int(np.ceil(N_DATES / SETTINGS.refit_every))