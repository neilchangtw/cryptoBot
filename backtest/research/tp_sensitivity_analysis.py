"""TP 計算、回吐與門檻敏感度研究（只讀／不改正式策略）。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base


def metrics(frame: pd.DataFrame) -> dict:
    pnl = frame["pnl_usd"].astype(float)
    gains, losses = pnl[pnl > 0].sum(), pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    tp = frame[frame["exit_reason"].eq("TP")]
    return {
        "n": len(frame),
        "wr": float((pnl > 0).mean() * 100),
        "pnl": float(pnl.sum()),
        "pf": float(gains / abs(losses)) if losses < 0 else math.inf,
        "mdd": float(abs((cum - cum.cummax()).min())),
        "tp_n": len(tp),
        "tp_pnl": float(tp["pnl_usd"].sum()),
        "tp_losses": int((tp["pnl_usd"] < 0).sum()),
    }


def run(engine, candles: pd.DataFrame, start: str, end: str | None,
        realistic: bool = True) -> pd.DataFrame:
    indicators = engine.compute_indicators(candles)
    start_bar = int(np.searchsorted(candles["datetime"].to_numpy(), pd.Timestamp(start)))
    trades = engine.simulate_v14_detailed(
        indicators, candles["datetime"].to_numpy(), start_bar=start_bar,
        realistic=realistic, slip_bps=0.0, margin_schedule=base.MARGIN_SCHEDULE,
    )
    frame = pd.DataFrame(trades)
    if end is not None:
        frame = frame[pd.to_datetime(frame["entry_dt"]) < pd.Timestamp(end)].copy()
    return frame


def evaluate(engine, candles: pd.DataFrame) -> dict:
    periods = {
        "full": ("2024-09-04", None),
        "discovery": ("2024-09-04", "2026-01-01"),
        "validation": ("2026-01-01", None),
        "recent": ("2026-06-01", None),
    }
    original = {
        "L_TP": engine.L_TP,
        "S_TP": engine.S_TP,
        "L_REGIME": dict(engine._L_TP_BR),
    }

    def period_metrics(realistic: bool = True) -> dict:
        return {
            name: metrics(run(engine, candles, start, end, realistic=realistic))
            for name, (start, end) in periods.items()
        }

    result = {"baseline": period_metrics(), "baseline_ideal": period_metrics(False)}
    l_rows, s_rows = [], []
    try:
        for tp in [0.015, 0.020, 0.025, 0.030, 0.035, 0.040]:
            engine.L_TP = tp
            engine._L_TP_BR = {"DOWN": tp + 0.005}
            engine.S_TP = original["S_TP"]
            row = {"l_tp": tp, "l_down_tp": tp + 0.005}
            row.update(period_metrics())
            l_rows.append(row)

        engine.L_TP = original["L_TP"]
        engine._L_TP_BR = dict(original["L_REGIME"])
        for tp in [0.0100, 0.0125, 0.0150, 0.0175, 0.0200, 0.0225, 0.0250, 0.0300]:
            engine.S_TP = tp
            row = {"s_tp": tp}
            row.update(period_metrics())
            s_rows.append(row)
    finally:
        engine.L_TP = original["L_TP"]
        engine.S_TP = original["S_TP"]
        engine._L_TP_BR = original["L_REGIME"]
    result["l_sensitivity"] = l_rows
    result["s_sensitivity"] = s_rows
    return result


def tp_fill_diagnostics(frame: pd.DataFrame) -> dict:
    tp = frame[frame["exit_reason"].eq("TP")].copy()
    tp["target_pct"] = np.where(
        tp["side"].eq("S"), 2.0,
        np.where(tp["entry_regime"].eq("DOWN"), 4.0, 3.5),
    )
    tp["giveback_pct_point"] = tp["target_pct"] - tp["pnl_pct"]
    return {
        "n": len(tp),
        "closed_better_than_target": int((tp["giveback_pct_point"] < 0).sum()),
        "closed_at_or_worse_than_target": int((tp["giveback_pct_point"] >= 0).sum()),
        "closed_as_net_loss": int((tp["pnl_usd"] < 0).sum()),
        "median_giveback_pct_point": float(tp["giveback_pct_point"].median()),
        "mean_giveback_pct_point": float(tp["giveback_pct_point"].mean()),
        "mean_realized_move_pct": float(tp["pnl_pct"].mean()),
        "mean_target_pct": float(tp["target_pct"].mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-json", type=Path)
    args = ap.parse_args()
    candles = pd.read_csv(base.DATA_PATH)
    candles["datetime"] = pd.to_datetime(candles["datetime"])
    engine = base.load_engine()
    result = evaluate(engine, candles)
    baseline_frame = run(engine, candles, "2024-09-04", None, realistic=True)
    result["tp_fill_diagnostics"] = tp_fill_diagnostics(baseline_frame)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload, encoding="utf-8")
        print(f"wrote {args.output_json}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
