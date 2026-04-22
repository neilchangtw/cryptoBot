"""
V23 Path R Round 5 — R4 refinement attempt to push G7 4/6
Strategy: check alternative grid points near R4 best, prioritize WF consistency
over pure IS maximization.

R4 best: TH_UP=0.04, TH_SIDE=0.01, WF 3/6
Grid showed TH_UP=0.03, TH_SIDE=0.001 → OOS $+4622 (highest). Try that.
Also try neighborhood WF-maximization.
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


def run_asym(th_up, th_side, start, end):
    block_L = (slope_use > th_up) & (~np.isnan(slope_use))
    block_S = (np.abs(slope_use) < th_side) & (~np.isnan(slope_use))
    return run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                           block_bars_L=block_L, block_bars_S=block_S)


def wf_count(tu, ts):
    wf = 0
    for w in range(6):
        sb = w * (N // 6); eb = (w+1) * (N // 6) if w < 5 else N
        t_o = run_asym(tu, ts, sb, eb)
        t_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, sb, eb)
        if stats_from(t_o)['pnl'] > stats_from(t_b)['pnl']:
            wf += 1
    return wf


# Widen TH_UP around 0.03-0.04; TH_SIDE explore fine grid
print("\nFull IS/OOS + WF grid:")
print(f"{'TH_UP':>7} {'TH_SIDE':>8} {'IS PnL':>8} {'OOS PnL':>9} {'ALL PnL':>9} {'WF':>4}")
candidates = [
    (0.025, 0.010), (0.030, 0.010), (0.035, 0.010), (0.040, 0.010),
    (0.030, 0.001), (0.030, 0.005), (0.030, 0.015),
    (0.035, 0.005), (0.035, 0.015),
    (0.040, 0.005), (0.040, 0.015),
    (0.045, 0.010), (0.050, 0.010),
]
results = []
for tu, ts in candidates:
    ti = run_asym(tu, ts, 0, IS_END)
    to = run_asym(tu, ts, IS_END, N)
    si = stats_from(ti); so = stats_from(to)
    wf = wf_count(tu, ts)
    all_p = si['pnl'] + so['pnl']
    results.append((tu, ts, si['pnl'], so['pnl'], all_p, wf))
    print(f"{tu:>7.3f} {ts:>8.4f} ${si['pnl']:>+7.0f} ${so['pnl']:>+8.0f} ${all_p:>+8.0f} {wf:>4}")

# Baseline for ref
tr_is_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END)
tr_oos_b = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N)
bp_is = stats_from(tr_is_b)['pnl']; bp_oos = stats_from(tr_oos_b)['pnl']
print(f"\nBASE: IS ${bp_is:+.0f}  OOS ${bp_oos:+.0f}  ALL ${bp_is+bp_oos:+.0f}")

# Pick highest WF, tiebreak by ALL PnL
results.sort(key=lambda x: (-x[5], -x[4]))
best = results[0]
print(f"\nBest WF-priority: TH_UP={best[0]} TH_SIDE={best[1]}  IS ${best[2]:+.0f}  OOS ${best[3]:+.0f}  WF {best[5]}/6")

# Check if any hits 4+
hit4 = [r for r in results if r[5] >= 4 and r[4] > bp_is + bp_oos]
if hit4:
    print(f"\nConfigs with WF>=4 AND PnL > base: {len(hit4)}")
    for r in hit4:
        print(f"  TH_UP={r[0]} TH_SIDE={r[1]}  ALL ${r[4]:+.0f}  WF {r[5]}/6")
else:
    print("\nNo config reaches WF>=4 with PnL > base. R4 remains best.")

print("\nVERDICT R5:")
if hit4:
    print("-> R5 PROMOTES (found WF>=4 config)")
else:
    print("-> R5 confirms R4 as ceiling. Path R closes at CONDITIONAL.")
