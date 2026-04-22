"""
V22 R2 Full Audit — iHS_N5_tol3 L TP=3.5% MH=48 SL=3.5% CD=6
IS $1458 (73% WR) / OOS $448 (60% WR)
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H, L, C, O, DT, TOTAL, IS_END, simulate, stats
import importlib.util

spec = importlib.util.spec_from_file_location("r2mod",
    os.path.join(os.path.dirname(__file__), "v22_r2_headshoulders.py"))
r2mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r2mod)

N_FRACTAL = 5
TOL = 0.03
TP = 0.035; SL = 0.035; MH = 48; CD = 6

def build_ihs(N, tol):
    _, ihs = r2mod.build_hs_signals(N, shoulder_tol=tol)
    return ihs

ihs = build_ihs(N_FRACTAL, TOL)
print(f"iHS signals: total {int(ihs.sum())}, IS {int(ihs[:IS_END].sum())}, OOS {int(ihs[IS_END:].sum())}")

# G1/G2/G3 baseline
print("\n" + "="*60)
print("BASELINE (G1/G2/G3)")
print("="*60)
is_t = simulate(ihs, "L", 0, IS_END, TP, SL, MH, CD)
oos_t = simulate(ihs, "L", IS_END, TOTAL, TP, SL, MH, CD)
s_is = stats(is_t); s_oos = stats(oos_t)
print(f"  IS:  {s_is['n']}t  PnL ${s_is['pnl']:.0f}  WR {s_is['wr']:.1f}%  MDD ${s_is['mdd']:.0f}  {s_is['pos']}/{s_is['total']}mo+")
print(f"  OOS: {s_oos['n']}t  PnL ${s_oos['pnl']:.0f}  WR {s_oos['wr']:.1f}%  MDD ${s_oos['mdd']:.0f}  {s_oos['pos']}/{s_oos['total']}mo+")

combined = s_is['pnl'] + s_oos['pnl']
print(f"  Combined PnL: ${combined:.0f}")

# G4 neighborhood stability
print("\n" + "="*60)
print("G4: Neighborhood Stability (TP × MH)")
print("="*60)
print(f"{'TP':>6} {'MH':>4} {'IS n':>5} {'IS PnL':>8} {'OOS PnL':>8}")
for tp_p in [0.030, 0.035, 0.040]:
    for mh_p in [36, 48, 60]:
        ti = simulate(ihs, "L", 0, IS_END, tp_p, SL, mh_p, CD)
        to = simulate(ihs, "L", IS_END, TOTAL, tp_p, SL, mh_p, CD)
        si = stats(ti); so = stats(to)
        marker = " <--" if tp_p == TP and mh_p == MH else ""
        print(f"{tp_p:>6.3f} {mh_p:>4} {si['n']:>5} ${si['pnl']:>7.0f} ${so['pnl']:>7.0f}{marker}")

# G4b: N_fractal × tol grid
print(f"\n{'N':>4} {'tol':>5} {'IS n':>5} {'IS PnL':>8} {'OOS PnL':>8}")
for nn in [3, 5, 7]:
    for tt in [0.02, 0.03, 0.05, 0.08]:
        ih = build_ihs(nn, tt)
        ti = simulate(ih, "L", 0, IS_END, TP, SL, MH, CD)
        to = simulate(ih, "L", IS_END, TOTAL, TP, SL, MH, CD)
        si = stats(ti); so = stats(to)
        marker = " <--" if nn == N_FRACTAL and tt == TOL else ""
        print(f"{nn:>4} {tt:>5.2f} {si['n']:>5} ${si['pnl']:>7.0f} ${so['pnl']:>7.0f}{marker}")

# G6 Swap Test
print("\n" + "="*60)
print("G6: Swap Test (IS<>OOS degradation)")
print("="*60)
is_pnl = s_is['pnl']; oos_pnl = s_oos['pnl']
fwd = (is_pnl - oos_pnl) / is_pnl * 100 if is_pnl else 0
bwd = (oos_pnl - is_pnl) / oos_pnl * 100 if oos_pnl else 0
print(f"  Forward (IS→OOS): {fwd:+.1f}%  {'PASS' if abs(fwd) < 50 else 'FAIL'} (abs < 50%)")
print(f"  Backward (OOS→IS): {bwd:+.1f}%  {'PASS' if abs(bwd) < 50 else 'FAIL'} (abs < 50%)")

# G7 Walk-Forward 6 windows
print("\n" + "="*60)
print("G7: Walk-Forward (6 windows)")
print("="*60)
win_size = TOTAL // 6
wf_pnls = []; wf_n = []
for w in range(6):
    s_b = w*win_size; e_b = (w+1)*win_size if w<5 else TOTAL
    t = simulate(ihs, "L", s_b, e_b, TP, SL, MH, CD)
    sw = stats(t)
    print(f"  W{w+1} [{s_b}:{e_b}]: {sw['n']}t PnL ${sw['pnl']:.0f} WR {sw['wr']:.0f}%")
    wf_pnls.append(sw['pnl']); wf_n.append(sw['n'])
pos = sum(1 for p in wf_pnls if p > 0)
has_n = sum(1 for n in wf_n if n > 0)
print(f"  Positive: {pos}/{has_n} windows")
print(f"  {'PASS' if pos >= 4 else 'FAIL'} (>=4/6)")

# G8 Time Reversal
print("\n" + "="*60)
print("G8: Time Reversal")
print("="*60)
# Reverse data, recompute indicators, simulate
H_orig = H.copy(); L_orig = L.copy(); C_orig = C.copy(); O_orig = O.copy()
H_rev = H_orig[::-1].copy()
L_rev = L_orig[::-1].copy()
C_rev = C_orig[::-1].copy()
O_rev = O_orig[::-1].copy()

# Monkey-patch r2mod to use reversed data
import v22_common as vc
vc.H = H_rev; vc.L = L_rev; vc.C = C_rev; vc.O = O_rev
r2mod.H = H_rev; r2mod.L = L_rev; r2mod.C = C_rev; r2mod.O = O_rev

ihs_rev = build_ihs(N_FRACTAL, TOL)
t_rev = simulate(ihs_rev, "L", 0, TOTAL, TP, SL, MH, CD)
s_rev = stats(t_rev)
print(f"  Original combined: ${combined:.0f}")
print(f"  Reversed: {s_rev['n']}t PnL ${s_rev['pnl']:.0f}")
print(f"  {'PASS (reversed positive)' if s_rev['pnl'] > 0 else 'FAIL (reversed negative, regime-dependent)'}")

# Restore
vc.H = H_orig; vc.L = L_orig; vc.C = C_orig; vc.O = O_orig
r2mod.H = H_orig; r2mod.L = L_orig; r2mod.C = C_orig; r2mod.O = O_orig

# G9 Remove-best-month
print("\n" + "="*60)
print("G9: Remove-Best-Month")
print("="*60)
all_t = is_t + oos_t
monthly = {}
for t in all_t:
    monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
if monthly:
    best_mo = max(monthly, key=monthly.get)
    best_pnl = monthly[best_mo]
    remaining = sum(v for k,v in monthly.items() if k != best_mo)
    print(f"  Best month: {best_mo} ${best_pnl:.0f}")
    print(f"  Full: ${sum(monthly.values()):.0f}  Without best: ${remaining:.0f}")
    print(f"  {'PASS' if remaining > 0 else 'FAIL'}")

# V14 overlap check
print("\n" + "="*60)
print("V14 Breakout Overlap Check")
print("="*60)
def gk_pctile(o, h, l, c, n_short=5, n_long=20, pw=100):
    ln_hl = np.log(h/l); ln_co = np.log(c/o)
    gk = 0.5*ln_hl**2 - (2*np.log(2)-1)*ln_co**2
    gks = pd.Series(gk).rolling(n_short).mean().values
    gkl = pd.Series(gk).rolling(n_long).mean().values
    ratio = gks / gkl
    ratio_s = np.full_like(ratio, np.nan)
    ratio_s[1:] = ratio[:-1]
    pct = np.full(len(ratio), np.nan)
    for i in range(pw-1, len(ratio)):
        w = ratio_s[i-pw+1:i+1]
        valid = w[~np.isnan(w)]
        if len(valid) > 1:
            pct[i] = (valid < valid[-1]).sum() / (len(valid)-1) * 100
    return pct

gp = gk_pctile(O, H, L, C)
c_s = pd.Series(C)
bo_up = (c_s > c_s.shift(1).rolling(15).max()).values
v14_L_sig = (gp < 25) & bo_up

ihs_entry_bars = [t["entry_bar"] for t in is_t + oos_t]
within_2 = 0
for eb in ihs_entry_bars:
    window = v14_L_sig[max(0,eb-2):min(TOTAL,eb+3)]
    if window.any():
        within_2 += 1
overlap = within_2 / len(ihs_entry_bars) * 100 if ihs_entry_bars else 0
print(f"  iHS entries: {len(ihs_entry_bars)}")
print(f"  Within ±2 bar of V14 L signal: {within_2} ({overlap:.0f}%)")
print(f"  {'PASS (independent edge)' if overlap < 70 else 'FAIL (V14 overlap)'}")

# Final
print("\n" + "="*70)
print("FINAL AUDIT SUMMARY")
print("="*70)
print(f"  Candidate: iHS N={N_FRACTAL} tol={TOL} TP={TP} MH={MH} SL={SL} CD={CD}")
print(f"  Sample: IS {s_is['n']}t + OOS {s_oos['n']}t = {s_is['n']+s_oos['n']}t (sparse warning)")
print(f"  IS:  ${s_is['pnl']:.0f} (WR {s_is['wr']:.0f}%)")
print(f"  OOS: ${s_oos['pnl']:.0f} (WR {s_oos['wr']:.0f}%)")
