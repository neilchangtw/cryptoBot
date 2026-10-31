"""V44 診斷：2021-03～2024-06 與 2024-07 之後，行情與策略績效差在哪裡（純診斷，不改策略）。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_period_compare.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_mh_diagnosis import entry_features

P_END = pd.Timestamp("2024-07-01")


def trade_stats(g: pd.DataFrame) -> str:
    n = len(g)
    if not n:
        return "  0 筆"
    tp, mh = g[g.exit_reason == "TP"], g[g.exit_reason == "MH"]
    return (f"{n:3d}筆 WR {(g.pnl_usd > 0).mean() * 100:4.0f}% ${g.pnl_usd.sum():6,.0f} 均${g.pnl_usd.mean():5.1f} "
            f"TP {len(tp) / n * 100:3.0f}% MH {len(mh) / n * 100:3.0f}% SN {(g.exit_reason == 'SN').mean() * 100:3.0f}% "
            f"MFE中位 {g.mfe_pct.median():4.2f}%")


def breakout_follow(c, h, l, sig, d, tp, sl, bars):
    """所有訊號（不管有沒有成交）之後 bars 根內，先碰 +tp 還是先碰 -sl（L；S 對稱）。回傳 (先TP比例, 都沒碰比例, 筆數)。"""
    win = none = n = 0
    for i in np.flatnonzero(sig):
        if i + bars >= len(c):
            continue
        n += 1
        e = c[i]
        hit = None
        for j in range(i + 1, i + bars + 1):
            up = (h[j] / e - 1) if d == 1 else (1 - l[j] / e)
            dn = (1 - l[j] / e) if d == 1 else (h[j] / e - 1)
            if dn >= sl:
                hit = "sl"; break
            if up >= tp:
                hit = "tp"; break
        win += hit == "tp"
        none += hit is None
    return win / max(n, 1) * 100, none / max(n, 1) * 100, n


def main() -> None:
    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    f = f.join(entry_features(candles, ind, f))
    f["P"] = np.where(f.entry_dt < P_END, "P1", "P2")
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    c, h, l = (candles[k].to_numpy(float) for k in ("close", "high", "low"))
    t = pd.to_datetime(dt)
    p1 = (np.arange(len(c)) >= sb) & (t < P_END).to_numpy()
    p2 = (t >= P_END).to_numpy()
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 2021-03～2024-06，P2 2024-07 之後")

    print("\n[1] 行情本身")
    r = np.diff(np.log(c), prepend=np.nan)
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))) / c * 100
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    slope = np.asarray(ind["slope"], float)
    for name, m in (("P1", p1), ("P2", p2)):
        if not m.any():
            continue
        rr = r[m]
        ac1 = pd.Series(rr).autocorr(1)
        ac24 = pd.Series(pd.Series(rr).rolling(24).sum().to_numpy()[::24]).autocorr(1)
        reg = pd.Series([engine._classify_regime(s) if not np.isnan(s) else "NA" for s in slope[m]]).value_counts(normalize=True) * 100
        yrs = m.sum() / 8760
        print(f"  {name} {yrs:.1f} 年 價格 {c[m][0]:,.0f}→{c[m][-1]:,.0f}（年化 {((c[m][-1] / c[m][0]) ** (1 / yrs) - 1) * 100:+.0f}%）"
              f" 年化波動 {np.nanstd(rr) * np.sqrt(8760) * 100:.0f}% 1h振幅中位 {np.nanmedian(tr[m]):.2f}% ATR14中位 {np.nanmedian(atr[m]):.2f}%")
        print(f"     1h 報酬自相關 {ac1:+.3f}，日報酬自相關 {ac24:+.3f}（正=趨勢延續，負=反轉）；regime 時間占比 " +
              " ".join(f"{k} {v:.0f}%" for k, v in reg.items()))

    print("\n[2] 突破訊號本身的延續性（所有 GK 壓縮突破訊號，不管有沒有成交；先碰 TP 還是先碰 -2%／10 根內）")
    pL, pS = ind["pctile_L"], ind["pctile_S"]
    sigL = (np.nan_to_num(pL, nan=100) < engine.L_GK_TH) & np.asarray(ind["brk_up"], bool)
    sigS = (np.nan_to_num(pS, nan=100) < engine.S_GK_TH) & np.asarray(ind["brk_dn"], bool)
    for side, sig, d, tp, mhb in (("L", sigL, 1, engine.L_TP, 6), ("S", sigS, -1, engine.S_TP, 10)):
        for name, m in (("P1", p1), ("P2", p2)):
            w, none, n = breakout_follow(c, h, l, sig & m, d, tp, 0.02, mhb)
            print(f"  {side} {name} {n:4d} 個訊號：{mhb} 根內先到 TP {tp * 100:.1f}% 的 {w:4.1f}%，都沒到（≈MH）{none:4.1f}%")

    print("\n[3] 策略交易：方向 × 期間")
    for side in ("L", "S"):
        for name in ("P1", "P2"):
            print(f"  {side} {name} {trade_stats(f[(f.side == side) & (f.P == name)])}")

    print("\n[4] 方向 × 進場 regime × 期間")
    for (side, reg), g in f.groupby(["side", "entry_regime"]):
        print(f"  {side} {reg:8s} P1 {trade_stats(g[g.P == 'P1'])}")
        print(f"  {side} {reg:8s} P2 {trade_stats(g[g.P == 'P2'])}")

    print("\n[5] 進場特徵中位數 P1 vs P2")
    for col in ("atr_pct", "brk_atr", "gk_pctile", "slope_pct", "ret24_dir", "rel_vol"):
        for side in ("L", "S"):
            a, b = f[(f.side == side) & (f.P == "P1")][col], f[(f.side == side) & (f.P == "P2")][col]
            print(f"  {side} {col:14s} P1 {a.median():7.2f}  P2 {b.median():7.2f}")

    print("\n[6] 逐半年")
    f["H"] = f.entry_dt.dt.year.astype(str) + np.where(f.entry_dt.dt.month <= 6, "H1", "H2")
    for hlf, g in f.groupby("H"):
        print(f"  {hlf} L {trade_stats(g[g.side == 'L'])} | S {trade_stats(g[g.side == 'S'])}")


if __name__ == "__main__":
    main()
