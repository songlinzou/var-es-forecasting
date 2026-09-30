"""Loss functions for comparing risk forecasts.

A loss scores one forecast against one realized outcome; lower is better.
Each loss here is "consistent" for what it scores: its expected value is
smallest when the forecast equals the true quantity, so a model cannot
improve its average loss by deliberately misreporting.

Conventions follow the rest of the project: VaR and ES are positive loss
numbers, so the alpha-quantile of returns is -VaR and the ES of returns is -ES.

Quantile ("tick") loss, for VaR:
    L = (alpha - 1[y <= q]) * (y - q),              q = -VaR

FZ0 loss (Patton, Ziegel & Chen, 2019), for VaR and ES jointly:
    L = -1[y <= v] (v - y) / (alpha e) + v / e + ln(-e) - 1,   v = -VaR, e = -ES
    ES alone has no consistent loss function (Gneiting, 2011), but the pair
    (VaR, ES) does (Fissler & Ziegel, 2016); FZ0 is the member of that family
    whose loss differences do not depend on the units of returns.

QLIKE, for variance forecasts sigma2 against a proxy h:
    L = ln(sigma2) + h / sigma2
    This differs from the normalized form h/sigma2 - ln(h/sigma2) - 1 only by
    terms that do not involve the forecast, so model rankings are identical;
    it also stays finite on days when the proxy is exactly zero. QLIKE (and MSE)
    rank forecasts correctly even with a noisy but unbiased proxy (Patton, 2011).
"""

from __future__ import annotations

import numpy as np


def quantile_loss(returns, var, alpha: float) -> np.ndarray:
    """Tick loss of a VaR forecast at tail probability alpha."""
    y = np.asarray(returns, dtype=float)
    q = -np.asarray(var, dtype=float)
    return (alpha - (y <= q)) * (y - q)


def fz0_loss(returns, var, es, alpha: float) -> np.ndarray:
    """FZ0 joint loss of VaR and ES forecasts at tail probability alpha."""
    y = np.asarray(returns, dtype=float)
    v = -np.asarray(var, dtype=float)
    e = -np.asarray(es, dtype=float)
    if np.any(e >= 0):
        raise ValueError("FZ0 needs ES > 0 in the positive-loss convention (e = -ES < 0).")
    tail_shortfall = np.where(y <= v, v - y, 0.0)
    return -tail_shortfall / (alpha * e) + v / e + np.log(-e) - 1.0


def qlike(variance_forecast, proxy) -> np.ndarray:
    """QLIKE loss of a variance forecast against a variance proxy."""
    s2 = np.asarray(variance_forecast, dtype=float)
    if np.any(s2 <= 0):
        raise ValueError("Variance forecasts must be positive.")
    return np.log(s2) + np.asarray(proxy, dtype=float) / s2


def mse_variance(variance_forecast, proxy) -> np.ndarray:
    """Squared error of a variance forecast against a variance proxy."""
    return (np.asarray(proxy, dtype=float) - np.asarray(variance_forecast, dtype=float)) ** 2