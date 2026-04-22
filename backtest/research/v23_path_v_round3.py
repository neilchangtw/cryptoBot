"""
V23 Path V Round 3 — Inverse-ATR sizing (risk targeting)
假設：V14 最壞 30d $-570 出現在 2024-04-18 高波動段 (ATR 峰)。
      按 ATR% 反比縮放倉位可將該段風險動態壓低，不犧牲低波段的全倉收益。
公式：scale = clip(TARGET_ATR / ATR%, FLOOR, CAP)
新增參數：TARGET_ATR (目標波動), FLOOR (最小倉位)  → CAP 固定 1.0
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


def true_range(h, l, c):
    pc = np.roll(c, 1); pc[0] = c[0]
    return np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))


tr = true_range(h, l, c)
atr = pd.Series(tr).rolling(14).mean().values
atr_pct = atr / c
atr_pct_use = np.roll(atr_pct, 1); atr_pct_use[0] = np.nan


def run_iv(target, floor, start, end):
    size = np.full(N, 1.0)
    valid = ~np.isnan(atr_pct_use) & (atr_pct_use > 0)
    raw = np.where(valid, target / atr_pct_use, 1.0)
    size = np.clip(raw, floor, 1.0)
    size[~valid] = 1.0
    return run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                           size_scale=size)


tr_is_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_b); base_oos = stats_from(tr_oos_b)

print(f"\nATR% stats: median {np.nanmedian(atr_pct)*100:.3f}%  p25 {np.nanpercentile(atr_pct,25)*100:.3f}%  p75 {np.nanpercentile(atr_pct,75)*100:.3f}%")
print(f"BASE  IS ${base_is['pnl']:+.0f}  OOS ${base_oos['pnl']:+.0f}")

print("\n" + "="*100)
print("Grid: TARGET_ATR x FLOOR")
print("="*100)
print(f"{'TARGET':>8} {'FLOOR':>6} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
targets = [0.005, 0.007, 0.010, 0.015]
floors = [0.25, 0.50, 0.75]
grid = []
for tg in targets:
    for fl in floors:
        ti = run_iv(tg, fl, 0, IS_END)
        to = run_iv(tg, fl, IS_END, N)
        si = stats_from(ti); so = stats_from(to)
        grid.append((tg, fl, ti, to, si, so))
        print(f"{tg:>8.4f} {fl:>6.2f} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
              f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

best = max(grid, key=lambda x: x[4]['pnl'])
TG, FL, tr_is, tr_oos, si, so = best
print(f"\nBest IS TARGET={TG} FLOOR={FL}  IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}")

print(f"\nG7 WF:")
wf = 0
for w in range(6):
    sb=w*(N//6); eb=(w+1)*(N//6) if w<5 else N
    t_o = run_iv(TG, FL, sb, eb)
    t_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po=stats_from(t_o)['pnl']; pb=stats_from(t_b)['pnl']
    if po>pb: wf+=1
    print(f"  W{w+1}: base ${pb:+.0f} over ${po:+.0f} {'+' if po>pb else '-'}")
print(f"G7 {wf}/6")

# G8
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
tr_r = true_range(h_r,l_r,c_r)
atr_r = pd.Series(tr_r).rolling(14).mean().values
atr_pct_r = atr_r / c_r
atr_pct_r_use = np.roll(atr_pct_r, 1); atr_pct_r_use[0] = np.nan
valid_r = ~np.isnan(atr_pct_r_use) & (atr_pct_r_use > 0)
raw_r = np.where(valid_r, TG / atr_pct_r_use, 1.0)
size_r = np.clip(raw_r, FL, 1.0)
size_r[~valid_r] = 1.0
tr_base_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
tr_over_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,size_scale=size_r)
brp=stats_from(tr_base_r)['pnl']; orp=stats_from(tr_over_r)['pnl']
print(f"\nG8: base ${brp:+.0f}  over ${orp:+.0f}  delta ${orp-brp:+.0f}")

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

imp_pnl=ot['pnl']>bt['pnl']; imp_mdd=ot['mdd']<bt['mdd']
imp_w30=w30o>w30b; imp_g8=orp>brp
print(f"\nVERDICT: PnL imp {imp_pnl}  MDD imp {imp_mdd}  W30 imp {imp_w30}  G8 imp {imp_g8}  WF {wf}/6")
if imp_pnl and imp_g8 and wf>=4:
    print("-> PROMOTED")
elif (imp_mdd or imp_w30) and imp_g8:
    print("-> CONDITIONAL")
else:
    print("-> REJECTED")
