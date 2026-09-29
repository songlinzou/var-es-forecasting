"""Standardized shock distributions (mean 0, variance 1) for GARCH models.

Each distribution provides:
    logpdf(z, shape)  log density of a standardized shock z
    ppf(q, shape)     quantile function; turns a variance forecast into VaR (Step 3)

and its shape parameters' names, bounds and starting values.

Skewed Student-t (Hansen, 1994)
-------------------------------
With nu > 2 degrees of freedom and skewness lambda in (-1, 1):

    g(z) = b c [1 + ((b z + a) / (1 - lambda))^2 / (nu - 2)]^(-(nu+1)/2)   for z < -a/b
    g(z) = b c [1 + ((b z + a) / (1 + lambda))^2 / (nu - 2)]^(-(nu+1)/2)   for z >= -a/b

    c = G((nu+1)/2) / (sqrt(pi (nu-2)) G(nu/2)),   a = 4 lambda c (nu-2)/(nu-1),
    b = sqrt(1 + 3 lambda^2 - a^2)

The constants a and b shift and rescale the density so it keeps mean 0 and
variance 1. lambda < 0 makes the left tail heavier; lambda = 0 gives the
symmetric Student-t. Same parametrization as the `arch` package.
"""

from __future__ import annotations

import numpy as np
from scipy import special, stats


class Normal:
    name = "normal"
    shape_names: list[str] = []
    shape_bounds: list[tuple[float, float]] = []
    shape_starts: list[list[float]] = [[]]

    @staticmethod
    def logpdf(z: np.ndarray, shape) -> np.ndarray:
        return -0.5 * (np.log(2 * np.pi) + z**2)

    @staticmethod
    def ppf(q, shape) -> np.ndarray:
        return stats.norm.ppf(q)


class StudentT:
    """Student-t rescaled to unit variance (a standard t has variance nu / (nu - 2))."""

    name = "t"
    shape_names = ["nu"]
    shape_bounds = [(2.05, 500.0)]
    shape_starts = [[8.0]]

    @staticmethod
    def logpdf(z: np.ndarray, shape) -> np.ndarray:
        (nu,) = shape
        const = special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
        return const - (nu + 1) / 2 * np.log1p(z**2 / (nu - 2))

    @staticmethod
    def ppf(q, shape) -> np.ndarray:
        (nu,) = shape
        return stats.t.ppf(q, nu) * np.sqrt((nu - 2) / nu)


class SkewT:
    """Hansen's (1994) skewed Student-t with unit variance."""

    name = "skewt"
    shape_names = ["nu", "lambda"]
    shape_bounds = [(2.05, 300.0), (-0.9999, 0.9999)]
    shape_starts = [[8.0, 0.0], [8.0, -0.1]]

    @staticmethod
    def constants(nu: float, lam: float) -> tuple[float, float, float]:
        """Return (a, b, log c) from the module docstring."""
        log_c = special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
        a = 4 * lam * np.exp(log_c) * (nu - 2) / (nu - 1)
        b = np.sqrt(1 + 3 * lam**2 - a**2)
        return a, b, log_c

    @classmethod
    def logpdf(cls, z: np.ndarray, shape) -> np.ndarray:
        nu, lam = shape
        a, b, log_c = cls.constants(nu, lam)
        side = np.where(z < -a / b, 1 - lam, 1 + lam)
        return np.log(b) + log_c - (nu + 1) / 2 * np.log1p(((b * z + a) / side) ** 2 / (nu - 2))

    @classmethod
    def ppf(cls, q, shape) -> np.ndarray:
        nu, lam = shape
        a, b, _ = cls.constants(nu, lam)
        q = np.asarray(q, dtype=float)
        left = q < (1 - lam) / 2
        t_quantile = np.where(
            left,
            stats.t.ppf(q / (1 - lam), nu),
            stats.t.ppf(0.5 + (q - (1 - lam) / 2) / (1 + lam), nu),
        )
        side = np.where(left, 1 - lam, 1 + lam)
        return (t_quantile * side * np.sqrt(1 - 2 / nu) - a) / b


DISTRIBUTIONS = {d.name: d for d in (Normal, StudentT, SkewT)}