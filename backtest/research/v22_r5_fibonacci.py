"""
V22 R5: Fibonacci Retracement

演算法:
  1. N-bar fractal 偵測 confirmed pivot high/low
  2. 找最近的 swing leg (confirmed pivot_low → pivot_high 為 bullish leg)
  3. 計算回撤比例 pullback = (swing_high - close) / (swing_high - swing_low)
  4. 進場: pullback 接近 38.2% / 50% / 61.8% 時 long
     (bullish leg → 回撤買進)
     反向 (bearish leg, swing_high → swing_low): 反彈至 38.2% 等 short
  5. 無上帝視角: swing leg 在 RS pivot confirmed 後才有效
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

def build_fib_signals(N, fib_levels=[0.382, 0.5, 0.618], tol=0.03,
                      min_leg_span=20, max_leg_span=200, sig_gap=15):
    """
    At bar i, find last confirmed swing leg (pivot pair).
    Bullish leg: last confirmed pivot_low → latest confirmed pivot_high, with pivot_high being
                 most recent (up-leg). Then measure current retracement from swing high.
    Bearish leg: last confirmed pivot_high → latest confirmed pivot_low (down-leg).

    Signal when close[i-1] is within tol of a fib level AND close[i] crosses back through it
    (mean reversion to trend continuation).
    """
    ph, pl = detect_fractals(N)
    sigs = {f"fib{int(f*1000)}_L": np.zeros(TOTAL, dtype=bool) for f in fib_levels}
    sigs.update({f"fib{int(f*1000)}_S": np.zeros(TOTAL, dtype=bool) for f in fib_levels})

    last_sig = -999
    pivots = []  # (bar, price, type)
    for i in range(TOTAL):
        conf_bar = i - N
        if conf_bar >= 0:
            if ph[conf_bar]: pivots.append((conf_bar, H[conf_bar], 'H'))
            if pl[conf_bar]: pivots.append((conf_bar, L[conf_bar], 'L'))
            pivots = pivots[-20:]

        if len(pivots) < 2 or i < 52: continue
        # Find latest swing leg: most recent two opposite-type pivots
        last_pivot = pivots[-1]
        prev_pivot = None
        for p in reversed(pivots[:-1]):
            if p[2] != last_pivot[2]:
                prev_pivot = p
                break
        if prev_pivot is None: continue
        leg_span = last_pivot[0] - prev_pivot[0]
        if leg_span < min_leg_span or leg_span > max_leg_span: continue
        # Leg must be recent (last pivot within last 80 bars)
        if i - last_pivot[0] > 80: continue

        # Bullish up-leg: prev=L, last=H. Retracement from last_pivot[1] toward prev_pivot[1].
        # If current close in [L, H], compute retracement.
        if prev_pivot[2] == 'L' and last_pivot[2] == 'H':
            sw_lo = prev_pivot[1]; sw_hi = last_pivot[1]
            leg_size = sw_hi - sw_lo
            c_prev = C[i-1]; c_now = C[i]
            if leg_size > 0:
                # retracement at prev bar
                pb_prev = (sw_hi - c_prev) / leg_size
                pb_now = (sw_hi - c_now) / leg_size
                for f in fib_levels:
                    # enter long when retracement touches f level and then bounces up
                    # Detect cross: pb_prev >= f (reached retrace) and pb_now < f - 0.01 (bouncing up)
                    if pb_prev >= f - tol and pb_prev <= f + tol and pb_now < pb_prev - 0.01:
                        if i - last_sig >= sig_gap:
                            sigs[f"fib{int(f*1000)}_L"][i] = True
                            last_sig = i
                            break

        # Bearish down-leg: prev=H, last=L. Retracement (rebound) from last_pivot[1] toward prev_pivot[1].
        if prev_pivot[2] == 'H' and last_pivot[2] == 'L':
            sw_hi = prev_pivot[1]; sw_lo = last_pivot[1]
            leg_size = sw_hi - sw_lo
            c_prev = C[i-1]; c_now = C[i]
            if leg_size > 0:
                pb_prev = (c_prev - sw_lo) / leg_size
                pb_now = (c_now - sw_lo) / leg_size
                for f in fib_levels:
                    if pb_prev >= f - tol and pb_prev <= f + tol and pb_now < pb_prev - 0.01:
                        if i - last_sig >= sig_gap:
                            sigs[f"fib{int(f*1000)}_S"][i] = True
                            last_sig = i
                            break
    return sigs

# Build
signals_all = {}
for N in [3, 5]:
    sigs = build_fib_signals(N)
    for k, arr in sigs.items():
        side = "L" if k.endswith("_L") else "S"
        signals_all[f"{k}_N{N}"] = (arr, side)

print("\nSignal counts:")
for k, (arr, side) in signals_all.items():
    tot = int(arr.sum()); is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    print(f"  {k:<22} {side}: total {tot} (IS {is_c}, OOS {oos_c})")

# Grid
print("\n" + "="*100)
print("V22 R5 Fibonacci — IS grid")
print("="*100)
best = grid_evaluate(signals_all, tp_grid=[0.02, 0.025, 0.03, 0.035, 0.04],
                     mh_grid=[6, 12, 24, 48], sl_pct=0.035, cd=6, min_n=8)

if not best:
    print("\nNo IS+ configs. R5 REJECTED at stage 1.")
else:
    print(f"\n{'Signal':<22} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
    print("-"*90)
    for r in best[:20]:
        s = r["is"]
        print(f"{r['sig']:<22} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f} ${s['mdd']:>5.0f}")

    print("\n" + "="*125)
    print("Top OOS confirmation")
    print("="*125)
    confirm_oos(best, signals_all, sl_pct=0.035, cd=6, top_n=15)
