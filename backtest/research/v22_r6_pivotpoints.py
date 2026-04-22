"""
V22 R6: Classical Pivot Points

日 / 週 pivot point:
  PP = (H + L + C) / 3
  R1 = 2*PP - L,  S1 = 2*PP - H
  R2 = PP + (H-L), S2 = PP - (H-L)
  R3 = H + 2*(PP-L), S3 = L - 2*(H-PP)

訊號:
  - close break above R1/R2/R3 (long)
  - close break below S1/S2/S3 (short)
  - 反轉版本: close touch S1 (long mean-reversion)
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from v22_common import H, L, C, O, DT, TOTAL, IS_END, simulate, stats, grid_evaluate, confirm_oos

print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

df = pd.DataFrame({"H": H, "L": L, "C": C, "O": O, "datetime": DT})
df["datetime"] = pd.to_datetime(df["datetime"])
df["date"] = df["datetime"].dt.date
df["week"] = df["datetime"].dt.to_period("W")

# Daily pivot
daily = df.groupby("date").agg(dH=("H","max"), dL=("L","min"), dC=("C","last")).reset_index()
daily["PP"] = (daily["dH"] + daily["dL"] + daily["dC"]) / 3
daily["R1"] = 2*daily["PP"] - daily["dL"]
daily["S1"] = 2*daily["PP"] - daily["dH"]
daily["R2"] = daily["PP"] + (daily["dH"] - daily["dL"])
daily["S2"] = daily["PP"] - (daily["dH"] - daily["dL"])
daily["prev_PP"] = daily["PP"].shift(1)
daily["prev_R1"] = daily["R1"].shift(1)
daily["prev_S1"] = daily["S1"].shift(1)
daily["prev_R2"] = daily["R2"].shift(1)
daily["prev_S2"] = daily["S2"].shift(1)
df_m = df.merge(daily[["date","prev_PP","prev_R1","prev_S1","prev_R2","prev_S2"]],
                on="date", how="left")

# Weekly pivot
weekly = df.groupby("week").agg(wH=("H","max"), wL=("L","min"), wC=("C","last")).reset_index()
weekly["wPP"] = (weekly["wH"] + weekly["wL"] + weekly["wC"]) / 3
weekly["wR1"] = 2*weekly["wPP"] - weekly["wL"]
weekly["wS1"] = 2*weekly["wPP"] - weekly["wH"]
weekly["wR2"] = weekly["wPP"] + (weekly["wH"] - weekly["wL"])
weekly["wS2"] = weekly["wPP"] - (weekly["wH"] - weekly["wL"])
weekly["prev_wPP"] = weekly["wPP"].shift(1)
weekly["prev_wR1"] = weekly["wR1"].shift(1)
weekly["prev_wS1"] = weekly["wS1"].shift(1)
weekly["prev_wR2"] = weekly["wR2"].shift(1)
weekly["prev_wS2"] = weekly["wS2"].shift(1)
df_m = df_m.merge(weekly[["week","prev_wPP","prev_wR1","prev_wS1","prev_wR2","prev_wS2"]],
                  on="week", how="left")

# Shift close by 1 (avoid look-ahead at current bar)
c_prev = np.roll(C, 1); c_prev[0] = C[0]
c_prev2 = np.roll(C, 2); c_prev2[:2] = C[:2]

# Daily signals
def first_cross_up(c_prev, c_prev2, level):
    """Returns boolean: close[i-1] > level AND close[i-2] <= level"""
    arr = (c_prev > level) & (c_prev2 <= level)
    return arr.astype(bool) & ~np.isnan(level)

def first_cross_dn(c_prev, c_prev2, level):
    arr = (c_prev < level) & (c_prev2 >= level)
    return arr.astype(bool) & ~np.isnan(level)

sigs = {}
for lvl_name, lvl_col, side_brk in [
    ("dR1", "prev_R1", "L"), ("dR2", "prev_R2", "L"),
    ("dS1", "prev_S1", "S"), ("dS2", "prev_S2", "S"),
    ("dPP", "prev_PP", "L"),
    ("wR1", "prev_wR1", "L"), ("wR2", "prev_wR2", "L"),
    ("wS1", "prev_wS1", "S"), ("wS2", "prev_wS2", "S"),
    ("wPP", "prev_wPP", "L"),
]:
    level = df_m[lvl_col].values
    if side_brk == "L":
        sig = first_cross_up(c_prev, c_prev2, level)
    else:
        sig = first_cross_dn(c_prev, c_prev2, level)
    sig = np.nan_to_num(sig, nan=0).astype(bool)
    sigs[f"brk_{lvl_name}"] = (sig, side_brk)

# Also mean-reversion: touch S1 → long, touch R1 → short
# ... using bar low touching prev S1 level (within 0.5%)
for lvl_name, lvl_col, side_rev in [
    ("dS1_rev", "prev_S1", "L"), ("dR1_rev", "prev_R1", "S"),
    ("wS1_rev", "prev_wS1", "L"), ("wR1_rev", "prev_wR1", "S"),
]:
    level = df_m[lvl_col].values
    if side_rev == "L":
        # Low touches level (from above) and close is above
        touch = (L <= level * 1.005) & (C > level)
        # require previous bar was above level (so we're crossing back up)
        prev_above = np.roll(C > level, 1)
        prev_above[0] = False
        sig = touch & prev_above
    else:
        touch = (H >= level * 0.995) & (C < level)
        prev_below = np.roll(C < level, 1)
        prev_below[0] = False
        sig = touch & prev_below
    sig = np.nan_to_num(sig, nan=0).astype(bool)
    sigs[lvl_name] = (sig, side_rev)

print("\nSignal counts:")
for k, (arr, side) in sigs.items():
    tot = int(arr.sum())
    is_c = int(arr[:IS_END].sum()); oos_c = int(arr[IS_END:].sum())
    print(f"  {k:<15} {side}: total {tot} (IS {is_c}, OOS {oos_c})")

# Grid
print("\n" + "="*100)
print("V22 R6 Pivot Points — IS grid")
print("="*100)
best = grid_evaluate(sigs, tp_grid=[0.02, 0.025, 0.03, 0.035],
                     mh_grid=[6, 12, 24, 48], sl_pct=0.035, cd=6, min_n=10)

if not best:
    print("\nNo IS+ configs. R6 REJECTED at stage 1.")
else:
    print(f"\n{'Signal':<18} {'Side':<4} {'TP':>5} {'MH':>4} {'n':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
    print("-"*85)
    for r in best[:15]:
        s = r["is"]
        print(f"{r['sig']:<18} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {s['n']:>4} ${s['pnl']:>6.0f} {s['wr']:>4.1f}% {s['pf']:>5.2f} ${s['mdd']:>5.0f}")

    print("\n" + "="*125)
    print("Top OOS confirmation")
    print("="*125)
    confirm_oos(best, sigs, sl_pct=0.035, cd=6, top_n=12)
