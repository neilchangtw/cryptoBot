"""
V30 R2 — 最佳桶顯著性檢驗（多重比較校正 + IS/OOS 一致性）

R1 發現 L × fr_last Q1（負 funding）WR 80.8% avg $54.7 是 30 個桶中最好的。
問題：30 桶挑最好的一桶，本來就會有一桶很亮。
檢驗：permutation 下「30 桶中最佳桶 avg PnL / WR」的分佈，看實際值排第幾；
      另看 L-Q1 效果 IS（前半）/ OOS（後半）是否同向。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v25_engine import load_data, build_slope, build_r_gate, run_v25
from v30_r1_funding_diag import build_funding_features, V25D


def buckets_of(vals, pnls, n_bins=5):
    qs = np.quantile(vals, np.linspace(0, 1, n_bins + 1)[1:-1])
    out = []
    for k in range(n_bins):
        lo = -np.inf if k == 0 else qs[k - 1]
        hi = np.inf if k == n_bins - 1 else qs[k]
        m = (vals > lo) & (vals <= hi)
        if m.sum() >= 5:
            out.append(pnls[m])
    return out


def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    dt = pd.to_datetime(df['datetime'])
    slope_use = build_slope(c)
    blk_L, blk_S = build_r_gate(slope_use)
    fr_last, fr_ma21, fr_pct = build_funding_features(dt)
    feats = {'fr_last': fr_last, 'fr_ma21': fr_ma21, 'fr_pct': fr_pct}

    trs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, len(o),
                  block_bars_L=blk_L, block_bars_S=blk_S, **V25D)
    for t in trs:
        for k, arr in feats.items():
            t[k] = arr[t['entry_bar']]

    # 實際 30 桶的最佳 avg / 最佳 WR
    best_avg, best_wr = -np.inf, -np.inf
    for side in ('L', 'S'):
        for fname in feats:
            sub = [t for t in trs if t['side'] == side and not np.isnan(t[fname])]
            vals = np.array([t[fname] for t in sub])
            pnls = np.array([t['pnl'] for t in sub])
            for b in buckets_of(vals, pnls):
                best_avg = max(best_avg, b.mean())
                best_wr = max(best_wr, (b > 0).mean() * 100)
    print(f"實際 30 桶最佳: avg=${best_avg:.1f} / WR={best_wr:.1f}%")

    # permutation: shuffle 每側 pnl 相對特徵 2000 次，重算「全體最佳桶」
    rng = np.random.default_rng(7)
    cnt_avg = cnt_wr = 0
    N_PERM = 2000
    side_data = {}
    for side in ('L', 'S'):
        side_data[side] = {}
        for fname in feats:
            sub = [t for t in trs if t['side'] == side and not np.isnan(t[fname])]
            side_data[side][fname] = (
                np.array([t[fname] for t in sub]),
                np.array([t['pnl'] for t in sub]))
    for _ in range(N_PERM):
        sim_avg, sim_wr = -np.inf, -np.inf
        for side in ('L', 'S'):
            # 同側共用一次 shuffle（特徵間高度相關，保守做法是每特徵獨立 shuffle pnl）
            for fname in feats:
                vals, pnls = side_data[side][fname]
                perm = rng.permutation(pnls)
                for b in buckets_of(vals, perm):
                    sim_avg = max(sim_avg, b.mean())
                    sim_wr = max(sim_wr, (b > 0).mean() * 100)
        if sim_avg >= best_avg: cnt_avg += 1
        if sim_wr >= best_wr: cnt_wr += 1
    print(f"permutation(最佳桶): p_avg={cnt_avg/N_PERM:.3f}, p_wr={cnt_wr/N_PERM:.3f}")

    # L × fr_last<0 的 IS/OOS 一致性
    IS_END = len(o) // 2
    for label, lo, hi in [('fr_last<0 (Q1近似)', -np.inf, 0.0),
                          ('fr_last>=0', 0.0, np.inf)]:
        for half, cond in [('IS ', lambda t: t['entry_bar'] < IS_END),
                           ('OOS', lambda t: t['entry_bar'] >= IS_END)]:
            sub = [t for t in trs if t['side'] == 'L' and not np.isnan(t['fr_last'])
                   and lo < t['fr_last'] <= hi and cond(t)]
            if sub:
                p = np.array([t['pnl'] for t in sub])
                print(f"  L {label:20s} {half}: n={len(sub):>3} avg=${p.mean():>7.1f} "
                      f"WR={(p>0).mean()*100:5.1f}%")


if __name__ == '__main__':
    main()
