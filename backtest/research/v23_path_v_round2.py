"""
V23 Path V Round 2 — SIDE regime 縮倉
假設：S2 揭露 V14 在 SIDE regime (|slope| < TH) 每筆只賺 $13-17，風險 MDD $448。
      在 SIDE 區段縮倉可降低最壞 drawdown，同時保留 UP/DN 趨勢段全倉。
新增參數：TH_SIDE (slope 閾值), scale_side (SIDE 區倉位比例)
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, load_data,
                                worst_rolling_dd, monthly_pnl)

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
print(f"Bars={N}")

sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan


def run_vol(th_side, sc, start, end):
    size = np.ones(N)
    side_mask = (np.abs(slope_use) < th_side) & (~np.isnan(slope_use))
    size[side_mask] = sc
    return run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                           size_scale=size)


tr_is_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_b); base_oos = stats_from(tr_oos_b)
print(f"\nBASE  IS ${base_is['pnl']:+.0f}  OOS ${base_oos['pnl']:+.0f}")

print("\n" + "="*100)
print("Grid: TH_SIDE x scale_side (SIDE 縮倉)")
print("="*100)
print(f"{'TH':>7} {'scale':>7} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
ths = [0.010, 0.015, 0.020, 0.025, 0.030]
scales = [0.0, 0.25, 0.50, 0.75]
grid = []
for th in ths:
    for sc in scales:
        ti = run_vol(th, sc, 0, IS_END)
        to = run_vol(th, sc, IS_END, N)
        si = stats_from(ti); so = stats_from(to)
        grid.append((th, sc, ti, to, si, so))
        print(f"{th:>7.3f} {sc:>7.2f} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
              f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

best = max(grid, key=lambda x: x[4]['pnl'])
TH, SC, tr_is, tr_oos, si, so = best
print(f"\nBest IS TH={TH} scale={SC}  IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}")

print(f"\nG7 WF:")
wf = 0
for w in range(6):
    sb=w*(N//6); eb=(w+1)*(N//6) if w<5 else N
    t_o = run_vol(TH, SC, sb, eb)
    t_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po=stats_from(t_o)['pnl']; pb=stats_from(t_b)['pnl']
    if po>pb: wf+=1
    print(f"  W{w+1}: base ${pb:+.0f} over ${po:+.0f} {'+' if po>pb else '-'}")
print(f"G7 {wf}/6")

# G8
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
sma_r = pd.Series(c_r).rolling(200).mean().values
slope_r = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma_r[i]) and sma_r[i-100]>0:
        slope_r[i]=(sma_r[i]-sma_r[i-100])/sma_r[i-100]
slope_r_use=np.roll(slope_r,1); slope_r_use[0]=np.nan
size_r=np.ones(N)
size_r[(np.abs(slope_r_use)<TH) & (~np.isnan(slope_r_use))]=SC
tr_base_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
tr_over_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,size_scale=size_r)
brp=stats_from(tr_base_r)['pnl']; orp=stats_from(tr_over_r)['pnl']
print(f"\nG8: base ${brp:+.0f}  over ${orp:+.0f}  delta ${orp-brp:+.0f}")

# Risk
all_b = tr_is_b+tr_oos_b; all_o=tr_is+tr_oos
bt=stats_from(all_b); ot=stats_from(all_o)
w30b=worst_rolling_dd(all_b,N,30*24,1)[0][1]
w30o=worst_rolling_dd(all_o,N,30*24,1)[0][1]
print(f"\nRisk Map:")
print(f"Trades  {bt['n']} -> {ot['n']}")
print(f"PnL     ${bt['pnl']:+.0f} -> ${ot['pnl']:+.0f}  ({ot['pnl']-bt['pnl']:+.0f})")
print(f"MDD     ${bt['mdd']:.0f} -> ${ot['mdd']:.0f}")
print(f"Sharpe  {bt['sharpe']:.2f} -> {ot['sharpe']:.2f}")
print(f"Worst30 ${w30b:+.0f} -> ${w30o:+.0f}")
print(f"Reversed ${brp:+.0f} -> ${orp:+.0f}")

imp_pnl = ot['pnl']>bt['pnl']
imp_mdd = ot['mdd']<bt['mdd']
imp_w30 = w30o>w30b
imp_g8 = orp>brp
print(f"\nVERDICT: PnL imp {imp_pnl}  MDD imp {imp_mdd}  W30 imp {imp_w30}  G8 imp {imp_g8}  WF {wf}/6")
if imp_pnl and imp_g8 and wf>=4:
    print("-> PROMOTED")
elif (imp_mdd or imp_w30) and imp_g8:
    print("-> CONDITIONAL")
else:
    print("-> REJECTED")
