"""Tests for the from-scratch GARCH implementation in var_es.models.garch."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from var_es.models.garch import (
    GarchSpec,
    backcast,
    conditional_variance,
    fit_garch,
    log_likelihood_terms,
)
from var_es.models.reference import fit_arch_reference

ALL_SPECS = [
    GarchSpec(asymmetric=False, dist="normal"),
    GarchSpec(asymmetric=False, dist="t"),
    GarchSpec(asymmetric=True, dist="normal"),
    GarchSpec(asymmetric=True, dist="t"),
]


def simulate(n=5000, mu=0.05, omega=0.02, alpha=0.03, gamma=0.10, beta=0.90, nu=None, seed=0):
    """Simulate returns from a GJR-GARCH(1,1) with normal or unit-variance t shocks."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n) if nu is None else rng.standard_t(nu, n) * np.sqrt((nu - 2) / nu)
    eps, sigma2 = np.empty(n), np.empty(n)
    sigma2[0] = omega / (1 - alpha - gamma / 2 - beta)
    for t in range(n):
        if t > 0:
            sigma2[t] = omega + (alpha + gamma * (eps[t - 1] < 0)) * eps[t - 1] ** 2 + beta * sigma2[t - 1]
        eps[t] = np.sqrt(sigma2[t]) * z[t]
    return pd.Series(mu + eps, index=pd.bdate_range("2000-01-03", periods=n))


def _simulate_for(spec: GarchSpec, **kwargs) -> pd.Series:
    params = {"gamma": 0.10 if spec.asymmetric else 0.0, "alpha": 0.03 if spec.asymmetric else 0.08}
    params["nu"] = 6 if spec.dist == "t" else None
    params.update(kwargs)
    return simulate(**params)


# --- Building blocks ------------------------------------------------------------------------


def _variance_loop(omega, alpha, gamma, beta, resid, start):
    """The recursion written as a plain loop, as you would on a whiteboard."""
    sigma2 = np.empty(len(resid))
    for t in range(len(resid)):
        if t == 0:
            sigma2[t] = omega + (alpha + 0.5 * gamma) * start + beta * start
        else:
            neg = resid[t - 1] < 0
            sigma2[t] = omega + (alpha + gamma * neg) * resid[t - 1] ** 2 + beta * sigma2[t - 1]
    return sigma2


@pytest.mark.parametrize("gamma", [0.0, 0.12])
def test_fast_recursion_matches_plain_loop(gamma):
    resid = np.random.default_rng(1).standard_normal(500)
    start = backcast(resid)
    fast = conditional_variance(0.02, 0.05, gamma, 0.9, resid, start)
    slow = _variance_loop(0.02, 0.05, gamma, 0.9, resid, start)
    np.testing.assert_allclose(fast, slow, rtol=1e-12)


def test_backcast_is_weighted_average_of_early_squared_residuals():
    resid = np.full(200, 2.0)
    assert backcast(resid) == pytest.approx(4.0)


def test_normal_likelihood_matches_scipy():
    y = simulate(n=300, gamma=0.0, alpha=0.08).to_numpy()
    spec = GarchSpec(dist="normal")
    theta = np.array([0.05, 0.02, 0.08, 0.90])
    start = backcast(y - y.mean())

    sigma2 = conditional_variance(0.02, 0.08, 0.0, 0.90, y - 0.05, start)
    expected = stats.norm.logpdf(y, loc=0.05, scale=np.sqrt(sigma2))
    np.testing.assert_allclose(log_likelihood_terms(theta, y, spec, start), expected, rtol=1e-10)


def test_student_t_likelihood_matches_scipy():
    y = simulate(n=300, gamma=0.1, nu=6).to_numpy()
    spec = GarchSpec(asymmetric=True, dist="t")
    nu = 6.0
    theta = np.array([0.05, 0.02, 0.03, 0.10, 0.90, nu])
    start = backcast(y - y.mean())

    sigma2 = conditional_variance(0.02, 0.03, 0.10, 0.90, y - 0.05, start)
    scale = np.sqrt(sigma2 * (nu - 2) / nu)  # a t with this scale has variance sigma2
    expected = stats.t.logpdf(y, df=nu, loc=0.05, scale=scale)
    np.testing.assert_allclose(log_likelihood_terms(theta, y, spec, start), expected, rtol=1e-10)


# --- Estimation --------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_matches_arch_package(spec):
    y = _simulate_for(spec)
    ours = fit_garch(y, spec)
    ref = fit_arch_reference(y, spec)

    assert ours.converged
    assert ours.loglik == pytest.approx(ref["loglik"], abs=1e-4)
    np.testing.assert_allclose(ours.params, ref["params"], atol=1e-3)
    np.testing.assert_allclose(ours.std_errors, ref["std_errors"], rtol=0.02)


def test_recovers_true_parameters_on_a_long_sample():
    y = simulate(n=10_000, alpha=0.03, gamma=0.10, beta=0.90, nu=6, seed=11)
    params = fit_garch(y, GarchSpec(asymmetric=True, dist="t")).params

    assert params["alpha"] == pytest.approx(0.03, abs=0.02)
    assert params["gamma"] == pytest.approx(0.10, abs=0.03)
    assert params["beta"] == pytest.approx(0.90, abs=0.03)
    assert params["nu"] == pytest.approx(6.0, abs=1.0)


def test_parameter_on_bound_gets_nan_standard_error():
    # With no symmetric ARCH effect, alpha is estimated at its lower bound of 0.
    y = simulate(alpha=0.0, gamma=0.15, beta=0.90, nu=6, seed=0)
    result = fit_garch(y, GarchSpec(asymmetric=True, dist="t"))

    assert result.params["alpha"] == pytest.approx(0.0, abs=1e-8)
    assert np.isnan(result.std_errors["alpha"])
    assert result.std_errors.drop("alpha").notna().all()


def test_fitted_model_is_stationary():
    result = fit_garch(simulate(), GarchSpec(asymmetric=True))
    assert result.persistence < 1
    assert result.half_life == pytest.approx(np.log(0.5) / np.log(result.persistence))


def test_standardized_residuals_have_unit_variance():
    result = fit_garch(simulate(nu=6), GarchSpec(asymmetric=True, dist="t"))
    assert result.std_resid.var() == pytest.approx(1.0, abs=0.1)
    assert len(result.std_resid) == result.n_obs


def test_forecast_equals_the_next_step_of_the_recursion():
    y = simulate()
    spec = GarchSpec(asymmetric=True)
    result = fit_garch(y, spec)
    p = result.params

    # sigma2 for day n+1 depends only on data up to day n, so append any value.
    arr = np.append(y.to_numpy(), 0.0)
    start = backcast(y.to_numpy() - y.mean())
    sigma2 = conditional_variance(p["omega"], p["alpha"], p["gamma"], p["beta"], arr - p["mu"], start)
    assert result.forecast_variance() == pytest.approx(sigma2[-1], rel=1e-12)


def test_information_criteria():
    result = fit_garch(simulate(), GarchSpec())
    assert result.aic == pytest.approx(2 * 4 - 2 * result.loglik)
    assert result.bic == pytest.approx(4 * np.log(result.n_obs) - 2 * result.loglik)


# --- Specification ------------------------------------------------------------------------


def test_param_names():
    assert GarchSpec().param_names == ["mu", "omega", "alpha", "beta"]
    assert GarchSpec(asymmetric=True, dist="t").param_names == [
        "mu", "omega", "alpha", "gamma", "beta", "nu",
    ]


def test_unknown_distribution_rejected():
    with pytest.raises(ValueError, match="dist must be one of"):
        GarchSpec(dist="skewt")