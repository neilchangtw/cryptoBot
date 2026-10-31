"""V44 第 17 輪：ETH 大趨勢上漲時不做空（只影響 S）。

預登記：doc/v44_maxhold_reframe_plan.md 第 20 節。訊號棒 i 的 R = close[i-1] / close[i-1-24×d] − 1；R > x → 不開 S。
候選：中心 T15D30（x=15%、d=30）；鄰域 T10D30、T20D30、T15D20。
探索（不判 PASS）：TS（價格在 SMA720 上方且 SMA720 比 168 根前高時不開 S）；COMBO（T15D30 ＋ 第 15 輪 Q20W30 暫停 L+S）。
期間 P1 = 進場 < 2024-07-01，P2 = 之後；2026-07 起只列參考。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r17_strendskip.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r15_lowvolpause import build_sim, ok_mask, part, run

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-07-01")
CANDS = {"T15D30": (0.15, 30), "T10D30": (0.10, 30), "T20D30": (0.20, 30), "T15D20": (0.15, 20)}
CENTER, NEIGHBORS = "T15D30", ("T10D30", "T20D30", "T15D20")


def trend_ok(c: np.ndarray, x: float, d: int) -> np.ndarray:
    s = pd.Series(c)
    r = (s.shift(1) / s.shift(1 + 24 * d) - 1).to_numpy()
    return ~(r > x)                                                # NaN → 不擋


def ts_ok(c: np.ndarray) -> np.ndarray:
    s = pd.Series(c).shift(1)
    sma = s.rolling(720).mean()
    return ~((s > sma) & (sma > sma.shift(168))).to_numpy()


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
    pause = ok_mask(candles, 20, 30)
    extras = {name: {"okL": all_ok, "okS": trend_ok(c, x, d)} for name, (x, d) in CANDS.items()}
    extras["TS"] = {"okL": all_ok, "okS": ts_ok(c)}
    extras["COMBO"] = {"okL": pause, "okS": pause & trend_ok(c, *CANDS[CENTER])}
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    t = pd.to_datetime(dt)
    p1 = (np.arange(n) >= sb) & (t < P_END).to_numpy()
    p2 = (t >= P_END).to_numpy()
    p3 = (t >= RECENT).to_numpy()
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後；近期 = {RECENT:%Y-%m-%d} 起（只參考）")

    print("\n[S 被擋時間占比]")
    for name, e in extras.items():
        m = e["okS"]
        print(f"  {name:7s} P1 {(~m[p1]).mean() * 100:4.1f}%  P2 {(~m[p2]).mean() * 100:4.1f}%  近期 {(~m[p3]).mean() * 100:4.1f}%")

    base0 = run(sim, ind, dt, {}, 0)
    s0 = base0[base0.side == "S"]
    blocked = {}
    print("\n[基準 S 交易：未擋 vs 被擋（0bp）]")
    for name, e in extras.items():
        ok = e["okS"][s0.entry_bar.astype(int).to_numpy()]
        for lab, m in (("未擋", ok), ("被擋", ~ok)):
            a, b, r = s0[m & (s0.entry_dt < P_END).to_numpy()], s0[m & (s0.entry_dt >= P_END).to_numpy()], s0[m & (s0.entry_dt >= RECENT).to_numpy()]
            if lab == "被擋":
                blocked[name] = (a.pnl_usd.sum(), b.pnl_usd.sum())
            print(f"  {name:7s} {lab} P1 {stats(a)} | P2 {stats(b)} | 近期 {stats(r)}")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  全部 P1 | P2 | 近期 || S P1 | S P2 | S 近期")
        for name in ["基準"] + list(extras):
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, {} if name == "基準" else extras[name], slip)
            a, b, r = f[f.entry_dt < P_END], f[f.entry_dt >= P_END], f[f.entry_dt >= RECENT]
            res[(slip, name)] = (part(a), part(b), f)
            s = f[f.side == "S"]
            print(f"  {name:7s} {stats(a)} | {stats(b)} | {stats(r)} || {stats(s[s.entry_dt < P_END])} | {stats(s[s.entry_dt >= P_END])} | {stats(s[s.entry_dt >= RECENT])}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r17_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(extras):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 T15D30]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    ok["P1 MDD不增逾10%"] = res[(0, CENTER)][0]["mdd"] <= res[(0, "基準")][0]["mdd"] * 1.1
    for slip in (0, 2):
        ok[f"P2 {slip}bp PnL勝"] = res[(slip, CENTER)][1]["pnl"] > res[(slip, "基準")][1]["pnl"]
    ok["P2 MDD不增逾10%"] = res[(0, CENTER)][1]["mdd"] <= res[(0, "基準")][1]["mdd"] * 1.1
    nb = sum(res[(0, m)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for m in NEIGHBORS)
    ok["鄰域P1至少2/3勝"] = nb >= 2
    ok["被擋S P1<0"] = blocked[CENTER][0] < 0
    ok["被擋S P2<0"] = blocked[CENTER][1] < 0
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域 P1 0bp 勝基準 {nb}/3；被擋 S 基準合計 P1 ${blocked[CENTER][0]:,.0f}、P2 ${blocked[CENTER][1]:,.0f}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
