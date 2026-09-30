# Final summary: development period vs locked test period

## Primary hypothesis

GJR-GARCH-MIDAS with the credit spread vs GJR-GARCH(1,1)-skewt (expanding). Pre-registered rule: supported on a loss if the average loss is lower and the Diebold-Mariano p-value is below 0.05. MCS at 90%.

| period and loss | vs baseline | DM p | credit spread in MCS | baseline in MCS | verdict |
|---|---|---|---|---|---|
| Development (2004-2019): FZ0 (VaR and ES 97.5%) | -0.92% | 0.3060 | yes | yes | not supported |
| Development (2004-2019): QLIKE (variance) | -1.36% | 0.0210 | yes | no | supported |
| Locked test (2020-2026): FZ0 (VaR and ES 97.5%) | +0.98% | 0.3847 | yes | yes | not supported |
| Locked test (2020-2026): QLIKE (variance) | +0.59% | 0.2137 | no | yes | not supported |

## All macro-augmented models

Each cell reads development -> locked test. Loss relative to the expanding-window baseline (negative = better); backtest rejections out of 8 tests at 5%.

| model | FZ0 vs baseline | in FZ0 MCS | QLIKE vs baseline | in QLIKE MCS | backtest rejections |
|---|---|---|---|---|---|
| MIDAS-skewt: credit_spread | -0.92% -> +0.98% | yes -> yes | -1.36% -> +0.59% | yes -> no | 0 -> 2 |
| MIDAS-skewt: term_spread | -0.35% -> +0.94% | yes -> yes | +0.04% -> +1.03% | no -> no | 0 -> 1 |
| MIDAS-skewt: ip_growth | -0.08% -> -2.32% | yes -> yes | +0.71% -> -1.00% | no -> yes | 1 -> 1 |
| GJR-GARCH(1,1)-skewt (expanding) | +0.00% -> +0.00% | yes -> yes | +0.00% -> +0.00% | no -> yes | 0 -> 1 |

## The nine rolling-window models

Each cell reads development -> locked test: rank by FZ0 loss, membership of the 90% MCS, and backtest rejections out of 8.

| model | FZ0 rank | in FZ0 MCS | backtest rejections |
|---|---|---|---|
| GJR-GARCH(1,1)-skewt | 1 -> 2 | yes -> yes | 0 -> 1 |
| FHS-GJR | 2 -> 1 | yes -> yes | 0 -> 0 |
| GJR-GARCH(1,1)-t | 3 -> 4 | no -> yes | 5 -> 5 |
| GARCH(1,1)-skewt | 4 -> 3 | no -> yes | 3 -> 1 |
| GJR-GARCH(1,1)-normal | 5 -> 6 | no -> yes | 6 -> 6 |
| GARCH(1,1)-t | 6 -> 5 | no -> yes | 5 -> 6 |
| GARCH(1,1)-normal | 7 -> 7 | no -> yes | 6 -> 6 |
| EWMA-0.94 | 8 -> 8 | no -> yes | 6 -> 6 |
| HS-250 | 9 -> 9 | no -> yes | 8 -> 8 |
