"""
V22 R4: Harmonic Patterns (Gartley / Bat / Butterfly / Crab)

XABCD 點用 N-bar fractal confirmed 偵測
Bullish: X high → A low → B high → C low → D low (enter L at D)
Bearish: X low → A high → B low → C high → D high (enter S at D)

Ratio 定義:
  AB/XA, BC/AB, CD/BC, AD/XA

Patterns (with tolerance ±5%):
  Gartley L:  AB/XA=0.618, BC/AB=[0.382, 0.886], CD/BC=[1.272, 1.618], AD/XA=0.786
  Bat L:      AB/XA=[0.382,0.500], BC/AB=[0.382,0.886], CD/BC=[1.618,2.618], AD/XA=0.886
  Butterfly L: AB/XA=0.786, BC/AB=[0.382,0.886], CD/BC=[1.618,2.618], AD/XA=[1.272,1.618]
  Crab L:     AB/XA=[0.382,0.618], BC/AB=[0.382,0.886], CD/BC=[2.618,3.618], AD/XA=1.618

入場: D confirmed (N bar 後) → 當 bar close 站上/跌破 D 附近 trigger → O[i+1]
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H, L, C, O, DT, TOTAL, IS_END, simulate, stats, grid_evaluate, confirm_oos

print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

def detect_fractals(N):
    ph = np.zeros(TOTAL, dtype=bool)
    pl = np.zeros(TOTAL, dtype=bool)
    for i in range(N, TOTAL - N):
        if all(H[i] > H[i-k] for k in range(1, N+1)) and all(H[i] > H[i+k] for k in range(1, N+1)):
            ph[i] = True
        if all(L[i] < L[i-k] for k in range(1, N+1)) and all(L[i] < L[i+k] for k in range(1, N+1)):
            pl[i] = True
    return ph, pl

def in_range(v, lo, hi, tol=0.0):
    return v >= lo*(1-tol) and v <= hi*(1+tol)

def near(v, target, tol=0.05):
    return abs(v - target) / target < tol

PATTERNS = {
    # (name, ab_xa_range, bc_ab_range, cd_bc_range, ad_xa_range)
    "Gartley":   (0.618, 0.618, (0.382, 0.886), (1.272, 1.618), 0.786, 0.786),
    "Bat":       ((0.382, 0.500), None, (0.382, 0.886), (1.618, 2.618), 0.886, 0.886),
    "Butterfly": (0.786, 0.786, (0.382, 0.886), (1.618, 2.618), (1.272, 1.618), None),
    "Crab":      ((0.382, 0.618), None, (0.382, 0.886), (2.618, 3.618), 1.618, 1.618),
}

def check_pattern(pattern_name, ab_xa, bc_ab, cd_bc, ad_xa, tol=0.1):
    spec = PATTERNS[pattern_name]
    ab_spec = spec[0] if isinstance(spec[0], tuple) else (spec[0], spec[0])
    bc_spec = spec[2]
    cd_spec = spec[3]
    ad_spec = spec[4] if isinstance(spec[4], tuple) else (spec[4], spec[4])
    if not in_range(ab_xa, ab_spec[0], ab_spec[1], tol): return False
    if not in_range(bc_ab, bc_spec[0], bc_spec[1], tol): return False
    if not in_range(cd_bc, cd_spec[0], cd_spec[1], tol): return False
    if not in_range(ad_xa, ad_spec[0], ad_spec[1], tol): return False
    return True

def build_harmonic_signals(N, tol=0.1, max_pattern_span=200):
    """
    Bullish XABCD: lows at X, B, D; highs at A, C (relative to neighbors)
    Bearish XABCD: highs at X, B, D; lows at A, C

    Iterate over fractals. For each new confirmed fractal, check if it forms D of
    a valid harmonic pattern with earlier 4 fractals.
    """
    ph, pl = detect_fractals(N)
    # pivot list: (bar, price, type) where type in {'H', 'L'}
    pivots = []
    for i in range(TOTAL):
        if ph[i]: pivots.append((i, H[i], 'H'))
        if pl[i]: pivots.append((i, L[i], 'L'))
    pivots.sort(key=lambda x: x[0])

    # signals
    harm_L = {name: np.zeros(TOTAL, dtype=bool) for name in PATTERNS}
    harm_S = {name: np.zeros(TOTAL, dtype=bool) for name in PATTERNS}
    last_sig = -999

    # Need XABCD in alternating order. Bullish: H-L-H-L (A-B-C-D relative, X is first)
    # Actually: X is a pivot before A, pattern is X-A-B-C-D
    # For bullish L: X(H)-A(L)-B(H)-C(L)-D(L)? No. Standard:
    #   Bullish (long trigger at D): X=high, A=low, B=high, C=low, D=low (deeper than B? no)
    # Actually let me use classical: for bullish pattern enter long at D
    #   X(H) -> A(L) -> B(H) -> C(L) -> D(L where D < C)
    # For bearish (short at D):
    #   X(L) -> A(H) -> B(L) -> C(H) -> D(H where D > C)

    for di in range(4, len(pivots)):
        X = pivots[di-4]; A = pivots[di-3]; B = pivots[di-2]; C = pivots[di-1]; D = pivots[di]
        # Check span
        if D[0] - X[0] > max_pattern_span: continue
        if D[0] - X[0] < 30: continue
        # Check D bar is confirmed at D[0]+N
        signal_bar = D[0] + N  # earliest we can act
        if signal_bar >= TOTAL or signal_bar - last_sig < 30: continue

        # Bullish structure: X=H, A=L, B=H, C=L, D=L
        if X[2]=='H' and A[2]=='L' and B[2]=='H' and C[2]=='L' and D[2]=='L':
            XA = abs(X[1] - A[1])
            AB = abs(A[1] - B[1])
            BC = abs(B[1] - C[1])
            CD = abs(C[1] - D[1])
            AD = abs(A[1] - D[1])
            if XA <= 0 or AB <= 0 or BC <= 0: continue
            ab_xa = AB/XA; bc_ab = BC/AB; cd_bc = CD/BC; ad_xa = AD/XA
            for name in PATTERNS:
                if check_pattern(name, ab_xa, bc_ab, cd_bc, ad_xa, tol=tol):
                    if signal_bar < TOTAL:
                        harm_L[name][signal_bar] = True
                        last_sig = signal_bar
                        break

        # Bearish structure: X=L, A=H, B=L, C=H, D=H
        if X[2]=='L' and A[2]=='H' and B[2]=='L' and C[2]=='H' and D[2]=='H':
            XA = abs(X[1] - A[1])
            AB = abs(A[1] - B[1])
            BC = abs(B[1] - C[1])
            CD = abs(C[1] - D[1])
            AD = abs(A[1] - D[1])
            if XA <= 0 or AB <= 0 or BC <= 0: continue
            ab_xa = AB/XA; bc_ab = BC/AB; cd_bc = CD/BC; ad_xa = AD/XA
            for name in PATTERNS:
                if check_pattern(name, ab_xa, bc_ab, cd_bc, ad_xa, tol=tol):
                    if signal_bar < TOTAL:
                        harm_S[name][signal_bar] = True
                        last_sig = signal_bar
                        break

    return harm_L, harm_S

# Build signals
signals = {}
for N in [3, 5]:
    for tol in [0.10, 0.15]:
        harm_L, harm_S = build_harmonic_signals(N, tol=tol)
        for name in PATTERNS:
            signals[f"{name[:4]}_L_N{N}_tol{int(tol*100)}"] = (harm_L[name], "L")
            signals[f"{name[:4]}_S_N{N}_tol{int(tol*100)}"] = (harm_S[name], "S")

print("\nSignal counts:")
for k, (arr, side) in signals.items():
    tot = int(arr.sum()); is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    if tot > 0:
        print(f"  {k:<30} {side}: total {tot} (IS {is_c}, OOS {oos_c})")

# Combined per side
comb_L = {N: {tol: np.zeros(TOTAL, dtype=bool) for tol in [10, 15]} for N in [3, 5]}
comb_S = {N: {tol: np.zeros(TOTAL, dtype=bool) for tol in [10, 15]} for N in [3, 5]}
for k, (arr, side) in signals.items():
    parts = k.split('_')
    N = int(parts[2][1:]); tol = int(parts[3][3:])
    if side == "L": comb_L[N][tol] |= arr
    else: comb_S[N][tol] |= arr
for N in [3, 5]:
    for tol in [10, 15]:
        signals[f"ALL_L_N{N}_tol{tol}"] = (comb_L[N][tol], "L")
        signals[f"ALL_S_N{N}_tol{tol}"] = (comb_S[N][tol], "S")

# Grid
print("\n" + "="*100)
print("V22 R4 Harmonic — IS grid")
print("="*100)
best = grid_evaluate(signals, tp_grid=[0.025, 0.035, 0.04],
                     mh_grid=[12, 24, 48], sl_pct=0.035, cd=6, min_n=6)

if not best:
    print("\nNo IS+ configs. R4 REJECTED at stage 1.")
else:
    print(f"\n{'Signal':<30} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
    print("-"*90)
    for r in best[:15]:
        s = r["is"]
        print(f"{r['sig']:<30} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f} ${s['mdd']:>5.0f}")

    print("\n" + "="*125)
    print("Top OOS confirmation")
    print("="*125)
    confirm_oos(best, signals, sl_pct=0.035, cd=6, top_n=10)
