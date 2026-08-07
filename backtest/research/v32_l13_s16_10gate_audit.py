"""
V32 full 10-Gate audit for the independent-lookback candidate L13/S16.

The production logic is loaded from v14_export_trades.py.  Only L_BRK/S_BRK
are changed; all V14+R+V25-D gates, exits, circuit breakers and realistic
execution remain unchanged.  This script is audit-only and does not edit
strategy.py or live configuration.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_PATH = os.path.join(ROOT, "data", "ETHUSDT_1h_latest730d.csv")
ENGINE_PATH = os.path.join(os.path.dirname(__file__), "v14_export_trades.py")

L_CANDIDATE = 13
S_CANDIDATE = 16
BASELINE = (15, 15)
WARMUP = 310
RNG_SEED = 42
RANDOM_TRIALS = 100


def load_engine():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location("v32_audit_engine", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def data_audit(df: pd.DataFrame) -> dict:
    gaps = df["datetime"].diff().dropna()
    bad_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    result = {
        "rows": len(df),
        "start": str(df["datetime"].iloc[0]),
        "end": str(df["datetime"].iloc[-1]),
        "duplicates": int(df["datetime"].duplicated().sum()),
        "bad_gaps": int((gaps != pd.Timedelta(hours=1)).sum()),
        "nan_cells": int(df.isna().sum().sum()),
        "bad_ohlc": int(bad_ohlc.sum()),
        "nonpositive_volume": int((df["volume"] <= 0).sum()),
    }
    return result


def load_data():
    return pd.read_csv(DATA_PATH, parse_dates=["datetime"])


def set_params(engine, l_brk: int, s_brk: int):
    engine.L_BRK = int(l_brk)
    engine.S_BRK = int(s_brk)


def run(engine, df: pd.DataFrame, l_brk: int, s_brk: int,
        realistic: bool = True, slip_bps: float = 0.0):
    set_params(engine, l_brk, s_brk)
    ind = engine.compute_indicators(df)
    trades = engine.simulate_v14_detailed(
        ind,
        df["datetime"].astype(str).values,
        start_bar=WARMUP,
        realistic=realistic,
        slip_bps=slip_bps,
        margin_schedule=None,
    )
    return trades, ind


def metrics(trades, start=None, end=None):
    if not trades:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "pf": 0.0, "mdd": 0.0,
                "avg": 0.0, "months": {}, "worst_month": 0.0}
    x = pd.DataFrame(trades)
    dt = pd.to_datetime(x["entry_dt"])
    mask = pd.Series(True, index=x.index)
    if start is not None:
        mask &= dt >= pd.Timestamp(start)
    if end is not None:
        mask &= dt < pd.Timestamp(end)
    x = x.loc[mask].copy()
    if x.empty:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "pf": 0.0, "mdd": 0.0,
                "avg": 0.0, "months": {}, "worst_month": 0.0}
    pnl = x["pnl_usd"].astype(float)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    eq = pnl.cumsum()
    month = dt.loc[x.index].dt.to_period("M")
    by_month = x.assign(month=month).groupby("month")["pnl_usd"].sum()
    return {
        "n": int(len(x)),
        "pnl": float(pnl.sum()),
        "wr": float((pnl > 0).mean() * 100),
        "pf": float(wins / losses) if losses else 999.0,
        "mdd": float((eq.cummax() - eq).max()),
        "avg": float(pnl.mean()),
        "months": {str(k): float(v) for k, v in by_month.items()},
        "worst_month": float(by_month.min()) if len(by_month) else 0.0,
    }


def side_metrics(trades, side: str, start=None, end=None):
    return metrics([t for t in trades if t["side"] == side], start, end)


def fmt(m):
    return f"n={m['n']} PnL=${m['pnl']:+.0f} WR={m['wr']:.1f}% PF={m['pf']:.2f} MDD=${m['mdd']:.0f}"


def wf(trades, n_bars: int, folds: int = 6):
    edges = np.linspace(WARMUP, n_bars, folds + 1, dtype=int)
    values = []
    for i in range(folds):
        a, b = edges[i], edges[i + 1]
        subset = [t for t in trades if a <= int(t["entry_bar"]) < b]
        values.append({
            "fold": i + 1,
            "start_bar": int(a),
            "end_bar": int(b),
            "n": len(subset),
            "pnl": float(sum(t["pnl_usd"] for t in subset)),
        })
    return values


def print_gate(num, name, status, detail):
    print(f"G{num} {name:<30} {status:<11} {detail}")


def main():
    parser = argparse.ArgumentParser(description="V32 independent-lookback 10-Gate audit")
    parser.add_argument("--l", type=int, default=L_CANDIDATE, dest="l_candidate")
    parser.add_argument("--s", type=int, default=S_CANDIDATE, dest="s_candidate")
    parser.add_argument("--reference-l", type=int, default=BASELINE[0], dest="reference_l")
    parser.add_argument("--reference-s", type=int, default=BASELINE[1], dest="reference_s")
    args = parser.parse_args()
    l_candidate = args.l_candidate
    s_candidate = args.s_candidate
    reference = (args.reference_l, args.reference_s)
    is_reference_run = (l_candidate, s_candidate) == reference
    engine = load_engine()
    df = load_data()
    dts = df["datetime"]
    start = dts.iloc[0]
    end = dts.iloc[-1] + pd.Timedelta(hours=1)
    split = start + (end - start) / 2
    n = len(df)

    print("=" * 100)
    print(f"V32 FULL 10-GATE AUDIT — INDEPENDENT BREAKOUT LOOKBACK L{l_candidate} / S{s_candidate}")
    print("=" * 100)
    print(f"Data: {start} ~ {end} | rows={n} | split={split}")
    print("Engine: V14+R+V25-D | realistic=True | slip=0 bps | flat 200U research basis")
    print(f"Candidate: L_BRK={l_candidate}, S_BRK={s_candidate} | baseline: L_BRK={reference[0]}, S_BRK={reference[1]}")

    # Preflight data and current-engine parity.
    da = data_audit(df)
    data_ok = not any(da[k] for k in ["duplicates", "bad_gaps", "nan_cells", "bad_ohlc", "nonpositive_volume"])
    print("\nPREFLIGHT")
    print(f"Data integrity: {'PASS' if data_ok else 'FAIL'} {da}")

    cand_trades, cand_ind = run(engine, df, l_candidate, s_candidate)
    base_trades, base_ind = run(engine, df, *reference)
    is_m = metrics(cand_trades, start, split)
    oos_m = metrics(cand_trades, split, end)
    full_m = metrics(cand_trades, start, end)
    base_is = metrics(base_trades, start, split)
    base_oos = metrics(base_trades, split, end)
    base_full = metrics(base_trades, start, end)

    print("\nBASELINE VS CANDIDATE")
    print(f"Baseline IS  {fmt(base_is)} | OOS {fmt(base_oos)} | Full {fmt(base_full)}")
    print(f"Candidate IS {fmt(is_m)} | OOS {fmt(oos_m)} | Full {fmt(full_m)}")
    for side in ["L", "S"]:
        print(f"  {side} candidate IS {fmt(side_metrics(cand_trades, side, start, split))} | "
              f"OOS {fmt(side_metrics(cand_trades, side, split, end))}")

    statuses = {}

    # G1: IS positive.
    g1 = is_m["pnl"] > 0 and all(side_metrics(cand_trades, s, start, split)["pnl"] > 0 for s in ["L", "S"])
    statuses[1] = "PASS" if g1 else "FAIL"
    print_gate(1, "IS positive", statuses[1], fmt(is_m))

    # G2: OOS positive.
    g2 = oos_m["pnl"] > 0 and all(side_metrics(cand_trades, s, split, end)["pnl"] > 0 for s in ["L", "S"])
    statuses[2] = "PASS" if g2 else "FAIL"
    print_gate(2, "OOS positive", statuses[2], fmt(oos_m))

    # G3: incremental improvement has same sign IS/OOS.
    delta_is = is_m["pnl"] - base_is["pnl"]
    delta_oos = oos_m["pnl"] - base_oos["pnl"]
    if is_reference_run:
        statuses[3] = "N/A"
        print_gate(3, "IS/OOS uplift same sign", statuses[3], "基準策略沒有相對候選的增量改善可測")
    else:
        g3 = np.sign(delta_is) == np.sign(delta_oos) and delta_is != 0
        statuses[3] = "PASS" if g3 else "FAIL"
        print_gate(3, "IS/OOS uplift same sign", statuses[3],
                   f"delta IS=${delta_is:+.0f}, OOS=${delta_oos:+.0f}")

    # G4: 3x3 local neighborhood around the candidate.
    neighborhood = []
    for l_brk in [l_candidate - 1, l_candidate, l_candidate + 1]:
        for s_brk in [s_candidate - 1, s_candidate, s_candidate + 1]:
            if (l_brk, s_brk) == (l_candidate, s_candidate):
                continue
            tr, _ = run(engine, df, l_brk, s_brk)
            mm = metrics(tr, start, end)
            oo = metrics(tr, split, end)
            neighborhood.append((l_brk, s_brk, mm, oo))
    neighbor_ok = sum(oo["pnl"] > 0 and oo["pnl"] >= oos_m["pnl"] * 0.70 for _, _, _, oo in neighborhood)
    g4 = neighbor_ok >= 6
    statuses[4] = "PASS" if g4 else "FAIL"
    print_gate(4, "3x3 parameter neighborhood", statuses[4],
               f"{neighbor_ok}/8 neighbors OOS positive and >=70% of candidate")
    for l_brk, s_brk, mm, oo in neighborhood:
        print(f"    L{l_brk}/S{s_brk}: full ${mm['pnl']:+.0f}, OOS ${oo['pnl']:+.0f}, MDD ${mm['mdd']:.0f}")

    # G5: randomized same-count breakout cascade/null test.
    set_params(engine, l_candidate, s_candidate)
    valid_l = np.arange(WARMUP, n)[~np.isnan(cand_ind["pctile_L"][WARMUP:])]
    valid_s = np.arange(WARMUP, n)[~np.isnan(cand_ind["pctile_S"][WARMUP:])]
    count_l = int(np.sum(cand_ind["brk_up"][WARMUP:]))
    count_s = int(np.sum(cand_ind["brk_dn"][WARMUP:]))
    rng = np.random.default_rng(RNG_SEED)
    random_pnls = []
    for _ in range(RANDOM_TRIALS):
        ind = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in cand_ind.items()}
        rb_l = np.zeros(n, dtype=bool)
        rb_s = np.zeros(n, dtype=bool)
        rb_l[rng.choice(valid_l, size=count_l, replace=False)] = True
        rb_s[rng.choice(valid_s, size=count_s, replace=False)] = True
        ind["brk_up"] = rb_l
        ind["brk_dn"] = rb_s
        tr = engine.simulate_v14_detailed(ind, df["datetime"].astype(str).values,
                                          start_bar=WARMUP, realistic=True,
                                          slip_bps=0, margin_schedule=None)
        random_pnls.append(sum(t["pnl_usd"] for t in tr))
    rank = float(np.mean(np.array(random_pnls) < full_m["pnl"]) * 100)
    g5 = rank >= 80
    statuses[5] = "PASS" if g5 else "FAIL"
    print_gate(5, "randomized breakout cascade", statuses[5],
               f"candidate ${full_m['pnl']:+.0f}, random mean ${np.mean(random_pnls):+.0f}, rank={rank:.0f}th")

    # G6: swap / degradation test.
    fwd = (oos_m["pnl"] - is_m["pnl"]) / abs(is_m["pnl"]) * 100 if is_m["pnl"] else 999
    bwd = (is_m["pnl"] - oos_m["pnl"]) / abs(oos_m["pnl"]) * 100 if oos_m["pnl"] else 999
    g6 = abs(fwd) < 50 and abs(bwd) < 50
    statuses[6] = "PASS" if g6 else "FAIL"
    print_gate(6, "IS/OOS swap degradation", statuses[6], f"forward={fwd:+.1f}%, backward={bwd:+.1f}%")

    # G7: six continuous-calendar folds.
    folds = wf(cand_trades, n, folds=6)
    positive_folds = sum(x["pnl"] > 0 for x in folds)
    g7 = positive_folds >= 4
    statuses[7] = "PASS" if g7 else "FAIL"
    print_gate(7, "6-fold Walk-Forward", statuses[7],
               f"{positive_folds}/6 positive; fold PnL={[round(x['pnl']) for x in folds]}")

    # G8: reverse OHLCV time-order stress test.
    df_rev = df.iloc[::-1].reset_index(drop=True).copy()
    tr_rev, _ = run(engine, df_rev, l_candidate, s_candidate)
    # Reversed timestamps are intentionally descending.  Do not apply a
    # normal ascending date interval here; that would silently filter out all
    # reversed trades and turn the test into a reporting bug.
    rev_m = metrics(tr_rev)
    g8 = rev_m["pnl"] > 0
    statuses[8] = "PASS" if g8 else "FAIL"
    print_gate(8, "time reversal", statuses[8], f"original=${full_m['pnl']:+.0f}, reversed=${rev_m['pnl']:+.0f}")

    # G9: remove the best month.
    best_month = max(full_m["months"], key=full_m["months"].get)
    without_best = full_m["pnl"] - full_m["months"][best_month]
    g9 = without_best > 0
    statuses[9] = "PASS" if g9 else "FAIL"
    print_gate(9, "remove best month", statuses[9],
               f"best={best_month} ${full_m['months'][best_month]:+.0f}; remaining=${without_best:+.0f}")

    # G10: degrees of freedom / selection penalty.
    # The production candidate has two knobs, but it was selected from 21x21
    # combinations.  Report this honestly as conditional rather than calling
    # a broad search a clean pass.
    if is_reference_run:
        g10 = True
        statuses[10] = "PASS"
        g10_detail = "目前鎖定基準，沒有使用本次 OOS 結果重新選參數"
    else:
        g10 = False
        statuses[10] = "CONDITIONAL"
        g10_detail = "2 production knobs, but selected from 441 L/S combinations"
    print_gate(10, "degrees of freedom", statuses[10],
               g10_detail)

    print("\n" + "=" * 100)
    pass_count = sum(v == "PASS" for v in statuses.values())
    cond_count = sum(v == "CONDITIONAL" for v in statuses.values())
    fail_count = sum(v == "FAIL" for v in statuses.values())
    na_count = sum(v == "N/A" for v in statuses.values())
    print(f"10-GATE SUMMARY: {pass_count}/10 PASS, {cond_count} CONDITIONAL, {fail_count} FAIL, {na_count} N/A")
    for i in range(1, 11):
        print(f"  G{i}: {statuses[i]}")
    if fail_count == 0 and cond_count == 0:
        verdict = "PROMOTED"
    elif fail_count == 0:
        verdict = "CONDITIONAL"
    else:
        verdict = "NO PROMOTION"
    print(f"VERDICT: {verdict}")


if __name__ == "__main__":
    main()
