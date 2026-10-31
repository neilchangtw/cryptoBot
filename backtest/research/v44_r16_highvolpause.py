"""V44 第 16 輪：高波動時整個策略暫停（補第 15 輪的另一端）。

預登記：doc/v44_maxhold_reframe_plan.md 第 19 節。第 15 輪低波動暫停通過（SHADOW），但 2021 高波動打 SafeNet 的虧損
完全沒改善。期間比對顯示策略適合中等波動：太低走不到 TP，太高先被反向打到 SafeNet。
規則同第 15 輪的波動百分位 R（過去 w 天 ATR14% 中位、在過去 730 天的百分位），改成 R > q → 暫停。
候選：中心 H80W30（q=80、w=30）；鄰域 H70W30、H90W30、H80W14。
探索 BAND：同時套用第 15 輪 Q20W30 與 H80W30（只看兩者是否互補，不另判 PASS）。
期間 P1 = 進場 < 2024-07-01，P2 = 之後。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r16_highvolpause.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r15_lowvolpause import build_sim, part, run, vol_state

P_END = pd.Timestamp("2024-07-01")
CANDS = {"H80W30": (80, 30), "H70W30": (70, 30), "H90W30": (90, 30), "H80W14": (80, 14)}
CENTER, NEIGHBORS = "H80W30", ("H70W30", "H90W30", "H80W14")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    masks = {name: ~(vol_state(candles, w)[1] > q) for name, (q, w) in CANDS.items()}
    masks["BAND"] = masks[CENTER] & ~(vol_state(candles, 30)[1] < 20)
    t = pd.to_datetime(dt)
    p1 = (np.arange(len(t)) >= sb) & (t < P_END).to_numpy()
    p2 = (t >= P_END).to_numpy()
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")
    print("\n[暫停時間占比]")
    for name, m in masks.items():
        print(f"  {name:7s} P1 {(~m[p1]).mean() * 100:4.1f}%  P2 {(~m[p2]).mean() * 100:4.1f}%")

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易：暫停期間 vs 平常（0bp）]")
    for name in masks:
        ok = masks[name][base0.entry_bar.astype(int).to_numpy()]
        cells = []
        for lab, m in (("平常", ok), ("暫停期", ~ok)):
            for pn, pm in (("P1", base0.entry_dt < P_END), ("P2", base0.entry_dt >= P_END)):
                g = base0[m & pm.to_numpy()]
                s = part(g)
                cells.append(f"{lab}{pn} {s['n']:3d}筆 WR {s['wr']:4.0f}% SN {(g.exit_reason == 'SN').mean() * 100 if len(g) else 0:3.0f}% ${s['pnl']:6,.0f}")
        print(f"  {name:7s} " + " | ".join(cells))

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(masks):
            extra = {} if name == "基準" else {"okL": masks[name], "okS": masks[name]}
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, extra, slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            sn = int((f[f.entry_dt < P_END].exit_reason == "SN").sum())
            res[(slip, name)] = (a, b, f)
            print(f"  {name:7s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} SN {sn:3d}筆 MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r16_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(masks):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 H80W30]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    ok["P1 MDD不增逾10%"] = res[(0, CENTER)][0]["mdd"] <= res[(0, "基準")][0]["mdd"] * 1.1
    for slip in (0, 2):
        ok[f"P2 {slip}bp PnL勝"] = res[(slip, CENTER)][1]["pnl"] > res[(slip, "基準")][1]["pnl"]
    ok["P2 MDD不增逾10%"] = res[(0, CENTER)][1]["mdd"] <= res[(0, "基準")][1]["mdd"] * 1.1
    nb = sum(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    ok["鄰域P1至少2/3勝"] = nb >= 2
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域 P1 0bp 勝基準 {nb}/3")
    a, b = res[(0, "BAND")][0], res[(0, "BAND")][1]
    print(f"  探索 BAND（低波動＋高波動都暫停）：P1 ${a['pnl']:,.0f} MDD ${a['mdd']:,.0f} | P2 ${b['pnl']:,.0f} MDD ${b['mdd']:,.0f}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
