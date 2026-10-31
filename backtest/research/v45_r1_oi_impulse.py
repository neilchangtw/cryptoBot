"""V45 第 1 輪：OI 確認急漲做多（預登記 doc/v45_big_move_plan.md）。

用法：
    python backtest/research/v45_r1_oi_impulse.py                開發期
    python backtest/research/v45_r1_oi_impulse.py --holdout      保留期（只准一次，寫 holdout_log）
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import v44_r19_late_diag as r19
import v45_engine as E
import v40_dvol_bookdepth as v40

DEV0, SPLIT, HOLD0, END = map(pd.Timestamp, ("2021-12-01", "2023-04-01", "2024-07-01", "2026-10-31"))
LOG = Path("doc/research_results/v45/holdout_log.txt")
CENTER = dict(K=2.5, X=0.03, H=48, S=0.05)
NEIGH = [("K", 2), ("K", 3), ("X", .02), ("X", .04), ("H", 24), ("H", 72), ("S", .03), ("S", .08)]
RUNS = 1000


def run(d, f, a, b, K, X, H, S):
    t = (d.dt >= a) & (d.dt < b)
    return E.sim(d, (t & (d.z6 >= K) & (d.oichg >= X)).values, 1, H, S, fund=f)


def perm(d, f, a, b, N, cand_mask, p):
    """同 H/S，從 cand_mask 的 24 根去重事件隨機挑 N 個。"""
    rng = np.random.default_rng(45)
    idx = np.flatnonzero(cand_mask)
    res = []
    arr = np.zeros(len(d), bool)
    for _ in range(RUNS):
        arr[:] = False
        arr[rng.choice(idx, min(N, len(idx)), replace=False)] = True
        res.append(E.sim(d, arr, 1, p["H"], p["S"], fund=f).pnl.sum())
    return np.array(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()
    d = pd.read_pickle("data/v45/feat.pkl")
    f = E.load_funding()
    p = CENTER
    dv = run(d, f, DEV0, HOLD0, **p)
    a, b = dv[dv.entry_dt < SPLIT].pnl.sum(), dv[dv.entry_dt >= SPLIT].pnl.sum()
    print("開發期", E.stats(dv), f"前段 {a:.0f} 後段 {b:.0f}", "D1", a > 0 and b > 0)
    mk = ((d.dt >= DEV0) & (d.dt < HOLD0) & (d.z6 >= p["K"])).values
    pr = perm(d, f, DEV0, HOLD0, len(dv), mk, p)
    print(f"D2 開發期置換第 97.5 百分位 {np.percentile(pr, 97.5):.0f}，實際 {dv.pnl.sum():.0f} 第 {(pr < dv.pnl.sum()).mean() * 100:.1f}", dv.pnl.sum() >= np.percentile(pr, 97.5))
    for nm, v in NEIGH:
        print(" 開發鄰域", nm, v, E.stats(run(d, f, DEV0, HOLD0, **{**p, nm: v})))
    if not args.holdout:
        return
    if LOG.exists():
        raise SystemExit("保留期已跑過，拒絕第二次")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(f"V45 R1 U 候選 {pd.Timestamp.now()} {p}\n", encoding="utf-8")

    ho = run(d, f, HOLD0, END, **p)
    ho.to_csv("doc/research_results/v45/r1_holdout_trades.csv", index=False)
    s = E.stats(ho)
    print("\n保留期", s)
    print(ho.groupby(ho.entry_dt.dt.year).pnl.agg(["count", "sum"]).round())
    print(ho[["entry_dt", "exit_dt", "why", "ret", "pnl"]].to_string())
    E.SLIP = 0.0005
    h5 = run(d, f, HOLD0, END, **p).pnl.sum()
    E.SLIP = 0.0002
    print("H1", ho.pnl.sum() > 0 and h5 > 0, f"5bp {h5:.0f}")
    print("H2", s["N"] >= 15 and s["pf"] >= 1.3)
    m = ho.groupby(ho.exit_dt.dt.to_period("M")).pnl.sum()
    h3a, h3b = ho.pnl.sum() - ho.pnl.max(), ho.pnl.sum() - m.max()
    print("H3", h3a > 0 and h3b > 0, f"去最佳單筆 {h3a:.0f} 去最佳月 {h3b:.0f}")
    ns = [(nm, v, run(d, f, HOLD0, END, **{**p, nm: v})) for nm, v in NEIGH]
    for nm, v, t in ns:
        print(" 鄰域", nm, v, E.stats(t))
    print("H4", sum(t.pnl.sum() > 0 for _, _, t in ns) >= 6)
    tm = ((d.dt >= HOLD0) & (d.dt < END)).values
    pr1 = perm(d, f, HOLD0, END, len(ho), tm & (d.z6 >= p["K"]).values, p)
    pr2 = perm(d, f, HOLD0, END, len(ho), tm, p)
    print(f"H5 OI 置換 95% {np.percentile(pr1, 95):.0f}（第 {(pr1 < ho.pnl.sum()).mean() * 100:.1f}）隨機做多 95% {np.percentile(pr2, 95):.0f}（第 {(pr2 < ho.pnl.sum()).mean() * 100:.1f}）",
          ho.pnl.sum() >= max(np.percentile(pr1, 95), np.percentile(pr2, 95)))
    anti = E.sim(d, (tm & (d.z6 >= p["K"]).values & (d.oichg < 0).values), 1, p["H"], p["S"], fund=f)
    print("H6", ho.pnl.sum() > anti.pnl.sum(), "反向對照", E.stats(anti))

    candles, engine, ind, dt = v40.load_long()
    base = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    bh = base[base.entry_dt >= HOLD0]
    mb = bh.groupby(pd.to_datetime(bh.exit_dt).dt.to_period("M")).pnl_usd.sum()
    all_m = pd.concat([mb, m], axis=1, keys=["b", "u"]).fillna(0)
    bh2 = pd.DataFrame(dict(exit_dt=pd.to_datetime(bh.exit_dt), pnl=bh.pnl_usd))
    combo = pd.concat([bh2, ho[["exit_dt", "pnl"]]])
    print("H7 月相關", round(all_m.b.corr(all_m.u), 2), "現行 MDD", round(E.mdd(bh2)), "合計 MDD", round(E.mdd(combo)), "現行 PnL", round(bh2.pnl.sum()), "合計", round(combo.pnl.sum()))

    c, ti = d.close.values, d.dt.values
    ev = [e for e in r19.find_events(c, 0, 0.08) if e[1] == 1 and HOLD0 <= d.dt.iloc[e[0]] < END]
    miss = hit = hit_all = 0
    for i, _, r in ev:
        a0, b0 = d.dt.iloc[max(0, i - 24)], d.dt.iloc[min(len(d) - 1, i + 24)]
        live = ((bh.side == "L") & (bh.entry_dt >= a0) & (bh.entry_dt <= b0)).any()
        u = ((ho.entry_dt >= a0) & (ho.entry_dt <= b0)).any()
        hit_all += u
        if not live:
            miss += 1
            hit += u
    print(f"H8 漲 ≥8% 事件 {len(ev)} 個，U 接到 {hit_all}；現行漏掉 {miss}，U 補到 {hit}（{hit / max(miss, 1) * 100:.0f}%）")


if __name__ == "__main__":
    main()
