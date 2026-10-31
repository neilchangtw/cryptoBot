"""V44 診斷：2026-08～10 的行情最像歷史哪一段，那些時期後來有沒有恢復（純診斷，不改策略）。

滾動 3 個月、每月往前一步切窗（2021-03 起），每窗算行情指紋與策略績效；以 2026-08～10 為目標，
只用標準化後的行情指紋算距離，列最像的 5 個窗及其之後 3、6 個月的策略表現。
固定 200U、realistic、0bp、每筆 +$5，同 v40.load_long 與現行引擎。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_regime_analog.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_period_compare import breakout_follow

ROOT = Path(__file__).resolve().parents[2]
LIVE_CSV = ROOT / "data_live" / "trades.csv"
START = pd.Timestamp("2021-03-01")
TARGET = pd.Timestamp("2026-08-01")
P_END = pd.Timestamp("2024-07-01")
MKT = ["vol", "atr", "rng", "ac_d", "UP", "MILD_UP", "SIDE", "DOWN", "eth", "L_tp", "L_adv", "S_tp", "S_adv"]
TOP = 5


def trades_stats(g: pd.DataFrame) -> dict:
    n = len(g)
    if not n:
        return dict(n=0, wr=np.nan, pnl=0.0, tp=np.nan, mh=np.nan, sn=np.nan)
    return dict(n=n, wr=(g.pnl_usd > 0).mean() * 100, pnl=g.pnl_usd.sum(), tp=(g.exit_reason == "TP").mean() * 100,
                mh=(g.exit_reason == "MH").mean() * 100, sn=(g.exit_reason == "SN").mean() * 100)


def fmt(s: dict) -> str:
    return f"{s['n']:3d}筆 WR {s['wr']:3.0f}% ${s['pnl']:6,.0f} TP {s['tp']:3.0f}% MH {s['mh']:3.0f}% SN {s['sn']:3.0f}%"


def windows(last: pd.Timestamp) -> list:
    out, a = [], START
    while a + pd.DateOffset(months=3) <= last:
        out.append((a, a + pd.DateOffset(months=3)))
        a += pd.DateOffset(months=1)
    return out


def fingerprint(a, b, t, c, h, l, r, tr, atr, reg, sigL, sigS, L_TP, S_TP) -> dict:
    m = ((t >= a) & (t < b)).to_numpy()
    d = pd.Series(r[m]).rolling(24).sum().to_numpy()[23::24]
    fp = dict(vol=np.nanstd(r[m]) * np.sqrt(8760) * 100, atr=np.nanmedian(atr[m]), rng=np.nanmedian(tr[m]),
              ac_d=pd.Series(d).autocorr(1), eth=(c[m][-1] / c[m][0] - 1) * 100)
    vc = pd.Series(reg[m]).value_counts(normalize=True) * 100
    for k in ("UP", "MILD_UP", "SIDE", "DOWN"):
        fp[k] = vc.get(k, 0.0)
    for side, sig, d_, tp, bars in (("L", sigL, 1, L_TP, 6), ("S", sigS, -1, S_TP, 10)):
        win, none, n = breakout_follow(c, h, l, sig & m, d_, tp, 0.02, bars)
        fp[f"{side}_tp"], fp[f"{side}_adv"], fp[f"{side}_n"] = win, 100 - win - none, n
    return fp


def main() -> None:
    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    c, h, l = (candles[k].to_numpy(float) for k in ("close", "high", "low"))
    t = pd.to_datetime(dt)
    r = np.diff(np.log(c), prepend=np.nan)
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))) / c * 100
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    slope = np.asarray(ind["slope"], float)
    reg = np.array([engine._classify_regime(s) if not np.isnan(s) else "NA" for s in slope])
    sigL = (np.nan_to_num(ind["pctile_L"], nan=100) < engine.L_GK_TH) & np.asarray(ind["brk_up"], bool)
    sigS = (np.nan_to_num(ind["pctile_S"], nan=100) < engine.S_GK_TH) & np.asarray(ind["brk_dn"], bool)
    last = (t.iloc[-1] + pd.Timedelta(hours=1)).to_period("M").to_timestamp() + pd.DateOffset(months=1)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}（最後一窗的 10 月未滿）；交易 {len(f)} 筆")

    rows = []
    for a, b in windows(last):
        g = f[(f.entry_dt >= a) & (f.entry_dt < b)]
        fp = fingerprint(a, b, t, c, h, l, r, tr, atr, reg, sigL, sigS, engine.L_TP, engine.S_TP)
        rows.append(dict(a=a, b=b, **fp, all=trades_stats(g), L=trades_stats(g[g.side == "L"]), S=trades_stats(g[g.side == "S"])))
    w = pd.DataFrame(rows)

    print("\n[1] 滾動 3 個月指紋與策略績效")
    print("  起始     vol ATR 振幅 日自相關  UP  MU  SD  DN ETH%  訊號L先TP/逆2%  訊號S先TP/逆2% | L | S")
    for x in w.itertuples():
        print(f"  {x.a:%Y-%m} {x.vol:4.0f} {x.atr:.2f} {x.rng:.2f} {x.ac_d:+.2f} {x.UP:3.0f} {x.MILD_UP:3.0f} {x.SIDE:3.0f} {x.DOWN:3.0f} {x.eth:+4.0f}"
              f"  {x.L_tp:3.0f}/{x.L_adv:3.0f}({x.L_n:3d})  {x.S_tp:3.0f}/{x.S_adv:3.0f}({x.S_n:3d}) | {fmt(x.L)} | {fmt(x.S)}")

    tgt = w[w.a == TARGET].iloc[0]
    hist = w[w.b <= TARGET].copy()
    mu, sd = hist[MKT].mean(), hist[MKT].std().replace(0, 1)
    z = (hist[MKT] - mu) / sd
    tz = (tgt[MKT].astype(float) - mu) / sd
    hist["dist"] = np.sqrt(((z - tz) ** 2).mean(axis=1))

    def after(b, months):
        e = b + pd.DateOffset(months=months)
        g = f[(f.entry_dt >= b) & (f.entry_dt < e)]
        return trades_stats(g), e <= last

    print("\n[2] 目標窗 2026-08～10")
    print("  " + " ".join(f"{k} {float(tgt[k]):.1f}" for k in MKT) + f" | {fmt(tgt['all'])}")
    print(f"\n  最像的 {TOP} 窗（只用行情欄位；窗之間可能重疊）")
    for x in hist.sort_values("dist").head(TOP).itertuples():
        a3, ok3 = after(x.b, 3)
        a6, ok6 = after(x.b, 6)
        print(f"  {x.a:%Y-%m}～{x.b - pd.Timedelta(days=1):%Y-%m} 距離 {x.dist:.2f} vol {x.vol:.0f} ATR {x.atr:.2f} ETH {x.eth:+.0f}% 訊號L {x.L_tp:.0f}/{x.L_adv:.0f} S {x.S_tp:.0f}/{x.S_adv:.0f}"
              f" | 該窗 {fmt(x.all)}")
        print(f"     之後3月{'' if ok3 else '(不足)'} {fmt(a3)} | 之後6月{'' if ok6 else '(不足)'} {fmt(a6)}")

    print("\n  各維度距離最大（目標與最像一窗的 z 差）")
    best = z.loc[hist.dist.idxmin()]
    for k, v in (best - tz).abs().sort_values(ascending=False).head(5).items():
        print(f"    {k} 差 {v:.2f}σ")

    print("\n  對照：所有歷史窗之後 3／6 個月的平均（不看相似度）")
    p3 = [after(b, 3)[0]["pnl"] for b in hist.b if after(b, 3)[1]]
    p6 = [after(b, 6)[0]["pnl"] for b in hist.b if after(b, 6)[1]]
    print(f"    之後3月 平均 ${np.mean(p3):,.0f} 中位 ${np.median(p3):,.0f}（{len(p3)} 窗，正 {np.mean(np.array(p3) > 0) * 100:.0f}%）；"
          f"之後6月 平均 ${np.mean(p6):,.0f} 中位 ${np.median(p6):,.0f}（{len(p6)} 窗，正 {np.mean(np.array(p6) > 0) * 100:.0f}%）")

    print("\n  另看：策略績效本身差（WR<40%）的歷史窗，之後多久恢復")
    for x in hist[hist["all"].map(lambda s: s["n"] >= 10 and s["wr"] < 40)].itertuples():
        a3, ok3 = after(x.b, 3)
        a6, ok6 = after(x.b, 6)
        print(f"    {x.a:%Y-%m} 起 {fmt(x.all)} | 之後3月 {fmt(a3)} | 之後6月 {fmt(a6)}")

    print("\n[3] 2026-08～10 方向 × regime，對照 P1 同組")
    cur = f[f.entry_dt >= TARGET]
    p1 = f[f.entry_dt < P_END]
    for (side, rg), g in cur.groupby(["side", "entry_regime"]):
        o = p1[(p1.side == side) & (p1.entry_regime == rg)]
        print(f"  {side} {rg:8s} 近期 {fmt(trades_stats(g))} | P1 {fmt(trades_stats(o))}")

    print("\n[4] 實盤")
    if LIVE_CSV.exists():
        lv = pd.read_csv(LIVE_CSV)
        print(f"  {LIVE_CSV} {len(lv)} 筆，欄位 {list(lv.columns)[:12]}")
    else:
        print(f"  這台沒有 {LIVE_CSV}，跳過，只用回測。")


if __name__ == "__main__":
    main()
