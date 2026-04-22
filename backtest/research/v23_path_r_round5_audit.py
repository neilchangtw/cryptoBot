"""
V23 Path R R5 Full 10-Gate Audit
Winner: TH_UP=0.045, TH_SIDE=0.010 (both gates live, WF 4/6, ALL PnL $+6,503)
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, load_data,
                                worst_rolling_dd, monthly_pnl)

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
TH_UP = 0.045
TH_SIDE = 0.010

sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan


def run_asym(th_up, th_side, start, end, slope_u=slope_use,
             o=o, h=h, l=l, c=c, hr=hours, dw=dows, mk=mks, dk=dks):
    block_L = (slope_u > th_up) & (~np.isnan(slope_u))
    block_S = (np.abs(slope_u) < th_side) & (~np.isnan(slope_u))
    return run_v14_overlay(o, h, l, c, hr, dw, mk, dk, start, end,
                           block_bars_L=block_L, block_bars_S=block_S)


tr_is_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END)
tr_oos_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N)
base_is = stats_from(tr_is_b); base_oos = stats_from(tr_oos_b)

tr_is = run_asym(TH_UP, TH_SIDE, 0, IS_END)
tr_oos = run_asym(TH_UP, TH_SIDE, IS_END, N)
si = stats_from(tr_is); so = stats_from(tr_oos)
all_b = tr_is_b + tr_oos_b; all_o = tr_is + tr_oos
bt = stats_from(all_b); ot = stats_from(all_o)

print(f"TH_UP={TH_UP}  TH_SIDE={TH_SIDE}")
print(f"BASE  IS ${base_is['pnl']:+.0f}  OOS ${base_oos['pnl']:+.0f}  ALL ${bt['pnl']:+.0f}")
print(f"R5    IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}  ALL ${ot['pnl']:+.0f}")

# G1-G4
g1 = si['pnl'] > 0; g2 = so['pnl'] > 0
g3 = np.sign(si['pnl'] - base_is['pnl']) == np.sign(so['pnl'] - base_oos['pnl'])
print(f"\nG1 IS+: {'PASS' if g1 else 'FAIL'}")
print(f"G2 OOS+: {'PASS' if g2 else 'FAIL'}")
print(f"G3 same-sign: {'PASS' if g3 else 'FAIL'}")

# G4 neighborhood
print(f"\nG4 Neighborhood:")
neighbors = [(0.04, 0.010), (0.05, 0.010), (0.045, 0.005), (0.045, 0.015)]
deg_ok = True
for tu, ts in neighbors:
    t_n = run_asym(tu, ts, 0, IS_END)
    n_pnl = stats_from(t_n)['pnl']
    d = (si['pnl'] - n_pnl) / abs(si['pnl'])
    ok = abs(d) <= 0.30
    if not ok: deg_ok = False
    print(f"  ({tu},{ts}) IS ${n_pnl:+.0f} deg {d*100:+.1f}% {'OK' if ok else 'FAIL'}")
print(f"G4 PASS: {deg_ok}")

# G5 Cascade
print(f"\nG5 Cascade 100x random:")
blk_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
blk_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))
nL = int(blk_L.sum()); nS = int(blk_S.sum())
rng = np.random.default_rng(42)
pnls = []
for trial in range(100):
    idx_L = rng.choice(N, nL, replace=False)
    idx_S = rng.choice(N, nS, replace=False)
    rbL = np.zeros(N, bool); rbL[idx_L] = True
    rbS = np.zeros(N, bool); rbS[idx_S] = True
    t_r = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, N,
                          block_bars_L=rbL, block_bars_S=rbS)
    pnls.append(stats_from(t_r)['pnl'])
pct = sum(1 for p in pnls if p < ot['pnl']) / 100 * 100
g5 = pct >= 80
print(f"  block L={nL} S={nS}  R5 ${ot['pnl']:+.0f}  rand mean ${np.mean(pnls):+.0f}  rank {pct:.0f}th  {'PASS' if g5 else 'FAIL'}")

# G6 swap
fwd = (si['pnl'] - so['pnl']) / abs(si['pnl']) * 100 if si['pnl'] else 0
bwd = (so['pnl'] - si['pnl']) / abs(so['pnl']) * 100 if so['pnl'] else 0
g6 = abs(fwd) < 50 and abs(bwd) < 50
print(f"\nG6 Swap: Fwd {fwd:+.1f}% Bwd {bwd:+.1f}%  {'PASS' if g6 else 'FAIL'}")

# G7 WF
print(f"\nG7 WF 6-window:")
wf = 0
for w in range(6):
    sb = w * (N // 6); eb = (w+1) * (N // 6) if w < 5 else N
    t_o = run_asym(TH_UP, TH_SIDE, sb, eb)
    t_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, sb, eb)
    po = stats_from(t_o)['pnl']; pb = stats_from(t_b)['pnl']
    if po > pb: wf += 1
    print(f"  W{w+1}: base ${pb:+.0f} over ${po:+.0f} {'+' if po>pb else '-'}")
print(f"G7 {wf}/6  {'PASS' if wf>=4 else 'FAIL'}")

# G8 reversal
o_r = o[::-1].copy(); h_r = h[::-1].copy(); l_r = l[::-1].copy(); c_r = c[::-1].copy()
hr_r = hours[::-1].copy(); dw_r = dows[::-1].copy()
mk_r = mks[::-1].copy(); dk_r = dks[::-1].copy()
sma_r = pd.Series(c_r).rolling(200).mean().values
slope_r = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma_r[i]) and sma_r[i-100] > 0:
        slope_r[i] = (sma_r[i] - sma_r[i-100]) / sma_r[i-100]
slope_r_use = np.roll(slope_r, 1); slope_r_use[0] = np.nan
blk_Lr = (slope_r_use > TH_UP) & (~np.isnan(slope_r_use))
blk_Sr = (np.abs(slope_r_use) < TH_SIDE) & (~np.isnan(slope_r_use))
tr_base_r = run_v14_overlay(o_r, h_r, l_r, c_r, hr_r, dw_r, mk_r, dk_r, 0, N)
tr_over_r = run_v14_overlay(o_r, h_r, l_r, c_r, hr_r, dw_r, mk_r, dk_r, 0, N,
                             block_bars_L=blk_Lr, block_bars_S=blk_Sr)
brp = stats_from(tr_base_r)['pnl']; orp = stats_from(tr_over_r)['pnl']
g8 = orp > brp
print(f"\nG8: base ${brp:+.0f} over ${orp:+.0f} delta ${orp-brp:+.0f}  {'PASS' if g8 else 'FAIL'}")

# G9 remove-best-month
all_t = tr_is + tr_oos
mop = monthly_pnl(all_t)
bm = max(mop, key=mop.get)
without = sum(v for k, v in mop.items() if k != bm)
g9 = without > 0
print(f"\nG9 Remove best {bm} ${mop[bm]:+.0f}  without ${without:+.0f}  {'PASS' if g9 else 'FAIL'}")

# R-G11 AUC
from sklearn.metrics import roc_auc_score
fwd_ret = np.roll(c, -1) / c - 1; fwd_ret[-1] = 0
valid = ~np.isnan(slope_use)
yL = (fwd_ret[valid] > 0).astype(int); xL = (slope_use[valid] > TH_UP).astype(int)
ys = (fwd_ret[valid] < 0).astype(int); xs = (np.abs(slope_use[valid]) < TH_SIDE).astype(int)
try: aucL = roc_auc_score(yL, xL)
except: aucL = 0.5
try: aucS = roc_auc_score(ys, xs)
except: aucS = 0.5
g11L = abs(aucL - 0.5) <= 0.05; g11S = abs(aucS - 0.5) <= 0.05
print(f"\nR-G11 L AUC {aucL:.3f} {'PASS' if g11L else 'FAIL'}  S AUC {aucS:.3f} {'PASS' if g11S else 'FAIL'}")

# Risk map
w30b = worst_rolling_dd(all_b, N, 30*24, 1)[0][1]
w30o = worst_rolling_dd(all_o, N, 30*24, 1)[0][1]
print(f"\nRisk Map:")
print(f"  Trades {bt['n']} -> {ot['n']} ({ot['n']-bt['n']:+d})")
print(f"  PnL ${bt['pnl']:+.0f} -> ${ot['pnl']:+.0f} ({ot['pnl']-bt['pnl']:+.0f})")
print(f"  MDD ${bt['mdd']:.0f} -> ${ot['mdd']:.0f} ({bt['mdd']-ot['mdd']:+.0f})")
print(f"  Sharpe {bt['sharpe']:.2f} -> {ot['sharpe']:.2f} ({ot['sharpe']-bt['sharpe']:+.2f})")
print(f"  Worst 30d ${w30b:+.0f} -> ${w30o:+.0f}")
print(f"  Reversed ${brp:+.0f} -> ${orp:+.0f}")

# Summary
gates = [('G1 IS+', g1), ('G2 OOS+', g2), ('G3 same-sign', g3), ('G4 neigh', deg_ok),
         ('G5 cascade', g5), ('G6 swap', g6), (f'G7 WF {wf}/6', wf>=4),
         ('G8 reversal', g8), ('G9 remove-best', g9), ('G10 param<=2', True),
         (f'R-G11L {aucL:.2f}', g11L), (f'R-G11S {aucS:.2f}', g11S),
         ('R-G12 transp', True)]
p = sum(1 for _, ok in gates if ok)
print(f"\n{'='*50}")
print(f"R5 GATES: {p}/{len(gates)} PASS")
for n, ok in gates:
    print(f"  {n}: {'PASS' if ok else 'FAIL'}")
print(f"{'='*50}")
if p == len(gates): print("-> PROMOTED (full green)")
elif p >= 11: print("-> CONDITIONAL PROMOTE (strong)")
elif p >= 9: print("-> CONDITIONAL")
else: print("-> REJECTED")
