"""Value at Risk and Expected Shortfall.

Conventions
-----------
- `level` is a confidence level such as 0.99; the tail probability is
  alpha = 1 - level.
- VaR and ES are reported as positive loss numbers, in the same units as
  returns (percent):

      VaR = -(mu + sigma * q),     ES = -(mu + sigma * e)

  where q is the alpha-quantile of the standardized shock z and
  e = E[z | z <= q] is its expected value in the tail beyond q.
- ES averages the losses beyond VaR, so ES >= VaR at the same level.
"""

from __future__ import annotations

import numpy as np
from scipy import integrate, stats

from var_es.models.distributions import DISTRIBUTIONS, SkewT


def standardized_quantile(dist: str, shape, alpha: float) -> float:
    """The alpha-quantile q of the standardized shock distribution."""
    return float(DISTRIBUTIONS[dist].ppf(alpha, shape))


def standardized_es(dist: str, shape, alpha: float) -> float:
    """E[z | z <= q_alpha] for the standardized shock distribution (a negative number).

    Closed forms for all three distributions. For Hansen's skewed-t, the left
    tail (alpha < (1 - lambda)/2) is a shifted and rescaled Student-t, so

        ES = [(1 - lambda) * sqrt((nu-2)/nu) * ES_t(alpha / (1 - lambda)) - a] / b

    where ES_t is the lower-tail ES of a standard t. Otherwise ES falls back to
    numerical integration of the quantile function, (1/alpha) * integral of
    q(u) du from 0 to alpha.
    """
    if dist == "normal":
        q = stats.norm.ppf(alpha)
        return float(-stats.norm.pdf(q) / alpha)

    if dist == "t":
        (nu,) = shape
        return float(_standard_t_es(nu, alpha) * np.sqrt((nu - 2) / nu))  # rescale to unit variance

    if dist == "skewt":
        nu, lam = shape
        if alpha < (1 - lam) / 2:
            a, b, _ = SkewT.constants(nu, lam)
            es_t = _standard_t_es(nu, alpha / (1 - lam))
            return float(((1 - lam) * np.sqrt((nu - 2) / nu) * es_t - a) / b)

    return numerical_es(dist, shape, alpha)


def _standard_t_es(nu: float, alpha: float) -> float:
    """Lower-tail ES of a standard (not rescaled) Student-t."""
    t_q = stats.t.ppf(alpha, nu)
    return float(-(nu + t_q**2) / (nu - 1) * stats.t.pdf(t_q, nu) / alpha)


def numerical_es(dist: str, shape, alpha: float) -> float:
    """ES by integrating the quantile function over the tail (any distribution)."""
    ppf = DISTRIBUTIONS[dist].ppf
    value, _ = integrate.quad(lambda u: float(ppf(u, shape)), 0.0, alpha, limit=200)
    return float(value / alpha)


def parametric_var_es(mu: float, sigma: float, q: float, e: float) -> tuple[float, float]:
    """VaR and ES from a location, scale and standardized tail quantities."""
    return -(mu + sigma * q), -(mu + sigma * e)


def empirical_var_es(sample: np.ndarray, alpha: float) -> tuple[float, float]:
    """VaR and ES from the empirical distribution of a sample (positive losses).

    The quantile uses linear interpolation (numpy's default); ES averages
    the observations at or below that quantile.
    """
    sample = np.asarray(sample, dtype=float)
    q = float(np.quantile(sample, alpha))
    return -q, -float(sample[sample <= q].mean())