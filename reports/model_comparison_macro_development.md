# Model comparison: development period

4,027 one-day-ahead forecasts per model (2004-2019). Lower loss is better. Diebold-Mariano (DM) tests compare each model with the benchmark, **GJR-GARCH(1,1)-skewt (expanding)**, using Newey-West standard errors; a positive DM t means the model has higher loss than the benchmark. The Model Confidence Set (MCS, 90%) uses a stationary bootstrap with mean block length 10 and 10,000 replications. The locked test period is not used.

## Summary

Rank by average loss; * marks models in the 90% MCS.

| model | VaR 99.0%, tick loss | VaR 97.5%, tick loss | VaR and ES 97.5%, FZ0 loss | variance, QLIKE vs gk_overnight | variance, QLIKE vs sq_ret | variance, QLIKE vs parkinson | variance, MSE vs gk_overnight |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: credit_spread | 3 * | 2 * | 1 * | 1 * | 1 * | 3 * | 4 * |
| MIDAS-skewt: term_spread | 1 * | 1 * | 2 * | 3 | 3 * | 1 * | 2 * |
| MIDAS-skewt: ip_growth | 4 * | 4 * | 3 * | 4 | 4 * | 4 | 3 * |
| GJR-GARCH(1,1)-skewt (expanding) | 2 * | 3 * | 4 * | 2 | 2 * | 2 * | 1 * |

## VaR 99.0%, tick loss

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: term_spread | 0.0322 | 1 | -0.37% | -1.1035 | 0.2698 | 1.0000 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.0324 | 2 | +0.00% | n/a | n/a | 0.8774 | yes |
| MIDAS-skewt: credit_spread | 0.0324 | 3 | +0.05% | 0.0642 | 0.9488 | 0.8774 | yes |
| MIDAS-skewt: ip_growth | 0.0325 | 4 | +0.30% | 0.4360 | 0.6628 | 0.8465 | yes |

## VaR 97.5%, tick loss

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: term_spread | 0.0669 | 1 | -0.34% | -1.8888 | 0.0589 | 1.0000 | yes |
| MIDAS-skewt: credit_spread | 0.0670 | 2 | -0.10% | -0.1752 | 0.8609 | 0.8177 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.0671 | 3 | +0.00% | n/a | n/a | 0.8177 | yes |
| MIDAS-skewt: ip_growth | 0.0672 | 4 | +0.15% | 0.3097 | 0.7568 | 0.8177 | yes |

## VaR and ES 97.5%, FZ0 loss

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: credit_spread | 0.8902 | 1 | -0.92% | -1.0236 | 0.3060 | 1.0000 | yes |
| MIDAS-skewt: term_spread | 0.8953 | 2 | -0.35% | -0.7405 | 0.4590 | 0.6607 | yes |
| MIDAS-skewt: ip_growth | 0.8977 | 3 | -0.08% | -0.1671 | 0.8673 | 0.6607 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.8984 | 4 | +0.00% | n/a | n/a | 0.5131 | yes |

## variance, QLIKE vs gk_overnight

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: credit_spread | 0.6702 | 1 | -1.36% | -2.3088 | 0.0210 | 1.0000 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.6794 | 2 | +0.00% | n/a | n/a | 0.0398 | no |
| MIDAS-skewt: term_spread | 0.6797 | 3 | +0.04% | 0.1678 | 0.8667 | 0.0398 | no |
| MIDAS-skewt: ip_growth | 0.6843 | 4 | +0.71% | 2.2017 | 0.0277 | 0.0065 | no |

## variance, QLIKE vs sq_ret

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: credit_spread | 0.6560 | 1 | -0.84% | -1.2101 | 0.2262 | 1.0000 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.6616 | 2 | +0.00% | n/a | n/a | 0.4921 | yes |
| MIDAS-skewt: term_spread | 0.6620 | 3 | +0.07% | 0.1602 | 0.8728 | 0.4921 | yes |
| MIDAS-skewt: ip_growth | 0.6629 | 4 | +0.20% | 0.6107 | 0.5414 | 0.4921 | yes |

Note: Squared returns are unbiased but very noisy, so differences are harder to detect.

## variance, QLIKE vs parkinson

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| MIDAS-skewt: term_spread | 0.3726 | 1 | -0.75% | -1.9478 | 0.0514 | 1.0000 | yes |
| GJR-GARCH(1,1)-skewt (expanding) | 0.3755 | 2 | +0.00% | n/a | n/a | 0.3781 | yes |
| MIDAS-skewt: credit_spread | 0.3779 | 3 | +0.66% | 0.7675 | 0.4428 | 0.3781 | yes |
| MIDAS-skewt: ip_growth | 0.3881 | 4 | +3.37% | 7.5872 | 3.3e-14 | 0 | no |

Note: Parkinson misses the overnight move (it captures about two-thirds of close-to-close variance), so it is biased low and QLIKE on it favours models that under-forecast. Kept because it was pre-registered; interpret with caution.

## variance, MSE vs gk_overnight

| model | mean loss | rank | vs benchmark | DM t | DM p | MCS p | in MCS |
|---|---|---|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | 9.2654 | 1 | +0.00% | n/a | n/a | 1.0000 | yes |
| MIDAS-skewt: term_spread | 9.2692 | 2 | +0.04% | 0.1627 | 0.8708 | 0.8545 | yes |
| MIDAS-skewt: ip_growth | 9.8024 | 3 | +5.80% | 0.6571 | 0.5111 | 0.5413 | yes |
| MIDAS-skewt: credit_spread | 10.1 | 4 | +8.92% | 1.5186 | 0.1289 | 0.1268 | yes |

Note: MSE is dominated by a few crisis days, so it is less informative than QLIKE.

The MCS implementation reproduces the `arch` package's MCS(method="max") exactly when given the same bootstrap draws (see tests/test_evaluation.py).

![Cumulative FZ0 loss](figures/model_comparison_macro_development.png)
