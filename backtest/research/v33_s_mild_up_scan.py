"""
V33: S / MILD_UP targeted research.

Hypothesis comes from the first real-paper/live window (2026-06-01 to
2026-07-30): S/MILD_UP has a low win rate and many MaxHold losses.  This
script tests only that regime while locking the rest of V14+R+V25-D.

Stage 1 scans one variable at a time:
  - S MILD_UP entry slope cap: <2%, <3%, <4%, or block all MILD_UP
  - S MILD_UP MaxHold: 8, 9, 10 bars
Stage 2 evaluates the small cross-product for diagnosis.  No production
files are changed.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np
import pandas as pd


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_PATH = os.path.join(ROOT, "data", "ETHUSDT_1h_latest730d.csv")
ENGINE_PATH = os.path.join(os.path.dirname(__file__), "v14_export_trades.py")
WARMUP = 310
LIVE_START = pd.Timestamp("2026-06-01")
LIVE_END = pd.Timestamp("2026-08-01")
MID = pd.Timestamp("2025-07-30")


def load_engine():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location("v33_engine", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def metric(trades, start=None, end=None, side=None, regime=None):
    x = pd.DataFrame(trades)
    if x.empty:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "mdd": 0.0, "avg": 0.0,
                "pf": 0.0, "worst_month": 0.0, "pos_months": 0}
    if side is not None:
        x = x[x["side"] == side]
    if regime is not None:
        x = x[x["entry_regime"] == regime]
    dt = pd.to_datetime(x["entry_dt"])
    mask = pd.Series(True, index=x.index)
    if start is not None:
        mask &= dt >= pd.Timestamp(start)
    if end is not None:
        mask &= dt < pd.Timestamp(end)
    x = x.loc[mask]
    if x.empty:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "mdd": 0.0, "avg": 0.0,
                "pf": 0.0, "worst_month": 0.0, "pos_months": 0}
    p = x["pnl_usd"].astype(float)
    wins = p[p > 0].sum()
    losses = -p[p < 0].sum()
    eq = p.cumsum()
    months = x.assign(month=pd.to_datetime(x["entry_dt"]).dt.to_period("M")) \
        .groupby("month")["pnl_usd"].sum()
    return {
        "n": int(len(x)),
        "pnl": float(p.sum()),
        "wr": float((p > 0).mean() * 100),
        "mdd": float((eq.cummax() - eq).max()),
        "avg": float(p.mean()),
        "pf": float(wins / losses) if losses else 999.0,
        "worst_month": float(months.min()) if len(months) else 0.0,
        "pos_months": int((months > 0).sum()),
    }


def run(engine, df, mu_entry_cap=None, mu_mh=None):
    # Reset production parameters, then apply one research-only override.
    engine.L_BRK = 15
    engine.S_BRK = 15
    engine._S_MH_BR = {"UP": 8}
    ind = engine.compute_indicators(df)
    if mu_entry_cap is not None:
        slope = ind["slope"]
        ind["regime_block_s"] = ind["regime_block_s"] | (slope >= mu_entry_cap)
    if mu_mh is not None:
        engine._S_MH_BR = {"UP": 8, "MILD_UP": int(mu_mh)}
    return engine.simulate_v14_detailed(
        ind,
        df["datetime"].astype(str).values,
        start_bar=WARMUP,
        realistic=True,
        slip_bps=0.0,
        margin_schedule=None,
    )


def fold_pnl(trades, df, folds=6):
    edges = np.linspace(WARMUP, len(df), folds + 1, dtype=int)
    vals = []
    for i in range(folds):
        a, b = edges[i], edges[i + 1]
        vals.append(sum(t["pnl_usd"] for t in trades if a <= int(t["entry_bar"]) < b))
    return vals


def label(kind, value):
    if kind == "base":
        return "BASE S_MU default"
    if kind == "entry":
        return f"ENTRY S_MU slope<{value * 100:.1f}%"
    return f"EXIT S_MU MH={value}"


def report(name, trades, df):
    full = metric(trades)
    is_m = metric(trades, None, MID)
    pre_live = metric(trades, MID, LIVE_START)
    live = metric(trades, LIVE_START, LIVE_END)
    down = metric(trades, None, LIVE_END, side="S", regime="DOWN")
    mu = metric(trades, LIVE_START, LIVE_END, side="S", regime="MILD_UP")
    return {
        "name": name, "trades": trades, "full": full, "is": is_m,
        "pre_live": pre_live, "live": live, "down": down, "mu": mu,
        "wf": fold_pnl(trades, df),
    }


def line(r):
    f, i, p, l, d, mu = r["full"], r["is"], r["pre_live"], r["live"], r["down"], r["mu"]
    return (f"{r['name']:<30} full ${f['pnl']:+7.0f} n{f['n']:3d} MDD${f['mdd']:3.0f} | "
            f"IS ${i['pnl']:+7.0f} | pre-live ${p['pnl']:+6.0f} | "
            f"live ${l['pnl']:+6.0f} n{l['n']:2d} | "
            f"S-DOWN ${d['pnl']:+6.0f} | S-MU ${mu['pnl']:+6.0f} n{mu['n']:2d}")


def main():
    engine = load_engine()
    df = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
    print("=" * 120)
    print("V33 S / MILD_UP TARGETED RESEARCH")
    print("=" * 120)
    print(f"Data: {df.datetime.iloc[0]} ~ {df.datetime.iloc[-1]} ({len(df)} bars)")
    print(f"Primary split: {MID.date()} | live-observation window: {LIVE_START.date()} ~ {LIVE_END.date()}")
    print("Locked: L15/S15 entry, all L logic, S DOWN/UP logic, V14+R+V25-D exits and realistic execution")

    cases = [("base", None, None)]
    for cap in [0.02, 0.03, 0.04, 0.045]:
        cases.append(("entry", cap, None))
    for mh in [8, 9, 10]:
        cases.append(("exit", None, mh))

    results = []
    for kind, cap, mh in cases:
        name = label(kind, cap if kind == "entry" else mh)
        results.append(report(name, run(engine, df, cap, mh), df))

    print("\nSTAGE 1: ONE-VARIABLE SCAN")
    for r in results:
        print(line(r))
        print(f"    WF {[round(x) for x in r['wf']]} ({sum(x > 0 for x in r['wf'])}/6 positive)")

    base = results[0]
    print("\nSTAGE 1 DELTAS VS BASELINE")
    for r in results[1:]:
        print(f"{r['name']:<30} IS {r['is']['pnl']-base['is']['pnl']:+.0f} | "
              f"pre-live {r['pre_live']['pnl']-base['pre_live']['pnl']:+.0f} | "
              f"live {r['live']['pnl']-base['live']['pnl']:+.0f} | "
              f"full {r['full']['pnl']-base['full']['pnl']:+.0f} | "
              f"MDD {r['full']['mdd']-base['full']['mdd']:+.0f}")

    # Stage 2: small cross-product, shown for diagnosis even if no single
    # variable is strictly promotable.  This prevents a hidden interaction
    # from being missed while keeping the search space pre-registered.
    print("\nSTAGE 2: ENTRY CAP × MILD_UP MAXHOLD DIAGNOSTIC")
    combo_results = []
    for cap in [0.02, 0.03, 0.04, 0.045]:
        for mh in [8, 9, 10]:
            name = f"ENTRY<{cap * 100:.1f}% + MH{mh}"
            combo_results.append(report(name, run(engine, df, cap, mh), df))
    for r in sorted(combo_results, key=lambda x: x["pre_live"]["pnl"], reverse=True):
        print(line(r))

    # A conservative pre-declared screen: positive IS and pre-live, no loss
    # in S/DOWN, no more than 10% MDD increase, and 5/6 positive folds.
    eligible = []
    for r in results[1:] + combo_results:
        if (r["is"]["pnl"] > 0 and r["pre_live"]["pnl"] > 0
                and r["down"]["pnl"] >= base["down"]["pnl"]
                and r["full"]["mdd"] <= base["full"]["mdd"] * 1.10
                and sum(x > 0 for x in r["wf"]) >= 5):
            eligible.append(r)
    print("\nPRE-DECLARED ELIGIBLE CANDIDATES")
    if eligible:
        for r in sorted(eligible, key=lambda x: x["pre_live"]["pnl"], reverse=True):
            print(line(r))
    else:
        print("NONE")


if __name__ == "__main__":
    main()
