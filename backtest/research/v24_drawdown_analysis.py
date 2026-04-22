"""
V14+R 回測虧損分析（20x / 10x / 5x 三檔）
- 單筆最大虧損
- 最大連續虧損筆數
- 累積權益最大回撤（含日期）
- 最壞單月
- 最壞 7/30/90 天窗口
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, load_data, worst_rolling_dd, monthly_pnl

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o)
dt = pd.to_datetime(df['datetime'])

TH_UP = 0.045; TH_SIDE = 0.010
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan
block_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
block_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

CONFIGS = [
    ("V14+R @ 20x", 4000, 4.0),
    ("V14+R @ 10x", 2000, 2.0),
    ("V14+R @ 5x",  1000, 1.0),
]

for lbl, nt, fe in CONFIGS:
    print("="*70)
    print(f"  {lbl}  (notional ${nt}, fee ${fe})")
    print("="*70)
    trades = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, N,
                              block_bars_L=block_L, block_bars_S=block_S,
                              notional_L=nt, notional_S=nt, fee_L=fe, fee_S=fe)
    trades.sort(key=lambda t: t['exit_bar'])

    pnls = [t['pnl'] for t in trades]
    losses = [p for p in pnls if p < 0]

    # 1. Single biggest loss
    worst_trade = min(trades, key=lambda t: t['pnl'])
    worst_trade_entry_dt = dt.iloc[worst_trade['entry_bar']]
    worst_trade_exit_dt = dt.iloc[worst_trade['exit_bar']]

    # 2. Max consecutive losses
    max_consec = 0; cur = 0
    consec_start = None; best_start = None; best_end = None
    for i, t in enumerate(trades):
        if t['pnl'] < 0:
            if cur == 0: consec_start = i
            cur += 1
            if cur > max_consec:
                max_consec = cur
                best_start = consec_start; best_end = i
        else:
            cur = 0
    if best_start is not None:
        consec_loss_sum = sum(trades[i]['pnl'] for i in range(best_start, best_end+1))
        consec_start_dt = dt.iloc[trades[best_start]['entry_bar']]
        consec_end_dt = dt.iloc[trades[best_end]['exit_bar']]

    # 3. Equity curve MDD
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    mdd = -dd.min()
    mdd_end_idx = int(np.argmin(dd))
    # find peak before mdd_end
    peak_val = peak[mdd_end_idx]
    peak_idx = int(np.where(eq[:mdd_end_idx+1] == peak_val)[0][0]) if mdd_end_idx > 0 else 0
    mdd_start_dt = dt.iloc[trades[peak_idx]['exit_bar']]
    mdd_end_dt = dt.iloc[trades[mdd_end_idx]['exit_bar']]

    # 4. Monthly
    m_pnl = monthly_pnl(trades)
    worst_month_key = min(m_pnl, key=m_pnl.get)
    worst_month_val = m_pnl[worst_month_key]
    pos_mo = sum(1 for v in m_pnl.values() if v > 0)
    tot_mo = len(m_pnl)

    # 5. Rolling 7/30/90
    w7  = worst_rolling_dd(trades, N, 24*7,  top_n=1)[0][1]
    w30 = worst_rolling_dd(trades, N, 24*30, top_n=1)[0][1]
    w90 = worst_rolling_dd(trades, N, 24*90, top_n=1)[0][1]

    print(f"\n【單筆最大虧損】")
    print(f"  ${worst_trade['pnl']:+.2f}  ({worst_trade['side']}, {worst_trade['reason']})")
    print(f"  進場 {worst_trade_entry_dt} → 出場 {worst_trade_exit_dt}")

    print(f"\n【最大連續虧損】")
    print(f"  連續 {max_consec} 筆，累計 ${consec_loss_sum:+.2f}")
    print(f"  期間 {consec_start_dt.date()} → {consec_end_dt.date()}")

    print(f"\n【累積權益最大回撤 MDD】")
    print(f"  ${mdd:.2f}（{mdd/nt*100:.1f}% of notional）")
    print(f"  從 {mdd_start_dt.date()} (peak ${peak_val:+.0f}) 跌到 {mdd_end_dt.date()} (trough ${eq[mdd_end_idx]:+.0f})")

    print(f"\n【最壞單月】")
    y = worst_month_key // 100; m = worst_month_key % 100
    print(f"  {y}-{m:02d}  ${worst_month_val:+.0f}")
    print(f"  正收益月: {pos_mo}/{tot_mo}")

    print(f"\n【滾動窗口最大回撤】")
    print(f"  最壞 7 天:  ${w7:+.0f}")
    print(f"  最壞 30 天: ${w30:+.0f}")
    print(f"  最壞 90 天: ${w90:+.0f}")

    print(f"\n【交易總覽】")
    print(f"  總筆數 {len(trades)} / 虧損筆數 {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
    print(f"  平均虧損 ${np.mean(losses):+.2f} / 虧損中位數 ${np.median(losses):+.2f}")
    print()
