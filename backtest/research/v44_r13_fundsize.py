"""V44 第 13 輪：funding 擁擠時「減碼」而不是不做。

預登記：doc/v44_maxhold_reframe_plan.md 第 16 節。第 12 輪 F80 擋單在 P1 有效（MH 少虧 $2,158、MDD -35%），
但 P2 被擋的單其實賺錢，整體輸 $1,006。減碼保留這些單、只降低擁擠時的曝險，是第 6 輪「配置」那類唯一有效的做法。
進出場規則完全不變，只改每筆名目：擁擠時 × m，其餘 × 1；比較一律換算成每 200U 平均保證金（同第 6 輪）。
擁擠定義沿用第 12 輪（L：funding ≥ 過去 270 次第 q 百分位；S：≤ 第 100-q 百分位；保守對齊）。

候選：中心 S80h（q=80、m=0.5）；鄰域 S80a（m=0.3）、S80b（m=0.7）、S70h、S90h（m=0.5）。
期間 P1 = 進場 < 2024-07-01，P2 = 之後。

用法（個人電腦 D:\\cryptoBot；funding 檔沿用第 12 輪 data/v44/ETH_funding.csv）：
    python backtest/research/v44_r13_fundsize.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR, build_sim, run
from v44_r12_funding import funding_state

P_END = pd.Timestamp("2024-07-01")
CANDS = {"S80h": (80, 0.5), "S80a": (80, 0.3), "S80b": (80, 0.7), "S70h": (70, 0.5), "S90h": (90, 0.5)}
CENTER = "S80h"
NEIGHBORS = ("S80a", "S80b", "S70h", "S90h")


def norm(f: pd.DataFrame) -> dict:
    k = 200.0 / f.margin.mean()
    out = {"avg": f.margin.mean()}
    for pn, g in (("P1", f[f.entry_dt < P_END]), ("P2", f[f.entry_dt >= P_END])):
        p = g.pnl_usd.to_numpy() * k
        eq = np.cumsum(p)
        mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(p) else 0.0
        mh = (g.exit_reason == "MH").to_numpy()
        out[pn] = dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(), mh=p[mh].sum(), mdd=mdd)
    out["years"] = (f.pnl_usd * k).groupby(f.entry_dt.dt.year).sum().to_dict()
    return out


def show(name, s):
    a, b = s["P1"], s["P2"]
    return (f"  {name:5s} 均保證金 {s['avg']:4.0f}U | 每200U  P1 {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH ${a['mh']:7,.0f} MDD ${a['mdd']:5,.0f}"
            f" | P2 {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH ${b['mh']:7,.0f} MDD ${b['mdd']:5,.0f}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    _, pct = funding_state(dt)
    ones = np.ones(len(dt))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]")
        res[(slip, "基準")] = norm(run(sim, ind, dt, ones, slip))
        print(show("基準", res[(slip, "基準")]))
        for name, (q, m) in CANDS.items():
            okL, okS = pct[q]
            f = run(sim, ind, dt, np.where(okL, 1.0, m), slip, np.where(okS, 1.0, m))
            res[(slip, name)] = norm(f)
            print(show(name, res[(slip, name)]))
            if slip == 0:
                f.to_csv(OUT_DIR / f"r13_{name}_trades.csv", index=False)

    print("\n[逐年，0bp，每200U]")
    yrs = sorted(res[(0, "基準")]["years"])
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        print("  " + " ".join(f"{res[(0, name)]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 S80h，每200U]")
    ok = {}
    for pn in ("P1", "P2"):
        for slip in (0, 2, 5):
            ok[f"{pn} {slip}bp PnL勝"] = res[(slip, CENTER)][pn]["pnl"] > res[(slip, "基準")][pn]["pnl"]
        ok[f"{pn} MDD不增"] = res[(0, CENTER)][pn]["mdd"] <= res[(0, "基準")][pn]["mdd"]
    ok["P1 MH少虧"] = res[(0, CENTER)]["P1"]["mh"] > res[(0, "基準")]["P1"]["mh"]
    nb = sum(res[(0, n)][pn]["pnl"] > res[(0, "基準")][pn]["pnl"] for n in NEIGHBORS for pn in ("P1", "P2"))
    ok["鄰域8格至少6格勝"] = nb >= 6
    wy = [y for y in yrs if y >= 2022]
    better = sum(res[(0, CENTER)]["years"].get(y, 0) > res[(0, "基準")]["years"].get(y, 0) for y in wy)
    ok["2022起過半年份勝"] = better * 2 > len(wy)
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域勝基準 {nb}/8 格；2022 起勝基準 {better}/{len(wy)} 年")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
