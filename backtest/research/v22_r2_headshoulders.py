"""
V22 R2: Head & Shoulders / Inverse H&S

演算法:
  1. 用 N-bar fractal 偵測 confirmed pivot high / low (N=3, 需 3 bar 後才 confirm)
  2. 維護近期 confirmed pivots 序列
  3. H&S Top 檢測: 最近 3 pivot highs (LS, Head, RS)
     - Head > LS AND Head > RS
     - LS ≈ RS (相對差 < 5%)
     - 兩者之間有兩個 troughs (neckline candidates)
     - neckline = max(T1, T2) (保守 neckline)
  4. 進場訊號: 在 RS 確認後，close 跌破 neckline → short
  5. Inverse H&S: 同理，long 在 close 升破 neckline

防上帝視角:
  - Pivot 需 confirmed (N bar 後才認定)
  - 訊號在 close[i] 跌破 neckline 時觸發，進場在 O[i+1]
  - 所有 pivot 比較只用歷史
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H, L, C, O, DT, TOTAL, IS_END, simulate, stats, grid_evaluate, confirm_oos

print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

def detect_fractals(N):
    """
    Return arrays:
      ph[i] = True if bar i is a pivot high confirmed at bar i+N
      pl[i] = True if bar i is a pivot low confirmed at bar i+N
      ph_confirmed_at[i] = the bar where ph at i-N becomes known (= i)
    """
    ph = np.zeros(TOTAL, dtype=bool)
    pl = np.zeros(TOTAL, dtype=bool)
    for i in range(N, TOTAL - N):
        is_ph = all(H[i] > H[i-k] for k in range(1, N+1)) and all(H[i] > H[i+k] for k in range(1, N+1))
        is_pl = all(L[i] < L[i-k] for k in range(1, N+1)) and all(L[i] < L[i+k] for k in range(1, N+1))
        ph[i] = is_ph; pl[i] = is_pl
    return ph, pl

def build_hs_signals(N, shoulder_tol=0.05, max_span=80, min_span=15):
    """
    Return: (hs_top_short_sig, ihs_long_sig) — boolean arrays of signal bars.
    At bar i:
      - 用所有在 bar i-N 之前 confirmed 的 pivot
      - 找最近三個 ph: LS < Head > RS, LS≈RS, 兩個 troughs 在之間
      - neckline = max(T1, T2) for H&S top; min(T1, T2) for iHS
      - 若 close[i-1] 最後一次 > neckline 而 close[i] <= neckline → short signal at bar i
        (同一 H&S pattern 只觸發一次)
    """
    ph, pl = detect_fractals(N)
    hs_top = np.zeros(TOTAL, dtype=bool)
    ihs    = np.zeros(TOTAL, dtype=bool)

    # At bar i, pivots confirmed up to bar i-N
    # Track recent confirmed pivots (bar_idx, price, type)
    highs = []  # list of (bar, price)
    lows  = []
    last_pattern_neck_top = None
    last_pattern_neck_ihs = None
    last_hs_break_bar = -999
    last_ihs_break_bar = -999

    for i in range(TOTAL):
        conf_bar = i - N
        if conf_bar >= 0:
            if ph[conf_bar]:
                highs.append((conf_bar, H[conf_bar]))
            if pl[conf_bar]:
                lows.append((conf_bar, L[conf_bar]))
            # 限制序列長度
            highs = highs[-10:]
            lows = lows[-10:]

        # ====== H&S Top (short) ======
        if len(highs) >= 3:
            ls, hd, rs = highs[-3], highs[-2], highs[-1]
            span = rs[0] - ls[0]
            if min_span <= span <= max_span:
                if hd[1] > ls[1] and hd[1] > rs[1]:
                    shoulder_diff = abs(ls[1] - rs[1]) / max(ls[1], rs[1])
                    if shoulder_diff < shoulder_tol:
                        # 兩個 troughs 在 [ls[0], rs[0]] 間
                        troughs = [lb for lb in lows if ls[0] < lb[0] < rs[0]]
                        if len(troughs) >= 2:
                            t1 = troughs[0][1]; t2 = troughs[-1][1]
                            neckline = max(t1, t2)  # 保守: 用較高的 trough
                            # 此 pattern 在 bar rs[0]+N 後可用
                            if i >= rs[0] + N and i < rs[0] + N + 30:
                                # 判斷是否剛跌破 neckline (close_prev > neckline, close_now <= neckline)
                                if i >= 1 and C[i-1] > neckline and C[i] <= neckline and i > last_hs_break_bar + 20:
                                    hs_top[i] = True
                                    last_hs_break_bar = i

        # ====== Inverse H&S (long) ======
        if len(lows) >= 3:
            ls, hd, rs = lows[-3], lows[-2], lows[-1]
            span = rs[0] - ls[0]
            if min_span <= span <= max_span:
                if hd[1] < ls[1] and hd[1] < rs[1]:
                    shoulder_diff = abs(ls[1] - rs[1]) / max(ls[1], rs[1])
                    if shoulder_diff < shoulder_tol:
                        peaks_between = [pb for pb in highs if ls[0] < pb[0] < rs[0]]
                        if len(peaks_between) >= 2:
                            p1 = peaks_between[0][1]; p2 = peaks_between[-1][1]
                            neckline = min(p1, p2)
                            if i >= rs[0] + N and i < rs[0] + N + 30:
                                if i >= 1 and C[i-1] < neckline and C[i] >= neckline and i > last_ihs_break_bar + 20:
                                    ihs[i] = True
                                    last_ihs_break_bar = i

    return hs_top, ihs

# ---- Build signals for multiple fractal N ----
signals = {}
for N in [3, 5, 7]:
    for tol in [0.03, 0.05, 0.08]:
        hs_top, ihs = build_hs_signals(N, shoulder_tol=tol)
        signals[f"HSTop_N{N}_tol{int(tol*100)}"] = (hs_top, "S")
        signals[f"iHS_N{N}_tol{int(tol*100)}"]    = (ihs, "L")

print("\nSignal counts:")
for k, (arr, side) in signals.items():
    print(f"  {k:<22} {side}: {int(arr.sum())} signals (IS: {int(arr[:IS_END].sum())}, OOS: {int(arr[IS_END:].sum())})")

# ---- Grid search ----
print("\n" + "="*100)
print("V22 R2 H&S — IS grid (TP × MH, SL=3.5%, CD=6)")
print("="*100)

best = grid_evaluate(signals, tp_grid=[0.02, 0.025, 0.03, 0.035, 0.04],
                     mh_grid=[6, 12, 24, 48], sl_pct=0.035, cd=6, min_n=6)

if not best:
    print("\nNo IS+ configs. R2 REJECTED at stage 1.")
else:
    print(f"\n{'Signal':<24} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
    print("-"*90)
    for r in best[:20]:
        s = r["is"]
        print(f"{r['sig']:<24} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f} ${s['mdd']:>5.0f}")

    print("\n" + "="*125)
    print("Top candidates OOS confirmation")
    print("="*125)
    confirm_oos(best, signals, sl_pct=0.035, cd=6, top_n=15)
