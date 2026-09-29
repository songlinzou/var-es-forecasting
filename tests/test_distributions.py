"""Tests for the standardized shock distributions."""

import numpy as np
import pytest
from scipy import integrate

from var_es.models.distributions import DISTRIBUTIONS, Normal, SkewT, StudentT

CASES = [
    (Normal, []),
    (StudentT, [6.0]),
    (SkewT, [6.0, -0.3]),
    (SkewT, [4.5, 0.4]),
]
IDS = ["normal", "t", "skewt-left", "skewt-right"]


def _moment(dist, shape, power):
    def integrand(z):
        return z**power * np.exp(dist.logpdf(np.array([z]), shape)[0])

    value, _ = integrate.quad(integrand, -np.inf, np.inf, limit=200)
    return value


@pytest.mark.parametrize("dist, shape", CASES, ids=IDS)
def test_density_is_standardized(dist, shape):
    assert _moment(dist, shape, 0) == pytest.approx(1.0, abs=1e-6)  # integrates to 1
    assert _moment(dist, shape, 1) == pytest.approx(0.0, abs=1e-6)  # mean 0
    assert _moment(dist, shape, 2) == pytest.approx(1.0, abs=1e-5)  # variance 1


@pytest.mark.parametrize("dist, shape", CASES, ids=IDS)
@pytest.mark.parametrize("q", [0.01, 0.025, 0.5, 0.9])
def test_ppf_inverts_the_cdf(dist, shape, q):
    quantile = float(dist.ppf(q, shape))

    def density(z):
        return np.exp(dist.logpdf(np.array([z]), shape)[0])

    probability, _ = integrate.quad(density, -np.inf, quantile, limit=200)
    assert probability == pytest.approx(q, abs=1e-7)


def test_skewt_with_zero_skewness_is_the_student_t():
    z = np.linspace(-5, 5, 21)
    np.testing.assert_allclose(SkewT.logpdf(z, [7.0, 0.0]), StudentT.logpdf(z, [7.0]), rtol=1e-12)
    q = np.array([0.01, 0.3, 0.8])
    np.testing.assert_allclose(SkewT.ppf(q, [7.0, 0.0]), StudentT.ppf(q, [7.0]), rtol=1e-12)


def test_negative_skewness_fattens_the_left_tail():
    symmetric = float(StudentT.ppf(0.01, [6.0]))
    left_skewed = float(SkewT.ppf(0.01, [6.0, -0.3]))
    assert left_skewed < symmetric


def test_matches_arch_package():
    from arch.univariate.distribution import SkewStudent, StudentsT

    z = np.linspace(-6, 6, 25)
    q = np.array([0.001, 0.01, 0.025, 0.5, 0.975])
    shape = np.array([6.0, -0.3])
    ones = np.ones_like(z)

    np.testing.assert_allclose(
        SkewT.logpdf(z, shape), SkewStudent().loglikelihood(shape, z, ones, individual=True)
    )
    np.testing.assert_allclose(SkewT.ppf(q, shape), SkewStudent().ppf(q, shape))
    np.testing.assert_allclose(
        StudentT.logpdf(z, [6.0]), StudentsT().loglikelihood(np.array([6.0]), z, ones, individual=True)
    )
    np.testing.assert_allclose(StudentT.ppf(q, [6.0]), StudentsT().ppf(q, np.array([6.0])))


def test_registry():
    assert set(DISTRIBUTIONS) == {"normal", "t", "skewt"}