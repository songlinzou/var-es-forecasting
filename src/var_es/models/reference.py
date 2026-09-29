"""Reference estimates from the `arch` package, used only to validate
the from-scratch implementation in var_es.models.garch."""

from __future__ import annotations

import pandas as pd

from var_es.models.garch import GarchSpec

_ARCH_NAMES = {"alpha[1]": "alpha", "gamma[1]": "gamma", "beta[1]": "beta", "eta": "nu"}
_ARCH_DISTS = {"normal": "normal", "t": "t", "skewt": "skewt"}


def fit_arch_reference(returns: pd.Series, spec: GarchSpec) -> dict:
    """Fit the same model with `arch` and return params, robust SEs and log-likelihood."""
    from arch import arch_model

    model = arch_model(
        pd.Series(returns, dtype=float).dropna(),
        mean="Constant",
        vol="GARCH",
        p=1,
        o=1 if spec.asymmetric else 0,
        q=1,
        dist=_ARCH_DISTS[spec.dist],
        rescale=False,
    )
    result = model.fit(disp="off")
    params = result.params.rename(index=_ARCH_NAMES)[spec.param_names]
    std_errors = result.std_err.rename(index=_ARCH_NAMES)[spec.param_names]
    return {"params": params, "std_errors": std_errors, "loglik": float(result.loglikelihood)}