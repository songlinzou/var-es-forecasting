"""GJR-GARCH-MIDAS (Engle, Ghysels & Sohn, 2013), estimated from scratch.

Model
-----
    r_t      = mu + eps_t,         eps_t = sqrt(tau_m * g_t) * z_t

Long-run component, constant within calendar month m and driven by the K
most recent monthly macro values known at the start of m:

    ln tau_m = m0 + theta * sum_{k=1..K} phi_k(w) * X_{m,k}

Short-run component, a GJR-GARCH with unconditional mean 1:

    g_t = (1 - alpha - gamma/2 - beta)
          + (alpha + gamma * 1[eps_{t-1} < 0]) * eps_{t-1}^2 / tau_{m(t-1)}
          + beta * g_{t-1}

Lag weights: restricted beta weights with w >= 1,

    phi_k(w) = (1 - k/(K+1))^(w-1) / sum_j (1 - j/(K+1))^(w-1)

w = 1 weights all K months equally; larger w puts more weight on recent months.

Nesting: with theta = 0, tau is constant, and sigma2_t = tau * g_t is exactly
a GJR-GARCH(1,1) with omega = tau * (1 - persistence). The recursion is
started with the same backcast as var_es.models.garch, so the two
log-likelihoods agree exactly in that case (see the tests). Note that w has
no effect when theta = 0, so a likelihood-ratio test of theta = 0 does not
have the usual chi-squared distribution (Davies, 1987); it is reported as
descriptive evidence only.

theta is in units of ln(variance) per unit of X: a one-unit rise in the
weighted macro variable multiplies the long-run variance by exp(theta).

Estimation: profile likelihood over w
-------------------------------------
The lag-shape parameter w is weakly identified and the likelihood can have
more than one peak in it (e.g. equal weights with theta < 0, or weight on the
last month with theta > 0). A joint optimizer started from the wrong side can
stall on the flat ridge between them. So w is first fixed at each value in
W_GRID while the other parameters are estimated (a well-behaved problem), the
best of these is kept, and a final joint step refines w from there. The joint
refinement is kept only if it converges to a higher likelihood.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, signal

from var_es.models.distributions import DISTRIBUTIONS
from var_es.models.garch import backcast
from var_es.models.inference import robust_std_errors

VARIANCE_NAMES = ["mu", "alpha", "gamma", "beta", "m0", "theta", "w"]
W_BOUNDS = (1.0, 50.0)
W_GRID = (1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)
# The profile fits only need to find the right peak; the final joint step is precise.
_PROFILE_OPTIONS = {"maxiter": 500, "ftol": 1e-9}
_JOINT_OPTIONS = {"maxiter": 500, "ftol": 1e-11}


def beta_weights(w: float, n_lags: int) -> np.ndarray:
    """Restricted beta lag weights phi_1..phi_K; they sum to 1."""
    k = np.arange(1, n_lags + 1)
    raw = (1 - k / (n_lags + 1)) ** (w - 1)
    return raw / raw.sum()


@dataclass(frozen=True)
class MidasSpec:
    dist: str = "skewt"

    def __post_init__(self) -> None:
        if self.dist not in DISTRIBUTIONS:
            raise ValueError(f"dist must be one of {list(DISTRIBUTIONS)}, got {self.dist!r}")

    @property
    def distribution(self):
        return DISTRIBUTIONS[self.dist]

    @property
    def param_names(self) -> list[str]:
        return VARIANCE_NAMES + self.distribution.shape_names

    @property
    def name(self) -> str:
        return f"GJR-GARCH-MIDAS-{self.dist}"


# --- Core computations -----------------------------------------------------------------------------


def daily_macro(returns_index: pd.DatetimeIndex, monthly_lags: pd.DataFrame) -> np.ndarray:
    """(n_days, K) matrix: each day gets the lag row of its calendar month."""
    lag_cols = [c for c in monthly_lags.columns if c.startswith("lag_")]
    months = pd.DatetimeIndex(returns_index).to_period("M")
    missing = months.difference(monthly_lags.index)
    if len(missing):
        raise ValueError(f"No macro lags for months: {[str(m) for m in missing[:5]]}")
    x = monthly_lags.loc[months, lag_cols].to_numpy(dtype=float)
    if np.isnan(x).any():
        raise ValueError("Macro lags contain NaN: not enough macro history for some months.")
    return x


def components(
    params: dict, returns: np.ndarray, x_daily: np.ndarray, start: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return (tau, g) for every day."""
    tau = np.exp(params["m0"] + params["theta"] * (x_daily @ beta_weights(params["w"], x_daily.shape[1])))
    resid = returns - params["mu"]
    alpha, gamma, beta = params["alpha"], params["gamma"], params["beta"]
    persistence = alpha + 0.5 * gamma + beta

    scaled = resid**2 / tau
    g_start = start / tau[0]
    shock = np.empty_like(resid)
    shock[0] = (alpha + 0.5 * gamma) * g_start
    shock[1:] = (alpha + gamma * (resid[:-1] < 0)) * scaled[:-1]
    g, _ = signal.lfilter([1.0], [1.0, -beta], (1 - persistence) + shock, zi=[beta * g_start])
    return tau, g


