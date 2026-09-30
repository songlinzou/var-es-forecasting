"""Tests for the loss functions and model-comparison methods."""

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy import stats

from var_es.evaluation.comparison import (
    ComparisonSettings,
    diebold_mariano,
    model_confidence_set,
    newey_west_lags,
    stationary_bootstrap_indices,
)
from var_es.evaluation.losses import fz0_loss, mse_variance, qlike, quantile_loss

ALPHA = 0.025
VAR_TRUE = -stats.norm.ppf(ALPHA)
ES_TRUE = stats.norm.pdf(stats.norm.ppf(ALPHA)) / ALPHA


@pytest.fixture(scope="module")
def normal_returns():
    return np.random.default_rng(3).standard_normal(400_000)


# --- Loss functions -------------------------------------------------------------------------


def test_quantile_loss_formula():
    # q = -2: a return of -3 is a hit (loss (0.025-1)*(-3+2)); a return of 1 is not.
    losses = quantile_loss([-3.0, 1.0], [2.0, 2.0], 0.025)
    np.testing.assert_allclose(losses, [(0.025 - 1) * (-1.0), 0.025 * 3.0])


def test_quantile_loss_is_smallest_at_the_true_var(normal_returns):
    def mean_loss(var):
        return quantile_loss(normal_returns, np.full(len(normal_returns), var), ALPHA).mean()

    assert mean_loss(VAR_TRUE) < mean_loss(VAR_TRUE * 0.9)
    assert mean_loss(VAR_TRUE) < mean_loss(VAR_TRUE * 1.1)


@pytest.mark.parametrize("var_scale, es_scale", [(0.9, 1.0), (1.0, 0.9), (1.0, 1.1), (1.1, 1.1)])
def test_fz0_is_smallest_at_the_true_var_and_es(normal_returns, var_scale, es_scale):
    n = len(normal_returns)

    def mean_loss(var, es):
        return fz0_loss(normal_returns, np.full(n, var), np.full(n, es), ALPHA).mean()

    assert mean_loss(VAR_TRUE, ES_TRUE) < mean_loss(VAR_TRUE * var_scale, ES_TRUE * es_scale)


def test_fz0_rejects_non_positive_es():
    with pytest.raises(ValueError, match="ES > 0"):
        fz0_loss([0.0], [1.0], [0.0], ALPHA)


def test_qlike_is_smallest_at_the_true_variance():
    rng = np.random.default_rng(5)
    sigma2 = np.exp(rng.normal(0, 0.5, 200_000))
    proxy = sigma2 * rng.standard_normal(200_000) ** 2  # noisy but unbiased

    assert qlike(sigma2, proxy).mean() < qlike(0.8 * sigma2, proxy).mean()
    assert qlike(sigma2, proxy).mean() < qlike(1.2 * sigma2, proxy).mean()


def test_qlike_ranks_like_the_normalized_form():
    rng = np.random.default_rng(6)
    proxy = rng.uniform(0.5, 2, 1000)
    a, b = rng.uniform(0.5, 2, 1000), rng.uniform(0.5, 2, 1000)

    def normalized(s2):
        return proxy / s2 - np.log(proxy / s2) - 1

    np.testing.assert_allclose(qlike(a, proxy) - qlike(b, proxy), normalized(a) - normalized(b))


def test_mse_variance():
    np.testing.assert_allclose(mse_variance([1.0, 2.0], [2.0, 2.0]), [1.0, 0.0])


# --- Diebold-Mariano ----------------------------------------------------------------------------


def test_newey_west_lag_rule():
    assert newey_west_lags(4027) == 9


def test_dm_standard_error_matches_statsmodels_hac():
    rng = np.random.default_rng(8)
    e = rng.standard_normal(2000)
    d = np.convolve(e, [1, 0.6, 0.3], mode="same")  # autocorrelated differences
    lags = 7

    ours = diebold_mariano(d, np.zeros_like(d), lags=lags)
    fit = sm.OLS(d, np.ones_like(d)).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags, "use_correction": False}
    )
    assert ours["t_stat"] == pytest.approx(fit.tvalues[0], rel=1e-10)


def test_dm_sign_and_significance():
    rng = np.random.default_rng(9)
    common = rng.standard_normal(3000)
    better = common + rng.normal(0, 0.3, 3000)
    worse = common + rng.normal(0, 0.3, 3000) + 0.1

    result = diebold_mariano(better, worse)
    assert result["mean_diff"] < 0
    assert result["p_value"] < 1e-6


# --- Model Confidence Set -------------------------------------------------------------------------


def _losses(shifts, n=1500, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    common = rng.standard_normal(n)
    return pd.DataFrame(
        {f"m{j}": 1 + 0.3 * common + 0.5 * rng.standard_normal(n) + s for j, s in enumerate(shifts)}
    )


@pytest.mark.parametrize("shifts", [[0, 0.02, 0.05, 0.1, 0.2], [0, 0, 0.01, 0.3]])
def test_mcs_matches_arch_with_the_same_bootstrap_draws(shifts):
    from arch.bootstrap import MCS

    losses = _losses(shifts)
    ref = MCS(losses, size=0.10, reps=500, block_size=10, method="max", bootstrap="stationary", seed=1)
    ref.compute()
    ours = model_confidence_set(losses, 0.90, 10, 500, indices=np.array(ref._bootstrap_indices))

    pd.testing.assert_series_equal(
        ours["mcs_p"], ref.pvalues["Pvalue"], check_names=False, check_index_type=False
    )
    assert set(ours.index[ours["in_mcs"]]) == set(ref.included)


def test_mcs_keeps_equally_good_models():
    result = model_confidence_set(_losses([0, 0, 0]), n_boot=500)
    assert result["in_mcs"].all()


def test_mcs_removes_clearly_worse_models():
    result = model_confidence_set(_losses([0, 0.5, 1.0]), n_boot=500)
    assert list(result.index[result["in_mcs"]]) == ["m0"]


def test_stationary_bootstrap_block_length():
    idx = stationary_bootstrap_indices(5000, block_length=10, size=20, rng=np.random.default_rng(0))
    assert idx.min() >= 0 and idx.max() < 5000
    continues = np.diff(idx, axis=1) == 1
    assert 1 / (1 - continues.mean()) == pytest.approx(10, rel=0.1)


# --- Settings ---------------------------------------------------------------------------------------


def test_comparison_settings_from_config():
    cfg = {"comparison": {"benchmark": "X", "mcs_confidence": 0.9, "block_length": 10,
                          "bootstrap_reps": 10_000}}
    settings = ComparisonSettings.from_config(cfg)
    assert settings.block_length == 10 and settings.seed == 0


def test_missing_comparison_section_is_explained():
    with pytest.raises(ValueError, match="no 'comparison' section"):
        ComparisonSettings.from_config({})


def test_invalid_comparison_settings():
    with pytest.raises(ValueError, match="bootstrap_reps"):
        ComparisonSettings("X", 0.9, 10, 50).validate()