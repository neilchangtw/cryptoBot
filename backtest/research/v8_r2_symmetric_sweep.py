"""
V8 Round 2: Symmetric TP/SL Sweep
Test multiple entry signals with symmetric 2% TP / 2% SL
Goal: find signals that give WR >= 70% with enough trades
"""
import pandas as pd
import numpy as np
import sys
import io

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

# ===== Indicators (all shifted) =====
df["ret"] = df["close"].pct_change()
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["roc_3"] = df["close"].pct_change(3).shift(1)
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)
df["roc_20"] = df["close"].pct_change(20).shift(1)

# TBR
df["tbr"] = df["tbv"] / df["volume"]
df["tbr_ma3"] = df["tbr"].rolling(3).mean().shift(1)
df["tbr_ma5"] = df["tbr"].rolling(5).mean().shift(1)

# Volume
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)

# Range position
df["range_pos_10"] = ((df["close"] - df["low"].rolling(10).min()) /
                       (df["high"].rolling(10).max() - df["low"].rolling(10).min())).shift(1)
df["range_pos_20"] = ((df["close"] - df["low"].rolling(20).min()) /
                       (df["high"].rolling(20).max() - df["low"].rolling(20).min())).shift(1)

# Volatility
df["atr14"] = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs()
], axis=1).max(axis=1).rolling(14).mean()
df["atr_pct"] = (df["atr14"] / df["close"]).shift(1)

# Consecutive direction
df["up"] = (df["close"] > df["close"].shift(1)).astype(int)
df["consec_down"] = 0
for i in range(1, len(df)):
    if df.iloc[i]["up"] == 0:
        df.iloc[i, df.columns.get_loc("consec_down")] = df.iloc[i-1]["consec_down"] + 1
df["consec_down_s"] = df["consec_down"].shift(1)

df["consec_up"] = 0
for i in range(1, len(df)):
    if df.iloc[i]["up"] == 1:
        df.iloc[i, df.columns.get_loc("consec_up")] = df.iloc[i-1]["consec_up"] + 1
df["consec_up_s"] = df["consec_up"].shift(1)

# Session
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek

# Pullback in trend
df["in_uptrend"] = (df["roc_20"] > 0).shift(1)
df["in_downtrend"] = (df["roc_20"] < 0).shift(1)
df["pullback_in_up"] = df["in_uptrend"] & (df["roc_3"].shift(1) < -0.005)  # shift(1) already in roc_3
df["bounce_in_down"] = df["in_downtrend"] & (df["roc_3"].shift(1) > 0.005)

# ===== Backtest Engine =====
NOTIONAL = 4000
FEE = 4.0
SAFENET_PCT = 0.045
SLIPPAGE_FACTOR = 0.25
WARMUP = 150

split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)
BLOCK_DAYS = {5, 6}