def log_likelihood_terms(
    theta_vec: np.ndarray, returns: np.ndarray, x_daily: np.ndarray, spec: MidasSpec, start: float
) -> np.ndarray:
    p = dict(zip(spec.param_names, theta_vec))
    tau, g = components(p, returns, x_daily, start)
    sigma2 = tau * g
    if not np.all(np.isfinite(sigma2)) or np.any(sigma2 <= 0):
        return np.full(len(returns), -np.inf)
    z = (returns - p["mu"]) / np.sqrt(sigma2)
    shape = [p[n] for n in spec.distribution.shape_names]
    return spec.distribution.logpdf(z, shape) - 0.5 * np.log(sigma2)


def one_step_variance(
    params: pd.Series, returns: np.ndarray, x_daily: np.ndarray, x_next: np.ndarray
) -> float:
    """Variance forecast for the day after `returns`.

    x_next is the lag row of the next day's month (possibly a new month).
    """
    p = params.to_dict()
    arr = np.asarray(returns, dtype=float)
    tau, g = components(p, arr, x_daily, backcast(arr - arr.mean()))
    resid = arr[-1] - p["mu"]
    persistence = p["alpha"] + 0.5 * p["gamma"] + p["beta"]
    g_next = (1 - persistence) + (p["alpha"] + p["gamma"] * (resid < 0)) * resid**2 / tau[-1] + p["beta"] * g[-1]
    tau_next = np.exp(p["m0"] + p["theta"] * (np.asarray(x_next) @ beta_weights(p["w"], len(x_next))))
    return float(tau_next * g_next)


# --- Estimation ---------------------------------------------------------------------------------------


@dataclass
class MidasResult:
    spec: MidasSpec
    params: pd.Series
    std_errors: pd.Series
    loglik: float
    n_obs: int
    converged: bool
    message: str
    tau: pd.Series
    g: pd.Series
    n_lags: int
    profile: dict  # w -> best parameter vector at that w; warm starts for the next refit

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
        p = self.params
        return float(p["alpha"] + 0.5 * p["gamma"] + p["beta"])

    @property
    def conditional_variance(self) -> pd.Series:
        return (self.tau * self.g).rename("sigma2")

    @property
    def variance_ratio(self) -> float:
        """Share of the variation in ln(variance) due to the long-run component."""
        return float(np.var(np.log(self.tau)) / np.var(np.log(self.conditional_variance)))

    @property
    def weights(self) -> np.ndarray:
        return beta_weights(float(self.params["w"]), self.n_lags)


