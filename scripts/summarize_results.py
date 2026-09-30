"""Final summary: development period versus the locked test period.

Run from the project root after both periods have been forecast:

    python scripts/summarize_results.py

Needs the four forecast files in data/processed/:
    forecasts_development.parquet         forecasts_locked_test.parquet
    forecasts_macro_development.parquet   forecasts_macro_locked_test.parquet

Pre-registered primary hypothesis: GJR-GARCH-MIDAS with the credit spread has
lower FZ0 loss (VaR and ES at 97.5%) and lower QLIKE than GJR-GARCH-skewt,
both with expanding windows. Decision rule, fixed before the locked test: the
hypothesis is supported on a loss if the average loss is lower AND the
Diebold-Mariano p-value is below 0.05.

Writes reports/final_summary.md and prints it.
"""

import dataclasses
from pathlib import Path

import pandas as pd

import compare_models as cm
import run_backtests as rb
from var_es.config import load_config
from var_es.evaluation.comparison import ComparisonSettings
from var_es.reporting import md_table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed"
REPORT_PATH = PROJECT_ROOT / "reports" / "final_summary.md"
PERIODS = {"development": "Development (2004-2019)", "locked_test": "Locked test (2020-2026)"}

MACRO_BASELINE = "GJR-GARCH(1,1)-skewt (expanding)"
PRIMARY_MODEL = "MIDAS-skewt: credit_spread"
SIGNIFICANCE = 0.05


def main() -> None:
    cfg = load_config(PROJECT_ROOT / "configs" / "base.yaml")
    settings = ComparisonSettings.from_config(cfg)
    proxies = pd.read_parquet(PROCESSED / "returns_and_proxies.parquet")
    evaluation = cfg.get("evaluation", {})
    main_proxy = evaluation.get("volatility_proxy", "gk_overnight")

    files = {}
    for period in PERIODS:
        for kind, prefix in (("rolling", "forecasts"), ("macro", "forecasts_macro")):
            path = PROCESSED / f"{prefix}_{period}.parquet"
            if not path.is_file():
                raise SystemExit(f"{path} not found. Run the forecasting scripts for both periods first.")
            files[kind, period] = pd.read_parquet(path)

    fz0_name = next(n for n in _losses(files["macro", "development"], cfg, proxies, main_proxy) if "FZ0" in n)
    qlike_name = f"variance, QLIKE vs {main_proxy}"

    macro_results, rolling_results = {}, {}
    for period in PERIODS:
        macro_settings = dataclasses.replace(settings, benchmark=MACRO_BASELINE)
        losses = _losses(files["macro", period], cfg, proxies, main_proxy)
        macro_results[period] = {
            "fz0": cm._compare(losses[fz0_name][0], macro_settings),
            "qlike": cm._compare(losses[qlike_name][0], macro_settings),
            "rejections": _backtest_rejections(files["macro", period], cfg),
        }
        losses = _losses(files["rolling", period], cfg, proxies, main_proxy)
        rolling_results[period] = {
            "fz0": cm._compare(losses[fz0_name][0], settings),
            "rejections": _backtest_rejections(files["rolling", period], cfg),
        }

    report = _build_report(macro_results, rolling_results, settings)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved {REPORT_PATH.relative_to(PROJECT_ROOT)}.")


def _losses(forecasts, cfg, proxies, main_proxy) -> dict:
    models = list(forecasts.index.get_level_values("model").unique())
    return cm._loss_matrices(forecasts, models, cfg, proxies, main_proxy, robustness=[])


def _backtest_rejections(forecasts, cfg) -> pd.Series:
    models = list(forecasts.index.get_level_values("model").unique())
    var_tables = {lv: rb._var_table(forecasts, models, lv) for lv in cfg["risk"]["confidence_levels_var"]}
    es_tables = {lv: rb._es_table(forecasts, models, lv) for lv in cfg["risk"]["confidence_levels_es"]}
    return rb._verdicts(var_tables, es_tables, models)["rejections"]


