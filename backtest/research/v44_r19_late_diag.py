"""V44 第 19 輪 A：大行情為什麼錯過、晚進場是否比較差（純診斷，不是新規則）。

大行情：24 根 |報酬| ≥ 8%（另列 ≥ 12%），依幅度由大到小取不重疊事件。
晚進場：M = 方向 × (close[i-1] / close[i-1-N] − 1) × 100，除以 ATR14%（N=6、12、24），依全期基準交易五分位分組。
固定 200U、realistic、0bp、每筆 +$5，現行引擎。P1 = 進場 < 2024-07-01，P2 = 之後。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r19_late_diag.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-07-01")
BARS = 24
NS = (6, 12, 24)


def find_events(c, sb, move):
    r = np.full(len(c), np.nan)
    r[:-BARS] = c[BARS:] / c[:-BARS] - 1
    order = [i for i in np.argsort(-np.abs(np.nan_to_num(r))) if i >= sb and abs(r[i]) >= move]
    used = np.zeros(len(c), bool)
    out = []
    for i in order:
        if not used[max(0, i - BARS):i + BARS + 1].any():
            used[i:i + BARS + 1] = True
            out.append((int(i), 1 if r[i] > 0 else -1, float(r[i])))
    return sorted(out)


def miss_reason(i, d, ind, engine, f):
    """事件窗內第一次同向突破那根為何沒進場；窗內沒有突破就是「沒有突破」。"""
    brk = np.asarray(ind["brk_up"] if d == 1 else ind["brk_dn"], bool)
    hits = np.flatnonzero(brk[i:i + BARS]) + i
    if not len(hits):
        return "沒有突破 15 根高低"
    j = int(hits[0])
    side = "L" if d == 1 else "S"
    if side == "L":
        blk_h, blk_d, rb, p, th, cd = engine.L_BLK_H, engine.L_BLK_D, ind.get("regime_block_l"), ind["pctile_L"][j], engine.L_GK_TH, engine.L_CD
    else:
        blk_h, blk_d, rb, p, th, cd = engine.S_BLK_H, engine.S_BLK_D, ind.get("regime_block_s"), ind["pctile_S"][j], engine.S_GK_TH, engine.S_CD
    g = f[f.side == side]
    if ((g.entry_bar < j) & (g.exit_bar >= j)).any():
        return "已有持倉"
    if np.isnan(p) or p >= th:
        return "GK 不夠壓縮"
    if int(ind["hours"][j]) in blk_h or int(ind["dows"][j]) in blk_d:
        return "時段封鎖"
    if rb is not None and bool(rb[j]):
        return "R gate"
    last = g.exit_bar[g.exit_bar <= j]
    if len(last) and j - int(last.max()) < cd:
        return "冷卻"
    return "其他（熔斷／月上限）"


def event_table(f, ind, engine, c, h, l, t, sb, move):
    rows = []
    for i, d, r in find_events(c, sb, move):
        side = "L" if d == 1 else "S"
        end = i + BARS
        extreme = h[i:end + 1].max() if d == 1 else l[i:end + 1].min()
        same = f[(f.entry_bar >= i - BARS) & (f.entry_bar <= end) & (f.side == side)]
        wrong = f[(f.entry_bar >= i) & (f.entry_bar <= end) & (f.side != side)]
        pos = (same.entry_price.iloc[0] - c[i]) / (extreme - c[i]) * 100 if len(same) else np.nan
        rows.append(dict(dt=t.iloc[i], r=r * 100, d=d, same=len(same), pnl=same.pnl_usd.sum(), wrong=len(wrong), wrong_pnl=wrong.pnl_usd.sum(),
                         pos=pos, reason="" if len(same) else miss_reason(i, d, ind, engine, f)))
    return pd.DataFrame(rows)


def adverse_first(c, h, l, i, s, bars=10):
    e = c[i]
    for j in range(i + 1, min(i + bars + 1, len(c))):
        adv = (h[j] / e - 1) if s == -1 else (1 - l[j] / e)
        fav = (1 - l[j] / e) if s == -1 else (h[j] / e - 1)
        if adv >= 0.01:
            return True
        if fav >= 0.01:
            return False
    return False


def main() -> None:
    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    c, h, l = (candles[k].to_numpy(float) for k in ("close", "high", "low"))
    t = pd.to_datetime(dt)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；基準 {len(f)} 筆")

    print("\n[1] 大行情事件（同向進場範圍：事件前 24 根到事件結束）")
    for move in (0.08, 0.12):
        e = event_table(f, ind, engine, c, h, l, t, sb, move)
        print(f"\n  |r| ≥ {move * 100:.0f}%：{len(e)} 個；各年 " + " ".join(f"{y}:{n}" for y, n in e.dt.dt.year.value_counts().sort_index().items()))
        for lab, g in (("P1", e[e.dt < P_END]), ("P2", e[e.dt >= P_END]), ("近期", e[e.dt >= RECENT])):
            hit = g[g.same > 0]
            print(f"   {lab}: {len(g)} 個，有同向單 {len(hit)} 個（{len(hit) / max(len(g), 1) * 100:.0f}%）${hit.pnl.sum():,.0f}，事件內反向單 {g.wrong.sum()} 筆 ${g.wrong_pnl.sum():,.0f}")
            if len(hit):
                bands = (("<25%", hit.pos < 25), ("25~60%", (hit.pos >= 25) & (hit.pos < 60)), ("≥60%", hit.pos >= 60))
                print("      進場位置中位 " + f"{hit.pos.median():.0f}%；" + "；".join(f"{b} {m.sum()} 個 ${hit.pnl[m].sum():,.0f}" for b, m in bands))
            miss = g[g.same == 0]
            if len(miss):
                print("      沒進場原因：" + "、".join(f"{k} {v}" for k, v in miss.reason.value_counts().items()))
        for x in e[e.dt >= RECENT].itertuples():
            state = f"同向 {x.same} 筆 位置 {x.pos:.0f}% ${x.pnl:,.0f}" if x.same else f"沒進場：{x.reason}"
            print(f"      {x.dt:%m-%d %H:%M} {x.r:+.1f}% {state}" + (f"｜反向 {x.wrong} 筆 ${x.wrong_pnl:,.0f}" if x.wrong else ""))

    print("\n[2] 晚進場：M/ATR 五分位（切點用全期基準交易）")
    i = f.entry_bar.astype(int).to_numpy()
    s = np.where(f.side == "L", 1, -1)
    pc = np.roll(c, 1); pc[0] = np.nan
    atr = pd.Series(np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))).rolling(14).mean().to_numpy() / c * 100
    f["adv1"] = [adverse_first(c, h, l, a, b) for a, b in zip(i, s)]
    for n in NS:
        f[f"m{n}"] = s * (c[i - 1] / c[i - 1 - n] - 1) * 100 / atr[i - 1]
        cuts = np.quantile(f[f"m{n}"], [0.2, 0.4, 0.6, 0.8])
        q = np.digitize(f[f"m{n}"], cuts)
        print(f"\n  N={n} 切點 " + " ".join(f"{x:.2f}" for x in cuts))
        means = {}
        for pn, sel in (("P1", f.entry_dt < P_END), ("P2", f.entry_dt >= P_END)):
            means[pn] = []
            for k in range(5):
                g = f[(q == k) & sel]
                means[pn].append(g.pnl_usd.mean())
                print(f"   {pn} Q{k + 1} {len(g):3d}筆 WR {(g.pnl_usd > 0).mean() * 100:3.0f}% ${g.pnl_usd.sum():6,.0f} 均${g.pnl_usd.mean():6.1f} MH {(g.exit_reason == 'MH').mean() * 100:3.0f}% 先逆向1% {g.adv1.mean() * 100:3.0f}%")
        corr = {pn: pd.Series(v).rank().corr(pd.Series(range(5), dtype=float)) for pn, v in means.items()}
        print(f"   均 PnL 與分位的 Spearman：P1 {corr['P1']:+.2f}、P2 {corr['P2']:+.2f}；Q5 均 PnL − Q1：P1 {means['P1'][4] - means['P1'][0]:+.1f}、P2 {means['P2'][4] - means['P2'][0]:+.1f}")

    print("\n[3] 2026-07 起每筆（N=12）")
    for x in f[f.entry_dt >= RECENT].itertuples():
        print(f"  {x.entry_dt:%m-%d %H:%M} {x.side} {x.entry_regime:8s} M/ATR {x.m12:5.2f} {x.exit_reason:3s} ${x.pnl_usd:6.1f} 先逆向1% {'是' if x.adv1 else '否'}")
    print(f"\n  P1 基準交易 M/ATR 第 70／80／90 百分位（N=12）：" + " ".join(f"{np.percentile(f[f.entry_dt < P_END].m12, p):.3f}" for p in (70, 80, 90)))


if __name__ == "__main__":
    main()
