"""
V22 R3: Triangle Breakout (Ascending / Descending / Symmetric)

演算法（無上帝視角）：
  1. N-bar fractal pivots (N=3, confirmed after N bars)
  2. 取最近 M 個 pivots, 用最小二乘擬合 upper 和 lower trendline
  3. 分類:
     - Ascending triangle: upper slope ~0 (flat top), lower slope > 0 (rising lows)
     - Descending triangle: upper slope < 0, lower slope ~0
     - Symmetric: upper slope < 0, lower slope > 0 (converging)
  4. 進場: close break out of apex trendline (shift(1) 強制)
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

def fit_line(xs, ys):
    """Return (slope, intercept) via least squares"""
    if len(xs) < 2: return 0, 0
    xs = np.array(xs, dtype=float); ys = np.array(ys, dtype=float)
    n = len(xs)
    mx = xs.mean(); my = ys.mean()
    dx = xs - mx; dy = ys - my
    var = (dx**2).sum()
    if var < 1e-12: return 0, my
    slope = (dx * dy).sum() / var
    intercept = my - slope * mx
    return slope, intercept

def build_triangle_signals(N, M=4, slope_flat_thresh=0.5, min_span=20, max_span=100):
    """
    At bar i, look back at last M confirmed pivot highs and M confirmed pivot lows.
    Fit trendlines. Detect triangle. Signal when close breaks trendline.
    slope_flat_thresh: if |slope| / price * 1000 < thresh → "flat"
    """
    ph, pl = detect_fractals(N)
    asc_brk  = np.zeros(TOTAL, dtype=bool)  # L
    desc_brk = np.zeros(TOTAL, dtype=bool)  # S
    sym_brk_L = np.zeros(TOTAL, dtype=bool)
    sym_brk_S = np.zeros(TOTAL, dtype=bool)

    highs = []  # (bar, price)
    lows = []
    last_sig_bar = -999

    for i in range(TOTAL):
        conf_bar = i - N
        if conf_bar >= 0:
            if ph[conf_bar]:
                highs.append((conf_bar, H[conf_bar]))
            if pl[conf_bar]:
                lows.append((conf_bar, L[conf_bar]))
            highs = highs[-M:]
            lows = lows[-M:]

        if len(highs) < M or len(lows) < M: continue
        if i < 52: continue

        all_bars = [h[0] for h in highs] + [l[0] for l in lows]
        span = max(all_bars) - min(all_bars)
        if span < min_span or span > max_span: continue

        # Fit lines
        h_slope, h_int = fit_line([p[0] for p in highs], [p[1] for p in highs])
        l_slope, l_int = fit_line([p[0] for p in lows],  [p[1] for p in lows])

        # Current trendline values at bar i-1
        upper_i = h_slope * (i-1) + h_int
        lower_i = l_slope * (i-1) + l_int
        if upper_i <= lower_i: continue
        price_ref = C[i-1]
        h_slope_norm = abs(h_slope) / price_ref * 1000
        l_slope_norm = abs(l_slope) / price_ref * 1000
        h_flat = h_slope_norm < slope_flat_thresh
        l_flat = l_slope_norm < slope_flat_thresh

        # Classify
        tri_type = None
        if h_flat and l_slope > 0:
            tri_type = "ASC"
        elif l_flat and h_slope < 0:
            tri_type = "DESC"
        elif h_slope < 0 and l_slope > 0:
            tri_type = "SYM"

        if tri_type is None: continue
        if i - last_sig_bar < 20: continue

        # Breakout check: close[i-1] was inside, close[i] breaks
        c_prev = C[i-1]; c_now = C[i]
        # Upper trendline value at bar i
        upper_now = h_slope * i + h_int
        lower_now = l_slope * i + l_int

        if tri_type == "ASC":
            if c_prev <= upper_i and c_now > upper_now:
                asc_brk[i] = True
                last_sig_bar = i
        elif tri_type == "DESC":
            if c_prev >= lower_i and c_now < lower_now:
                desc_brk[i] = True
                last_sig_bar = i
        elif tri_type == "SYM":
            if c_prev <= upper_i and c_now > upper_now:
                sym_brk_L[i] = True
                last_sig_bar = i
            elif c_prev >= lower_i and c_now < lower_now:
                sym_brk_S[i] = True
                last_sig_bar = i

    return asc_brk, desc_brk, sym_brk_L, sym_brk_S

# Build signals
signals = {}
for N in [3, 5]:
    for M in [4, 5, 6]:
        asc, desc, symL, symS = build_triangle_signals(N, M)
        signals[f"ASC_N{N}_M{M}"]   = (asc, "L")
        signals[f"DESC_N{N}_M{M}"]  = (desc, "S")
        signals[f"SYM_L_N{N}_M{M}"] = (symL, "L")
        signals[f"SYM_S_N{N}_M{M}"] = (symS, "S")

print("\nSignal counts:")
for k, (arr, side) in signals.items():
    is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    print(f"  {k:<22} {side}: IS {is_c} | OOS {oos_c}")

# Grid search
print("\n" + "="*100)
print("V22 R3 Triangle — IS grid (TP × MH, SL=3.5%, CD=6)")
print("="*100)

best = grid_evaluate(signals, tp_grid=[0.02, 0.025, 0.03, 0.035, 0.04],
                     mh_grid=[6, 12, 24, 48], sl_pct=0.035, cd=6, min_n=6)

if not best:
    print("\nNo IS+ configs. R3 REJECTED at stage 1.")
else:
    print(f"\n{'Signal':<22} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
    print("-"*85)
    for r in best[:20]:
        s = r["is"]
        print(f"{r['sig']:<22} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f} ${s['mdd']:>5.0f}")

    print("\n" + "="*125)
    print("Top candidates OOS confirmation")
    print("="*125)
    confirm_oos(best, signals, sl_pct=0.035, cd=6, top_n=15)
