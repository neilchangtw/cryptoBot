"""V44 第 18 輪：大趨勢下跌不做多／只做順勢，回答「逆勢單是否比較差」。

預登記：doc/v44_maxhold_reframe_plan.md 第 21 節。R = close[i-1] / close[i-1-24×d] − 1。
候選：中心 L15D30（R < −15% 不開 L）；鄰域 L10D30、L20D30、L15D20。
探索（不判 PASS）：WT（R>0 只做 L、R<0 只做 S）；WT5（|R|≤5% 兩邊都做，>5% 只做順勢）。
期間 P1 = 進場 < 2024-07-01，P2 = 之後；2026-07 起只列參考。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r18_ltrendskip.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r15_lowvolpause import build_sim, part, run

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-07-01")
CANDS = {"L15D30": (0.15, 30), "L10D30": (0.10, 30), "L20D30": (0.20, 30), "L15D20": (0.15, 20)}
CENTER, NEIGHBORS = "L15D30", ("L10D30", "L20D30", "L15D20")
BUCKETS = [(-np.inf, -0.15, "< -15%"), (-0.15, -0.05, "-15~-5%"), (-0.05, 0.05, "-5~5%"), (0.05, 0.15, "5~15%"), (0.15, np.inf, "> 15%")]


def past_return(c: np.ndarray, d: int) -> np.ndarray:
    s = pd.Series(c)
    return (s.shift(1) / s.shift(1 + 24 * d) - 1).to_numpy()


def stats(g: pd.DataFrame) -> str:
    s = part(g)
    return f"{s['n']:3d}筆 WR {s['wr']:5.1f}% ${s['pnl']:7,.0f} MDD ${s['mdd']:5,.0f}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    c = candles["close"].to_numpy(float)
    n = len(c)
    all_ok = np.ones(n, bool)
    r30 = past_return(c, 30)
    extras = {name: {"okL": ~(past_return(c, d) < -x), "okS": all_ok} for name, (x, d) in CANDS.items()}
    extras["WT"] = {"okL": ~(r30 < 0), "okS": ~(r30 > 0)}
    extras["WT5"] = {"okL": ~(r30 < -0.05), "okS": ~(r30 > 0.05)}
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    t = pd.to_datetime(dt)
    p1 = (np.arange(n) >= sb) & (t < P_END).to_numpy()
    p2 = (t >= P_END).to_numpy()
    p3 = (t >= RECENT).to_numpy()
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後；近期 = {RECENT:%Y-%m-%d} 起（只參考）")

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易依過去 30 天報酬 R 分組（0bp）：逆勢單是否比較差]")
    i = base0.entry_bar.astype(int).to_numpy()
    base0["R"] = r30[i]
    for side in ("L", "S"):
        d = base0[base0.side == side]
        for lo, hi, lab in BUCKETS:
            g = d[(d.R > lo) & (d.R <= hi)]
            print(f"  {side} R {lab:8s} P1 {stats(g[g.entry_dt < P_END])} | P2 {stats(g[g.entry_dt >= P_END])} | 近期 {stats(g[g.entry_dt >= RECENT])}")
    print("  逆勢 vs 順勢合計（L 逆勢 = R<0，S 逆勢 = R>0）")
    against = ((base0.side == "L") & (base0.R < 0)) | ((base0.side == "S") & (base0.R > 0))
    for lab, m in (("順勢", ~against), ("逆勢", against)):
        print(f"    {lab} P1 {stats(base0[m & (base0.entry_dt < P_END)])} | P2 {stats(base0[m & (base0.entry_dt >= P_END)])}")

    print("\n[被擋時間占比（L / S）]")
    for name, e in extras.items():
        print(f"  {name:7s} P1 {(~e['okL'][p1]).mean() * 100:4.1f}%/{(~e['okS'][p1]).mean() * 100:4.1f}%  P2 {(~e['okL'][p2]).mean() * 100:4.1f}%/{(~e['okS'][p2]).mean() * 100:4.1f}%"
              f"  近期 {(~e['okL'][p3]).mean() * 100:4.1f}%/{(~e['okS'][p3]).mean() * 100:4.1f}%")

    l0 = base0[base0.side == "L"]
    blocked = {}
    print("\n[基準 L 交易：未擋 vs 被擋（0bp）]")
    for name, e in extras.items():
        ok = e["okL"][l0.entry_bar.astype(int).to_numpy()]
        for lab, m in (("未擋", ok), ("被擋", ~ok)):
            a, b, r = l0[m & (l0.entry_dt < P_END).to_numpy()], l0[m & (l0.entry_dt >= P_END).to_numpy()], l0[m & (l0.entry_dt >= RECENT).to_numpy()]
            if lab == "被擋":
                blocked[name] = (a.pnl_usd.sum(), b.pnl_usd.sum())
            print(f"  {name:7s} {lab} P1 {stats(a)} | P2 {stats(b)} | 近期 {stats(r)}")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  全部 P1 | P2 | 近期 || L P1 | L P2 | L 近期")
        for name in ["基準"] + list(extras):
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, {} if name == "基準" else extras[name], slip)
            a, b, r = f[f.entry_dt < P_END], f[f.entry_dt >= P_END], f[f.entry_dt >= RECENT]
            res[(slip, name)] = (part(a), part(b), f)
            s = f[f.side == "L"]
            print(f"  {name:7s} {stats(a)} | {stats(b)} | {stats(r)} || {stats(s[s.entry_dt < P_END])} | {stats(s[s.entry_dt >= P_END])} | {stats(s[s.entry_dt >= RECENT])}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r18_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(extras):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 L15D30]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    ok["P1 MDD不增逾10%"] = res[(0, CENTER)][0]["mdd"] <= res[(0, "基準")][0]["mdd"] * 1.1
    for slip in (0, 2):
        ok[f"P2 {slip}bp PnL勝"] = res[(slip, CENTER)][1]["pnl"] > res[(slip, "基準")][1]["pnl"]
    ok["P2 MDD不增逾10%"] = res[(0, CENTER)][1]["mdd"] <= res[(0, "基準")][1]["mdd"] * 1.1
    nb = sum(res[(0, m)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for m in NEIGHBORS)
    ok["鄰域P1至少2/3勝"] = nb >= 2
    ok["被擋L P1<0"] = blocked[CENTER][0] < 0
    ok["被擋L P2<0"] = blocked[CENTER][1] < 0
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域 P1 0bp 勝基準 {nb}/3；被擋 L 基準合計 P1 ${blocked[CENTER][0]:,.0f}、P2 ${blocked[CENTER][1]:,.0f}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
