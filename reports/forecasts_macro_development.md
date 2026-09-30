# Macro-augmented forecasts: development period

4,027 one-day-ahead forecasts, 2004-01-02 to 2019-12-31. All models use an expanding window from 2000 and are re-estimated every 5 days. The locked test period is not used.

| model | exceptions 99.0% | rate 99.0% (target 1.0%) | exceptions 97.5% | rate 97.5% (target 2.5%) | mean ES 97.5% | refits | failed refits |
|---|---|---|---|---|---|---|---|
| GJR-GARCH(1,1)-skewt (expanding) | 44 | 1.09% | 111 | 2.76% | 2.6236 | 806 | 0 |
| MIDAS-skewt: credit_spread | 44 | 1.09% | 107 | 2.66% | 2.6649 | 806 | 0 |
| MIDAS-skewt: term_spread | 44 | 1.09% | 103 | 2.56% | 2.6343 | 806 | 592 |
| MIDAS-skewt: ip_growth | 42 | 1.04% | 103 | 2.56% | 2.6847 | 806 | 590 |

## Real-time estimates of theta

The macro coefficient as estimated at each refit, using only data up to that date.

| model | first | median | last | share positive |
|---|---|---|---|---|
| MIDAS-skewt: credit_spread | 0.6520 | 0.5790 | 0.6049 | 100% |
| MIDAS-skewt: term_spread | -0.0200 | 0.1127 | 0.1330 | 93% |
| MIDAS-skewt: ip_growth | 1.2109 | 0.5144 | -0.1295 | 88% |

Formal evaluation: backtests_macro_development.md and model_comparison_macro_development.md.

![Real-time theta](figures/midas_theta_paths.png)
