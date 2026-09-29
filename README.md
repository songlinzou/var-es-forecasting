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