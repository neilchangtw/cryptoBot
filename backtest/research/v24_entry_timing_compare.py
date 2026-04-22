"""
V14 vs V14+R 進場時機對比
- 總進場筆數差異
- 每月進場分佈
- 進場小時/週日分佈（session filter 不變，應該一樣）
- V14 有但 V14+R 剔除的 trade（regime 過濾掉的）
- 平均持倉時間
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, load_data

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

# V14 baseline
tr_v14 = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, N)
# V14+R
tr_r = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, N,
                        block_bars_L=block_L, block_bars_S=block_S)

print("="*75)
print("V14 vs V14+R 進場時機對比（2Y, 17633 bars）")
print("="*75)

def side_count(trades, side):
    return sum(1 for t in trades if t['side'] == side)

print(f"\n【總進場筆數】")
print(f"  V14     L {side_count(tr_v14, 'L'):3d} / S {side_count(tr_v14, 'S'):3d} / 合計 {len(tr_v14):3d}")
print(f"  V14+R   L {side_count(tr_r, 'L'):3d} / S {side_count(tr_r, 'S'):3d} / 合計 {len(tr_r):3d}")
print(f"  減少    L {side_count(tr_v14,'L')-side_count(tr_r,'L')} / S {side_count(tr_v14,'S')-side_count(tr_r,'S')} / 合計 {len(tr_v14)-len(tr_r)}")

# 找出 V14 有但 V14+R 沒的 trade
v14_set = set((t['side'], t['entry_bar']) for t in tr_v14)
r_set = set((t['side'], t['entry_bar']) for t in tr_r)
removed_keys = v14_set - r_set
removed_trades = [t for t in tr_v14 if (t['side'], t['entry_bar']) in removed_keys]

print(f"\n【V14 有、V14+R 剔除的 trade】共 {len(removed_trades)} 筆")
if removed_trades:
    rm_pnl = sum(t['pnl'] for t in removed_trades)
    rm_L = [t for t in removed_trades if t['side']=='L']
    rm_S = [t for t in removed_trades if t['side']=='S']
    rm_L_pnl = sum(t['pnl'] for t in rm_L)
    rm_S_pnl = sum(t['pnl'] for t in rm_S)
    print(f"  L 剔除 {len(rm_L)} 筆 / 原本會賺 ${rm_L_pnl:+.0f}")
    print(f"  S 剔除 {len(rm_S)} 筆 / 原本會賺 ${rm_S_pnl:+.0f}")
    print(f"  合計剔除 PnL ${rm_pnl:+.0f} ← R gate 認為這些是壞 regime 的 trade")
    print(f"  （負值代表剔除的是虧錢 trade，正值代表剔除的是賺錢 trade）")
    print(f"  剔除 WR: L {sum(1 for t in rm_L if t['pnl']>0)}/{len(rm_L)} = {sum(1 for t in rm_L if t['pnl']>0)/max(len(rm_L),1)*100:.0f}%")
    print(f"          S {sum(1 for t in rm_S if t['pnl']>0)}/{len(rm_S)} = {sum(1 for t in rm_S if t['pnl']>0)/max(len(rm_S),1)*100:.0f}%")

# 進場小時分佈
print(f"\n【進場小時分佈（UTC+8）】")
print(f"  {'Hour':<6}{'V14':>6}{'V14+R':>8}{'差異':>8}")
for hr in range(24):
    v14_n = sum(1 for t in tr_v14 if hours[t['entry_bar']] == hr)
    r_n = sum(1 for t in tr_r if hours[t['entry_bar']] == hr)
    # Convert UTC to UTC+8
    hr8 = (hr + 8) % 24
    if v14_n > 0 or r_n > 0:
        marker = "  ← block" if hr8 in {0,1,2,12} else ""
        print(f"  {hr8:02d}:00  {v14_n:>4}  {r_n:>6}  {r_n-v14_n:>+6}{marker}")

# 平均持倉時間
v14_hold = np.mean([t['bars_held'] for t in tr_v14])
r_hold = np.mean([t['bars_held'] for t in tr_r])
print(f"\n【平均持倉小時】")
print(f"  V14   {v14_hold:.2f} bar")
print(f"  V14+R {r_hold:.2f} bar  ← 應該差不多，出場邏輯不變")

# 每月進場筆數（抽樣）
print(f"\n【每月進場筆數對比（抽樣）】")
v14_by_m = {}; r_by_m = {}
for t in tr_v14:
    k = t['entry_mk']; v14_by_m[k] = v14_by_m.get(k,0)+1
for t in tr_r:
    k = t['entry_mk']; r_by_m[k] = r_by_m.get(k,0)+1
months = sorted(set(list(v14_by_m.keys()) + list(r_by_m.keys())))
print(f"  {'月份':<10}{'V14':>6}{'V14+R':>8}{'差異':>8}  狀況")
for mk in months:
    v14_n = v14_by_m.get(mk, 0); r_n = r_by_m.get(mk, 0)
    diff = r_n - v14_n
    note = ""
    if diff < 0:
        note = f"R 剔除 {-diff} 筆"
    y = mk // 100; m = mk % 100
    print(f"  {y}-{m:02d}    {v14_n:>4}  {r_n:>6}  {diff:>+6}   {note}")
