"""
V23 Path R Round 2 — Regime Gate via ATR percentile
假說: 低波動區間的 15-bar breakout 多為假突破（吃到 SafeNet or 時間出場）。
      過濾 ATR percentile < TH 的 bar。
新增參數: atr_pctile_threshold (1 個 knob, ATR 期間 n=14, pctile window=200 固定)
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

# === Regime classifier: ATR percentile ===
# True Range, ATR(14), rolling percentile over 200 bars
def true_range(h, l, c):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    return tr

tr = true_range(h, l, c)
atr = pd.Series(tr).rolling(14).mean().values
# ATR as % of close
atr_pct = atr / c

# Percentile over 200 bars
def rolling_pctile(vals, w):
    out = np.full(len(vals), np.nan)
    for i in range(w-1, len(vals)):
        win = vals[i-w+1:i+1]; v = win[~np.isnan(win)]
        if len(v) < 10: continue
        out[i] = np.sum(v <= vals[i]) / len(v) * 100
    return out

atr_pctile_arr = rolling_pctile(atr_pct, 200)
atr_pctile_use = np.roll(atr_pctile_arr, 1); atr_pctile_use[0] = np.nan


def run_with_gate(th, start, end):
    block = (atr_pctile_use < th) & (~np.isnan(atr_pctile_use))
    return run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                           block_bars_L=block, block_bars_S=block)


# Baseline
tr_is_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_base); base_oos = stats_from(tr_oos_base)
print(f"\nBASELINE V14  IS: {base_is['n']}t ${base_is['pnl']:+.0f} MDD${base_is['mdd']:.0f}  "
      f"OOS: {base_oos['n']}t ${base_oos['pnl']:+.0f} MDD${base_oos['mdd']:.0f}")

# Grid
print("\n" + "="*90)
print("Grid: ATR_pctile_threshold")
print("="*90)
print(f"{'TH':>6} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
thresholds = [10, 20, 25, 30, 40, 50, 60]
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

# Checks
is_delta = si['pnl'] - base_is['pnl']
oos_delta = so['pnl'] - base_oos['pnl']
print(f"\nIS delta: ${is_delta:+.0f}  OOS delta: ${oos_delta:+.0f}  "
      f"G3 {'PASS' if np.sign(is_delta)==np.sign(oos_delta) else 'FAIL'}")

idx = thresholds.index(TH_STAR)
neigh_ok = True
for di in [-1, 1]:
    j = idx + di
    if 0 <= j < len(thresholds):
        nis = grid[j][3]['pnl']
        degrade = (si['pnl'] - nis) / abs(si['pnl']) if si['pnl']!=0 else 0
        if abs(degrade) > 0.30:
            neigh_ok = False
print(f"G4 neighborhood: {'PASS' if neigh_ok else 'FAIL'}")

fwd = (si['pnl']-so['pnl'])/abs(si['pnl'])*100 if si['pnl'] else 0
bwd = (so['pnl']-si['pnl'])/abs(so['pnl'])*100 if so['pnl'] else 0
print(f"G6 Swap: Fwd {fwd:+.1f}% Bwd {bwd:+.1f}% {'PASS' if abs(fwd)<50 and abs(bwd)<50 else 'FAIL'}")

# G7 WF
print(f"\nG7 WF 6-window (overlay vs baseline per window):")
wf_pos = 0
for w in range(6):
    sb=w*(N//6); eb=(w+1)*(N//6) if w<5 else N
    t_over = run_with_gate(TH_STAR, sb, eb)
    t_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po=stats_from(t_over)['pnl']; pb=stats_from(t_base)['pnl']
    if po>pb: wf_pos+=1
    print(f"  W{w+1}: base ${pb:+.0f}  overlay ${po:+.0f}  {'+' if po>pb else '-'}")
print(f"  improved {wf_pos}/6  {'PASS' if wf_pos>=4 else 'FAIL'}")

# G8 reversed
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
tr_r = true_range(h_r, l_r, c_r)
atr_r = pd.Series(tr_r).rolling(14).mean().values
atr_pct_r = atr_r / c_r
atr_pctile_r = rolling_pctile(atr_pct_r, 200)
atr_pctile_r_use = np.roll(atr_pctile_r, 1); atr_pctile_r_use[0] = np.nan

tr_base_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
block_r = (atr_pctile_r_use < TH_STAR) & (~np.isnan(atr_pctile_r_use))
tr_over_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,
                               block_bars_L=block_r, block_bars_S=block_r)
brp = stats_from(tr_base_rev)['pnl']; orp = stats_from(tr_over_rev)['pnl']
print(f"\nG8 Time reversal: base ${brp:+.0f}  overlay ${orp:+.0f}  delta ${orp-brp:+.0f}  "
      f"{'PASS' if orp>brp else 'FAIL'}")

# G9
all_tr = tr_is + tr_oos
by_mo = monthly_pnl(all_tr)
best_m = max(by_mo, key=by_mo.get)
without = sum(v for k,v in by_mo.items() if k!=best_m)
print(f"G9 Remove best month {best_m} ${by_mo[best_m]:+.0f} -> without ${without:+.0f}  "
      f"{'PASS' if without>0 else 'FAIL'}")

# R-G11 Gate-not-prediction
from sklearn.metrics import roc_auc_score
fwd_ret = np.roll(c, -1)/c - 1; fwd_ret[-1] = 0
fwd_sign = (fwd_ret > 0).astype(int)
valid = ~np.isnan(atr_pctile_use)
y = fwd_sign[valid]; x = (atr_pctile_use[valid] < TH_STAR).astype(int)
if len(np.unique(y)) > 1 and len(np.unique(x)) > 1:
    auc = roc_auc_score(y, x)
else:
    auc = 0.5
print(f"R-G11 Gate-not-prediction: AUC={auc:.3f}  "
      f"{'PASS' if abs(auc-0.5)<=0.05 else 'FAIL (is prediction not gate)'}")
print(f"R-G12 Transparency: 1 number (ATR% 14-bar / 200-bar pctile)  PASS")

# Risk map
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
print(f"Worst 30d DD     ${w30_b:>+8.0f}   ${w30_o:>+8.0f}   ${w30_b-w30_o:+.0f}")
print(f"Reversed PnL     ${brp:>+8.0f}   ${orp:>+8.0f}   ${orp-brp:+.0f}")

print(f"\n{'='*90}")
print("Gates summary")
print(f"{'='*90}")
print(f"G1 IS+: {'PASS' if si['pnl']>0 else 'FAIL'}")
print(f"G2 OOS+: {'PASS' if so['pnl']>0 else 'FAIL'}")
print(f"G3 IS/OOS same sign: {'PASS' if np.sign(is_delta)==np.sign(oos_delta) else 'FAIL'}")
print(f"G4 neighborhood: {'PASS' if neigh_ok else 'FAIL'}")
print(f"G6 Swap: {'PASS' if abs(fwd)<50 and abs(bwd)<50 else 'FAIL'}")
print(f"G7 WF 4/6 improved: {'PASS' if wf_pos>=4 else 'FAIL'} ({wf_pos}/6)")
print(f"G8 reversal reduced: {'PASS' if orp>brp else 'FAIL'} (delta ${orp-brp:+.0f})")
print(f"G9 Remove best: {'PASS' if without>0 else 'FAIL'}")
print(f"G10 params<=2: PASS")
print(f"R-G11 not prediction: {'PASS' if abs(auc-0.5)<=0.05 else 'FAIL'}")
print(f"R-G12 transparent: PASS")
