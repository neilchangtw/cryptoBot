"""V45 第 2 輪：ETH 獨自急漲做多（預登記見 doc/v45_big_move_plan.md）。加 --holdout 跑保留期（只准一次）。"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
import v44_r19_late_diag as r19
import v45_engine as E
from v45_r1_oi_impulse import perm
from v45_r2_breadth import zmat

DEV0, SPLIT, HOLD0, END = map(pd.Timestamp, ("2021-03-01", "2022-11-15", "2024-07-01", "2026-10-28"))
LOG = Path("doc/research_results/v45/holdout_log_r2.txt")
P = dict(K=2.5, B=.4, H=48, S=.05)
NEIGH = [("K", 2), ("K", 3), ("B", .3), ("B", .5), ("H", 24), ("H", 72), ("S", .03), ("S", .08)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    d = pd.read_pickle("data/v45/feat.pkl")
    Z = zmat().reindex(d.dt)
    cnt = Z.notna().sum(axis=1).values
    d["br"] = np.where(cnt >= 7, (Z >= 1.5).sum(axis=1).values / np.maximum(cnt, 1), np.nan)
    f = E.load_funding()

    def run(a, b, K, B, H, S):
        return E.sim(d, ((d.dt >= a) & (d.dt < b) & (d.z6 >= K) & (d.br < B)).values, 1, H, S, fund=f)

    dv = run(DEV0, HOLD0, **P)
    a, b = dv[dv.entry_dt < SPLIT].pnl.sum(), dv[dv.entry_dt >= SPLIT].pnl.sum()
    print("開發期", E.stats(dv), f"前段 {a:.0f} 後段 {b:.0f} D1", a > 0 and b > 0)
    print(dv.groupby(dv.entry_dt.dt.year).pnl.agg(["count", "sum"]).round())
    pP = dict(H=P["H"], S=P["S"])
    mk = ((d.dt >= DEV0) & (d.dt < HOLD0) & (d.z6 >= P["K"]) & d.br.notna()).values
    pr = perm(d, f, DEV0, HOLD0, len(dv), mk, pP)
    ok = dv.pnl.sum() >= np.percentile(pr, 97.5)
    print(f"D2 97.5% {np.percentile(pr, 97.5):.0f} 第 {(pr < dv.pnl.sum()).mean() * 100:.1f} 百分位", ok)
    for nm, v in NEIGH:
        print(" 開發鄰域", nm, v, E.stats(run(DEV0, HOLD0, **{**P, nm: v})))
    if not args.holdout:
        return
    if not (ok and a > 0 and b > 0):
        raise SystemExit("D1/D2 未過，不動用保留期")
    if LOG.exists():
        raise SystemExit("保留期已跑過")
    LOG.write_text(f"V45 R2 E 候選 {pd.Timestamp.now()} {P}\n", encoding="utf-8")
    ho = run(HOLD0, END, **P)
    ho.to_csv("doc/research_results/v45/r2_holdout_trades.csv", index=False)
    s = E.stats(ho)
    print("\n保留期", s)
    print(ho.groupby(ho.entry_dt.dt.year).pnl.agg(["count", "sum"]).round())
    E.SLIP = .0005
    h5 = run(HOLD0, END, **P).pnl.sum()
    E.SLIP = .0002
    m = ho.groupby(ho.exit_dt.dt.to_period("M")).pnl.sum()
    print("H1", ho.pnl.sum() > 0 and h5 > 0, f"5bp {h5:.0f}")
    print("H2", s["N"] >= 15 and s["pf"] >= 1.3)
    x1, x2 = ho.pnl.sum() - ho.pnl.max(), ho.pnl.sum() - m.max()
    print("H3", x1 > 0 and x2 > 0, f"去最佳單筆 {x1:.0f} 去最佳月 {x2:.0f}")
    ns = [(nm, v, run(HOLD0, END, **{**P, nm: v})) for nm, v in NEIGH]
    for nm, v, t in ns:
        print(" 鄰域", nm, v, E.stats(t))
    print("H4", sum(t.pnl.sum() > 0 for *_, t in ns) >= 6)
    tm = ((d.dt >= HOLD0) & (d.dt < END)).values
    p1 = perm(d, f, HOLD0, END, len(ho), tm & (d.z6 >= P["K"]).values & d.br.notna().values, pP)
    p2 = perm(d, f, HOLD0, END, len(ho), tm, pP)
    print(f"H5 廣度置換第 {(p1 < ho.pnl.sum()).mean() * 100:.1f}、隨機做多第 {(p2 < ho.pnl.sum()).mean() * 100:.1f} 百分位", ho.pnl.sum() >= max(np.percentile(p1, 97.5), np.percentile(p2, 97.5)))
    anti = E.sim(d, tm & (d.z6 >= P["K"]).values & (d.br >= .7).values, 1, P["H"], P["S"], fund=f)
    print("H6", ho.pnl.sum() > anti.pnl.sum(), E.stats(anti))
    candles, engine, ind, dt = v40.load_long()
    base = v40.replay(v40.build_sim(engine), ind, dt)
    bh = base[(base.entry_dt >= HOLD0) & (base.entry_dt < END)]
    mb = bh.groupby(pd.to_datetime(bh.exit_dt).dt.to_period("M")).pnl_usd.sum()
    am = pd.concat([mb, m], axis=1, keys=["b", "u"]).fillna(0)
    b2 = pd.DataFrame(dict(exit_dt=pd.to_datetime(bh.exit_dt), pnl=bh.pnl_usd))
    cb = pd.concat([b2, ho[["exit_dt", "pnl"]]])
    print("H7 月相關", round(am.b.corr(am.u), 2), "MDD", round(E.mdd(b2)), "→", round(E.mdd(cb)), "PnL", round(b2.pnl.sum()), "→", round(cb.pnl.sum()))
    ev = [e for e in r19.find_events(d.close.values, 0, .08) if e[1] == 1 and HOLD0 <= d.dt.iloc[e[0]] < END]
    miss = hit = 0
    for i, *_ in ev:
        a0, b0 = d.dt.iloc[max(0, i - 24)], d.dt.iloc[min(len(d) - 1, i + 24)]
        if not ((bh.side == "L") & (bh.entry_dt >= a0) & (bh.entry_dt <= b0)).any():
            miss += 1
            hit += ((ho.entry_dt >= a0) & (ho.entry_dt <= b0)).any()
    print(f"H8 事件 {len(ev)}，現行漏 {miss}，補到 {hit}")


if __name__ == "__main__":
    main()
