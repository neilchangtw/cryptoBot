"""
V10 R1b: Fast MFE/MAE Analysis
Precompute forward price arrays for vectorized TP/SL simulation.
"""
import pandas as pd
import numpy as np

# ===== Load =====
eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])
eth["ret"] = eth["close"].pct_change()
btc["ret"] = btc["close"].pct_change()
eth["tbr"] = eth["tbv"] / eth["volume"]
eth = eth.merge(btc[["datetime", "ret"]].rename(columns={"ret": "btc_ret"}), on="datetime", how="left")

# Consecutive bars
consec = np.zeros(len(eth))
count = 0
prev_up = None
for i, r in enumerate(eth["ret"]):
    if pd.isna(r):
        continue
    is_up = r > 0
    count = count + 1 if is_up == prev_up else 1
    prev_up = is_up
    consec[i] = count if is_up else -count
eth["consec"] = consec

# TBR percentile
eth["tbr_pct"] = eth["tbr"].rolling(100).rank(pct=True).shift(1)

# ===== Precompute forward high/low arrays =====
MAX_HOLD = 15
N = len(eth)
opens = eth["open"].values
highs = eth["high"].values
lows = eth["low"].values
closes = eth["close"].values

def simulate_tp_sl(signal_indices, side, tp_pct, sl_pct, max_hold):
    """Vectorized-ish TP/SL simulation."""
    trades = []
    for idx in signal_indices:
        entry_bar = idx + 1
        if entry_bar >= N:
            continue
        entry_price = opens[entry_bar]
        if np.isnan(entry_price) or entry_price <= 0:
            continue

        exit_pnl = None
        exit_reason = None
        exit_bars = 0

        for h in range(1, max_hold + 1):
            bar_idx = entry_bar + h
            if bar_idx >= N:
                break

            if side == "long":
                lo_pct = (lows[bar_idx] - entry_price) / entry_price
                hi_pct = (highs[bar_idx] - entry_price) / entry_price
                if lo_pct <= -sl_pct:
                    exit_pnl = -sl_pct
                    exit_reason = "SL"
                    exit_bars = h
                    break
                if hi_pct >= tp_pct:
                    exit_pnl = tp_pct
                    exit_reason = "TP"
                    exit_bars = h
                    break
            else:
                hi_pct = (highs[bar_idx] - entry_price) / entry_price
                lo_pct = (lows[bar_idx] - entry_price) / entry_price
                if hi_pct >= sl_pct:
                    exit_pnl = -sl_pct
                    exit_reason = "SL"
                    exit_bars = h
                    break
                if lo_pct <= -tp_pct:
                    exit_pnl = tp_pct
                    exit_reason = "TP"
                    exit_bars = h
                    break

        if exit_pnl is None:
            bar_idx = min(entry_bar + max_hold, N - 1)
            if side == "long":
                exit_pnl = (closes[bar_idx] - entry_price) / entry_price
            else:
                exit_pnl = -(closes[bar_idx] - entry_price) / entry_price
            exit_reason = "MH"
            exit_bars = max_hold

        pnl_net = exit_pnl * 4000 - 4  # $4 fee
        trades.append((pnl_net, exit_reason, exit_bars))

    return trades


def compute_mfe_mae(signal_indices, side, max_hold=15):
    """Compute MFE/MAE for signal entries."""
    mfes = []
    maes = []
    for idx in signal_indices:
        entry_bar = idx + 1
        if entry_bar >= N:
            continue
        entry_price = opens[entry_bar]
        if np.isnan(entry_price) or entry_price <= 0:
            continue

        mfe = 0.0
        mae = 0.0
        for h in range(1, max_hold + 1):
            bar_idx = entry_bar + h
            if bar_idx >= N:
                break
            if side == "long":
                hi_pct = (highs[bar_idx] - entry_price) / entry_price
                lo_pct = (lows[bar_idx] - entry_price) / entry_price
            else:
                hi_pct = -(highs[bar_idx] - entry_price) / entry_price
                lo_pct = -(lows[bar_idx] - entry_price) / entry_price
                hi_pct, lo_pct = lo_pct, hi_pct
            mfe = max(mfe, hi_pct)
            mae = min(mae, lo_pct)
        mfes.append(mfe)
        maes.append(mae)
    return np.array(mfes), np.array(maes)


