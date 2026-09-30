# VaR and ES Forecasting with GARCH-Family and Macro-Augmented Models

## Research question
Does augmenting GARCH-family volatility models with macroeconomic factors
improve out-of-sample VaR and ES forecasts, in terms of both calibration
(coverage backtests) and accuracy (loss-based model comparison)?

## Scope
S&P 500 exposure through the SPY ETF, daily data from January 2000 to
August 2026; one-day-ahead 99% and 97.5% VaR and 97.5% ES; locked test
period from January 2020.

## Data
SPY (SPDR S&P 500 ETF) daily OHLC from the [Tiingo](https://www.tiingo.com)
API, including dividend-adjusted prices. Raw data is not committed; run
`scripts/download_data.py` with your own free Tiingo API key to recreate it.

Sources considered and not used:
- **Yahoo Finance (`yfinance`)**: requests were persistently rate-limited.
- **Stooq**: its S&P 500 symbol now points to a CFD ("US LargeCap"), not
  the official index, and it blocks automated downloads.

## Installation
```bash
git clone https://github.com/songlinzou/var-es-forecasting.git
cd var-es-forecasting
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Usage
```bash
export TIINGO_API_KEY="your-key-here"   # free key from tiingo.com
python scripts/download_data.py         # download the raw data snapshot
```

## Status
Work in progress: Step 1 (data pipeline).

## Data quality
Checks are in `src/var_es/data/quality.py`; run `python scripts/check_data.py`.

- 6,705 daily observations, matching the NYSE trading calendar exactly
  (no missing or extra days).
- No internal inconsistencies: all highs and lows contain the open and close,
  and the adjustment factor changes only on the 107 dividend dates... 
- 13 days with absolute log returns above 7%, all matching known market
  events (autumn 2008, March 2020, April 2025). All are kept: they are the
  tail events VaR and ES are meant to capture.
- Sample periods: 1,004 trading days before development (window: 1,000),
  4,027 in development, 1,674 in the locked test.

## Returns and volatility proxies
Daily log total returns from dividend-adjusted closes. Intraday range
estimators (Parkinson, Garman-Klass) capture about two-thirds of
close-to-close variance; adding the squared overnight return brings this
to 1.02. Volatility forecasts are therefore evaluated against Garman-Klass
plus the overnight term, with squared returns and Parkinson as robustness
checks.

## Stylized facts (2000–2019, before the locked test)
Full report: [reports/stylized_facts.md](reports/stylized_facts.md)

| Finding | Evidence | Implication |
|---|---|---|
| Mean is negligible | 0.023% per day, t = 1.38 | Constant mean; mean has little effect on 1-day VaR |
| Weak return autocorrelation | Lag-1 −0.06, robust p = 0.013 (Ljung–Box overstates it under heteroskedasticity) | AR(1) mean as a variant; explains ~0.4% of variance |
| Strong, persistent volatility clustering | Squared-return ACF 0.18 at lags 1 and 20; ARCH-LM p ≈ 0 | GARCH-type variance with high persistence |
| Fat tails | Excess kurtosis 10.6; 12 days beyond 5σ vs 0.003 expected | Student-t innovations, checked on standardized residuals |
| Little skewness | −0.07 | Skewed-t optional |
| Leverage effect | Next-day variance 1.5–1.7× higher after down days | Asymmetric GJR-GARCH |

### Model shortlist for Step 2
- **Mean:** constant, with AR(1) as a variant
- **Variance:** EWMA (RiskMetrics) and GARCH(1,1) as benchmarks; GJR-GARCH(1,1) for asymmetry
- **Innovations:** normal and Student-t
- **Checks:** Ljung–Box and ARCH-LM on standardized residuals and their squares, plus residual kurtosis

## GARCH estimation (2000–2019, in-sample)
Full report: [reports/garch_in_sample.md](reports/garch_in_sample.md).
GARCH log-likelihood implemented from scratch (`src/var_es/models/garch.py`),
validated against `arch` (parameters within 2e-4, log-likelihood within 1e-6).

- **GJR-GARCH(1,1)-t is preferred** by AIC and BIC. Student-t shocks and the
  leverage term each improve the log-likelihood by over 100.
- **Only negative shocks raise volatility:** alpha = 0 at its bound, gamma = 0.21.
- **The symmetric GARCH-t is misspecified:** persistence 0.998 implies a
  365-day half-life and 40% long-run volatility (sample: 19%). GJR-t gives
  46 days and 16.7%.
- **Residuals:** ARCH effects removed (ARCH-LM p = 0.09); excess kurtosis
  falls from 10.6 to 2.0, matching the fitted t (nu = 6.8, implied 2.1);
  skewness of -0.57 motivates a skewed-t distribution.
- **Skewed Student-t (Hansen, 1994) preferred:** GJR-GARCH-skewt has the lowest
  AIC and BIC (BIC 54 below GJR-t). lambda = -0.15 (t = -8.5; LR test p = 3e-15).
  Leverage and persistence are essentially unchanged.
- The fitted skewed-t implies skewness -0.43 and excess kurtosis 1.96, close to
  the residuals' -0.57 and 2.02.
- For the same variance, the skewed-t 1% quantile (-2.75) implies a 99% VaR
  18% larger than under normal shocks (-2.33).

## Backtests (development period, 2004–2019)
Full report: [reports/backtests_development.md](reports/backtests_development.md).
9 models, 4,027 one-day forecasts, rolling 1,000-day window, weekly re-estimation.

- **Two models pass all 8 tests:** GJR-GARCH-skewt (41 exceptions at 99% vs 40.3
  expected) and filtered historical simulation.
- **Tail shape drives 99% VaR:** all normal-based models fail (2.2–2.5% exceptions);
  Student-t models fail (1.5–1.7%); skewed-t models pass.
- **Leverage drives 97.5% VaR:** GARCH-skewt fails (3.35%); GJR-skewt passes (2.88%).
- **Only historical simulation fails independence:** P(exception | exception yesterday)
  = 8.2%, reflecting its slow reaction to volatility changes.
- **ES:** McNeil–Frey passes for all t and skewed-t models (tail sizes are right);
  Z2 still rejects the Student-t models because of too many exceptions.
- **Test sizes checked by simulation:** the ES tests use a studentized bootstrap;
  the textbook versions rejected a correct model only 1–2.5% of the time at 5%.
- **Caveat:** GJR-skewt was selected on in-sample fit over an overlapping period;
  the locked test period (2020–2026) provides the unbiased check.

## Model comparison (development period, 2004–2019)
Full report: [reports/model_comparison_development.md](reports/model_comparison_development.md).
Losses: tick loss (VaR), FZ0 (VaR and ES jointly), QLIKE (variance, vs gk_overnight).
Diebold–Mariano tests vs GJR-GARCH-skewt; 90% Model Confidence Set (stationary bootstrap).

- **VaR and ES jointly (FZ0):** only GJR-GARCH-skewt and FHS-GJR are in the MCS;
  GJR-GARCH-t is 2.9% worse (DM p = 0.006). These are the same two models that
  passed all backtests: calibration and accuracy agree.
- **Variance (QLIKE):** all three GJR models are in the MCS with insignificant
  differences; symmetric GARCH and EWMA are excluded (3–9% worse).
- **Interpretation:** leverage improves volatility forecasts; the skewed-t
  improves tail-risk forecasts; VaR and ES need both.
- 99% tick loss has little power (six models in the MCS), illustrating why
  backtests and loss comparisons are complementary.

## GARCH-MIDAS, in-sample (2000–2019)
Full report: [reports/midas_in_sample.md](reports/midas_in_sample.md).
GJR-GARCH-MIDAS with skewed-t shocks, one macro variable per model,
12 monthly lags known in real time.

- **Credit spread:** theta = 0.60 (t = 5.4); +1 sd raises long-run volatility by 24%;
  the long-run component explains 19% of the variation in log variance; AIC and BIC
  both improve. Persistence of the short-run component falls from 0.987 to 0.976.
- **Term spread:** theta = 0.19 (t = 2.9); a steeper curve coincides with high-volatility
  easing cycles. BIC prefers the baseline.
- **Industrial production growth:** theta = -0.73 (t = -2.2); not significant after a
  Bonferroni correction for three variables.
- **Caveat:** credit spreads are market prices that partly reflect equity volatility,
  so they are a market-implied risk indicator rather than pure macro fundamentals.

## Macro-augmented forecasts (development period, 2004–2019)
Reports: [forecasts](reports/forecasts_macro_development.md),
[backtests](reports/backtests_macro_development.md),
[comparison](reports/model_comparison_macro_development.md).
Expanding window from 2000; GJR-GARCH-MIDAS-skewt vs GJR-GARCH-skewt, same window.

- **Calibration:** all four models pass essentially every backtest; the baseline
  is already well calibrated.
- **Volatility (QLIKE):** the credit-spread model is 1.36% better (DM p = 0.021)
  and the only model in the 90% MCS. Borderline after a Bonferroni correction for
  three variables (threshold 0.017).
- **VaR and ES (FZ0):** credit spread ranks first (−0.92%) but not significantly
  (p = 0.31); all models are in the MCS.
- **Term spread and industrial production:** no improvement; industrial production
  slightly worsens volatility forecasts (+0.71%, p = 0.028).
- **Real-time stability:** the credit-spread coefficient was positive at every
  refit from 2004 onward (0.58–0.65).
- **Estimation note:** the lag-shape parameter is weakly identified, so estimation
  uses a profile likelihood over it. Industrial production's likelihood is nearly
  flat in its coefficient; 170 of 806 refits did not meet the strict convergence
  criterion.