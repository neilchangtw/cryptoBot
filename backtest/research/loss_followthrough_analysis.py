"""分析回測虧損單出場後的方向，以及 MaxHold 延長反事實。

只讀 K 線與附件交易明細；所有參數變更只存在於本 Python 行程記憶體中，
不會修改 strategy.py、.env 或任何實盤狀態。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base


HORIZONS = [3, 6, 12, 24, 48, 72]
BREAKEVEN_MOVE = 0.001  # 現行 round-trip fee / notional = 0.1%


def trade_directional_return(side: str, price: float, reference: float) -> float:
    sign = 1.0 if side == "L" else -1.0
    return sign * (price / reference - 1.0)


def favorable_and_adverse(side: str, bar: pd.Series, entry: float) -> tuple[float, float]:
    if side == "L":
        return bar["high"] / entry - 1.0, bar["low"] / entry - 1.0
    return entry / bar["low"] - 1.0, entry / bar["high"] - 1.0


def first_path_events(trade: pd.Series, future: pd.DataFrame, horizon: int) -> dict:
    side = trade["side"]
    entry = float(trade["entry_price"])
    regime = trade["regime_code"]
    tp_move = 0.020 if side == "S" else (0.040 if regime == "DOWN" else 0.035)
    stop_move = -0.040 if side == "S" else -0.035
    window = future.iloc[:horizon]
    first_be = first_tp = first_stop = None
    max_fav = -math.inf
    max_adv = math.inf
    for elapsed, (_, bar) in enumerate(window.iterrows(), start=1):
        fav, adv = favorable_and_adverse(side, bar, entry)
        max_fav = max(max_fav, fav)
        max_adv = min(max_adv, adv)
        # 同一根 1h K 棒內無法確認先後；沿用引擎的保守順序：SafeNet 優先。
        if first_stop is None and adv <= stop_move:
            first_stop = elapsed
        if first_be is None and fav >= BREAKEVEN_MOVE:
            first_be = elapsed
        if first_tp is None and fav >= tp_move:
            first_tp = elapsed
    terminal = window.iloc[-1] if len(window) else None
    terminal_entry_move = (
        trade_directional_return(side, float(terminal["close"]), entry)
        if terminal is not None else math.nan
    )
    terminal_exit_move = (
        trade_directional_return(side, float(terminal["close"]), float(trade["exit_price"]))
        if terminal is not None else math.nan
    )
    be_before_stop = first_be is not None and (first_stop is None or first_be < first_stop)
    tp_before_stop = first_tp is not None and (first_stop is None or first_tp < first_stop)
    return {
        "first_be_h": first_be,
        "first_tp_h": first_tp,
        "first_stop_h": first_stop,
        "be_before_stop": be_before_stop,
        "tp_before_stop": tp_before_stop,
        "max_favorable_pct": max_fav * 100 if max_fav > -math.inf else math.nan,
        "max_adverse_pct": max_adv * 100 if max_adv < math.inf else math.nan,
        "terminal_entry_move_pct": terminal_entry_move * 100,
        "terminal_exit_move_pct": terminal_exit_move * 100,
    }


def add_followthrough(losses: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    d = candles.copy()
    d["datetime"] = pd.to_datetime(d["datetime"])
    d = d.set_index("datetime", drop=False)
    rows = []
    for _, trade in losses.iterrows():
        # exit_dt 是成交邊界；下一根可持有 K 棒正好以 exit_dt 開盤。
        future = d.loc[d.index >= trade["exit_dt"]].iloc[: max(HORIZONS)]
        row = trade.to_dict()
        for horizon in HORIZONS:
            event = first_path_events(trade, future, horizon)
            for key, value in event.items():
                row[f"h{horizon}_{key}"] = value
        final = first_path_events(trade, future, max(HORIZONS))
        if final["tp_before_stop"]:
            row["classification"] = "held_too_short_tp"
        elif final["be_before_stop"]:
            row["classification"] = "held_too_short_be"
        else:
            row["classification"] = "wrong_or_no_followthrough"
        rows.append(row)
    return pd.DataFrame(rows)


def path_summary(enriched: pd.DataFrame) -> list[dict]:
    rows = []
    for horizon in HORIZONS:
        entry_move = enriched[f"h{horizon}_terminal_entry_move_pct"]
        exit_move = enriched[f"h{horizon}_terminal_exit_move_pct"]
        rows.append({
            "hours": horizon,
            "n": int(entry_move.notna().sum()),
            "avg_from_entry_pct": float(entry_move.mean()),
            "recovered_entry_rate": float((entry_move > BREAKEVEN_MOVE * 100).mean() * 100),
            "avg_after_exit_pct": float(exit_move.mean()),
            "continued_original_direction_rate": float((exit_move > 0).mean() * 100),
            "be_before_stop_rate": float(enriched[f"h{horizon}_be_before_stop"].mean() * 100),
            "tp_before_stop_rate": float(enriched[f"h{horizon}_tp_before_stop"].mean() * 100),
        })
    return rows


def grouped_classification(enriched: pd.DataFrame, columns: list[str]) -> list[dict]:
    rows = []
    for keys, group in enriched.groupby(columns, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        counts = group["classification"].value_counts()
        row = {column: value for column, value in zip(columns, keys)}
        row.update({
            "n": len(group),
            "held_too_short_tp": int(counts.get("held_too_short_tp", 0)),
            "held_too_short_be": int(counts.get("held_too_short_be", 0)),
            "wrong_or_no_followthrough": int(counts.get("wrong_or_no_followthrough", 0)),
            "pnl": float(group["pnl"].sum()),
        })
        rows.append(row)
    return rows


def engine_metrics(frame: pd.DataFrame) -> dict:
    pnl = frame["pnl_usd"].astype(float)
    gains, losses = pnl[pnl > 0].sum(), pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    return {
        "n": len(frame),
        "wr": float((pnl > 0).mean() * 100),
        "pnl": float(pnl.sum()),
        "pf": float(gains / abs(losses)) if losses < 0 else math.inf,
        "mdd": float(abs((cum - cum.cummax()).min())),
        "losses": int((pnl < 0).sum()),
    }


def run_engine(engine, candles: pd.DataFrame, start: str = "2024-09-04",
               end: str | None = None) -> pd.DataFrame:
    ind = engine.compute_indicators(candles)
    start_bar = int(np.searchsorted(candles["datetime"].to_numpy(), pd.Timestamp(start)))
    trades = engine.simulate_v14_detailed(
        ind, candles["datetime"].to_numpy(), start_bar=start_bar,
        realistic=True, slip_bps=0.0, margin_schedule=base.MARGIN_SCHEDULE,
    )
    frame = pd.DataFrame(trades)
    if end is not None and not frame.empty:
        frame = frame[pd.to_datetime(frame["entry_dt"]) < pd.Timestamp(end)].copy()
    return frame


def maxhold_variants(engine, candles: pd.DataFrame) -> list[dict]:
    original = {
        "L_MH": engine.L_MH,
        "L_CMH_MH": engine.L_CMH_MH,
        "S_MH": engine.S_MH,
        "L_REGIME": dict(engine._L_MH_BR),
        "S_REGIME": dict(engine._S_MH_BR),
    }
    variants = [("baseline", 0, 0)]
    for extra in range(1, 9):
        variants.extend([
            (f"L+{extra}h", extra, 0),
            (f"S+{extra}h", 0, extra),
            (f"L/S+{extra}h", extra, extra),
        ])
    periods = {
        "full": ("2024-09-04", None),
        "discovery": ("2024-09-04", "2026-01-01"),
        "validation": ("2026-01-01", None),
        "recent": ("2026-06-01", None),
    }
    rows = []
    try:
        for name, l_extra, s_extra in variants:
            engine.L_MH = original["L_MH"] + l_extra
            engine.L_CMH_MH = original["L_CMH_MH"] + l_extra
            engine.S_MH = original["S_MH"] + s_extra
            engine._L_MH_BR = {k: v + l_extra for k, v in original["L_REGIME"].items()}
            engine._S_MH_BR = {k: v + s_extra for k, v in original["S_REGIME"].items()}
            row = {"name": name, "l_extra": l_extra, "s_extra": s_extra}
            for period, (start, end) in periods.items():
                row[period] = engine_metrics(run_engine(engine, candles, start, end))
            rows.append(row)
    finally:
        engine.L_MH = original["L_MH"]
        engine.L_CMH_MH = original["L_CMH_MH"]
        engine.S_MH = original["S_MH"]
        engine._L_MH_BR = original["L_REGIME"]
        engine._S_MH_BR = original["S_REGIME"]
    return rows


def s_regime_maxhold_variants(engine, candles: pd.DataFrame) -> list[dict]:
    """只延長單一 S 進場 regime，確認近期效果由哪個 regime 驅動。"""
    original_mh = engine.S_MH
    original_regime = dict(engine._S_MH_BR)
    periods = {
        "full": ("2024-09-04", None),
        "discovery": ("2024-09-04", "2026-01-01"),
        "validation": ("2026-01-01", None),
        "recent": ("2026-06-01", None),
    }
    rows = []
    try:
        for regime in ["DOWN", "MILD_UP", "UP"]:
            baseline_mh = original_regime.get(regime, original_mh)
            for extra in range(1, 9):
                engine.S_MH = original_mh
                engine._S_MH_BR = dict(original_regime)
                engine._S_MH_BR[regime] = baseline_mh + extra
                row = {"regime": regime, "extra": extra, "effective_mh": baseline_mh + extra}
                for period, (start, end) in periods.items():
                    row[period] = engine_metrics(run_engine(engine, candles, start, end))
                rows.append(row)
    finally:
        engine.S_MH = original_mh
        engine._S_MH_BR = original_regime
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", type=Path, required=True)
    ap.add_argument("--output-json", type=Path)
    args = ap.parse_args()

    candles = pd.read_csv(base.DATA_PATH)
    candles["datetime"] = pd.to_datetime(candles["datetime"])
    trades = base.parse_trade_text(args.backtest, "backtest")
    losses = trades[trades["pnl"] < 0].copy()
    enriched = add_followthrough(losses, candles)
    engine = base.load_engine()

    classification = enriched["classification"].value_counts()
    result = {
        "trade_count": len(trades),
        "loss_count": len(losses),
        "classification_72h": {
            "held_too_short_tp": int(classification.get("held_too_short_tp", 0)),
            "held_too_short_be": int(classification.get("held_too_short_be", 0)),
            "wrong_or_no_followthrough": int(classification.get("wrong_or_no_followthrough", 0)),
        },
        "horizons": path_summary(enriched),
        "by_side": grouped_classification(enriched, ["side"]),
        "by_exit_reason": grouped_classification(enriched, ["reason_code"]),
        "by_side_regime": grouped_classification(enriched, ["side", "regime_code"]),
        "maxhold_variants": maxhold_variants(engine, candles),
        "s_regime_maxhold_variants": s_regime_maxhold_variants(engine, candles),
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload, encoding="utf-8")
        print(f"wrote {args.output_json}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
