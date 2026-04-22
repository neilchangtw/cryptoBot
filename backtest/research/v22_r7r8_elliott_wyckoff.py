"""
V22 R7: Elliott Wave (簡化 3-wave impulse)
    L-H-L sequence with wave 3 breakout (H2 > H1 means wave 3 extension)
    Dow Theory 的 HH+HL confirmation
V22 R8: Wyckoff (Spring / Upthrust via wide-range high-volume bars)
    Spring: 跌破 support 後快速收回 + 大量
    Upthrust: 突破 resistance 後快速收回 + 大量
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H, L, C, O, DT, TOTAL, IS_END, simulate, stats, grid_evaluate, confirm_oos

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
V = df["volume"].values.astype(float)
print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

# ============ R7: Elliott Wave ============
# Simplified: after confirmed pivot sequence L-H-L-H (waves 1-2-3 up), enter long when
# new H higher than previous H (= wave 3 takes out wave 1).

def detect_fractals(N):
    ph = np.zeros(TOTAL, dtype=bool)
    pl = np.zeros(TOTAL, dtype=bool)
    for i in range(N, TOTAL - N):
        if all(H[i] > H[i-k] for k in range(1, N+1)) and all(H[i] > H[i+k] for k in range(1, N+1)):
            ph[i] = True
        if all(L[i] < L[i-k] for k in range(1, N+1)) and all(L[i] < L[i+k] for k in range(1, N+1)):
            pl[i] = True
    return ph, pl

def build_elliott_signals(N):
    ph, pl = detect_fractals(N)
    pivots = []
    w3_L = np.zeros(TOTAL, dtype=bool)
    w3_S = np.zeros(TOTAL, dtype=bool)
    last_sig = -999

    w1_hi = None; w1_lo = None; w2_lo = None; w2_hi = None
    for i in range(TOTAL):
        conf = i - N
        if conf >= 0:
            if ph[conf]: pivots.append((conf, H[conf], 'H'))
            if pl[conf]: pivots.append((conf, L[conf], 'L'))
            pivots = pivots[-6:]

        # Need sequence [L, H, L] (wave 0-1-2) to look for wave 3 up
        # Wave 3 is identified when price breaks above wave 1 high
        if len(pivots) >= 3:
            # Bullish impulse: pivots[-3]=L(wave 0), pivots[-2]=H(wave 1), pivots[-1]=L(wave 2)
            p0, p1, p2 = pivots[-3], pivots[-2], pivots[-1]
            if p0[2]=='L' and p1[2]=='H' and p2[2]=='L' and p2[1] > p0[1]:
                # wave 2 doesn't go below wave 0 (Elliott rule)
                # Trigger wave 3 long when close > wave 1 high
                if i >= p2[0] + N and C[i] > p1[1] and C[i-1] <= p1[1]:
                    if i - last_sig >= 20:
                        w3_L[i] = True
                        last_sig = i
            # Bearish: pivots[-3]=H, p1=L, p2=H, p2 < p0
            if p0[2]=='H' and p1[2]=='L' and p2[2]=='H' and p2[1] < p0[1]:
                if i >= p2[0] + N and C[i] < p1[1] and C[i-1] >= p1[1]:
                    if i - last_sig >= 20:
                        w3_S[i] = True
                        last_sig = i
    return w3_L, w3_S

elliott_signals = {}
for N in [3, 5, 7]:
    wl, ws = build_elliott_signals(N)
    elliott_signals[f"EW3_L_N{N}"] = (wl, "L")
    elliott_signals[f"EW3_S_N{N}"] = (ws, "S")

print("\n[R7 Elliott Wave Signal counts]")
for k, (arr, side) in elliott_signals.items():
    tot = int(arr.sum()); is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    print(f"  {k:<18} {side}: total {tot} (IS {is_c}, OOS {oos_c})")

print("\n" + "="*100)
print("R7 Elliott — IS grid")
print("="*100)
best_r7 = grid_evaluate(elliott_signals, tp_grid=[0.025, 0.035, 0.04],
                        mh_grid=[12, 24, 48], sl_pct=0.035, cd=6, min_n=10)
if not best_r7:
    print("No IS+ configs. R7 REJECTED.")
else:
    print(f"{'Signal':<18} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5}")
    for r in best_r7[:10]:
        s = r["is"]
        print(f"{r['sig']:<18} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f}")
    print("\nOOS confirmation:")
    confirm_oos(best_r7, elliott_signals, sl_pct=0.035, cd=6, top_n=8)


# ============ R8: Wyckoff Spring / Upthrust ============
# Spring (long): bar low breaks N-bar recent low, then close back above old low, with volume > avg*K
# Upthrust (short): bar high breaks N-bar recent high, then close back below old high, with volume > avg*K

def build_wyckoff_signals(look=20, vol_mult=1.5):
    rec_hi = pd.Series(H).shift(1).rolling(look).max().values
    rec_lo = pd.Series(L).shift(1).rolling(look).min().values
    vol_ma = pd.Series(V).shift(1).rolling(20).mean().values
    spring = np.zeros(TOTAL, dtype=bool)
    upthrust = np.zeros(TOTAL, dtype=bool)
    for i in range(look+1, TOTAL):
        if np.isnan(rec_lo[i]) or np.isnan(vol_ma[i]) or vol_ma[i] == 0: continue
        # Spring: low[i] < rec_lo[i] AND close[i] > rec_lo[i] AND volume high
        if L[i] < rec_lo[i] and C[i] > rec_lo[i] and V[i] > vol_ma[i] * vol_mult:
            # Signal is at bar i, enter at O[i+1]
            spring[i] = True
        # Upthrust
        if H[i] > rec_hi[i] and C[i] < rec_hi[i] and V[i] > vol_ma[i] * vol_mult:
            upthrust[i] = True
    return spring, upthrust

wyckoff_signals = {}
for look in [10, 20, 30]:
    for vm in [1.3, 1.8, 2.5]:
        sp, up = build_wyckoff_signals(look, vm)
        wyckoff_signals[f"Spring_L{look}_v{int(vm*10)}"] = (sp, "L")
        wyckoff_signals[f"Upthrust_L{look}_v{int(vm*10)}"] = (up, "S")

print("\n[R8 Wyckoff Signal counts]")
for k, (arr, side) in wyckoff_signals.items():
    tot = int(arr.sum()); is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    print(f"  {k:<26} {side}: total {tot} (IS {is_c}, OOS {oos_c})")

print("\n" + "="*100)
print("R8 Wyckoff — IS grid")
print("="*100)
best_r8 = grid_evaluate(wyckoff_signals, tp_grid=[0.02, 0.025, 0.03, 0.035],
                        mh_grid=[6, 12, 24, 48], sl_pct=0.035, cd=6, min_n=10)
if not best_r8:
    print("No IS+ configs. R8 REJECTED.")
else:
    print(f"{'Signal':<26} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5}")
    for r in best_r8[:10]:
        s = r["is"]
        print(f"{r['sig']:<26} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f}")
    print("\nOOS confirmation:")
    confirm_oos(best_r8, wyckoff_signals, sl_pct=0.035, cd=6, top_n=8)
