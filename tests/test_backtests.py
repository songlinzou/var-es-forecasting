"""Tests for the VaR and ES backtests."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from var_es.backtest.es_tests import acerbi_szekely_z2, mcneil_frey
from var_es.backtest.var_tests import christoffersen, exceptions, kupiec, traffic_light


def _iid_hits(n: int, rate: float, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(size=n) < rate


# --- Exceptions ----------------------------------------------------------------------------


def test_exception_is_a_loss_beyond_var():
    returns = pd.Series([-3.0, -1.0, 2.0])
    var = pd.Series([2.0, 2.0, 2.0])
    assert list(exceptions(returns, var)) == [True, False, False]


# --- Kupiec ----------------------------------------------------------------------------------


def test_kupiec_matches_the_textbook_formula():
    n, x, p = 1000, 20, 0.01
    hits = np.zeros(n, dtype=bool)
    hits[:x] = True
    expected = 2 * (x * np.log(x / (n * p)) + (n - x) * np.log((n - x) / (n * (1 - p))))

    result = kupiec(hits, p)
    assert result["lr"] == pytest.approx(expected)
    assert result["p_value"] == pytest.approx(stats.chi2.sf(expected, 1))


def test_kupiec_accepts_the_expected_count_and_rejects_far_too_many():
    exact = np.zeros(1000, dtype=bool)
    exact[:10] = True
    assert kupiec(exact, 0.01)["p_value"] == pytest.approx(1.0)

    too_many = np.zeros(1000, dtype=bool)
    too_many[:30] = True
    assert kupiec(too_many, 0.01)["p_value"] < 1e-5


def test_kupiec_with_no_exceptions():
    result = kupiec(np.zeros(500, dtype=bool), 0.01)
    assert result["lr"] == pytest.approx(-2 * 500 * np.log(0.99))


# --- Christoffersen --------------------------------------------------------------------------


def test_christoffersen_counts_transitions():
    hits = [0, 0, 1, 1, 0, 0, 0, 1, 0, 0]
    result = christoffersen(hits, 0.3)
    assert (result["n00"], result["n01"], result["n10"], result["n11"]) == (4, 2, 2, 1)


def test_christoffersen_detects_clustered_exceptions():
    hits = np.zeros(5000, dtype=bool)
    for start in range(100, 5000, 500):
        hits[start : start + 5] = True  # runs of 5 consecutive exceptions, 1% overall
    result = christoffersen(hits, 0.01)

    assert kupiec(hits, 0.01)["p_value"] > 0.5  # the rate alone looks perfect
    assert result["p_ind"] < 1e-10  # but the clustering is caught


def test_christoffersen_accepts_independent_exceptions():
    result = christoffersen(_iid_hits(20_000, 0.01), 0.01)
    assert result["p_ind"] > 0.01
    assert result["p_cc"] > 0.01


def test_christoffersen_handles_no_consecutive_exceptions():
    hits = np.zeros(1000, dtype=bool)
    hits[::100] = True
    result = christoffersen(hits, 0.01)
    assert result["n11"] == 0
    assert np.isfinite(result["lr_ind"])


# --- Traffic light -----------------------------------------------------------------------------


def test_traffic_light_zones():
    hits = np.zeros(300, dtype=bool)
    hits[260:265] = True  # 5 exceptions: windows containing all five are yellow
    result = traffic_light(hits)

    assert result["green"] + result["yellow"] + result["red"] == pytest.approx(1.0)
    assert result["max_exceptions"] == 5
    assert result["yellow"] > 0 and result["red"] == 0


def test_traffic_light_needs_a_full_window():
    with pytest.raises(ValueError, match="at least 250"):
        traffic_light(np.zeros(100, dtype=bool))


# --- ES tests ----------------------------------------------------------------------------------

N = 200_000
ALPHA = 0.025
VAR_TRUE = -stats.norm.ppf(ALPHA)  # 1.960
ES_TRUE = stats.norm.pdf(stats.norm.ppf(ALPHA)) / ALPHA  # 2.338


@pytest.fixture(scope="module")
def normal_returns():
    return np.random.default_rng(1).standard_normal(N)


def _constant(value):
    return np.full(N, value)


@pytest.mark.parametrize("test", ["z2", "mcneil_frey"])
def test_es_tests_have_correct_size(test):
    """A correct model should be rejected about 5% of the time at the 5% level."""
    rng = np.random.default_rng(7)
    n_days, reps = 4000, 200
    rejections = 0
    for k in range(reps):
        r = rng.standard_normal(n_days)
        var, es = np.full(n_days, VAR_TRUE), np.full(n_days, ES_TRUE)
        if test == "z2":
            p = acerbi_szekely_z2(r, var, es, ALPHA, n_boot=499, seed=k)["p_value"]
        else:
            p = mcneil_frey(r, var, es, n_boot=499, seed=k)["p_value"]
        rejections += p < 0.05
    assert 0.02 <= rejections / reps <= 0.10


def test_correct_es_gives_z2_near_zero(normal_returns):
    result = acerbi_szekely_z2(normal_returns, _constant(VAR_TRUE), _constant(ES_TRUE), ALPHA, n_boot=9)
    assert abs(result["z2"]) < 0.05


def test_understated_es_is_rejected(normal_returns):
    es_low = _constant(0.85 * ES_TRUE)
    z2 = acerbi_szekely_z2(normal_returns, _constant(VAR_TRUE), es_low, ALPHA, n_boot=199)
    mf = mcneil_frey(normal_returns, _constant(VAR_TRUE), es_low, n_boot=999)

    assert z2["z2"] < 0 and z2["p_value"] < 0.01
    assert mf["mean_residual"] > 0 and mf["p_value"] < 0.01


def test_z2_also_rejects_too_many_exceptions(normal_returns):
    var_low, es_low = _constant(0.8 * VAR_TRUE), _constant(0.8 * ES_TRUE)
    assert acerbi_szekely_z2(normal_returns, var_low, es_low, ALPHA, n_boot=199)["p_value"] < 0.01


def test_overstated_es_is_not_flagged_as_too_low(normal_returns):
    mf = mcneil_frey(normal_returns, _constant(VAR_TRUE), _constant(1.2 * ES_TRUE), n_boot=999)
    assert mf["mean_residual"] < 0
    assert mf["p_value"] > 0.99


def test_mcneil_frey_needs_exceptions():
    result = mcneil_frey(np.zeros(100), np.ones(100), np.ones(100))
    assert np.isnan(result["p_value"])