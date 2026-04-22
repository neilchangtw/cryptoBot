"""
V23 Path R Round 4 — Asymmetric per-side gating (last Path R attempt)
假說: S2 揭露 L 最弱 regime = UP (WR 53%, $+9/t)，S 最弱 regime = SIDE (WR 49%, $+13/t)。
      分別過濾：block L when slope > TH_UP，block S when |slope| < TH_SIDE
新增參數: TH_UP (L gate), TH_SIDE (S gate)
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, load_data,
                                worst_rolling_dd, monthly_pnl)

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

# Slope (SMA200 100-bar pct change)
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan


def run_asym(th_up, th_side, start, end):
    # Block L when slope > th_up (remove UP regime L)
    block_L = (slope_use > th_up) & (~np.isnan(slope_use))
    # Block S when |slope| < th_side (remove SIDE regime S)
    block_S = (np.abs(slope_use) < th_side) & (~np.isnan(slope_use))
    return run_v14_overlay(o,h,l,c,hours,dows,mks,dks,start,end,
                           block_bars_L=block_L, block_bars_S=block_S)


# Baseline
tr_is_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_base); base_oos = stats_from(tr_oos_base)
print(f"\nBASELINE  IS: ${base_is['pnl']:+.0f} ({base_is['n']}t)  OOS: ${base_oos['pnl']:+.0f} ({base_oos['n']}t)")

# 2D grid
print("\n" + "="*100)
print("Grid: TH_UP (L block) x TH_SIDE (S block)")
print("="*100)
print(f"{'TH_UP':>7} {'TH_SIDE':>8} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
ups = [0.02, 0.03, 0.04, 0.05, 0.99]  # 0.99 = no block
sides = [0.005, 0.010, 0.015, 0.020, 0.025, 0.001]  # 0.001 = ~no block
grid = []
for tu in ups:
    for ts in sides:
        ti = run_asym(tu, ts, 0, IS_END)
        to = run_asym(tu, ts, IS_END, N)
        si = stats_from(ti); so = stats_from(to)
        grid.append((tu, ts, ti, to, si, so))
        print(f"{tu:>7.3f} {ts:>8.4f} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
              f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

# Best IS
best = max(grid, key=lambda x: x[4]['pnl'])
TH_U, TH_S, tr_is, tr_oos, si, so = best
print(f"\nBest IS: TH_UP={TH_U}, TH_SIDE={TH_S}  IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}")

is_delta = si['pnl']-base_is['pnl']; oos_delta = so['pnl']-base_oos['pnl']
print(f"IS delta ${is_delta:+.0f}  OOS delta ${oos_delta:+.0f}")

# G7
print(f"\nG7 WF 6-window:")
wf_imp = 0
for w in range(6):
    sb=w*(N//6); eb=(w+1)*(N//6) if w<5 else N
    t_over = run_asym(TH_U, TH_S, sb, eb)
    t_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po=stats_from(t_over)['pnl']; pb=stats_from(t_base)['pnl']
    if po>pb: wf_imp+=1
    print(f"  W{w+1}: base ${pb:+.0f}  overlay ${po:+.0f}  {'+' if po>pb else '-'}")
print(f"  improved {wf_imp}/6")

# G8
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
sma_r = pd.Series(c_r).rolling(200).mean().values
slope_r = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma_r[i]) and sma_r[i-100] > 0:
        slope_r[i] = (sma_r[i] - sma_r[i-100])/sma_r[i-100]
slope_r_use = np.roll(slope_r,1); slope_r_use[0]=np.nan
tr_base_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
block_Lr = (slope_r_use > TH_U) & (~np.isnan(slope_r_use))
block_Sr = (np.abs(slope_r_use) < TH_S) & (~np.isnan(slope_r_use))
tr_over_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,
                               block_bars_L=block_Lr, block_bars_S=block_Sr)
brp = stats_from(tr_base_rev)['pnl']; orp = stats_from(tr_over_rev)['pnl']
print(f"\nG8: base ${brp:+.0f}  overlay ${orp:+.0f}  delta ${orp-brp:+.0f}")

# Risk map
all_base = tr_is_base+tr_oos_base; all_over = tr_is+tr_oos
bt = stats_from(all_base); ot = stats_from(all_over)
print(f"\n{'='*90}")
print("Risk Map")
print(f"{'='*90}")
print(f"                 baseline      overlay   delta")
print(f"Trades           {bt['n']:>9}     {ot['n']:>9}     {ot['n']-bt['n']:+d}")
print(f"PnL              ${bt['pnl']:>+8.0f}   ${ot['pnl']:>+8.0f}   ${ot['pnl']-bt['pnl']:+.0f}")
print(f"MDD              ${bt['mdd']:>8.0f}   ${ot['mdd']:>8.0f}   ${bt['mdd']-ot['mdd']:+.0f}")
print(f"Sharpe           {bt['sharpe']:>9.2f}   {ot['sharpe']:>9.2f}   {ot['sharpe']-bt['sharpe']:+.2f}")
w30b = worst_rolling_dd(all_base, N, 30*24, 1)[0][1]
w30o = worst_rolling_dd(all_over, N, 30*24, 1)[0][1]
print(f"Worst 30d        ${w30b:>+8.0f}   ${w30o:>+8.0f}   ${w30b-w30o:+.0f}")
print(f"Reversed         ${brp:>+8.0f}   ${orp:>+8.0f}   ${orp-brp:+.0f}")

# Verdict
print(f"\n{'='*90}")
print("VERDICT Path R R4")
print(f"{'='*90}")
imp_pnl = ot['pnl'] > bt['pnl']
imp_mdd = ot['mdd'] < bt['mdd']
imp_g8 = orp > brp
print(f"PnL improve: {imp_pnl}  MDD improve: {imp_mdd}  G8 improve: {imp_g8}  WF imp: {wf_imp}/6")
if imp_pnl and imp_g8 and wf_imp >= 4:
    print("-> PROMOTED (main progress)")
elif imp_g8 and (imp_pnl or imp_mdd):
    print("-> CONDITIONAL (partial progress)")
elif imp_g8:
    print("-> SIDE-PROGRESS (G8 only)")
else:
    print("-> REJECTED")
