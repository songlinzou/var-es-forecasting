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
