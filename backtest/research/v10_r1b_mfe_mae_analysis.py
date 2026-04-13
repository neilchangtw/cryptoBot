"""
V10 R1b: MFE/MAE Analysis for Mean Reversion Entry Signals
目標：找出最佳 TP/SL 配置，使期望值 > 手續費

分析方法：
  對每個entry signal，追蹤接下來 N 根 bar 的：
  - MFE (Max Favorable Excursion): 最大浮盈
  - MAE (Max Adverse Excursion): 最大浮虧
  - TP hit rate at various levels
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

# Merge BTC
eth = eth.merge(btc[["datetime", "ret"]].rename(columns={"ret": "btc_ret"}), on="datetime", how="left")

# Consecutive bars
consec = []
count = 0
prev_up = None
for r in eth["ret"]:
    if pd.isna(r):
        consec.append(0); continue
    is_up = r > 0
    count = count + 1 if is_up == prev_up else 1
    prev_up = is_up
    consec.append(count if is_up else -count)
eth["consec"] = consec

# TBR percentile (rolling 100)
eth["tbr_pct"] = eth["tbr"].rolling(100).rank(pct=True).shift(1)

MAX_HOLD = 20  # analyze up to 20 bars ahead

def compute_mfe_mae(df, signal_mask, side, max_hold=MAX_HOLD):
    """
    For each signal bar, compute MFE and MAE over next max_hold bars.
    side: 'long' or 'short'
    Returns DataFrame with columns: mfe, mae, final_ret, bars
    """
    indices = df.index[signal_mask].tolist()
    results = []

    for idx in indices:
        if idx + max_hold + 1 >= len(df):
            continue
        entry_price = df.iloc[idx + 1]["open"]  # enter on next bar open
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        mfe = 0.0
        mae = 0.0

        for h in range(1, max_hold + 1):
            bar_idx = idx + 1 + h
            if bar_idx >= len(df):
                break

            bar = df.iloc[bar_idx]
            if side == "long":
                high_pct = (bar["high"] - entry_price) / entry_price
                low_pct = (bar["low"] - entry_price) / entry_price
                close_pct = (bar["close"] - entry_price) / entry_price
                mfe = max(mfe, high_pct)
                mae = min(mae, low_pct)
            else:  # short
                high_pct = -(bar["high"] - entry_price) / entry_price
                low_pct = -(bar["low"] - entry_price) / entry_price
                close_pct = -(bar["close"] - entry_price) / entry_price
                mfe = max(mfe, low_pct)  # short profits from low
                mae = min(mae, high_pct)  # short losses from high

        final_pct = (df.iloc[idx + 1 + min(max_hold, len(df) - idx - 2)]["close"] - entry_price) / entry_price
        if side == "short":
            final_pct = -final_pct

        results.append({"mfe": mfe, "mae": mae, "final_ret": final_pct, "idx": idx})

    return pd.DataFrame(results)


def tp_sl_simulation(df, signal_mask, side, tp_pct, sl_pct, max_hold, fee_pct=0.001):
    """
    Simulate fixed TP/SL strategy.
    Returns: trades list with pnl
    """
    indices = df.index[signal_mask].tolist()
    trades = []

    for idx in indices:
        if idx + max_hold + 1 >= len(df):
            continue
        entry_price = df.iloc[idx + 1]["open"]
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        exit_price = None
        exit_reason = None
        exit_bar = 0

        for h in range(1, max_hold + 1):
            bar_idx = idx + 1 + h
            if bar_idx >= len(df):
                break

            bar = df.iloc[bar_idx]

            if side == "long":
                # Check SL first (conservative)
                low_pct = (bar["low"] - entry_price) / entry_price
                high_pct = (bar["high"] - entry_price) / entry_price

                if low_pct <= -sl_pct:
                    exit_price = entry_price * (1 - sl_pct)
                    exit_reason = "SL"
                    exit_bar = h
                    break
                if high_pct >= tp_pct:
                    exit_price = entry_price * (1 + tp_pct)
                    exit_reason = "TP"
                    exit_bar = h
                    break
            else:  # short
                high_pct = (bar["high"] - entry_price) / entry_price
                low_pct = (bar["low"] - entry_price) / entry_price

                if high_pct >= sl_pct:
                    exit_price = entry_price * (1 + sl_pct)
                    exit_reason = "SL"
                    exit_bar = h
                    break
                if low_pct <= -tp_pct:
                    exit_price = entry_price * (1 - tp_pct)
                    exit_reason = "TP"
                    exit_bar = h
                    break

        if exit_price is None:
            # MaxHold exit
            bar_idx = min(idx + 1 + max_hold, len(df) - 1)
            exit_price = df.iloc[bar_idx]["close"]
            exit_reason = "MaxHold"
            exit_bar = max_hold

        if side == "long":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = -(exit_price - entry_price) / entry_price

        notional = 4000
        pnl_raw = pnl_pct * notional
        fee = notional * fee_pct  # $4
        pnl_net = pnl_raw - fee

        trades.append({
            "idx": idx,
            "datetime": df.iloc[idx]["datetime"],
            "entry": entry_price,
            "exit": exit_price,
            "reason": exit_reason,
            "bars": exit_bar,
            "pnl_pct": pnl_pct,
            "pnl_net": pnl_net,
            "side": side,
        })

    return pd.DataFrame(trades)


# ===== Define entry signals =====
signals = {}

# Long signals
signals["L_streak3_tbr20"] = (eth["consec"] <= -3) & (eth["tbr_pct"] < 0.2)
signals["L_streak2_tbr20"] = (eth["consec"] <= -2) & (eth["tbr_pct"] < 0.2)
signals["L_streak3"] = eth["consec"] <= -3
signals["L_streak2_tbr30"] = (eth["consec"] <= -2) & (eth["tbr_pct"] < 0.3)
signals["L_btc_crash"] = eth["btc_ret"] < -0.015  # BTC < -1.5%
signals["L_streak2_btc_dn"] = (eth["consec"] <= -2) & (eth["btc_ret"] < -0.005)

# Short signals
signals["S_streak3_tbr80"] = (eth["consec"] >= 3) & (eth["tbr_pct"] > 0.8)
signals["S_streak2_tbr80"] = (eth["consec"] >= 2) & (eth["tbr_pct"] > 0.8)
signals["S_streak3"] = eth["consec"] >= 3
signals["S_streak3_tbr70"] = (eth["consec"] >= 3) & (eth["tbr_pct"] > 0.7)

print("=" * 80)
print("V10 R1b: MFE/MAE Analysis")
print("=" * 80)

# ===== MFE Analysis =====
print("\n=== MFE/MAE Distribution (20 bar window) ===\n")
print(f"{'Signal':<25} {'N':>5} {'MFE_med':>8} {'MFE_75':>8} {'MFE_90':>8} {'MAE_med':>8} {'MAE_25':>8}")
print("-" * 80)

for name, mask in signals.items():
    side = "long" if name.startswith("L") else "short"
    mfe_df = compute_mfe_mae(eth, mask, side)
    if len(mfe_df) < 20:
        continue

    mfe_med = mfe_df["mfe"].median() * 100
    mfe_75 = mfe_df["mfe"].quantile(0.75) * 100
    mfe_90 = mfe_df["mfe"].quantile(0.90) * 100
    mae_med = mfe_df["mae"].median() * 100
    mae_25 = mfe_df["mae"].quantile(0.25) * 100

    print(f"{name:<25} {len(mfe_df):>5} {mfe_med:>7.2f}% {mfe_75:>7.2f}% {mfe_90:>7.2f}% {mae_med:>7.2f}% {mae_25:>7.2f}%")

# ===== TP/SL Grid Search =====
print("\n\n=== TP/SL Optimization Grid ===\n")
print("Testing TP: 1.0-3.5%, SL: 1.5-4.5%, MaxHold: 5-15 bars")
print("For each signal, showing top 5 configs by net PnL\n")

tp_range = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
sl_range = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
mh_range = [5, 8, 10, 12, 15]

best_configs = {}

for name, mask in signals.items():
    side = "long" if name.startswith("L") else "short"
    n_signals = mask.sum()
    if n_signals < 30:
        continue

    results = []
    for tp in tp_range:
        for sl in sl_range:
            if sl <= tp:  # SL must be wider than TP for mean reversion
                continue
            for mh in mh_range:
                trades = tp_sl_simulation(eth, mask, side, tp/100, sl/100, mh)
                if len(trades) < 20:
                    continue

                total_pnl = trades["pnl_net"].sum()
                wr = (trades["pnl_net"] > 0).mean()
                avg_pnl = trades["pnl_net"].mean()
                tp_rate = (trades["reason"] == "TP").mean()
                sl_rate = (trades["reason"] == "SL").mean()
                mh_rate = (trades["reason"] == "MaxHold").mean()

                results.append({
                    "tp": tp, "sl": sl, "mh": mh,
                    "N": len(trades), "pnl": total_pnl,
                    "wr": wr, "avg": avg_pnl,
                    "tp_rate": tp_rate, "sl_rate": sl_rate, "mh_rate": mh_rate,
                })

    if not results:
        continue

    res_df = pd.DataFrame(results).sort_values("pnl", ascending=False)
    best_configs[name] = res_df.head(5)

    print(f"\n--- {name} (total signals: {n_signals}) ---")
    print(f"{'TP%':>5} {'SL%':>5} {'MH':>4} {'N':>5} {'TotPnL':>10} {'WR%':>6} {'Avg$':>8} {'TP%':>6} {'SL%':>6} {'MH%':>6}")
    print("-" * 70)
    for _, r in res_df.head(5).iterrows():
        print(f"{r['tp']:>5.1f} {r['sl']:>5.1f} {int(r['mh']):>4} {int(r['N']):>5} {r['pnl']:>10.0f} {r['wr']*100:>5.1f} {r['avg']:>8.1f} {r['tp_rate']*100:>5.1f} {r['sl_rate']*100:>5.1f} {r['mh_rate']*100:>5.1f}")

# ===== Best overall configs =====
print("\n\n=== OVERALL BEST CONFIGS (sorted by total PnL) ===\n")
all_results = []
for name, df in best_configs.items():
    for _, r in df.iterrows():
        all_results.append({**r.to_dict(), "signal": name})

if all_results:
    all_df = pd.DataFrame(all_results).sort_values("pnl", ascending=False)
    print(f"{'Signal':<25} {'TP%':>5} {'SL%':>5} {'MH':>4} {'N':>5} {'TotPnL':>10} {'WR%':>6} {'Avg$':>8}")
    print("-" * 75)
    for _, r in all_df.head(15).iterrows():
        print(f"{r['signal']:<25} {r['tp']:>5.1f} {r['sl']:>5.1f} {int(r['mh']):>4} {int(r['N']):>5} {r['pnl']:>10.0f} {r['wr']*100:>5.1f} {r['avg']:>8.1f}")

print("\n\n=== DONE ===")
