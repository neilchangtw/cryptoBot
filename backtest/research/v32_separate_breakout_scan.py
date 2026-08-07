"""
V32: L/S independent breakout lookback scan.

Uses the current v14_export_trades engine (V14+R+V25-D exits and gates),
changing only L_BRK and S_BRK.  This is a research script; it does not alter
strategy.py or the live configuration.
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data", "ETHUSDT_1h_latest730d.csv")
ENGINE_PATH = os.path.join(os.path.dirname(__file__), "v14_export_trades.py")


def load_engine():
    spec = importlib.util.spec_from_file_location("v14_engine_v32", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, ROOT)
    spec.loader.exec_module(mod)
    return mod


def metrics(trades, start=None, end=None):
    if not trades:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "pf": 0.0, "mdd": 0.0,
                "avg": 0.0, "worst_month": 0.0, "pos_months": 0,
                "months": 0}
    df = pd.DataFrame(trades)
    dt = pd.to_datetime(df["entry_dt"])
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= dt >= pd.Timestamp(start)
    if end is not None:
        mask &= dt < pd.Timestamp(end)
    df = df.loc[mask].copy()
    if df.empty:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "pf": 0.0, "mdd": 0.0,
                "avg": 0.0, "worst_month": 0.0, "pos_months": 0,
                "months": 0}
    pnl = df["pnl_usd"].astype(float)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    mdd = float((cum.cummax() - cum).max())
    month_pnl = df.assign(month=dt.loc[df.index].dt.to_period("M")).groupby("month")["pnl_usd"].sum()
    return {
        "n": int(len(df)),
        "pnl": float(pnl.sum()),
        "wr": float((pnl > 0).mean() * 100),
        "pf": float(wins / losses) if losses else 999.0,
        "mdd": mdd,
        "avg": float(pnl.mean()),
        "worst_month": float(month_pnl.min()) if len(month_pnl) else 0.0,
        "pos_months": int((month_pnl > 0).sum()),
        "months": int(len(month_pnl)),
    }


def wf_metrics(trades, start, end, folds=6):
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    edges = pd.date_range(start, end, periods=folds + 1)
    vals = []
    for i in range(folds):
        vals.append(metrics(trades, edges[i], edges[i + 1]))
    return {
        "wf_pos": sum(x["pnl"] > 0 for x in vals),
        "wf_pnl": float(sum(x["pnl"] for x in vals)),
        "wf_min": float(min(x["pnl"] for x in vals)),
        "wf_vals": [round(x["pnl"], 2) for x in vals],
    }


def run(engine, df, l_brk, s_brk):
    engine.L_BRK = int(l_brk)
    engine.S_BRK = int(s_brk)
    ind = engine.compute_indicators(df)
    return engine.simulate_v14_detailed(
        ind,
        df["datetime"].astype(str).values,
        start_bar=engine.WARMUP,
        realistic=True,
        slip_bps=0.0,
        margin_schedule=None,
    )


def main():
    engine = load_engine()
    df = pd.read_csv(DATA)
    dts = pd.to_datetime(df["datetime"])
    start = dts.iloc[0]
    end = dts.iloc[-1] + pd.Timedelta(hours=1)
    split = start + (end - start) / 2

    # Broad integer scan catches possible local optima, while the report also
    # emphasizes the established research neighborhood around 10-20 bars.
    values = list(range(5, 26))
    results = []
    for l_brk in values:
        for s_brk in values:
            trades = run(engine, df, l_brk, s_brk)
            full = metrics(trades, start, end)
            is_m = metrics(trades, start, split)
            oos = metrics(trades, split, end)
            wf = wf_metrics(trades, start, end)
            results.append({
                "l": l_brk, "s": s_brk,
                "full": full, "is": is_m, "oos": oos, "wf": wf,
            })

    def line(r):
        f, o, w = r["full"], r["oos"], r["wf"]
        return (f"L{r['l']:02d}/S{r['s']:02d} "
                f"full ${f['pnl']:+.0f} n{f['n']} WR{f['wr']:.1f}% MDD${f['mdd']:.0f} "
                f"OOS ${o['pnl']:+.0f} n{o['n']} WR{o['wr']:.1f}% MDD${o['mdd']:.0f} "
                f"WF {w['wf_pos']}/6 min${w['wf_min']:+.0f}")

    baseline = next(r for r in results if r["l"] == 15 and r["s"] == 15)
    print(f"DATA {start} ~ {end} | split {split} | realistic=True | flat 200U")
    print("BASELINE " + line(baseline))

    print("\nTOP 15 BY OOS PNL")
    for r in sorted(results, key=lambda x: (x["oos"]["pnl"], x["wf"]["wf_pos"], x["oos"]["mdd"]), reverse=True)[:15]:
        print(line(r))

    print("\nTOP 15 BY IS PNL (THEN SHOW OOS RESULT)")
    for r in sorted(results, key=lambda x: x["is"]["pnl"], reverse=True)[:15]:
        print(line(r) + f" IS ${r['is']['pnl']:+.0f} n{r['is']['n']}")

    # Prefer positive OOS, strong WF, and a non-catastrophic worst fold.  This
    # is a diagnostic ranking, not a new optimization objective.
    robust = [r for r in results if r["wf"]["wf_pos"] >= 4 and r["wf"]["wf_min"] > -250]
    print("\nTOP 15 ROBUST (WF >= 4/6 AND WORST FOLD > -$250)")
    for r in sorted(robust, key=lambda x: (x["oos"]["pnl"], x["wf"]["wf_pos"], -x["oos"]["mdd"]), reverse=True)[:15]:
        print(line(r))

    print("\nINDEPENDENT L SCAN (S=15), TOP 10 OOS")
    for r in sorted((x for x in results if x["s"] == 15), key=lambda x: x["oos"]["pnl"], reverse=True)[:10]:
        print(line(r))

    print("\nINDEPENDENT S SCAN (L=15), TOP 10 OOS")
    for r in sorted((x for x in results if x["l"] == 15), key=lambda x: x["oos"]["pnl"], reverse=True)[:10]:
        print(line(r))

    print("\nINDEPENDENT SCANS SELECTED ON IS, VALIDATED ON OOS")
    l_is = sorted((x for x in results if x["s"] == 15), key=lambda x: x["is"]["pnl"], reverse=True)[:10]
    s_is = sorted((x for x in results if x["l"] == 15), key=lambda x: x["is"]["pnl"], reverse=True)[:10]
    print("L candidates (S=15)")
    for r in l_is:
        print(line(r) + f" IS ${r['is']['pnl']:+.0f}")
    print("S candidates (L=15)")
    for r in s_is:
        print(line(r) + f" IS ${r['is']['pnl']:+.0f}")

    print("\nWF FOLD PNL FOR TOP ROBUST CANDIDATES")
    for r in sorted(robust, key=lambda x: (x["oos"]["pnl"], x["wf"]["wf_pos"]), reverse=True)[:10]:
        print(f"L{r['l']:02d}/S{r['s']:02d}: {r['wf']['wf_vals']}")


if __name__ == "__main__":
    main()
