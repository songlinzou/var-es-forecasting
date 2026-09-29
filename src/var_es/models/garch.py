"""GARCH-family volatility models, estimated by maximum likelihood from scratch.

Model: constant mean with GJR-GARCH(1,1) variance.

    r_t      = mu + eps_t,        eps_t = sigma_t * z_t
    sigma2_t = omega + (alpha + gamma * 1[eps_{t-1} < 0]) * eps_{t-1}^2 + beta * sigma2_{t-1}

The z_t are independent with mean 0 and variance 1: either standard normal,
or Student-t rescaled to unit variance (nu > 2 degrees of freedom).
Setting gamma = 0 gives the symmetric GARCH(1,1).

How the likelihood is built
---------------------------
Given sigma2_t, the return r_t has density f(eps_t / sigma_t) / sigma_t, where
f is the density of z_t. Because sigma2_t depends only on past data, the joint
density of all returns factorizes into these one-step conditional densities,
so the log-likelihood is a simple sum over days:

    normal:   l_t = -1/2 * [ ln(2 pi) + ln(sigma2_t) + eps_t^2 / sigma2_t ]

    Student-t (unit variance):
              l_t = ln G((nu+1)/2) - ln G(nu/2) - 1/2 ln(pi (nu-2)) - 1/2 ln(sigma2_t)
                    - (nu+1)/2 * ln(1 + eps_t^2 / ((nu-2) sigma2_t))

where G is the gamma function. The (nu - 2) terms come from rescaling a
Student-t, whose variance is nu / (nu - 2), to have variance 1.

Constraints: omega > 0, alpha >= 0, alpha + gamma >= 0, beta >= 0 keep the
variance positive; alpha + gamma/2 + beta < 1 keeps it stationary (gamma is
halved because a symmetric shock is negative half the time).

The recursion is started from a backcast: an exponentially weighted average
of the first 75 squared residuals, the same convention as the `arch` package.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, signal, special

DISTRIBUTIONS = ("normal", "t")
NU_BOUNDS = (2.05, 500.0)
_BOUND_TOLERANCE = 1e-6


# --- Specification ------------------------------------------------------------------------


@dataclass(frozen=True)
class GarchSpec:
    """Which GARCH-family model to estimate."""

    asymmetric: bool = False  # add the GJR leverage term gamma
    dist: str = "normal"  # "normal" or "t"

    def __post_init__(self) -> None:
        if self.dist not in DISTRIBUTIONS:
            raise ValueError(f"dist must be one of {DISTRIBUTIONS}, got {self.dist!r}")

    @property
    def name(self) -> str:
        variance = "GJR-GARCH(1,1)" if self.asymmetric else "GARCH(1,1)"
        return f"{variance}-{'t' if self.dist == 't' else 'normal'}"

    @property
    def param_names(self) -> list[str]:
        names = ["mu", "omega", "alpha"]
        if self.asymmetric:
            names.append("gamma")
        names.append("beta")
        if self.dist == "t":
            names.append("nu")
        return names


# --- Core computations ------------------------------------------------------------------------


def backcast(resid: np.ndarray) -> float:
    """Starting value for the recursion: EWMA (decay 0.94) of the first 75 squared residuals."""
    tau = min(75, len(resid))
    weights = 0.94 ** np.arange(tau)
    return float(np.sum(resid[:tau] ** 2 * weights / weights.sum()))


def conditional_variance(
    omega: float, alpha: float, gamma: float, beta: float, resid: np.ndarray, start: float
) -> np.ndarray:
    """Run the GJR-GARCH(1,1) variance recursion.

    The recursion sigma2_t = x_t + beta * sigma2_{t-1} is a first-order linear
    filter, so scipy's lfilter computes it in compiled code. It gives exactly
    the same numbers as a Python loop (see the tests), just much faster.
    For t = 0 the unobserved lagged values are replaced by `start`, and the
    asymmetric term by half of it.
    """
    eps2 = resid**2
    shock = np.empty_like(resid)
    shock[0] = (alpha + 0.5 * gamma) * start
    shock[1:] = (alpha + gamma * (resid[:-1] < 0)) * eps2[:-1]
    sigma2, _ = signal.lfilter([1.0], [1.0, -beta], omega + shock, zi=[beta * start])
    return sigma2


def log_likelihood_terms(
    params: np.ndarray, returns: np.ndarray, spec: GarchSpec, start: float
) -> np.ndarray:
    """Per-day log-likelihood contributions l_t (see the module docstring)."""
    p = _unpack(params, spec)
    resid = returns - p["mu"]
    sigma2 = conditional_variance(p["omega"], p["alpha"], p["gamma"], p["beta"], resid, start)
    if not np.all(np.isfinite(sigma2)) or np.any(sigma2 <= 0):
        return np.full(len(returns), -np.inf)

    if spec.dist == "normal":
        return -0.5 * (np.log(2 * np.pi) + np.log(sigma2) + resid**2 / sigma2)

    nu = p["nu"]
    const = (
        special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
    )
    return const - 0.5 * np.log(sigma2) - (nu + 1) / 2 * np.log1p(resid**2 / ((nu - 2) * sigma2))


# --- Estimation ----------------------------------------------------------------------------


@dataclass
class GarchResult:
    """A fitted GARCH-family model."""

    spec: GarchSpec
    params: pd.Series
    std_errors: pd.Series  # robust (Bollerslev-Wooldridge); NaN for parameters at a bound
    loglik: float
    n_obs: int
    converged: bool
    message: str
    conditional_variance: pd.Series
    std_resid: pd.Series
    last_resid: float

    @property
    def n_params(self) -> int:
        return len(self.params)

    @property
    def aic(self) -> float:
        return 2 * self.n_params - 2 * self.loglik

    @property
    def bic(self) -> float:
        return self.n_params * np.log(self.n_obs) - 2 * self.loglik

    @property
    def persistence(self) -> float:
        """alpha + gamma/2 + beta: how slowly variance shocks die out."""
        p = self.params
        return float(p["alpha"] + 0.5 * p.get("gamma", 0.0) + p["beta"])

    @property
    def half_life(self) -> float:
        """Days for a variance shock to decay by half."""
        return float(np.log(0.5) / np.log(self.persistence))

    @property
    def unconditional_variance(self) -> float:
        return float(self.params["omega"] / (1 - self.persistence))

    def forecast_variance(self) -> float:
        """One-step-ahead variance forecast for the day after the sample."""
        p = self.params
        gamma = p.get("gamma", 0.0)
        eps2 = self.last_resid**2
        last_sigma2 = float(self.conditional_variance.iloc[-1])
        return float(
            p["omega"] + (p["alpha"] + gamma * (self.last_resid < 0)) * eps2 + p["beta"] * last_sigma2
        )

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "estimate": self.params,
                "robust_se": self.std_errors,
                "t_stat": self.params / self.std_errors,
            }
        )


def fit_garch(returns: pd.Series, spec: GarchSpec = GarchSpec()) -> GarchResult:
    """Estimate a GARCH-family model by maximum likelihood.

    Uses SLSQP (which handles the stationarity inequality constraint) from the
    best of a small grid of starting values.
    """
    y = pd.Series(returns, dtype=float).dropna()
    arr = y.to_numpy()
    start = backcast(arr - arr.mean())
    bounds = _bounds(arr, spec)

    def objective(theta: np.ndarray) -> float:
        value = -np.mean(log_likelihood_terms(theta, arr, spec, start))
        return value if np.isfinite(value) else 1e10

    x0 = min(_starting_values(arr, spec), key=objective)
    result = optimize.minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=_constraints(spec),
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    theta = result.x
    terms = log_likelihood_terms(theta, arr, spec, start)
    p = _unpack(theta, spec)
    resid = arr - p["mu"]
    sigma2 = conditional_variance(p["omega"], p["alpha"], p["gamma"], p["beta"], resid, start)

    return GarchResult(
        spec=spec,
        params=pd.Series(theta, index=spec.param_names),
        std_errors=pd.Series(
            _robust_std_errors(theta, arr, spec, start, bounds), index=spec.param_names
        ),
        loglik=float(terms.sum()),
        n_obs=len(arr),
        converged=bool(result.success),
        message=str(result.message),
        conditional_variance=pd.Series(sigma2, index=y.index, name="sigma2"),
        std_resid=pd.Series(resid / np.sqrt(sigma2), index=y.index, name="std_resid"),
        last_resid=float(resid[-1]),
    )


# --- Helpers -------------------------------------------------------------------------------


def _unpack(theta: np.ndarray, spec: GarchSpec) -> dict[str, float]:
    p = dict(zip(spec.param_names, theta))
    p.setdefault("gamma", 0.0)
    return p


def _bounds(arr: np.ndarray, spec: GarchSpec) -> list[tuple[float, float]]:
    var = float(np.var(arr))
    scale = float(np.max(np.abs(arr)))
    bounds = {
        "mu": (-scale, scale),
        "omega": (1e-8 * var, 10 * var),
        "alpha": (0.0, 1.0),
        "gamma": (-1.0, 2.0),  # alpha + gamma >= 0 is imposed as a constraint
        "beta": (0.0, 1.0),
        "nu": NU_BOUNDS,
    }
    return [bounds[name] for name in spec.param_names]


def _constraints(spec: GarchSpec) -> list[dict]:
    names = spec.param_names
    i_alpha, i_beta = names.index("alpha"), names.index("beta")
    i_gamma = names.index("gamma") if spec.asymmetric else None

    def stationarity(theta):
        gamma = theta[i_gamma] if i_gamma is not None else 0.0
        return 1.0 - (theta[i_alpha] + 0.5 * gamma + theta[i_beta])

    constraints = [{"type": "ineq", "fun": stationarity}]
    if i_gamma is not None:
        constraints.append({"type": "ineq", "fun": lambda theta: theta[i_alpha] + theta[i_gamma]})
    return constraints


def _starting_values(arr: np.ndarray, spec: GarchSpec) -> list[np.ndarray]:
    mu, var = float(arr.mean()), float(arr.var())
    gammas = (0.0, 0.1) if spec.asymmetric else (0.0,)
    candidates = []
    for alpha, beta, gamma in itertools.product((0.03, 0.06, 0.1), (0.85, 0.9, 0.94), gammas):
        persistence = alpha + gamma / 2 + beta
        if persistence >= 0.995:
            continue
        values = {"mu": mu, "omega": var * (1 - persistence), "alpha": alpha,
                  "gamma": gamma, "beta": beta, "nu": 8.0}
        candidates.append(np.array([values[name] for name in spec.param_names]))
    return candidates


def _robust_std_errors(
    theta: np.ndarray, arr: np.ndarray, spec: GarchSpec, start: float, bounds: list
) -> np.ndarray:
    """Bollerslev-Wooldridge robust standard errors: A^-1 B A^-1.

    A is minus the Hessian of the log-likelihood and B the outer product of
    the per-day scores, both by central finite differences. They stay valid
    if the assumed distribution of z_t is wrong (quasi-maximum likelihood).
    Parameters sitting on a bound have no standard error in the usual sense,
    so they are held fixed and reported as NaN.
    """
    at_bound = np.array(
        [min(abs(v - lo), abs(v - hi)) < _BOUND_TOLERANCE * max(1.0, abs(v))
         for v, (lo, hi) in zip(theta, bounds)]
    )
    free = np.flatnonzero(~at_bound)
    se = np.full(len(theta), np.nan)
    if len(free) == 0:
        return se

    def terms(sub: np.ndarray) -> np.ndarray:
        full = theta.copy()
        full[free] = sub
        return log_likelihood_terms(full, arr, spec, start)

    x = theta[free]
    h = 1e-4 * np.maximum(np.abs(x), 1e-2)
    k = len(x)

    scores = np.empty((len(arr), k))
    for i in range(k):
        step = np.zeros(k)
        step[i] = h[i]
        scores[:, i] = (terms(x + step) - terms(x - step)) / (2 * h[i])

    hessian = np.empty((k, k))
    for i in range(k):
        for j in range(i, k):
            ei, ej = np.zeros(k), np.zeros(k)
            ei[i], ej[j] = h[i], h[j]
            value = (
                terms(x + ei + ej).sum() - terms(x + ei - ej).sum()
                - terms(x - ei + ej).sum() + terms(x - ei - ej).sum()
            ) / (4 * h[i] * h[j])
            hessian[i, j] = hessian[j, i] = value

    try:
        a_inv = np.linalg.inv(-hessian)
    except np.linalg.LinAlgError:
        return se
    cov = a_inv @ (scores.T @ scores) @ a_inv
    se[free] = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    return se