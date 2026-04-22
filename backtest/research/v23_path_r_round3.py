"""
V23 Path R Round 3 — ADX 作為 regime classifier
假說: 低 ADX = 盤整，trend 訊號不可靠。高 ADX = 明確趨勢，breakout 方向性強。
      block entries when ADX(14) < TH
新增參數: adx_threshold (1 knob, ADX period=14 固定)
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


def compute_adx(h, l, c, period=14):
    n = len(h)
    up_move = np.zeros(n); dn_move = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i-1]; dn = l[i-1] - l[i]
        up_move[i] = up if (up > dn and up > 0) else 0
        dn_move[i] = dn if (dn > up and dn > 0) else 0
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    atr = pd.Series(tr).rolling(period).mean().values
    pdi = 100 * pd.Series(up_move).rolling(period).mean().values / np.maximum(atr, 1e-10)
    mdi = 100 * pd.Series(dn_move).rolling(period).mean().values / np.maximum(atr, 1e-10)
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-10)
    adx = pd.Series(dx).rolling(period).mean().values
    return adx


adx = compute_adx(h, l, c, 14)
adx_use = np.roll(adx, 1); adx_use[0] = np.nan


def run_with_gate(th, start, end):
    block = (adx_use < th) & (~np.isnan(adx_use))
    return run_v14_overlay(o,h,l,c,hours,dows,mks,dks,start,end,
                           block_bars_L=block, block_bars_S=block)


tr_is_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_base); base_oos = stats_from(tr_oos_base)
print(f"\nBASELINE V14  IS: {base_is['n']}t ${base_is['pnl']:+.0f} MDD${base_is['mdd']:.0f}  "
      f"OOS: {base_oos['n']}t ${base_oos['pnl']:+.0f} MDD${base_oos['mdd']:.0f}")

# Grid
print("\n" + "="*90)
print("Grid: ADX threshold")
print("="*90)
print(f"{'TH':>6} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
thresholds = [10, 15, 18, 20, 22, 25, 30, 35]
grid = []
for th in thresholds:
    ti = run_with_gate(th, 0, IS_END)
    to = run_with_gate(th, IS_END, N)
    si = stats_from(ti); so = stats_from(to)
    grid.append((th, ti, to, si, so))
    print(f"{th:>6} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
          f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

best = max(grid, key=lambda x: x[3]['pnl'])
TH_STAR = best[0]; tr_is = best[1]; tr_oos = best[2]
si = best[3]; so = best[4]
print(f"\nBest IS TH*: {TH_STAR}  (IS ${si['pnl']:+.0f}, OOS ${so['pnl']:+.0f})")

# Check vs baseline
is_delta = si['pnl'] - base_is['pnl']
oos_delta = so['pnl'] - base_oos['pnl']
print(f"IS delta: ${is_delta:+.0f}  OOS delta: ${oos_delta:+.0f}")

# G7 WF
print(f"\nG7 WF 6-window:")
wf_imp = 0
for w in range(6):
    sb=w*(N//6); eb=(w+1)*(N//6) if w<5 else N
    t_over = run_with_gate(TH_STAR, sb, eb)
    t_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po=stats_from(t_over)['pnl']; pb=stats_from(t_base)['pnl']
    if po>pb: wf_imp+=1
    print(f"  W{w+1}: base ${pb:+.0f}  overlay ${po:+.0f}  {'+' if po>pb else '-'}")
print(f"  improved {wf_imp}/6")

# G8 reversal
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
adx_r = compute_adx(h_r, l_r, c_r, 14)
adx_r_use = np.roll(adx_r, 1); adx_r_use[0] = np.nan
tr_base_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
block_r = (adx_r_use < TH_STAR) & (~np.isnan(adx_r_use))
tr_over_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,
                               block_bars_L=block_r, block_bars_S=block_r)
brp = stats_from(tr_base_rev)['pnl']; orp = stats_from(tr_over_rev)['pnl']
print(f"\nG8: base ${brp:+.0f}  overlay ${orp:+.0f}  delta ${orp-brp:+.0f}")

# Full risk map
all_base = tr_is_base + tr_oos_base
all_over = tr_is + tr_oos
bt = stats_from(all_base); ot = stats_from(all_over)
print(f"\n{'='*90}")
print("Risk Map")
print(f"{'='*90}")
print(f"                 baseline      overlay   delta")
print(f"Trades           {bt['n']:>9}     {ot['n']:>9}     {ot['n']-bt['n']:+d}")
print(f"PnL              ${bt['pnl']:>+8.0f}   ${ot['pnl']:>+8.0f}   ${ot['pnl']-bt['pnl']:+.0f}")
print(f"MDD              ${bt['mdd']:>8.0f}   ${ot['mdd']:>8.0f}   ${bt['mdd']-ot['mdd']:+.0f}")
print(f"Sharpe           {bt['sharpe']:>9.2f}   {ot['sharpe']:>9.2f}   {ot['sharpe']-bt['sharpe']:+.2f}")
w30_b = worst_rolling_dd(all_base, N, 30*24, 1)[0][1]
w30_o = worst_rolling_dd(all_over, N, 30*24, 1)[0][1]
print(f"Worst 30d        ${w30_b:>+8.0f}   ${w30_o:>+8.0f}   ${w30_b-w30_o:+.0f}")
print(f"Reversed         ${brp:>+8.0f}   ${orp:>+8.0f}   ${orp-brp:+.0f}")

# R-G11
from sklearn.metrics import roc_auc_score
fwd_ret = np.roll(c, -1)/c - 1; fwd_ret[-1]=0
valid = ~np.isnan(adx_use)
y=(fwd_ret[valid]>0).astype(int); x=(adx_use[valid]<TH_STAR).astype(int)
try: auc = roc_auc_score(y,x)
except: auc = 0.5
print(f"\nR-G11 AUC: {auc:.3f}  {'PASS' if abs(auc-0.5)<=0.05 else 'FAIL'}")

print(f"\nVERDICT: PnL change ${ot['pnl']-bt['pnl']:+.0f}  MDD change ${bt['mdd']-ot['mdd']:+.0f}  "
      f"G7 {wf_imp}/6  G8 delta ${orp-brp:+.0f}")
if ot['pnl'] > bt['pnl'] and orp > brp and wf_imp >= 4:
    print("  -> PROMOTED")
elif orp > brp and ot['pnl'] > bt['pnl']:
    print("  -> CONDITIONAL (G8 + PnL both improve but WF weak)")
elif orp > brp:
    print("  -> SIDE-PROGRESS (only G8 improves, PnL worse)")
else:
    print("  -> REJECTED")
