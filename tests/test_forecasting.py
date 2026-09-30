"""Tests for the rolling forecasting engine in var_es.risk.forecasting."""

import numpy as np
import pandas as pd
import pytest

from var_es.models.garch import GarchSpec, fit_garch
from var_es.risk.forecasting import (
    ForecastSettings,
    ewma,
    forecast_dates,
    garch_forecasts,
    historical_simulation,
)
from var_es.risk.measures import empirical_var_es

SETTINGS = ForecastSettings(
    window=400, refit_every=7, historical_window=100, ewma_lambda=0.94,
    var_levels=(0.99, 0.975), es_levels=(0.975,),
)
N_DATES = 30


def _returns(n=500, seed=0) -> pd.Series:
    """GJR-GARCH-like returns, in percent."""
    rng = np.random.default_rng(seed)
    z = rng.standard_t(6, n) * np.sqrt(4 / 6)
    r, s2 = np.empty(n), 1.0
    for t in range(n):
        r[t] = 0.03 + np.sqrt(s2) * z[t]
        eps = r[t] - 0.03
        s2 = 0.02 + 0.15 * eps**2 * (eps < 0) + 0.88 * s2
    return pd.Series(r, index=pd.bdate_range("2010-01-04", periods=n, name="date"))


@pytest.fixture(scope="module")
def returns():
    return _returns()


@pytest.fixture(scope="module")
def dates(returns):
    return returns.index[SETTINGS.window : SETTINGS.window + N_DATES]


def _run(model: str, returns, dates) -> pd.DataFrame:
    if model == "hs":
        return historical_simulation(returns, dates, SETTINGS)
    if model == "ewma":
        return ewma(returns, dates, SETTINGS)
    if model == "fhs":
        return garch_forecasts(returns, dates, GarchSpec(True, "normal"), SETTINGS, fhs=True)["fhs"]
    return garch_forecasts(returns, dates, GarchSpec(True, "skewt"), SETTINGS)["forecasts"]


MODELS = ["hs", "ewma", "garch", "fhs"]


# --- No look-ahead ------------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS)
def test_forecasts_use_only_past_returns(model, returns, dates):
    cutoff = dates[15]
    changed = returns.copy()
    changed[changed.index >= cutoff] *= 5.0  # wildly different returns from the cutoff on

    before = _run(model, returns, dates)
    after = _run(model, changed, dates)
    cols = SETTINGS.columns + ["sigma"]

    earlier = before.index <= cutoff  # forecast for the cutoff day is made the day before
    pd.testing.assert_frame_equal(before.loc[earlier, cols], after.loc[earlier, cols])
    assert not before.loc[~earlier, cols].equals(after.loc[~earlier, cols])


# --- Individual models -----------------------------------------------------------------------


def test_historical_simulation_uses_the_trailing_window(returns, dates):
    result = historical_simulation(returns, dates, SETTINGS)
    d = dates[5]
    i = returns.index.get_loc(d)
    sample = returns.iloc[i - SETTINGS.historical_window : i].to_numpy()

    var, es = empirical_var_es(sample, 0.025)
    assert result.loc[d, "var_0.975"] == pytest.approx(var)
    assert result.loc[d, "es_0.975"] == pytest.approx(es)


def test_ewma_matches_a_plain_loop(returns, dates):
    result = ewma(returns, dates, SETTINGS)
    arr, lam = returns.to_numpy(), SETTINGS.ewma_lambda

    w = 0.94 ** np.arange(75)
    var = float(np.sum(arr[:75] ** 2 * w / w.sum()))  # the backcast start value
    forecasts = []
    for t in range(len(arr)):
        var = lam * var + (1 - lam) * arr[t] ** 2  # forecast for day t+1
        forecasts.append(var)

    i = returns.index.get_loc(dates[3])
    assert result.loc[dates[3], "sigma"] == pytest.approx(np.sqrt(forecasts[i - 1]))
    assert result.loc[dates[3], "var_0.99"] == pytest.approx(2.326348 * np.sqrt(forecasts[i - 1]))


def test_garch_refits_on_schedule_and_matches_a_direct_fit(returns, dates):
    spec = GarchSpec(True, "t")
    result = garch_forecasts(returns, dates, spec, SETTINGS)
    params = result["params"]

    assert len(params) == int(np.ceil(N_DATES / SETTINGS.refit_every))
    assert list(params.index) == list(dates[:: SETTINGS.refit_every])

    # On a refit date the forecast equals a fresh fit on that window.
    d = dates[SETTINGS.refit_every]
    i = returns.index.get_loc(d)
    direct = fit_garch(returns.iloc[i - SETTINGS.window : i], spec)
    assert result["forecasts"].loc[d, "sigma"] == pytest.approx(
        np.sqrt(direct.forecast_variance()), rel=1e-3
    )


def test_fhs_uses_the_garch_volatility(returns, dates):
    run = garch_forecasts(returns, dates, GarchSpec(True, "normal"), SETTINGS, fhs=True)
    np.testing.assert_allclose(run["fhs"]["sigma"], run["forecasts"]["sigma"])


@pytest.mark.parametrize("model", MODELS)
def test_risk_measures_are_ordered(model, returns, dates):
    result = _run(model, returns, dates)
    assert (result["var_0.99"] > result["var_0.975"]).all()
    assert (result["es_0.975"] >= result["var_0.975"]).all()
    assert (result["var_0.975"] > 0).all()


@pytest.mark.parametrize("model", MODELS)
def test_output_layout(model, returns, dates):
    result = _run(model, returns, dates)
    assert list(result.columns) == ["ret", "sigma", "var_0.99", "var_0.975", "es_0.975"]
    assert result.index.equals(pd.DatetimeIndex(dates, name="date"))
    np.testing.assert_array_equal(result["ret"], returns.loc[dates])


# --- Settings and dates ------------------------------------------------------------------------


def test_settings_from_config():
    cfg = {
        "sample_split": {"estimation_window": 1000},
        "risk": {"confidence_levels_var": [0.99, 0.975], "confidence_levels_es": [0.975]},
        "forecasting": {"refit_every": 5, "historical_window": 250, "ewma_lambda": 0.94},
    }
    settings = ForecastSettings.from_config(cfg)
    assert settings.window == 1000
    assert settings.columns == ["var_0.99", "var_0.975", "es_0.975"]


def test_missing_forecasting_section_is_explained():
    with pytest.raises(ValueError, match="no 'forecasting' section"):
        ForecastSettings.from_config({"sample_split": {}, "risk": {}})


def test_invalid_settings_rejected():
    with pytest.raises(ValueError, match="ewma_lambda"):
        ForecastSettings(1000, 5, 250, 1.5, (0.99,), (0.975,)).validate()


def test_forecast_dates_need_enough_history(returns):
    with pytest.raises(ValueError, match="window needs 400"):
        forecast_dates(returns.index, returns.index[100], returns.index[-1], window=400)