def fit_midas(
    returns: pd.Series,
    monthly_lags: pd.DataFrame,
    spec: MidasSpec = MidasSpec(),
    start_params: pd.Series | None = None,
    std_errors: bool = True,
    profile_starts: dict | None = None,
) -> MidasResult:
    """Estimate GJR-GARCH-MIDAS by maximum likelihood, profiling over w.

    Starting values for each fixed-w fit: that w's solution from the previous
    refit (profile_starts, taken from the previous result's .profile) if
    available, else start_params, else a grid.
    """
    y = pd.Series(returns, dtype=float).dropna()
    arr = y.to_numpy()
    x_daily = daily_macro(y.index, monthly_lags)
    start = backcast(arr - arr.mean())
    bounds = _bounds(arr, x_daily, spec)
    names = spec.param_names
    iw = names.index("w")

    def objective(v: np.ndarray) -> float:
        value = -np.mean(log_likelihood_terms(v, arr, x_daily, spec, start))
        return value if np.isfinite(value) else 1e10

    if start_params is not None:
        lo, hi = zip(*bounds)
        base = [np.clip(start_params[names].to_numpy(dtype=float), lo, hi)]
    else:
        base = _starting_values(arr, x_daily, spec)

    # 1. Profile: for each fixed w, estimate the other parameters.
    free_bounds = bounds[:iw] + bounds[iw + 1 :]
    lo, hi = zip(*bounds)
    best_value, best_v, best_ok, best_msg = np.inf, None, False, ""
    profile = {}
    for w in W_GRID:
        candidates = [np.insert(np.delete(c, iw), iw, w) for c in base]
        if profile_starts and w in profile_starts:
            candidates.append(np.clip(np.asarray(profile_starts[w], dtype=float), lo, hi))
        x0 = min(candidates, key=objective)

        def fixed_w(v_free, w=w):
            return objective(np.insert(v_free, iw, w))

        result = optimize.minimize(
            fixed_w, np.delete(x0, iw), method="SLSQP", bounds=free_bounds,
            constraints=_constraints(spec, fixed_w=w), options=_PROFILE_OPTIONS,
        )
        profile[w] = np.insert(result.x, iw, w)
        if result.fun < best_value:
            best_value, best_v = result.fun, profile[w]
            best_ok, best_msg = bool(result.success), str(result.message)

    # 2. Refine w jointly, keeping the result only if it converges and improves.
    joint = optimize.minimize(
        objective, best_v, method="SLSQP", bounds=bounds,
        constraints=_constraints(spec), options=_JOINT_OPTIONS,
    )
    if joint.success and joint.fun <= best_value:
        v, converged, message = joint.x, True, str(joint.message)
    else:
        v, converged, message = best_v, best_ok, f"profile over w (joint step: {joint.message})"

    p = dict(zip(names, v))
    tau, g = components(p, arr, x_daily, start)
    se = (
        robust_std_errors(lambda t: log_likelihood_terms(t, arr, x_daily, spec, start), v, bounds)
        if std_errors else np.full(len(v), np.nan)
    )
    return MidasResult(
        spec=spec,
        params=pd.Series(v, index=names),
        std_errors=pd.Series(se, index=names),
        loglik=float(log_likelihood_terms(v, arr, x_daily, spec, start).sum()),
        n_obs=len(arr),
        converged=converged,
        message=message,
        tau=pd.Series(tau, index=y.index, name="tau"),
        g=pd.Series(g, index=y.index, name="g"),
        n_lags=x_daily.shape[1],
        profile=profile,
    )


def _bounds(arr: np.ndarray, x_daily: np.ndarray, spec: MidasSpec) -> list[tuple[float, float]]:
    log_var = float(np.log(np.var(arr)))
    x_scale = float(np.std(x_daily)) or 1.0
    scale = float(np.max(np.abs(arr)))
    variance = {
        "mu": (-scale, scale),
        "alpha": (0.0, 1.0),
        "gamma": (-1.0, 2.0),
        "beta": (0.0, 1.0),
        "m0": (log_var - 10, log_var + 10),
        "theta": (-5.0 / x_scale, 5.0 / x_scale),  # at most a factor e^5 per standard deviation
        "w": W_BOUNDS,
    }
    return [variance[n] for n in VARIANCE_NAMES] + list(spec.distribution.shape_bounds)


def _constraints(spec: MidasSpec, fixed_w: float | None = None) -> list[dict]:
    """Stationarity and positivity; with fixed_w, for the vector without w."""
    names = spec.param_names if fixed_w is None else [n for n in spec.param_names if n != "w"]
    i = {name: names.index(name) for name in ("alpha", "gamma", "beta")}
    return [
        {"type": "ineq", "fun": lambda v: 1.0 - (v[i["alpha"]] + 0.5 * v[i["gamma"]] + v[i["beta"]])},
        {"type": "ineq", "fun": lambda v: v[i["alpha"]] + v[i["gamma"]]},
    ]


def _starting_values(arr: np.ndarray, x_daily: np.ndarray, spec: MidasSpec) -> list[np.ndarray]:
    mu, log_var = float(arr.mean()), float(np.log(arr.var()))
    x_mean = float(x_daily.mean())
    x_scale = float(np.std(x_daily)) or 1.0
    candidates = []
    grid = itertools.product(
        (0.02, 0.06), (0.1,), (0.85, 0.9), (-0.3, 0.0, 0.3), (1.0,), spec.distribution.shape_starts
    )  # w is set by the profile loop
    for alpha, gamma, beta, theta_sd, w, shape in grid:
        theta = theta_sd / x_scale
        m0 = log_var - theta * x_mean  # centre ln(tau) on the sample variance
        values = {"mu": mu, "alpha": alpha, "gamma": gamma, "beta": beta, "m0": m0, "theta": theta, "w": w}
        candidates.append(np.array([values[n] for n in VARIANCE_NAMES] + list(shape)))
    return candidates