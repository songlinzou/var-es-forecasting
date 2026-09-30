"""Tests for var_es.risk.measures."""

import numpy as np
import pytest
from scipy import stats

from var_es.risk.measures import (
    empirical_var_es,
    numerical_es,
    parametric_var_es,
    standardized_es,
    standardized_quantile,
)


@pytest.mark.parametrize("dist, shape", [("normal", []), ("t", [5.0]), ("t", [12.0])])
@pytest.mark.parametrize("alpha", [0.01, 0.025, 0.05])
def test_closed_form_es_matches_numerical_integration(dist, shape, alpha):
    assert standardized_es(dist, shape, alpha) == pytest.approx(
        numerical_es(dist, shape, alpha), rel=1e-7
    )


def test_normal_values_match_textbook_numbers():
    assert standardized_quantile("normal", [], 0.01) == pytest.approx(-2.3263, abs=1e-4)
    assert standardized_es("normal", [], 0.025) == pytest.approx(-2.3378, abs=1e-4)


def test_es_is_beyond_the_quantile():
    for dist, shape in [("normal", []), ("t", [6.0]), ("skewt", [7.0, -0.15])]:
        assert standardized_es(dist, shape, 0.025) < standardized_quantile(dist, shape, 0.025)


def test_left_skewness_makes_es_more_extreme():
    assert standardized_es("skewt", [7.0, -0.2], 0.025) < standardized_es("t", [7.0], 0.025)


def test_parametric_var_es_sign_convention():
    var, es = parametric_var_es(mu=0.05, sigma=2.0, q=-2.33, e=-2.67)
    assert var == pytest.approx(-(0.05 - 4.66))
    assert es == pytest.approx(-(0.05 - 5.34))
    assert es > var > 0


def test_empirical_var_es():
    sample = np.arange(1, 101, dtype=float) - 50.5  # -49.5, ..., 49.5
    var, es = empirical_var_es(sample, 0.05)

    assert var == pytest.approx(-np.quantile(sample, 0.05))
    assert es == pytest.approx(-sample[sample <= np.quantile(sample, 0.05)].mean())
    assert es > var


def test_empirical_matches_normal_on_a_large_sample():
    sample = stats.norm.rvs(size=400_000, random_state=0)
    var, es = empirical_var_es(sample, 0.025)
    assert var == pytest.approx(1.95996, abs=0.01)
    assert es == pytest.approx(2.33780, abs=0.01)