"""
V30 R1 — Funding rate 條件診斷（V14+R+V25-D 基準交易 × 進場時 funding 特徵）

問題：進場當下的 funding rate（市場多空擁擠度）能否區分 V14 交易的好壞？
      正 funding = 多頭付費（多頭擁擠）；負 funding = 空頭付費（空頭擁擠）。
      假說：L 在多頭極度擁擠時進場勝率較差；S 在空頭極度擁擠時進場勝率較差（squeeze 風險）。

方法：baseline 引擎跑全期間 → 每筆 trade 貼 3 個進場時特徵（嚴格用 bar close 前已知的 funding）：
      fr_last  = 最近一次結算費率
      fr_ma21  = 近 7 天（21 次結算）平均
      fr_pct   = fr_last 在近 90 天的 rolling percentile
      → 每側 × 每特徵 五分位分桶 PnL/WR + 極端桶 permutation test
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v25_engine import (load_data, build_slope, build_r_gate, run_v25, stats_from)

# V25-D 線上參數
V25D = dict(
    S_MH_BY_RG={'UP': 8},
    L_TP_BY_RG={'DOWN': 0.040},
    L_MH_BY_RG={'MILD_UP': 7},
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')


def build_funding_features(dt_utc8):
    """回傳與 1h bars 對齊的 fr_last / fr_ma21 / fr_pct（bar close 時已知）"""
    fu = pd.read_csv(os.path.join(DATA_DIR, 'ETHUSDT_funding.csv'))
    f_ts = fu['funding_time_ms'].values  # UTC ms
    f_rate = fu['rate'].values

    # bar close (UTC ms) = open(UTC+8) - 8h + 1h
    close_ms = ((dt_utc8 - pd.Timedelta(hours=8) + pd.Timedelta(hours=1))
                .astype('datetime64[ms]').astype('int64')).values

    n = len(close_ms)
    fr_last = np.full(n, np.nan)
    fr_ma21 = np.full(n, np.nan)
    fr_pct = np.full(n, np.nan)
    j = -1  # index of last funding event with ts <= close
    for i in range(n):
        while j + 1 < len(f_ts) and f_ts[j + 1] <= close_ms[i]:
            j += 1
        if j < 0:
            continue
        fr_last[i] = f_rate[j]
        if j >= 20:
            fr_ma21[i] = f_rate[j - 20:j + 1].mean()
        if j >= 269:
            win = f_rate[j - 269:j + 1]
            fr_pct[i] = (win <= f_rate[j]).sum() / len(win) * 100
    return fr_last, fr_ma21, fr_pct


def bucket_report(trs, feat_name, side):
    sub = [t for t in trs if t['side'] == side and not np.isnan(t[feat_name])]
    if len(sub) < 25:
        print(f"  [{side}] {feat_name}: n={len(sub)} 太少，跳過")
        return
    vals = np.array([t[feat_name] for t in sub])
    pnls = np.array([t['pnl'] for t in sub])
    qs = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
    print(f"  [{side}] {feat_name} (n={len(sub)}), 五分位切點: "
          + " / ".join(f"{q:.4g}" for q in qs))
    for k in range(5):
        lo = -np.inf if k == 0 else qs[k - 1]
        hi = np.inf if k == 4 else qs[k]
        m = (vals > lo) & (vals <= hi) if k > 0 else (vals <= hi)
        if m.sum() == 0:
            continue
        p = pnls[m]
        wr = (p > 0).mean() * 100
        print(f"    Q{k+1}: n={m.sum():>3} PnL=${p.sum():>8.0f} avg=${p.mean():>7.1f} WR={wr:5.1f}%")
    # permutation test: 最壞 quintile avg PnL vs 隨機
    worst_avg = min(pnls[(vals > (-np.inf if k == 0 else qs[k-1])) &
                         (vals <= (np.inf if k == 4 else qs[k]))].mean()
                    for k in range(5)
                    if ((vals > (-np.inf if k == 0 else qs[k-1])) &
                        (vals <= (np.inf if k == 4 else qs[k]))).sum() > 0)
    rng = np.random.default_rng(42)
    cnt = 0
    n_q = max(1, len(sub) // 5)
    for _ in range(2000):
        perm = rng.permutation(pnls)
        sim_worst = min(perm[k * n_q:(k + 1) * n_q].mean() for k in range(5))
        if sim_worst <= worst_avg:
            cnt += 1
    print(f"    最壞桶 avg=${worst_avg:.1f}, permutation p={cnt/2000:.3f}")


def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    dt = pd.to_datetime(df['datetime'])
    slope_use = build_slope(c)
    blk_L, blk_S = build_r_gate(slope_use)

    fr_last, fr_ma21, fr_pct = build_funding_features(dt)
    print(f"funding 特徵覆蓋率: fr_last {np.mean(~np.isnan(fr_last))*100:.1f}% / "
          f"fr_pct {np.mean(~np.isnan(fr_pct))*100:.1f}%")

    trs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, len(o),
                  block_bars_L=blk_L, block_bars_S=blk_S, **V25D)
    st = stats_from(trs)
    print(f"\nBaseline V14+R+V25-D: n={st['n']} PnL=${st['pnl']:.0f} "
          f"WR={st['wr']:.1f}% MDD=${st['mdd']:.0f}\n")

    for t in trs:
        eb = t['entry_bar']
        t['fr_last'] = fr_last[eb]
        t['fr_ma21'] = fr_ma21[eb]
        t['fr_pct'] = fr_pct[eb]

    for side in ('L', 'S'):
        print(f"===== {side} side =====")
        for feat in ('fr_last', 'fr_ma21', 'fr_pct'):
            bucket_report(trs, feat, side)
        print()


if __name__ == '__main__':
    main()
