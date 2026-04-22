"""
V23 Path V Round 1 — Vol Target (ATR pctile)
修正假設（基於 S2）：低波動 regime V14 /t 較差，不是高波動。
策略：ATR(14)/200-bar pctile 低於 TH → size_scale = scale_low（減倉），其他維持 1.0
新增參數：TH (pctile), scale_low (倉位比例)  → 符合 ≤2 knobs 規範
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


def true_range(h, l, c):
    pc = np.roll(c, 1); pc[0] = c[0]
    return np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))


def rolling_pctile(vals, w):
    out = np.full(len(vals), np.nan)
    for i in range(w-1, len(vals)):
        win = vals[i-w+1:i+1]; v = win[~np.isnan(win)]
        if len(v) < 10: continue
        out[i] = np.sum(v <= vals[i]) / len(v) * 100
    return out


tr = true_range(h, l, c)
atr = pd.Series(tr).rolling(14).mean().values
atr_pct = atr / c
atr_pctile = rolling_pctile(atr_pct, 200)
atr_pctile_use = np.roll(atr_pctile, 1); atr_pctile_use[0] = np.nan


def run_vol(th, scale_low, start, end):
    size = np.ones(N)
    low_vol = (atr_pctile_use < th) & (~np.isnan(atr_pctile_use))
    size[low_vol] = scale_low
    return run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                           size_scale=size)


tr_is_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_b); base_oos = stats_from(tr_oos_b)
print(f"\nBASE  IS ${base_is['pnl']:+.0f}  OOS ${base_oos['pnl']:+.0f}  MDD IS${base_is['mdd']:.0f}/OOS${base_oos['mdd']:.0f}")

print("\n" + "="*100)
print("Grid: TH_pctile x scale_low (block low vol)")
print("="*100)
print(f"{'TH':>6} {'scale':>7} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
ths = [20, 30, 40, 50]
scales = [0.0, 0.25, 0.50, 0.75]
grid = []
for th in ths:
    for sc in scales:
        ti = run_vol(th, sc, 0, IS_END)
        to = run_vol(th, sc, IS_END, N)
        si = stats_from(ti); so = stats_from(to)
        grid.append((th, sc, ti, to, si, so))
        print(f"{th:>6} {sc:>7.2f} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
              f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

best = max(grid, key=lambda x: x[4]['pnl'])
TH, SC, tr_is, tr_oos, si, so = best
print(f"\nBest IS TH={TH} scale={SC}  IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}")

# G7
print(f"\nG7 WF 6-window:")
wf = 0
for w in range(6):
    sb = w * (N // 6); eb = (w+1) * (N // 6) if w < 5 else N
    t_o = run_vol(TH, SC, sb, eb)
    t_b = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po = stats_from(t_o)['pnl']; pb = stats_from(t_b)['pnl']
    if po > pb: wf += 1
    print(f"  W{w+1}: base ${pb:+.0f}  over ${po:+.0f}  {'+' if po>pb else '-'}")
print(f"G7 {wf}/6")

# G8
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
tr_r = true_range(h_r, l_r, c_r)
atr_r = pd.Series(tr_r).rolling(14).mean().values
atr_pct_r = atr_r / c_r
atr_pctile_r = rolling_pctile(atr_pct_r, 200)
atr_pctile_r_use = np.roll(atr_pctile_r, 1); atr_pctile_r_use[0] = np.nan
size_r = np.ones(N)
size_r[(atr_pctile_r_use < TH) & (~np.isnan(atr_pctile_r_use))] = SC
tr_base_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
tr_over_r = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,size_scale=size_r)
brp = stats_from(tr_base_r)['pnl']; orp = stats_from(tr_over_r)['pnl']
print(f"\nG8: base ${brp:+.0f}  over ${orp:+.0f}  delta ${orp-brp:+.0f}")

# Risk map
all_b = tr_is_b + tr_oos_b; all_o = tr_is + tr_oos
bt = stats_from(all_b); ot = stats_from(all_o)
w30b = worst_rolling_dd(all_b, N, 30*24, 1)[0][1]
w30o = worst_rolling_dd(all_o, N, 30*24, 1)[0][1]
print(f"\n{'='*90}\nRisk Map\n{'='*90}")
print(f"                 baseline      overlay   delta")
print(f"Trades           {bt['n']:>9}     {ot['n']:>9}     {ot['n']-bt['n']:+d}")
print(f"PnL              ${bt['pnl']:>+8.0f}   ${ot['pnl']:>+8.0f}   ${ot['pnl']-bt['pnl']:+.0f}")
print(f"MDD              ${bt['mdd']:>8.0f}   ${ot['mdd']:>8.0f}   ${bt['mdd']-ot['mdd']:+.0f}")
print(f"Sharpe           {bt['sharpe']:>9.2f}   {ot['sharpe']:>9.2f}   {ot['sharpe']-bt['sharpe']:+.2f}")
print(f"Worst 30d        ${w30b:>+8.0f}   ${w30o:>+8.0f}   ${w30b-w30o:+.0f}")
print(f"Reversed         ${brp:>+8.0f}   ${orp:>+8.0f}   ${orp-brp:+.0f}")

# Verdict
print(f"\nVERDICT R1:")
imp_pnl = ot['pnl'] > bt['pnl']
imp_mdd = ot['mdd'] < bt['mdd']
imp_w30 = w30o > w30b  # less negative
imp_g8 = orp > brp
print(f"PnL imp {imp_pnl}  MDD imp {imp_mdd}  Worst30d imp {imp_w30}  G8 imp {imp_g8}  WF {wf}/6")
if imp_pnl and imp_w30 and imp_g8 and wf >= 4:
    print("-> PROMOTED")
elif (imp_w30 or imp_mdd) and imp_g8:
    print("-> CONDITIONAL (risk-focused)")
elif imp_pnl:
    print("-> MARGINAL")
else:
    print("-> REJECTED")
