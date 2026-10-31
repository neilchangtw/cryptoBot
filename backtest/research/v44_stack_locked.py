"""V44 三合一鎖定版參考回測（不判）：CT5 × N1 滾動版 × Q20W30。

預登記：doc/v44_maxhold_reframe_plan.md 第 26 節。每筆倍數 m = N1 × CT5 × Q（Q20W30 暫停 → 不開倉）。
N1 用第 6 輪滾動版：每年初只用之前的基準交易重排 GK 三分位勝率（1.5／0.9／0.6）。
也印出 viewer_stack_shadow.py 要用的當年切點與倍數。比較換算每 200U，同第 13 輪。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_stack_locked.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r6_gksize import KEYS, SCHEMES, rank_cuts, size_rolling
from v44_r13_fundsize import norm
from v44_r15_lowvolpause import ok_mask, part
from v44_r18_ltrendskip import past_return
from v44_r22_counter_size import build_sim, ct_size, run

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-07-01")
YEAR = 2026


def locked_table(base, year):
    prior = base[base.entry_dt < pd.Timestamp(f"{year}-01-01")]
    out = {}
    for side in KEYS:
        q, wr, order = rank_cuts(prior, side)
        mult = np.empty(3); mult[order] = SCHEMES["N1"]
        out[side] = dict(cuts=[round(float(x), 6) for x in q], wr=[round(float(x) * 100, 1) for x in wr], mult=[float(x) for x in mult])
    return out


def line(name, f):
    k = 200.0 / f.margin.mean()
    g = f.assign(pnl_usd=f.pnl_usd * k)
    cells = []
    for lab, x in (("P1", g[g.entry_dt < P_END]), ("P2", g[g.entry_dt >= P_END]), ("近期", g[g.entry_dt >= RECENT])):
        s = part(x)
        cells.append(f"{lab} {s['n']:3d}筆 WR {s['wr']:5.1f}% ${s['pnl']:7,.0f} MH ${s['mh']:7,.0f} MDD ${s['mdd']:5,.0f}")
    print(f"  {name:8s} 均 {f.margin.mean():4.0f}U " + " | ".join(cells))
    return g


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    c = candles["close"].to_numpy(float)
    ones = np.ones(len(c))
    base0 = run(sim, ind, dt, ones, ones, 0)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}")
    print(f"\n[{YEAR} 鎖定分組（切點＝GK 分位三等分；mult＝由低到高三組的 N1 倍數）]")
    print(" ", locked_table(base0, YEAR))

    n1 = size_rolling(ind, dt, base0, SCHEMES["N1"])
    ct = ct_size(past_return(c, 30), 0.5, True)
    q = ok_mask(candles, 20, 30)
    extra = {"okL": q, "okS": q}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp，每 200U]")
        b = line("基準", run(sim, ind, dt, ones, ones, slip))
        s = line("三合一", run(sim, ind, dt, ct[0] * n1["L"], ct[1] * n1["S"], slip, extra))
        line("N1滾動", run(sim, ind, dt, n1["L"], n1["S"], slip))
        if slip == 0:
            s.to_csv(OUT_DIR / "stack_locked_trades.csv", index=False)
            yrs = sorted(b.entry_dt.dt.year.unique())
            print("  逐年 " + " ".join(f"{y}" .rjust(7) for y in yrs))
            for name, g in (("基準", b), ("三合一", s)):
                yy = g.groupby(g.entry_dt.dt.year).pnl_usd.sum()
                print(f"  {name:4s} " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs))


if __name__ == "__main__":
    main()
