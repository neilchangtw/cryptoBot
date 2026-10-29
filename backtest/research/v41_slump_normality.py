"""V41 低潮檢查：2026-06～10 的交易，在 2021-03 至今的完整回測中算不算正常。

純診斷，不改策略、不下載新資料；用 V39 的 data/v39/ETH_1h_long.csv（固定 200U、realistic、每筆 +$5）。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v41_slump_normality.py
    .venv/bin/python backtest/research/v41_slump_normality.py --start 2026-06-01 --end 2026-11-01
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40


def max_drawdown(pnl: np.ndarray) -> float:
    eq = np.cumsum(pnl)
    return float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(eq) else 0.0


def longest_loss_streak(win: np.ndarray) -> int:
    best = cur = 0
    for w in win:
        cur = 0 if w else cur + 1
        best = max(best, cur)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-11-01")
    ap.add_argument("--perm", type=int, default=5000)
    a = ap.parse_args()

    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    pnl = f.pnl_usd.to_numpy(float)
    win = pnl > 0
    print(f"回測 {f.entry_dt.min():%Y-%m-%d} ～ {f.entry_dt.max():%Y-%m-%d}：{len(f)} 筆，"
          f"勝率 {win.mean() * 100:.1f}%，PnL ${pnl.sum():,.0f}，最大回撤 ${max_drawdown(pnl):,.0f}")

    print("\n[1] 每年")
    for y, g in f.groupby(f.entry_dt.dt.year):
        p = g.pnl_usd.to_numpy()
        print(f"  {y}：{len(g):3d} 筆  勝率 {(p > 0).mean() * 100:5.1f}%  PnL ${p.sum():8,.0f}  回撤 ${max_drawdown(p):6,.0f}")

    sel = ((f.entry_dt >= a.start) & (f.entry_dt < a.end)).to_numpy()
    idx = np.flatnonzero(sel)
    n = len(idx)
    if n < 5:
        print(f"\n{a.start}～{a.end} 只有 {n} 筆，無法比較"); return
    cw, cp = win[idx].mean(), pnl[idx].sum()
    cdd, cls = max_drawdown(pnl[idx]), longest_loss_streak(win[idx])
    g = f.iloc[idx]
    print(f"\n[2] 本期 {a.start} ～ {a.end}（回測重現）：{n} 筆  勝率 {cw * 100:.1f}%  PnL ${cp:,.0f}"
          f"  期內回撤 ${cdd:,.0f}  最長連虧 {cls} 筆")
    for side in ("L", "S"):
        s = g[g.side == side]
        if len(s):
            print(f"  {side}：{len(s)} 筆  勝率 {(s.pnl_usd > 0).mean() * 100:.1f}%  PnL ${s.pnl_usd.sum():,.0f}")
    if "exit_reason" in g:
        print("  出場：" + "、".join(f"{k} {v}" for k, v in g.exit_reason.value_counts().items()))

    # 同樣 n 筆的滾動視窗（只用本期之前的歷史）
    hist_end = idx[0]
    if hist_end < n * 2:
        print("\n本期之前歷史太短"); return
    cs_w = np.concatenate([[0], np.cumsum(win[:hist_end])])
    cs_p = np.concatenate([[0], np.cumsum(pnl[:hist_end])])
    rw = (cs_w[n:] - cs_w[:-n]) / n
    rp = cs_p[n:] - cs_p[:-n]
    print(f"\n[3] 本期之前所有連續 {n} 筆視窗（{len(rw)} 個）")
    print(f"  勝率：中位 {np.median(rw) * 100:.1f}%  最低 {rw.min() * 100:.1f}%  "
          f"5%分位 {np.percentile(rw, 5) * 100:.1f}%  → 本期 {cw * 100:.1f}% 低於 {(rw < cw).mean() * 100:.1f}% 的視窗、"
          f"≤ 本期的視窗占 {(rw <= cw).mean() * 100:.1f}%")
    print(f"  PnL：中位 ${np.median(rp):,.0f}  最差 ${rp.min():,.0f}  5%分位 ${np.percentile(rp, 5):,.0f}"
          f"  → ≤ 本期的視窗占 {(rp <= cp).mean() * 100:.1f}%")

    # 歷史上的獨立低潮事件（勝率 ≤ 本期）與之後是否回升
    starts, k = [], 0
    while k < len(rw):
        if rw[k] <= cw:
            starts.append(k); k += n
        else:
            k += 1
    print(f"\n[4] 歷史上勝率 ≤ {cw * 100:.1f}% 的獨立低潮：{len(starts)} 次")
    for s0 in starts:
        e0 = s0 + n
        nxt = win[e0:e0 + n]
        nxtp = pnl[e0:e0 + n]
        tail = f"之後 {len(nxt)} 筆勝率 {nxt.mean() * 100:.1f}%、PnL ${nxtp.sum():,.0f}" if len(nxt) else "之後無資料"
        print(f"  {f.entry_dt[s0]:%Y-%m-%d} ～ {f.entry_dt[e0 - 1]:%Y-%m-%d}：勝率 {win[s0:e0].mean() * 100:.1f}%"
              f"  PnL ${pnl[s0:e0].sum():,.0f}  → {tail}")

    hdd, hls = max_drawdown(pnl[:hist_end]), longest_loss_streak(win[:hist_end])
    print(f"\n[5] 本期之前歷史：最大回撤 ${hdd:,.0f}、最長連虧 {hls} 筆；本期 ${cdd:,.0f}、{cls} 筆")

    # 勝率長期趨勢：勝負對交易序號的相關，洗牌檢定（單尾：往下）
    rng = np.random.default_rng(0)
    t = np.arange(len(win), dtype=float)
    y = win.astype(float)
    r = np.corrcoef(t, y)[0, 1]
    null = np.array([np.corrcoef(t, rng.permutation(y))[0, 1] for _ in range(a.perm)])
    print(f"\n[6] 全期勝率趨勢：相關 r = {r:+.3f}，洗牌單尾 p（往下）= {(null <= r).mean():.4f}")
    y2 = y[f.entry_dt >= "2024-11-01"]
    t2 = np.arange(len(y2), dtype=float)
    r2 = np.corrcoef(t2, y2)[0, 1]
    null2 = np.array([np.corrcoef(t2, rng.permutation(y2))[0, 1] for _ in range(a.perm)])
    print(f"    近 2 年（2024-11 起）：r = {r2:+.3f}，p = {(null2 <= r2).mean():.4f}")

    pct = (rw <= cw).mean() * 100
    verdict = ("正常範圍（歷史上常見）" if pct >= 10 else
               "偏差但歷史上出現過" if pct > 0 and cw >= rw.min() else "超出歷史最差")
    print(f"\n判讀：本期勝率屬於「{verdict}」（≤ 本期的視窗占 {pct:.1f}%）")


if __name__ == "__main__":
    main()