# ===== Signals =====
signals = {
    "L_streak3_tbr20": ("long", np.where((eth["consec"] <= -3) & (eth["tbr_pct"] < 0.2))[0]),
    "L_streak2_tbr20": ("long", np.where((eth["consec"] <= -2) & (eth["tbr_pct"] < 0.2))[0]),
    "L_streak3": ("long", np.where(eth["consec"] <= -3)[0]),
    "L_streak2_tbr30": ("long", np.where((eth["consec"] <= -2) & (eth["tbr_pct"] < 0.3))[0]),
    "L_btc_crash15": ("long", np.where(eth["btc_ret"] < -0.015)[0]),
    "L_streak2_btcDn": ("long", np.where((eth["consec"] <= -2) & (eth["btc_ret"] < -0.005))[0]),
    "L_bigDrop2pct": ("long", np.where(eth["ret"] < -0.02)[0]),
    "S_streak3_tbr80": ("short", np.where((eth["consec"] >= 3) & (eth["tbr_pct"] > 0.8))[0]),
    "S_streak2_tbr80": ("short", np.where((eth["consec"] >= 2) & (eth["tbr_pct"] > 0.8))[0]),
    "S_streak3": ("short", np.where(eth["consec"] >= 3)[0]),
    "S_streak3_tbr70": ("short", np.where((eth["consec"] >= 3) & (eth["tbr_pct"] > 0.7))[0]),
}

print("=" * 80)
print("V10 R1b: MFE/MAE Analysis (Fast)")
print("=" * 80)

# ===== MFE/MAE =====
print("\n=== MFE/MAE Distribution (15 bar window) ===\n")
print(f"{'Signal':<22} {'N':>5} {'MFE_50':>7} {'MFE_75':>7} {'MFE_90':>7} {'MAE_50':>7} {'MAE_25':>7} {'MAE_10':>7}")
print("-" * 78)

for name, (side, indices) in signals.items():
    if len(indices) < 20:
        continue
    mfes, maes = compute_mfe_mae(indices, side)
    if len(mfes) < 20:
        continue
    print(f"{name:<22} {len(mfes):>5} "
          f"{np.median(mfes)*100:>6.2f}% {np.percentile(mfes,75)*100:>6.2f}% {np.percentile(mfes,90)*100:>6.2f}% "
          f"{np.median(maes)*100:>6.2f}% {np.percentile(maes,25)*100:>6.2f}% {np.percentile(maes,10)*100:>6.2f}%")

# ===== TP Hit Rate =====
print("\n\n=== TP Hit Rate Before SL (SL=4.5%, MaxHold=12) ===\n")
print(f"{'Signal':<22} {'N':>5}  {'TP1.0':>6} {'TP1.5':>6} {'TP2.0':>6} {'TP2.5':>6} {'TP3.0':>6} {'TP3.5':>6}")
print("-" * 75)

for name, (side, indices) in signals.items():
    if len(indices) < 20:
        continue
    rates = []
    for tp in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]:
        trades = simulate_tp_sl(indices, side, tp/100, 0.045, 12)
        if len(trades) < 20:
            rates.append(0)
            continue
        tp_count = sum(1 for t in trades if t[1] == "TP")
        rates.append(tp_count / len(trades))
    print(f"{name:<22} {len(indices):>5}  " + "  ".join(f"{r*100:>5.1f}%" for r in rates))

# ===== Grid Search (reduced) =====
print("\n\n=== TP/SL Grid (top configs per signal) ===\n")

tp_range = [1.5, 2.0, 2.5, 3.0]
sl_range = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
mh_range = [5, 8, 12]

for name, (side, indices) in signals.items():
    if len(indices) < 30:
        continue

    best = []
    for tp in tp_range:
        for sl in sl_range:
            if sl <= tp:
                continue
            for mh in mh_range:
                trades = simulate_tp_sl(indices, side, tp/100, sl/100, mh)
                if len(trades) < 20:
                    continue
                pnls = [t[0] for t in trades]
                total = sum(pnls)
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                avg = total / len(pnls)
                tp_r = sum(1 for t in trades if t[1] == "TP") / len(trades)
                sl_r = sum(1 for t in trades if t[1] == "SL") / len(trades)
                best.append((total, tp, sl, mh, len(trades), wr, avg, tp_r, sl_r))

    if not best:
        continue

    best.sort(reverse=True)
    print(f"--- {name} ---")
    print(f"{'TP%':>5} {'SL%':>5} {'MH':>3} {'N':>5} {'TotPnL':>9} {'WR%':>6} {'Avg$':>7} {'TP%':>5} {'SL%':>5}")
    for tot, tp, sl, mh, n, wr, avg, tp_r, sl_r in best[:5]:
        print(f"{tp:>5.1f} {sl:>5.1f} {mh:>3} {n:>5} {tot:>9.0f} {wr*100:>5.1f} {avg:>7.1f} {tp_r*100:>4.0f}% {sl_r*100:>4.0f}%")
    print()

print("=== DONE ===")
