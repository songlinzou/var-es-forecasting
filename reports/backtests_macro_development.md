# Backtests: development period

4,027 one-day-ahead forecasts per model (2004-2019). Tests at the 5% level. The locked test period is not used.

## Summary

| model | Kupiec 99.0% | indep. 99.0% | cond. cov. 99.0% | Kupiec 97.5% | indep. 97.5% | cond. cov. 97.5% | Z2 97.5% | McNeil-Frey 97.5% | rejections |
|---|---|---|---|---|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | pass | pass | pass | pass | pass | pass | pass | pass | 0 |
| MIDAS-skewt: credit_spread | pass | pass | pass | pass | pass | pass | pass | pass | 0 |
| MIDAS-skewt: term_spread | pass | pass | pass | pass | pass | pass | pass | pass | 0 |
| MIDAS-skewt: ip_growth | pass | pass | pass | pass | pass | pass | pass | REJECT | 1 |

With 4 models and 8 tests each, a few rejections at the 5% level would occur by chance even if every model were correct, so single borderline rejections should not be over-interpreted.

## VaR 99.0%

| model | exceptions | expected | rate | Kupiec p | independence p | cond. coverage p | P(hit after a hit) | green windows | yellow | red | worst window |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | 44 | 40.3 | 1.09% | 0.5605 | 0.0960 | 0.2112 | 4.5% | 89% | 11% | 0% | 6 |
| MIDAS-skewt: credit_spread | 45 | 40.3 | 1.12% | 0.4622 | 0.1049 | 0.2049 | 4.4% | 85% | 15% | 0% | 7 |
| MIDAS-skewt: term_spread | 46 | 40.3 | 1.14% | 0.3749 | 0.1143 | 0.1938 | 4.3% | 88% | 12% | 0% | 6 |
| MIDAS-skewt: ip_growth | 41 | 40.3 | 1.02% | 0.9082 | 0.4391 | 0.7364 | 2.4% | 90% | 10% | 0% | 6 |

Traffic light: share of rolling 250-day windows with 0-4 exceptions (green), 5-9 (yellow) and 10 or more (red).

## VaR 97.5%

| model | exceptions | expected | rate | Kupiec p | independence p | cond. coverage p | P(hit after a hit) |
|---|---|---|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | 111 | 100.7 | 2.76% | 0.3051 | 0.9716 | 0.5907 | 2.7% |
| MIDAS-skewt: credit_spread | 110 | 100.7 | 2.73% | 0.3536 | 0.5736 | 0.5552 | 3.6% |
| MIDAS-skewt: term_spread | 114 | 100.7 | 2.83% | 0.1877 | 0.6693 | 0.3832 | 3.5% |
| MIDAS-skewt: ip_growth | 105 | 100.7 | 2.61% | 0.6646 | 0.8729 | 0.8987 | 2.9% |

## ES 97.5%

| model | Z2 | Z2 p | mean exceedance residual | McNeil-Frey p |
|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | -0.1431 | 0.0884 | 0.0368 | 0.0848 |
| MIDAS-skewt: credit_spread | -0.1276 | 0.1084 | 0.0320 | 0.1222 |
| MIDAS-skewt: term_spread | -0.1709 | 0.0542 | 0.0340 | 0.1088 |
| MIDAS-skewt: ip_growth | -0.0902 | 0.1946 | 0.0453 | 0.0486 |

Z2 < 0 and a positive mean exceedance residual both indicate that ES is too low. p-values are one-sided (small when ES is underestimated), from a studentized bootstrap.

![Traffic light](figures/backtests_macro_development.png)
