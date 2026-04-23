"""
V25 R1 — Regime Stratification of V14+R Baseline Trades

目的：
  當前 V14+R 所有交易用同一組出場參數（L: TP 3.5%/MFE act 1%/tr 0.8%/MH 6/CMH bar2 -1%→5；
  S: TP 2%/MH 10），此腳本統計交易在 4 個 regime 下的分佈與績效，
  找出哪個 regime 有改善空間。

方法：
  1. 用 v24_engine 跑 V14+R 完整回測
  2. 對每筆 trade 的 entry_bar 取 sma_slope 分類 regime
  3. 按 (side, regime) cell 統計：n, WR, PnL, PF, avg_bars, exit_reason 分佈
  4. 比較 IS vs OOS 保持一致性（避免單點過擬合）

Regime 定義（與 dashboard 一致）：
  UP       : slope > +4.5%   (L blocked by R gate)
  MILD_UP  : +1.0% < slope <= +4.5%
  SIDE     : |slope| < 1.0%  (S blocked by R gate)
  DOWN     : slope < -1.0%
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, load_data

TH_UP = 0.045
TH_SIDE = 0.010

def regime_label(s):
    if np.isnan(s): return "NA"
    if s > TH_UP: return "UP"
    if abs(s) < TH_SIDE: return "SIDE"
    if s < -TH_SIDE: return "DOWN"
    return "MILD_UP"  # TH_SIDE <= slope <= TH_UP

def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    N = len(o); IS_END = N // 2

    sma200 = pd.Series(c).rolling(200).mean().values
    slope = np.full(N, np.nan)
    for i in range(300, N):
        if not np.isnan(sma200[i]) and sma200[i-100] > 0:
            slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
    slope_use = np.roll(slope, 1); slope_use[0] = np.nan
    block_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
    block_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

    # Baseline V14+R run full 2Y
    trades = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 300, N,
                             block_bars_L=block_L, block_bars_S=block_S)
    # Annotate each trade with entry regime
    for t in trades:
        t['regime'] = regime_label(slope_use[t['entry_bar']])
        t['is'] = t['entry_bar'] < IS_END

    # Print overall
    print("=" * 110)
    print("V25 R1 — V14+R Baseline Regime Stratification")
    print("=" * 110)
    tot_pnl = sum(t['pnl'] for t in trades)
    tot_wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100
    print(f"\n總計：{len(trades)} trades, PnL ${tot_pnl:.0f}, WR {tot_wr:.1f}%")

    # By side x regime
    print("\n--- 交易分佈 (Side × Regime) ---")
    for side in ['L', 'S']:
        print(f"\n### {side} 策略（regime gate: "
              f"{'block UP' if side=='L' else 'block SIDE'}）")
        hdr = f"{'Regime':<10} {'N':>4} {'PnL $':>8} {'WR%':>6} {'PF':>6} {'AvgHold':>8} {'AvgPnL':>8} | 出場分佈"
        print(hdr); print("-" * len(hdr))
        side_trs = [t for t in trades if t['side'] == side]
        for rg in ['UP', 'MILD_UP', 'DOWN', 'SIDE', 'NA']:
            cell = [t for t in side_trs if t['regime'] == rg]
            if not cell: continue
            n = len(cell)
            pnl = sum(t['pnl'] for t in cell)
            wins = [t for t in cell if t['pnl'] > 0]
            losses = [t for t in cell if t['pnl'] <= 0]
            wr = len(wins) / n * 100
            pf = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses else 99
            avg_hold = np.mean([t['bars_held'] for t in cell])
            avg_pnl = pnl / n
            rsn_cnt = {}
            for t in cell: rsn_cnt[t['reason']] = rsn_cnt.get(t['reason'], 0) + 1
            rsn_str = ' '.join(f"{k}:{v}" for k, v in sorted(rsn_cnt.items(), key=lambda x: -x[1]))
            print(f"{rg:<10} {n:>4} {pnl:>8.0f} {wr:>6.1f} {pf:>6.2f} {avg_hold:>8.1f} {avg_pnl:>8.1f} | {rsn_str}")

    # IS/OOS consistency check
    print("\n--- IS vs OOS consistency (Side × Regime) ---")
    for side in ['L', 'S']:
        print(f"\n### {side}")
        hdr = f"{'Regime':<10} {'IS_n':>5} {'IS_pnl':>7} {'IS_wr':>6} | {'OOS_n':>6} {'OOS_pnl':>8} {'OOS_wr':>6}"
        print(hdr); print("-" * len(hdr))
        for rg in ['UP', 'MILD_UP', 'DOWN', 'SIDE']:
            is_cell = [t for t in trades if t['side']==side and t['regime']==rg and t['is']]
            oos_cell = [t for t in trades if t['side']==side and t['regime']==rg and not t['is']]
            if not is_cell and not oos_cell: continue
            in_n = len(is_cell); oos_n = len(oos_cell)
            in_pnl = sum(t['pnl'] for t in is_cell); oos_pnl = sum(t['pnl'] for t in oos_cell)
            in_wr = sum(1 for t in is_cell if t['pnl']>0)/in_n*100 if in_n else 0
            oos_wr = sum(1 for t in oos_cell if t['pnl']>0)/oos_n*100 if oos_n else 0
            print(f"{rg:<10} {in_n:>5} {in_pnl:>7.0f} {in_wr:>6.1f} | {oos_n:>6} {oos_pnl:>8.0f} {oos_wr:>6.1f}")

    # Bars_held distribution by regime (for L, to inform MH tuning)
    print("\n--- L bars_held P25/P50/P75 by regime（贏家 vs 輸家）---")
    for rg in ['MILD_UP', 'DOWN', 'SIDE']:
        cell = [t for t in trades if t['side']=='L' and t['regime']==rg]
        if not cell: continue
        wins = [t['bars_held'] for t in cell if t['pnl']>0]
        losses = [t['bars_held'] for t in cell if t['pnl']<=0]
        def pct(arr, p):
            if not arr: return 0
            return np.percentile(arr, p)
        print(f"{rg:<10} WIN  n={len(wins):>3} P25={pct(wins,25):.1f} P50={pct(wins,50):.1f} P75={pct(wins,75):.1f}")
        print(f"{rg:<10} LOSS n={len(losses):>3} P25={pct(losses,25):.1f} P50={pct(losses,50):.1f} P75={pct(losses,75):.1f}")

    print("\n--- S bars_held P25/P50/P75 by regime ---")
    for rg in ['UP', 'MILD_UP', 'DOWN']:
        cell = [t for t in trades if t['side']=='S' and t['regime']==rg]
        if not cell: continue
        wins = [t['bars_held'] for t in cell if t['pnl']>0]
        losses = [t['bars_held'] for t in cell if t['pnl']<=0]
        def pct(arr, p):
            if not arr: return 0
            return np.percentile(arr, p)
        print(f"{rg:<10} WIN  n={len(wins):>3} P25={pct(wins,25):.1f} P50={pct(wins,50):.1f} P75={pct(wins,75):.1f}")
        print(f"{rg:<10} LOSS n={len(losses):>3} P25={pct(losses,25):.1f} P50={pct(losses,50):.1f} P75={pct(losses,75):.1f}")

if __name__ == "__main__":
    main()