def _pair(results: dict, loss: str, model: str, column: str) -> str:
    dev = results["development"][loss].loc[model, column]
    test = results["locked_test"][loss].loc[model, column]
    return f"{dev} -> {test}"


def _verdict(row) -> str:
    better = row["mean loss"] < row["baseline loss"]
    return "supported" if better and row["DM p"] < SIGNIFICANCE else "not supported"


def _build_report(macro, rolling, settings) -> str:
    # 1. Primary hypothesis.
    primary = {}
    for period, label in PERIODS.items():
        for loss_key, loss_label in (("fz0", "FZ0 (VaR and ES 97.5%)"), ("qlike", "QLIKE (variance)")):
            table = macro[period][loss_key]
            row = table.loc[PRIMARY_MODEL]
            primary[(label, loss_label)] = {
                "vs baseline": row["vs benchmark"],
                "DM p": row["DM p"],
                "credit spread in MCS": row["in MCS"],
                "baseline in MCS": table.loc[MACRO_BASELINE, "in MCS"],
                "mean loss": row["mean loss"],
                "baseline loss": table.loc[MACRO_BASELINE, "mean loss"],
            }
    primary = pd.DataFrame.from_dict(primary, orient="index")
    primary["verdict"] = primary.apply(_verdict, axis=1)
    primary.index = [f"{period}: {loss}" for period, loss in primary.index]
    primary = primary.drop(columns=["mean loss", "baseline loss"])

    # 2. All macro models; each cell reads "development -> locked test".
    dev, test = "development", "locked_test"
    macro_table = {}
    for model in macro[dev]["fz0"].index:
        macro_table[model] = {
            "FZ0 vs baseline": _pair(macro, "fz0", model, "vs benchmark"),
            "in FZ0 MCS": _pair(macro, "fz0", model, "in MCS"),
            "QLIKE vs baseline": _pair(macro, "qlike", model, "vs benchmark"),
            "in QLIKE MCS": _pair(macro, "qlike", model, "in MCS"),
            "backtest rejections": f"{int(macro[dev]['rejections'][model])} -> "
                                   f"{int(macro[test]['rejections'][model])}",
        }
    macro_table = pd.DataFrame.from_dict(macro_table, orient="index")

    # 3. The nine rolling models: does the development ranking hold?
    rolling_table = {}
    for model in rolling[dev]["fz0"].index:
        rolling_table[model] = {
            "FZ0 rank": _pair(rolling, "fz0", model, "rank"),
            "in FZ0 MCS": _pair(rolling, "fz0", model, "in MCS"),
            "backtest rejections": f"{int(rolling[dev]['rejections'][model])} -> "
                                   f"{int(rolling[test]['rejections'][model])}",
        }
    rolling_table = pd.DataFrame.from_dict(rolling_table, orient="index")
    rolling_table = rolling_table.loc[rolling[dev]["fz0"].sort_values("rank").index]

    lines = [
        "# Final summary: development period vs locked test period",
        "",
        "## Primary hypothesis",
        "",
        f"GJR-GARCH-MIDAS with the credit spread vs {MACRO_BASELINE}. Pre-registered rule: "
        f"supported on a loss if the average loss is lower and the Diebold-Mariano p-value is "
        f"below {SIGNIFICANCE}. MCS at {settings.mcs_confidence:.0%}.",
        "",
        md_table(primary, index_label="period and loss"),
        "",
        "## All macro-augmented models",
        "",
        "Each cell reads development -> locked test. Loss relative to the expanding-window "
        "baseline (negative = better); backtest rejections out of 8 tests at 5%.",
        "",
        md_table(macro_table, index_label="model"),
        "",
        "## The nine rolling-window models",
        "",
        f"Each cell reads development -> locked test: rank by FZ0 loss, membership of the "
        f"{settings.mcs_confidence:.0%} MCS, and backtest rejections out of 8.",
        "",
        md_table(rolling_table, index_label="model"),
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()