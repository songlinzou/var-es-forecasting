# GARCH-family models: in-sample estimates

Sample: 2000-01-04 to 2019-12-31 (5,030 daily returns, in percent). The locked test period is excluded.
All models have a constant mean. Estimated from scratch by maximum likelihood (`src/var_es/models/garch.py`).

## Parameter estimates (robust standard errors)

| parameter | GARCH(1,1)-normal | GARCH(1,1)-t | GJR-GARCH(1,1)-normal | GJR-GARCH(1,1)-t |
|---|---|---|---|---|
| mu | 0.0671 (0.0111) | 0.0812 (0.0099) | 0.0284 (0.0108) | 0.0527 (0.0099) |
| omega | 0.0216 (0.0050) | 0.0122 (0.0032) | 0.0227 (0.0040) | 0.0165 (0.0032) |
| alpha | 0.1182 (0.0137) | 0.1205 (0.0135) | 0 (at bound) | 5.9e-18 (at bound) |
| gamma | - | - | 0.2000 (0.0218) | 0.2123 (0.0247) |
| beta | 0.8649 (0.0139) | 0.8776 (0.0127) | 0.8772 (0.0116) | 0.8790 (0.0124) |
| nu | - | 6.0688 (0.5422) | - | 6.8086 (0.6776) |

"at bound": the estimate sits on a constraint (e.g. alpha = 0), where the usual standard error is not defined.

## Model comparison

|  | GARCH(1,1)-normal | GARCH(1,1)-t | GJR-GARCH(1,1)-normal | GJR-GARCH(1,1)-t |
|---|---|---|---|---|
| log-likelihood | -6,815.3 | -6,694.6 | -6,703.8 | -6,606.9 |
| AIC | 13,638.6 | 13,399.3 | 13,417.7 | 13,225.7 |
| BIC | 13,664.7 | 13,431.9 | 13,450.3 | 13,264.9 |
| persistence | 0.9831 | 0.9981 | 0.9773 | 0.9852 |
| half-life (days) | 40.6 | 364.9 | 30.1 | 46.4 |
| unconditional vol (%, annualized) | 17.9 | 40.2 | 15.9 | 16.7 |

Lowest AIC: **GJR-GARCH(1,1)-t**. Lowest BIC: **GJR-GARCH(1,1)-t**.

## Standardized residual diagnostics

If a model captures the volatility dynamics, its standardized residuals should show no remaining ARCH effects (high p-values). Remaining excess kurtosis indicates fat tails that normal shocks cannot capture.

|  | raw returns | GARCH(1,1)-normal | GARCH(1,1)-t | GJR-GARCH(1,1)-normal | GJR-GARCH(1,1)-t |
|---|---|---|---|---|---|
| skewness | -0.0731 | -0.5272 | -0.5886 | -0.5334 | -0.5680 |
| excess kurtosis | 10.6 | 1.9249 | 2.3944 | 1.7538 | 2.0382 |
| Ljung-Box p, z (10 lags) | 2.2e-07 | 0.0304 | 0.0253 | 0.0606 | 0.0297 |
| Ljung-Box p, z squared (10 lags) | 0 | 0.1194 | 0.3086 | 0.1242 | 0.1183 |
| ARCH-LM p (5 lags) | 1.3e-251 | 0.4643 | 0.6553 | 0.1453 | 0.0946 |

## Validation against the `arch` package

|  | GARCH(1,1)-normal | GARCH(1,1)-t | GJR-GARCH(1,1)-normal | GJR-GARCH(1,1)-t |
|---|---|---|---|---|
| max abs parameter difference | 8.2e-07 | 0.0001 | 1.1e-06 | 0.0002 |
| max relative SE difference | 9.9e-05 | 0.0001 | 0.1086 | 0.0923 |
| log-likelihood difference | 1.7e-08 | 2.3e-07 | 1.0e-07 | 6.2e-08 |
| parameters at bound | none | none | alpha | alpha |
| converged | True | True | True | True |

Parameter estimates and log-likelihoods should agree closely. When a parameter sits on its bound, standard errors differ by design: this implementation holds that parameter fixed, while `arch` treats it as free, which also changes the standard errors of parameters correlated with it.

## Figure

![Conditional volatility](figures/garch_conditional_volatility.png)