def run_backtest(long_signal_func, short_signal_func, tp_pct, sl_pct, max_hold,
                 max_same_l=2, max_same_s=2, max_total=3, exit_cd=3,
                 l_block_hours=None, s_allow_hours=None):
    """Run a full backtest with given parameters."""
    if l_block_hours is None:
        l_block_hours = {0, 1, 2, 3}
    if s_allow_hours is None:
        s_allow_hours = set(range(11, 22))

    trades = []
    open_positions = []
    equity = 1000.0
    peak_equity = 1000.0
    max_dd = 0.0
    daily_pnl = 0.0
    monthly_pnl = 0.0
    consec_losses = 0
    cooldown_until = 0
    current_month = None
    current_date = None
    last_exit_l = -999
    last_exit_s = -999

    for i in range(WARMUP, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        bar_dt = bar["datetime"]
        bar_month = bar_dt.strftime("%Y-%m")
        bar_date = bar_dt.date()

        if bar_date != current_date:
            daily_pnl = 0.0
            current_date = bar_date
        if bar_month != current_month:
            monthly_pnl = 0.0
            current_month = bar_month

        # Exit check
        for pos in list(open_positions):
            exit_price = None
            exit_reason = None
            hold_bars = i - pos["entry_bar"]

            # 1. Hard SL (before SafeNet)
            if pos["side"] == "long":
                sl_level = pos["entry"] * (1 - sl_pct)
                if bar["low"] <= sl_level:
                    # Apply slippage: some bars gap through SL
                    exit_price = max(sl_level, bar["low"])  # best case: exit at SL
                    exit_reason = "sl"
            else:
                sl_level = pos["entry"] * (1 + sl_pct)
                if bar["high"] >= sl_level:
                    exit_price = min(sl_level, bar["high"])
                    exit_reason = "sl"

            # 2. SafeNet (if SL not hit, check SafeNet)
            if exit_price is None:
                if pos["side"] == "long":
                    sn_level = pos["entry"] * (1 - SAFENET_PCT)
                    if bar["low"] <= sn_level:
                        exit_price = sn_level - (sn_level - bar["low"]) * SLIPPAGE_FACTOR
                        exit_reason = "safenet"
                else:
                    sn_level = pos["entry"] * (1 + SAFENET_PCT)
                    if bar["high"] >= sn_level:
                        exit_price = sn_level + (bar["high"] - sn_level) * SLIPPAGE_FACTOR
                        exit_reason = "safenet"

            # 3. TP
            if exit_price is None:
                if pos["side"] == "long":
                    tp_level = pos["entry"] * (1 + tp_pct)
                    if bar["high"] >= tp_level:
                        exit_price = tp_level
                        exit_reason = "tp"
                else:
                    tp_level = pos["entry"] * (1 - tp_pct)
                    if bar["low"] <= tp_level:
                        exit_price = tp_level
                        exit_reason = "tp"

            # 4. Time stop
            if exit_price is None and hold_bars >= max_hold:
                exit_price = bar["close"]
                exit_reason = "time_stop"

            if exit_price is not None:
                if pos["side"] == "long":
                    pnl = (exit_price - pos["entry"]) / pos["entry"] * NOTIONAL - FEE
                else:
                    pnl = (pos["entry"] - exit_price) / pos["entry"] * NOTIONAL - FEE

                trades.append({
                    "entry_bar": pos["entry_bar"],
                    "exit_bar": i,
                    "side": pos["side"],
                    "entry": pos["entry"],
                    "exit": exit_price,
                    "pnl": pnl,
                    "reason": exit_reason,
                    "hold_bars": hold_bars,
                    "entry_dt": pos["entry_dt"],
                    "exit_dt": bar_dt,
                })
                open_positions.remove(pos)

                equity += pnl
                daily_pnl += pnl
                monthly_pnl += pnl
                peak_equity = max(peak_equity, equity)
                max_dd = max(max_dd, peak_equity - equity)

                if pnl < 0:
                    consec_losses += 1
                    if consec_losses >= 5:
                        cooldown_until = i + 24
                else:
                    consec_losses = 0

                if pos["side"] == "long":
                    last_exit_l = i
                else:
                    last_exit_s = i

        # Risk checks
        if daily_pnl <= -300 or monthly_pnl <= -500 or i < cooldown_until:
            continue
        if bar["dow"] in BLOCK_DAYS:
            continue

        n_long = sum(1 for p in open_positions if p["side"] == "long")
        n_short = sum(1 for p in open_positions if p["side"] == "short")
        n_total = n_long + n_short

        if n_total >= max_total:
            continue

        entry_price = next_bar["open"]

        # L Entry
        if (n_long < max_same_l and
            bar["hour"] not in l_block_hours and
            i - last_exit_l >= exit_cd and
            long_signal_func(bar, i)):
            open_positions.append({
                "side": "long", "entry": entry_price,
                "entry_bar": i + 1, "entry_dt": next_bar["datetime"],
            })
            n_long += 1
            n_total += 1

        # S Entry
        if (n_short < max_same_s and
            n_total < max_total and
            bar["hour"] in s_allow_hours and
            i - last_exit_s >= exit_cd and
            short_signal_func(bar, i)):
            open_positions.append({
                "side": "short", "entry": entry_price,
                "entry_bar": i + 1, "entry_dt": next_bar["datetime"],
            })

    # Close remaining
    last_bar = df.iloc[-1]
    for pos in list(open_positions):
        if pos["side"] == "long":
            pnl = (last_bar["close"] - pos["entry"]) / pos["entry"] * NOTIONAL - FEE
        else:
            pnl = (pos["entry"] - last_bar["close"]) / pos["entry"] * NOTIONAL - FEE
        trades.append({
            "entry_bar": pos["entry_bar"], "exit_bar": len(df)-1,
            "side": pos["side"], "entry": pos["entry"], "exit": last_bar["close"],
            "pnl": pnl, "reason": "eod", "hold_bars": len(df)-1-pos["entry_bar"],
            "entry_dt": pos["entry_dt"], "exit_dt": last_bar["datetime"],
        })
        equity += pnl

    return pd.DataFrame(trades), equity, max_dd


def analyze(trade_df, name):
    """Quick analysis of a strategy."""
    if len(trade_df) == 0:
        print(f"  {name}: NO TRADES")
        return

    trade_df["entry_dt"] = pd.to_datetime(trade_df["entry_dt"])
    is_mask = trade_df["entry_dt"] < split_date
    oos_mask = ~is_mask

    for label, mask in [("IS", is_mask), ("OOS", oos_mask)]:
        t = trade_df[mask]
        if len(t) == 0:
            continue
        lt = t[t["side"] == "long"]
        st = t[t["side"] == "short"]

        def s(df, tag):
            if len(df) == 0:
                return f"{tag}: 0t"
            n = len(df)
            pnl = df["pnl"].sum()
            wr = (df["pnl"] > 0).mean()
            wins = df[df["pnl"] > 0]["pnl"]
            losses = df[df["pnl"] <= 0]["pnl"]
            pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 999
            return f"{tag}: {n}t ${pnl:.0f} PF {pf:.2f} WR {wr:.1%}"

        print(f"  {label} {s(t, 'ALL')} | {s(lt, 'L')} | {s(st, 'S')}")

    # OOS monthly breakdown
    oos = trade_df[oos_mask]
    if len(oos) > 0:
        oos_copy = oos.copy()
        oos_copy["month"] = oos_copy["entry_dt"].dt.strftime("%Y-%m")
        months = sorted(oos_copy["month"].unique())

        # Check gates
        l_oos = oos_copy[oos_copy["side"] == "long"]
        s_oos = oos_copy[oos_copy["side"] == "short"]

        l_monthly = l_oos.groupby("month").agg(
            n=("pnl", "count"), wr=("pnl", lambda x: (x>0).mean()), pnl=("pnl", "sum"))
        s_monthly = s_oos.groupby("month").agg(
            n=("pnl", "count"), wr=("pnl", lambda x: (x>0).mean()), pnl=("pnl", "sum"))

        if len(l_monthly) > 0:
            l_min_pnl = l_monthly["pnl"].min()
            l_min_wr = l_monthly["wr"].min()
            print(f"  L monthly: min_pnl=${l_min_pnl:.0f}, min_wr={l_min_wr:.1%}, months={len(l_monthly)}")
        if len(s_monthly) > 0:
            s_min_pnl = s_monthly["pnl"].min()
            s_min_wr = s_monthly["wr"].min()
            print(f"  S monthly: min_pnl=${s_min_pnl:.0f}, min_wr={s_min_wr:.1%}, months={len(s_monthly)}")

        # Exit reason distribution (OOS)
        reasons = oos.groupby("reason")["pnl"].agg(["count", "mean"])
        exits_str = " | ".join(f"{r}: {int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
        print(f"  Exits: {exits_str}")


# ===== Strategy Definitions =====
print("=" * 80)
print("V8 Round 2: Symmetric TP/SL Sweep")
print("=" * 80)

# --- Strategy A: Mean Reversion (dist_ema20 extreme) ---
print("\n--- A: Mean Reversion (dist EMA20) ---")
for dist_l, dist_s in [(-0.015, 0.015), (-0.02, 0.02), (-0.025, 0.025), (-0.03, 0.03)]:
    for tp, sl in [(0.015, 0.015), (0.02, 0.02), (0.015, 0.02)]:
        name = f"MR dist_l<{dist_l} dist_s>{dist_s} TP{tp*100:.1f}/SL{sl*100:.1f}"
        print(f"\n[{name}]")
        tdf, eq, mdd = run_backtest(
            long_signal_func=lambda bar, i, d=dist_l: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < d,
            short_signal_func=lambda bar, i, d=dist_s: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > d,
            tp_pct=tp, sl_pct=sl, max_hold=12
        )
        analyze(tdf, name)

# --- Strategy B: Pullback in Trend ---
print("\n\n--- B: Pullback in Trend ---")
for pb_pct in [0.005, 0.01, 0.015]:
    for tp, sl in [(0.015, 0.015), (0.02, 0.02)]:
        name = f"PB pullback>{pb_pct*100:.1f}% TP{tp*100:.1f}/SL{sl*100:.1f}"
        print(f"\n[{name}]")
        tdf, eq, mdd = run_backtest(
            long_signal_func=lambda bar, i, pb=pb_pct: (
                not np.isnan(bar["roc_20"]) and bar["roc_20"] > 0 and
                not np.isnan(bar["roc_3"]) and bar["roc_3"] < -pb
            ),
            short_signal_func=lambda bar, i, pb=pb_pct: (
                not np.isnan(bar["roc_20"]) and bar["roc_20"] < 0 and
                not np.isnan(bar["roc_3"]) and bar["roc_3"] > pb
            ),
            tp_pct=tp, sl_pct=sl, max_hold=12
        )
        analyze(tdf, name)

# --- Strategy C: Consecutive Bars (momentum exhaustion) ---
print("\n\n--- C: Consecutive Bars Reversal ---")
for consec_n in [3, 4, 5]:
    for tp, sl in [(0.015, 0.015), (0.02, 0.02)]:
        name = f"ConsecRev {consec_n}bars TP{tp*100:.1f}/SL{sl*100:.1f}"
        print(f"\n[{name}]")
        tdf, eq, mdd = run_backtest(
            long_signal_func=lambda bar, i, n=consec_n: (
                not np.isnan(bar["consec_down_s"]) and bar["consec_down_s"] >= n
            ),
            short_signal_func=lambda bar, i, n=consec_n: (
                not np.isnan(bar["consec_up_s"]) and bar["consec_up_s"] >= n
            ),
            tp_pct=tp, sl_pct=sl, max_hold=12
        )
        analyze(tdf, name)

# --- Strategy D: Range Position Extreme ---
print("\n\n--- D: Range Position Extreme ---")
for rp_l, rp_s in [(0.1, 0.9), (0.15, 0.85), (0.2, 0.8)]:
    for tp, sl in [(0.015, 0.015), (0.02, 0.02)]:
        name = f"RP L<{rp_l} S>{rp_s} TP{tp*100:.1f}/SL{sl*100:.1f}"
        print(f"\n[{name}]")
        tdf, eq, mdd = run_backtest(
            long_signal_func=lambda bar, i, rp=rp_l: (
                not np.isnan(bar["range_pos_20"]) and bar["range_pos_20"] < rp
            ),
            short_signal_func=lambda bar, i, rp=rp_s: (
                not np.isnan(bar["range_pos_20"]) and bar["range_pos_20"] > rp
            ),
            tp_pct=tp, sl_pct=sl, max_hold=12
        )
        analyze(tdf, name)

# --- Strategy E: Volume Spike + Direction ---
print("\n\n--- E: Volume Spike + Direction ---")
for vol_thresh in [1.5, 2.0, 2.5]:
    name = f"VolSpike>{vol_thresh} TP2/SL2"
    print(f"\n[{name}]")
    tdf, eq, mdd = run_backtest(
        long_signal_func=lambda bar, i, v=vol_thresh: (
            not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v and
            not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01
        ),
        short_signal_func=lambda bar, i, v=vol_thresh: (
            not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v and
            not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01
        ),
        tp_pct=0.02, sl_pct=0.02, max_hold=12
    )
    analyze(tdf, name)

# --- Strategy F: TBR Divergence ---
print("\n\n--- F: TBR Divergence ---")
for roc_thresh in [0.02, 0.03]:
    for tbr_thresh in [0.50, 0.52]:
        name = f"TBR_Div ROC{roc_thresh} TBR{tbr_thresh}"
        print(f"\n[{name}]")
        tdf, eq, mdd = run_backtest(
            long_signal_func=lambda bar, i, r=roc_thresh, t=tbr_thresh: (
                not np.isnan(bar["roc_5"]) and bar["roc_5"] < -r and
                not np.isnan(bar["tbr_ma3"]) and bar["tbr_ma3"] > t
            ),
            short_signal_func=lambda bar, i, r=roc_thresh, t=tbr_thresh: (
                not np.isnan(bar["roc_5"]) and bar["roc_5"] > r and
                not np.isnan(bar["tbr_ma3"]) and bar["tbr_ma3"] < (1.0 - t)
            ),
            tp_pct=0.02, sl_pct=0.02, max_hold=12
        )
        analyze(tdf, name)

# --- Strategy G: Momentum Burst (enter after big move in same direction) ---
print("\n\n--- G: Momentum Continuation ---")
for roc_thresh in [0.03, 0.04, 0.05]:
    name = f"MomCont ROC5>{roc_thresh*100:.0f}% TP2/SL2"
    print(f"\n[{name}]")
    tdf, eq, mdd = run_backtest(
        long_signal_func=lambda bar, i, r=roc_thresh: (
            not np.isnan(bar["roc_5"]) and bar["roc_5"] > r
        ),
        short_signal_func=lambda bar, i, r=roc_thresh: (
            not np.isnan(bar["roc_5"]) and bar["roc_5"] < -r
        ),
        tp_pct=0.02, sl_pct=0.02, max_hold=12
    )
    analyze(tdf, name)

print("\n" + "=" * 80)
print("Sweep complete.")
print("=" * 80)
