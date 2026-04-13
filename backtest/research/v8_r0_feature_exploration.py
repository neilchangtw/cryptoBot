"""
V8 Round 0: Feature Exploration
目標：理解可用特徵的統計特性和前瞻報酬預測力
重點：taker buy ratio, trade count, volume patterns, price action
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

# ===== 基礎特徵 =====
df["ret"] = df["close"].pct_change()
df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
df["range_pct"] = (df["high"] - df["low"]) / df["close"]  # bar range
df["body_pct"] = abs(df["close"] - df["open"]) / df["close"]  # body size
df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

# ===== Taker Buy Ratio (TBR) =====
df["tbr"] = df["tbv"] / df["volume"]  # taker buy ratio
df["tbr_ma3"] = df["tbr"].rolling(3).mean().shift(1)
df["tbr_ma5"] = df["tbr"].rolling(5).mean().shift(1)
df["tbr_z"] = ((df["tbr"] - df["tbr"].rolling(20).mean()) / df["tbr"].rolling(20).std()).shift(1)

# ===== Volume Features =====
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)  # volume spike
df["avg_trade_size"] = (df["qv"] / df["trades"]).shift(1)  # avg trade size in USDT
df["ats_ratio"] = (df["avg_trade_size"] / df["avg_trade_size"].rolling(20).mean()).shift(1)
df["trade_count_ratio"] = (df["trades"] / df["trades"].rolling(20).mean()).shift(1)

# ===== Price Position =====
df["range_pos_10"] = ((df["close"] - df["low"].rolling(10).min()) /
                       (df["high"].rolling(10).max() - df["low"].rolling(10).min())).shift(1)
df["range_pos_20"] = ((df["close"] - df["low"].rolling(20).min()) /
                       (df["high"].rolling(20).max() - df["low"].rolling(20).min())).shift(1)

# ===== Momentum =====
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)
df["roc_20"] = df["close"].pct_change(20).shift(1)

# ===== EMA =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)

# ===== Forward Returns =====
# For TP analysis: what's the max favorable/adverse excursion in next N bars?
for n in [6, 12, 18]:
    df[f"fwd_max_{n}"] = df["high"].rolling(n).max().shift(-n+1).shift(-1)
    df[f"fwd_min_{n}"] = df["low"].rolling(n).min().shift(-n+1).shift(-1)
    df[f"fwd_ret_{n}"] = df["close"].shift(-n) / df["close"] - 1

# For L: max upside / max downside in next N bars from next bar's open
# Approximate using next bar's open as entry
df["next_open"] = df["open"].shift(-1)
for n in [6, 12, 18]:
    # MFE for long: (highest high in next n bars - entry) / entry
    highs = pd.Series(index=df.index, dtype=float)
    lows = pd.Series(index=df.index, dtype=float)
    for i in range(len(df) - n - 1):
        window = df.iloc[i+1:i+1+n]
        highs.iloc[i] = window["high"].max()
        lows.iloc[i] = window["low"].min()
    df[f"mfe_long_{n}"] = (highs - df["next_open"]) / df["next_open"]
    df[f"mae_long_{n}"] = (df["next_open"] - lows) / df["next_open"]
    df[f"mfe_short_{n}"] = (df["next_open"] - lows) / df["next_open"]
    df[f"mae_short_{n}"] = (highs - df["next_open"]) / df["next_open"]

print("=" * 60)
print("V8 Round 0: Feature Exploration")
print("=" * 60)

# ===== 1. TBR Distribution =====
print("\n--- TBR (Taker Buy Ratio) Distribution ---")
tbr_valid = df["tbr"].dropna()
print(f"Mean: {tbr_valid.mean():.4f}")
print(f"Std:  {tbr_valid.std():.4f}")
print(f"Percentiles: 5%={tbr_valid.quantile(0.05):.4f}, 10%={tbr_valid.quantile(0.10):.4f}, "
      f"25%={tbr_valid.quantile(0.25):.4f}, 50%={tbr_valid.quantile(0.50):.4f}, "
      f"75%={tbr_valid.quantile(0.75):.4f}, 90%={tbr_valid.quantile(0.90):.4f}, "
      f"95%={tbr_valid.quantile(0.95):.4f}")

# ===== 2. TP Hit Rate Analysis =====
# Key question: given entry at next bar's open, what % of trades hit TP before SL?
print("\n--- TP Hit Rate Analysis (12-bar window) ---")
print("For LONG: TP = +X%, SL = SafeNet -4.5%")
print("For SHORT: TP = -X%, SL = SafeNet +4.5%")

valid = df.dropna(subset=["mfe_long_12", "mae_long_12"]).copy()
for tp_pct in [0.01, 0.015, 0.02, 0.025, 0.03]:
    l_hit_tp = (valid["mfe_long_12"] >= tp_pct).mean()
    s_hit_tp = (valid["mfe_short_12"] >= tp_pct).mean()
    print(f"  TP {tp_pct*100:.1f}%: L hit={l_hit_tp:.1%}, S hit={s_hit_tp:.1%}")

# ===== 3. Conditional TP Hit Rate by TBR =====
print("\n--- Conditional TP Hit Rate by TBR (12-bar, TP 1.5%) ---")
print("TBR low → selling pressure → potential L entry after exhaustion")
print("TBR high → buying pressure → potential S entry after exhaustion")

valid2 = df.dropna(subset=["tbr_ma3", "mfe_long_12", "mfe_short_12"]).copy()
tbr_bins = [(0, 0.40), (0.40, 0.44), (0.44, 0.48), (0.48, 0.52),
            (0.52, 0.56), (0.56, 0.60), (0.60, 1.0)]

print(f"\n{'TBR_MA3 Range':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10} {'L TP2.0%':>10} {'S TP2.0%':>10}")
for lo, hi in tbr_bins:
    mask = (valid2["tbr_ma3"] >= lo) & (valid2["tbr_ma3"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid2.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid2.loc[mask, "mfe_short_12"] >= 0.015).mean()
    l20 = (valid2.loc[mask, "mfe_long_12"] >= 0.02).mean()
    s20 = (valid2.loc[mask, "mfe_short_12"] >= 0.02).mean()
    print(f"  [{lo:.2f}, {hi:.2f})  {n:>6} {l15:>10.1%} {s15:>10.1%} {l20:>10.1%} {s20:>10.1%}")

# ===== 4. Conditional by Range Position =====
print("\n--- Conditional TP Hit Rate by Range Position 20 (12-bar, TP 1.5%) ---")
valid3 = df.dropna(subset=["range_pos_20", "mfe_long_12", "mfe_short_12"]).copy()
rp_bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]

print(f"\n{'RangePos20':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10} {'L TP2.0%':>10} {'S TP2.0%':>10}")
for lo, hi in rp_bins:
    mask = (valid3["range_pos_20"] >= lo) & (valid3["range_pos_20"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid3.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid3.loc[mask, "mfe_short_12"] >= 0.015).mean()
    l20 = (valid3.loc[mask, "mfe_long_12"] >= 0.02).mean()
    s20 = (valid3.loc[mask, "mfe_short_12"] >= 0.02).mean()
    print(f"  [{lo:.1f}, {hi:.1f})     {n:>6} {l15:>10.1%} {s15:>10.1%} {l20:>10.1%} {s20:>10.1%}")

# ===== 5. Conditional by ROC (momentum) =====
print("\n--- Conditional TP Hit Rate by ROC_10 (12-bar, TP 1.5%) ---")
valid4 = df.dropna(subset=["roc_10", "mfe_long_12", "mfe_short_12"]).copy()
roc_pcts = [(-999, -0.06), (-0.06, -0.04), (-0.04, -0.02), (-0.02, 0),
            (0, 0.02), (0.02, 0.04), (0.04, 0.06), (0.06, 999)]

print(f"\n{'ROC_10 Range':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10}")
for lo, hi in roc_pcts:
    mask = (valid4["roc_10"] >= lo) & (valid4["roc_10"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid4.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid4.loc[mask, "mfe_short_12"] >= 0.015).mean()
    print(f"  [{lo:>6.2f}, {hi:>5.2f})  {n:>6} {l15:>10.1%} {s15:>10.1%}")

# ===== 6. Conditional by Volume Ratio =====
print("\n--- Conditional TP Hit Rate by Volume Ratio (12-bar, TP 1.5%) ---")
valid5 = df.dropna(subset=["vol_ratio", "mfe_long_12", "mfe_short_12"]).copy()
vr_bins = [(0, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.8), (1.8, 3.0), (3.0, 999)]

print(f"\n{'VolRatio Range':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10}")
for lo, hi in vr_bins:
    mask = (valid5["vol_ratio"] >= lo) & (valid5["vol_ratio"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid5.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid5.loc[mask, "mfe_short_12"] >= 0.015).mean()
    print(f"  [{lo:>4.1f}, {hi:>4.1f})   {n:>6} {l15:>10.1%} {s15:>10.1%}")

# ===== 7. Conditional by Distance to EMA20 =====
print("\n--- Conditional TP Hit Rate by Dist to EMA20 (12-bar, TP 1.5%) ---")
valid6 = df.dropna(subset=["dist_ema20", "mfe_long_12", "mfe_short_12"]).copy()
ema_bins = [(-999, -0.04), (-0.04, -0.02), (-0.02, -0.01), (-0.01, 0),
            (0, 0.01), (0.01, 0.02), (0.02, 0.04), (0.04, 999)]

print(f"\n{'Dist EMA20':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10}")
for lo, hi in ema_bins:
    mask = (valid6["dist_ema20"] >= lo) & (valid6["dist_ema20"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid6.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid6.loc[mask, "mfe_short_12"] >= 0.015).mean()
    print(f"  [{lo:>6.2f}, {hi:>5.2f})  {n:>6} {l15:>10.1%} {s15:>10.1%}")

# ===== 8. Avg Trade Size Feature =====
print("\n--- Conditional TP Hit Rate by Avg Trade Size Ratio (12-bar, TP 1.5%) ---")
valid7 = df.dropna(subset=["ats_ratio", "mfe_long_12", "mfe_short_12"]).copy()
ats_bins = [(0, 0.7), (0.7, 0.85), (0.85, 1.0), (1.0, 1.15), (1.15, 1.3), (1.3, 999)]

print(f"\n{'ATS Ratio':<16} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10}")
for lo, hi in ats_bins:
    mask = (valid7["ats_ratio"] >= lo) & (valid7["ats_ratio"] < hi)
    n = mask.sum()
    if n < 20:
        continue
    l15 = (valid7.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid7.loc[mask, "mfe_short_12"] >= 0.015).mean()
    print(f"  [{lo:>4.1f}, {hi:>4.1f})   {n:>6} {l15:>10.1%} {s15:>10.1%}")

# ===== 9. Session Analysis =====
print("\n--- TP Hit Rate by Hour (UTC+8) ---")
valid8 = df.dropna(subset=["mfe_long_12", "mfe_short_12"]).copy()
valid8["hour"] = valid8["datetime"].dt.hour

print(f"\n{'Hour':>4} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10} {'L TP2%':>10} {'S TP2%':>10}")
for h in range(24):
    mask = valid8["hour"] == h
    n = mask.sum()
    l15 = (valid8.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid8.loc[mask, "mfe_short_12"] >= 0.015).mean()
    l20 = (valid8.loc[mask, "mfe_long_12"] >= 0.02).mean()
    s20 = (valid8.loc[mask, "mfe_short_12"] >= 0.02).mean()
    print(f"  {h:>2}  {n:>6} {l15:>10.1%} {s15:>10.1%} {l20:>10.1%} {s20:>10.1%}")

# ===== 10. Day of Week Analysis =====
print("\n--- TP Hit Rate by Day of Week ---")
valid8["dow"] = valid8["datetime"].dt.dayofweek
dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

print(f"\n{'Day':>4} {'Count':>6} {'L TP1.5%':>10} {'S TP1.5%':>10}")
for d in range(7):
    mask = valid8["dow"] == d
    n = mask.sum()
    l15 = (valid8.loc[mask, "mfe_long_12"] >= 0.015).mean()
    s15 = (valid8.loc[mask, "mfe_short_12"] >= 0.015).mean()
    print(f"  {dow_names[d]:>3}  {n:>6} {l15:>10.1%} {s15:>10.1%}")

# ===== 11. Combined: TBR + RangePos =====
print("\n--- Combined: TBR_MA3 + Range Position 20 (12-bar, TP 1.5%) ---")
valid9 = df.dropna(subset=["tbr_ma3", "range_pos_20", "mfe_long_12", "mfe_short_12"]).copy()

# L: low TBR + low range pos (sellers exhausted at range bottom)
print("\nL Candidates (low TBR + low range pos):")
for tbr_hi in [0.44, 0.46, 0.48]:
    for rp_hi in [0.2, 0.3, 0.4]:
        mask = (valid9["tbr_ma3"] < tbr_hi) & (valid9["range_pos_20"] < rp_hi)
        n = mask.sum()
        if n < 20:
            continue
        l15 = (valid9.loc[mask, "mfe_long_12"] >= 0.015).mean()
        l_mae = valid9.loc[mask, "mae_long_12"].median()
        print(f"  TBR<{tbr_hi}, RP<{rp_hi}: n={n:>5}, L_TP1.5%={l15:.1%}, med_MAE={l_mae:.2%}")

# S: high TBR + high range pos (buyers exhausted at range top)
print("\nS Candidates (high TBR + high range pos):")
for tbr_lo in [0.52, 0.54, 0.56]:
    for rp_lo in [0.6, 0.7, 0.8]:
        mask = (valid9["tbr_ma3"] > tbr_lo) & (valid9["range_pos_20"] > rp_lo)
        n = mask.sum()
        if n < 20:
            continue
        s15 = (valid9.loc[mask, "mfe_short_12"] >= 0.015).mean()
        s_mae = valid9.loc[mask, "mae_short_12"].median()
        print(f"  TBR>{tbr_lo}, RP>{rp_lo}: n={n:>5}, S_TP1.5%={s15:.1%}, med_MAE={s_mae:.2%}")

# ===== 12. Combined: ROC + TBR (divergence) =====
print("\n--- Divergence: Price down but TBR recovering / Price up but TBR dropping ---")
valid10 = df.dropna(subset=["roc_5", "tbr_ma3", "mfe_long_12", "mfe_short_12"]).copy()

# L divergence: price dropped (roc_5 < -2%) but TBR > 0.50 (buyers stepping in)
print("\nL Divergence (price down, TBR up):")
for roc_hi in [-0.02, -0.03, -0.04]:
    for tbr_lo in [0.50, 0.52, 0.54]:
        mask = (valid10["roc_5"] < roc_hi) & (valid10["tbr_ma3"] > tbr_lo)
        n = mask.sum()
        if n < 10:
            continue
        l15 = (valid10.loc[mask, "mfe_long_12"] >= 0.015).mean()
        l20 = (valid10.loc[mask, "mfe_long_12"] >= 0.02).mean()
        print(f"  ROC5<{roc_hi}, TBR>{tbr_lo}: n={n:>5}, L_TP1.5%={l15:.1%}, L_TP2%={l20:.1%}")

# S divergence: price rose (roc_5 > 2%) but TBR < 0.50 (sellers stepping in)
print("\nS Divergence (price up, TBR down):")
for roc_lo in [0.02, 0.03, 0.04]:
    for tbr_hi in [0.50, 0.48, 0.46]:
        mask = (valid10["roc_5"] > roc_lo) & (valid10["tbr_ma3"] < tbr_hi)
        n = mask.sum()
        if n < 10:
            continue
        s15 = (valid10.loc[mask, "mfe_short_12"] >= 0.015).mean()
        s20 = (valid10.loc[mask, "mfe_short_12"] >= 0.02).mean()
        print(f"  ROC5>{roc_lo}, TBR<{tbr_hi}: n={n:>5}, S_TP1.5%={s15:.1%}, S_TP2%={s20:.1%}")

print("\n" + "=" * 60)
print("Exploration complete. Use findings to formulate Round 1 hypothesis.")
print("=" * 60)
