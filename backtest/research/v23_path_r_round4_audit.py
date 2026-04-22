"""
V23 Path R Round 4 Full 10-Gate Audit
R4 best: TH_UP=0.04, TH_SIDE=0.010 (asymmetric per-side SMA200 slope gate)

Gates: G1-G10 + R-G11 AUC + R-G12 transparency
Key concerns: G7 3/6 WF below strict 4/6 threshold, need G5 cascade to check
whether R4 improvement is random or systematic.
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, load_data,
                                worst_rolling_dd, monthly_pnl)

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
TH_UP = 0.04
TH_SIDE = 0.010

sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan


def run_asym(th_up, th_side, start, end, o=o, h=h, l=l, c=c,
             hrs=hours, dws=dows, mks_=mks, dks_=dks, slope_use_=slope_use):
    block_L = (slope_use_ > th_up) & (~np.isnan(slope_use_))
    block_S = (np.abs(slope_use_) < th_side) & (~np.isnan(slope_use_))
    return run_v14_overlay(o, h, l, c, hrs, dws, mks_, dks_, start, end,
                           block_bars_L=block_L, block_bars_S=block_S)


tr_is_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END)
tr_oos_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N)
base_is = stats_from(tr_is_b); base_oos = stats_from(tr_oos_b)

tr_is = run_asym(TH_UP, TH_SIDE, 0, IS_END)
tr_oos = run_asym(TH_UP, TH_SIDE, IS_END, N)
si = stats_from(tr_is); so = stats_from(tr_oos)

all_b = tr_is_b + tr_oos_b; all_o = tr_is + tr_oos
bt = stats_from(all_b); ot = stats_from(all_o)

print(f"Bars={N}  IS[0:{IS_END}] OOS[{IS_END}:{N}]")
print(f"TH_UP={TH_UP}  TH_SIDE={TH_SIDE}")
print(f"\nBASE  IS ${base_is['pnl']:+.0f}  OOS ${base_oos['pnl']:+.0f}  ALL ${bt['pnl']:+.0f}")
print(f"R4    IS ${si['pnl']:+.0f}  OOS ${so['pnl']:+.0f}  ALL ${ot['pnl']:+.0f}")
print(f"delta IS ${si['pnl']-base_is['pnl']:+.0f}  OOS ${so['pnl']-base_oos['pnl']:+.0f}")

# G1-G4
print("\n" + "=" * 90)
print("G1-G4 Basic Gates")
print("=" * 90)
g1 = si['pnl'] > 0
g2 = so['pnl'] > 0
g3 = np.sign(si['pnl'] - base_is['pnl']) == np.sign(so['pnl'] - base_oos['pnl'])
print(f"G1 IS+ overlay:            {'PASS' if g1 else 'FAIL'} (${si['pnl']:+.0f})")
print(f"G2 OOS+ overlay:           {'PASS' if g2 else 'FAIL'} (${so['pnl']:+.0f})")
print(f"G3 IS/OOS delta same sign: {'PASS' if g3 else 'FAIL'}")

# G4 neighborhood in 2D grid
print(f"\nG4 Neighborhood (2D):")
neighbors = [(0.03, 0.010), (0.05, 0.010), (0.04, 0.005), (0.04, 0.015)]
best_pnl = si['pnl']
deg_ok = True
for tu, ts in neighbors:
    t_n = run_asym(tu, ts, 0, IS_END)
    n_pnl = stats_from(t_n)['pnl']
    d = (best_pnl - n_pnl) / abs(best_pnl)
    ok = abs(d) <= 0.30
    if not ok: deg_ok = False
    print(f"  TH_UP={tu} TH_SIDE={ts}  IS ${n_pnl:+.0f}  degrade {d*100:+.1f}%  {'OK' if ok else 'FAIL'}")
print(f"G4 PASS: {deg_ok}")

# G5 Cascade - 100x random block
print("\n" + "=" * 90)
print("G5 Cascade (100x random block, matched block ratio)")
print("=" * 90)
blk_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
blk_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))
nL = int(blk_L.sum()); nS = int(blk_S.sum())
print(f"block bars L={nL}/{N} ({nL/N*100:.1f}%)  S={nS}/{N} ({nS/N*100:.1f}%)")

rng = np.random.default_rng(42)
worse = 0; better = 0
pnls = []
for trial in range(100):
    idx_L = rng.choice(N, nL, replace=False)
    idx_S = rng.choice(N, nS, replace=False)
    rbL = np.zeros(N, bool); rbL[idx_L] = True
    rbS = np.zeros(N, bool); rbS[idx_S] = True
    t_r = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, N,
                          block_bars_L=rbL, block_bars_S=rbS)
    p = stats_from(t_r)['pnl']
    pnls.append(p)
    if p >= ot['pnl']: better += 1
    else: worse += 1
pct_rank = sum(1 for p in pnls if p < ot['pnl']) / len(pnls) * 100
print(f"R4 overlay PnL ${ot['pnl']:+.0f}")
print(f"Random 100x: mean ${np.mean(pnls):+.0f}  std ${np.std(pnls):.0f}  min ${min(pnls):+.0f}  max ${max(pnls):+.0f}")
print(f"R4 rank: {pct_rank:.0f}th percentile (beat {worse}/100 random)")
g5 = pct_rank >= 80
print(f"G5 PASS: {g5} (require >=80th percentile)")

# G6 swap
print("\n" + "=" * 90)
print("G6 Swap test")
print("=" * 90)
fwd = (si['pnl'] - so['pnl']) / abs(si['pnl']) * 100 if si['pnl'] else 0
bwd = (so['pnl'] - si['pnl']) / abs(so['pnl']) * 100 if so['pnl'] else 0
g6 = abs(fwd) < 50 and abs(bwd) < 50
print(f"IS on OOS: {fwd:+.1f}%  OOS on IS: {bwd:+.1f}%  {'PASS' if g6 else 'FAIL'}")

# G7 WF (already ran; record for completeness)
print("\n" + "=" * 90)
print("G7 WF 6-window")
print("=" * 90)
wf = 0
for w in range(6):
    sb = w * (N // 6); eb = (w+1) * (N // 6) if w < 5 else N
    t_o = run_asym(TH_UP, TH_SIDE, sb, eb)
    t_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, sb, eb)
    po = stats_from(t_o)['pnl']; pb = stats_from(t_b)['pnl']
    mark = '+' if po > pb else '-'
    if po > pb: wf += 1
    print(f"  W{w+1}: base ${pb:+.0f}  overlay ${po:+.0f}  {mark}")
print(f"G7 {wf}/6  {'PASS' if wf >= 4 else 'FAIL'}")

# G8 reversal
print("\n" + "=" * 90)
print("G8 Time reversal")
print("=" * 90)
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
print(f"Base reversed ${brp:+.0f}  Overlay reversed ${orp:+.0f}  delta ${orp-brp:+.0f}  {'PASS' if g8 else 'FAIL'}")

# G9 remove-best-month
print("\n" + "=" * 90)
print("G9 Remove best month")
print("=" * 90)
all_t = tr_is + tr_oos
mop = monthly_pnl(all_t)
bm = max(mop, key=mop.get)
without = sum(v for k, v in mop.items() if k != bm)
g9 = without > 0
print(f"Best month {bm} ${mop[bm]:+.0f}  without ${without:+.0f}  {'PASS' if g9 else 'FAIL'}")

# G10 param count
print("\n" + "=" * 90)
print("G10 Param count: 2 (TH_UP, TH_SIDE)  PASS")
print("=" * 90)

# R-G11 AUC
print("\n" + "=" * 90)
print("R-G11 Gate-not-prediction (AUC)")
print("=" * 90)
from sklearn.metrics import roc_auc_score
fwd_ret = np.roll(c, -1) / c - 1; fwd_ret[-1] = 0
valid = ~np.isnan(slope_use)
yL = (fwd_ret[valid] > 0).astype(int)
xL = ((slope_use[valid] > TH_UP)).astype(int)
ys = (fwd_ret[valid] < 0).astype(int)
xs = ((np.abs(slope_use[valid]) < TH_SIDE)).astype(int)
try: aucL = roc_auc_score(yL, xL)
except: aucL = 0.5
try: aucS = roc_auc_score(ys, xs)
except: aucS = 0.5
g11L = abs(aucL - 0.5) <= 0.05
g11S = abs(aucS - 0.5) <= 0.05
print(f"L gate (slope > {TH_UP}) AUC vs up-ret: {aucL:.3f}  {'PASS' if g11L else 'FAIL'}")
print(f"S gate (|slope|<{TH_SIDE}) AUC vs dn-ret: {aucS:.3f}  {'PASS' if g11S else 'FAIL'}")

# R-G12 Transparency
print("\n" + "=" * 90)
print("R-G12 Transparency: 2 numbers (SMA200 100-bar slope)  PASS")
print("=" * 90)

# Risk map
print("\n" + "=" * 90)
print("Risk Map")
print("=" * 90)
w30b = worst_rolling_dd(all_b, N, 30*24, 1)[0][1]
w30o = worst_rolling_dd(all_o, N, 30*24, 1)[0][1]
print(f"Trades           {bt['n']}  {ot['n']}  {ot['n']-bt['n']:+d}")
print(f"PnL              ${bt['pnl']:+.0f}  ${ot['pnl']:+.0f}  ${ot['pnl']-bt['pnl']:+.0f}")
print(f"MDD              ${bt['mdd']:.0f}  ${ot['mdd']:.0f}  ${bt['mdd']-ot['mdd']:+.0f}")
print(f"Sharpe           {bt['sharpe']:.2f}  {ot['sharpe']:.2f}  {ot['sharpe']-bt['sharpe']:+.2f}")
print(f"Worst 30d        ${w30b:+.0f}  ${w30o:+.0f}  ${w30b-w30o:+.0f}")
print(f"Reversed         ${brp:+.0f}  ${orp:+.0f}  ${orp-brp:+.0f}")

# Summary
print("\n" + "=" * 90)
print("GATE SUMMARY")
print("=" * 90)
gates = [('G1 IS+', g1), ('G2 OOS+', g2), ('G3 same-sign', g3), ('G4 neighborhood', deg_ok),
         ('G5 cascade', g5), ('G6 swap<50%', g6), (f'G7 WF {wf}/6', wf >= 4),
         ('G8 reversal', g8), ('G9 remove-best', g9), ('G10 params<=2', True),
         (f'R-G11 L AUC {aucL:.2f}', g11L), (f'R-G11 S AUC {aucS:.2f}', g11S),
         ('R-G12 transparency', True)]
p = sum(1 for _, ok in gates if ok)
f_ = len(gates) - p
for name, ok in gates:
    print(f"  {name}: {'PASS' if ok else 'FAIL'}")
print(f"\n{p}/{len(gates)} PASS, {f_} FAIL")

if p == len(gates):
    print("-> PROMOTED (all gates green)")
elif f_ <= 2:
    print("-> CONDITIONAL (most gates pass)")
else:
    print("-> REJECTED")
