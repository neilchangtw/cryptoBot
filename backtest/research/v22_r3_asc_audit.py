"""
V22 R3 Audit — ASC_N5_M5 L TP=0.04 MH=48 SL=0.035 CD=6
IS $851 / OOS $1614 (OOS > IS)
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H as H0, L as L0, C as C0, O as O0, DT, TOTAL, IS_END, simulate, stats
import v22_common as vc
import importlib.util

spec = importlib.util.spec_from_file_location("r3mod",
    os.path.join(os.path.dirname(__file__), "v22_r3_triangle.py"))
r3mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r3mod)

N = 5; M = 5; TP = 0.04; SL = 0.035; MH = 48; CD = 6

def build_asc():
    asc, desc, symL, symS = r3mod.build_triangle_signals(N, M)
    return asc

asc = build_asc()

# Baseline
is_t = simulate(asc, "L", 0, IS_END, TP, SL, MH, CD)
oos_t = simulate(asc, "L", IS_END, TOTAL, TP, SL, MH, CD)
s_is = stats(is_t); s_oos = stats(oos_t)
combined = s_is['pnl'] + s_oos['pnl']
print(f"BASELINE: IS {s_is['n']}t ${s_is['pnl']:.0f}  OOS {s_oos['n']}t ${s_oos['pnl']:.0f}  Combined ${combined:.0f}")

# G4 Grid
print("\nG4 TP×MH neighborhood:")
print(f"{'TP':>6} {'MH':>4} {'IS n':>5} {'IS PnL':>8} {'OOS PnL':>8}")
for tp in [0.030, 0.035, 0.040, 0.045, 0.050]:
    for mh in [24, 36, 48, 60]:
        ti = simulate(asc, "L", 0, IS_END, tp, SL, mh, CD)
        to = simulate(asc, "L", IS_END, TOTAL, tp, SL, mh, CD)
        tag = " <--" if tp == TP and mh == MH else ""
        print(f"{tp:>6.3f} {mh:>4} {len(ti):>5} ${sum(t['pnl'] for t in ti):>7.0f} ${sum(t['pnl'] for t in to):>7.0f}{tag}")

print("\nG4 N×M neighborhood:")
print(f"{'N':>3} {'M':>3} {'IS PnL':>8} {'OOS PnL':>8}")
for nn in [3, 5, 7]:
    for mm in [4, 5, 6]:
        try:
            asc_a, _, _, _ = r3mod.build_triangle_signals(nn, mm)
            ti = simulate(asc_a, "L", 0, IS_END, TP, SL, MH, CD)
            to = simulate(asc_a, "L", IS_END, TOTAL, TP, SL, MH, CD)
            tag = " <--" if nn == N and mm == M else ""
            print(f"{nn:>3} {mm:>3} ${sum(t['pnl'] for t in ti):>7.0f} ${sum(t['pnl'] for t in to):>7.0f}{tag}")
        except: pass

# G6 Swap
print("\nG6 Swap Test:")
fwd = (s_is['pnl'] - s_oos['pnl']) / s_is['pnl'] * 100 if s_is['pnl'] else 0
bwd = (s_oos['pnl'] - s_is['pnl']) / s_oos['pnl'] * 100 if s_oos['pnl'] else 0
print(f"  Forward (IS→OOS): {fwd:+.1f}%  {'PASS' if abs(fwd) < 50 else 'FAIL'}")
print(f"  Backward (OOS→IS): {bwd:+.1f}%  {'PASS' if abs(bwd) < 50 else 'FAIL'}")

# G7 WF
print("\nG7 Walk-Forward (6 windows):")
win = TOTAL // 6
wf_pos = 0; wf_total = 0
for w in range(6):
    s_b = w*win; e_b = (w+1)*win if w<5 else TOTAL
    t = simulate(asc, "L", s_b, e_b, TP, SL, MH, CD)
    p = sum(x['pnl'] for x in t)
    print(f"  W{w+1}: {len(t)}t ${p:.0f}")
    if len(t) > 0: wf_total += 1
    if p > 0: wf_pos += 1
print(f"  {wf_pos}/{wf_total} positive  {'PASS' if wf_pos >= 4 else 'FAIL'}")

# G8 Time Reversal
print("\nG8 Time Reversal:")
H_rev = H0[::-1].copy(); L_rev = L0[::-1].copy()
C_rev = C0[::-1].copy(); O_rev = O0[::-1].copy()
vc.H = H_rev; vc.L = L_rev; vc.C = C_rev; vc.O = O_rev
r3mod.H = H_rev; r3mod.L = L_rev; r3mod.C = C_rev; r3mod.O = O_rev
asc_rev = build_asc()
t_rev = simulate(asc_rev, "L", 0, TOTAL, TP, SL, MH, CD)
p_rev = sum(t['pnl'] for t in t_rev)
print(f"  Original combined: ${combined:.0f}")
print(f"  Reversed: {len(t_rev)}t ${p_rev:.0f}")
print(f"  {'PASS' if p_rev > 0 else 'FAIL (regime-dependent)'}")
# Restore
vc.H = H0; vc.L = L0; vc.C = C0; vc.O = O0
r3mod.H = H0; r3mod.L = L0; r3mod.C = C0; r3mod.O = O0

# G9 Remove-best-month
print("\nG9 Remove-Best-Month:")
all_t = is_t + oos_t
monthly = {}
for t in all_t:
    monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
if monthly:
    best_mo = max(monthly, key=monthly.get)
    rem = sum(v for k,v in monthly.items() if k != best_mo)
    print(f"  Best: {best_mo} ${monthly[best_mo]:.0f}   Without best: ${rem:.0f}  {'PASS' if rem>0 else 'FAIL'}")

# V14 overlap
print("\nV14 Overlap:")
def gk_pctile(h, l, c, o, ns=5, nl=20, pw=100):
    gk = 0.5*np.log(h/l)**2 - (2*np.log(2)-1)*np.log(c/o)**2
    gks = pd.Series(gk).rolling(ns).mean().values
    gkl = pd.Series(gk).rolling(nl).mean().values
    r = gks/gkl; rs = np.full_like(r, np.nan); rs[1:] = r[:-1]
    out = np.full(len(r), np.nan)
    for i in range(pw-1, len(r)):
        w = rs[i-pw+1:i+1]; v = w[~np.isnan(w)]
        if len(v)>1: out[i] = (v<v[-1]).sum()/(len(v)-1)*100
    return out
gp = gk_pctile(H0, L0, C0, O0)
c_s = pd.Series(C0)
bo_up = (c_s > c_s.shift(1).rolling(15).max()).values
v14_L = (gp < 25) & bo_up
entry_bars = [t["entry_bar"] for t in all_t]
within = sum(1 for eb in entry_bars if v14_L[max(0,eb-2):min(TOTAL,eb+3)].any())
overlap = within/len(entry_bars)*100 if entry_bars else 0
print(f"  {len(entry_bars)} entries, within ±2 bar of V14 L: {within} ({overlap:.0f}%)  {'PASS (independent)' if overlap<70 else 'FAIL'}")

print(f"\nFINAL: ASC_N5_M5 L TP={TP} MH={MH}  IS ${s_is['pnl']:.0f} OOS ${s_oos['pnl']:.0f}")
