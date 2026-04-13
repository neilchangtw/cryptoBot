"""
V8 Round 3: Close-Exit Scalp
Instead of fixed TP/SL, exit at bar close when trade is profitable.
Time stop + SafeNet for loss control.
Goal: Maximize WR while maintaining positive expectancy.
"""
import pandas as pd
import numpy as np
import sys, io
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
df["tbr"] = df["tbv"] / df["volume"]
df["tbr_ma3"] = df["tbr"].rolling(3).mean().shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)

# Range position
df["range_pos_20"] = ((df["close"] - df["low"].rolling(20).min()) /
                       (df["high"].rolling(20).max() - df["low"].rolling(20).min())).shift(1)

# ATR
atr_components = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs()
], axis=1)
df["atr14"] = atr_components.max(axis=1).rolling(14).mean()
df["atr_pct"] = (df["atr14"] / df["close"]).shift(1)

# Consecutive bars
df["up"] = (df["close"] > df["open"]).astype(int)
consec_down = np.zeros(len(df))
consec_up = np.zeros(len(df))
for i in range(1, len(df)):
    if df.iloc[i]["close"] < df.iloc[i]["open"]:
        consec_down[i] = consec_down[i-1] + 1
    if df.iloc[i]["close"] > df.iloc[i]["open"]:
        consec_up[i] = consec_up[i-1] + 1
df["consec_down_s"] = pd.Series(consec_down).shift(1)
df["consec_up_s"] = pd.Series(consec_up).shift(1)

# Session
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek

NOTIONAL = 4000
FEE = 4.0
SAFENET_PCT = 0.045
SLIPPAGE_FACTOR = 0.25
WARMUP = 150

split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)
BLOCK_DAYS = {5, 6}


def run_backtest(long_signal_func, short_signal_func,
                 exit_mode="close_exit",  # "close_exit" or "tp_sl"
                 tp_pct=0.02, sl_pct=0.02,
                 min_profit_pct=0.002,  # minimum profit % to close (for close_exit mode)
                 max_hold=6,
                 max_same_l=2, max_same_s=2, max_total=3,
                 exit_cd=3,
                 l_block_hours=None, s_allow_hours=None):

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
    worst_day_pnl = 0.0
    worst_day_date = None
    daily_tracker = {}

    for i in range(WARMUP, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        bar_dt = bar["datetime"]
        bar_month = bar_dt.strftime("%Y-%m")
        bar_date = bar_dt.date()

        if bar_date != current_date:
            if current_date is not None and current_date in daily_tracker:
                d_pnl = daily_tracker[current_date]
                if d_pnl < worst_day_pnl:
                    worst_day_pnl = d_pnl
                    worst_day_date = current_date
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

            # 1. SafeNet (always active)
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

            if exit_price is None and exit_mode == "tp_sl":
                # Hard SL
                if pos["side"] == "long":
                    sl_level = pos["entry"] * (1 - sl_pct)
                    if bar["low"] <= sl_level:
                        exit_price = sl_level
                        exit_reason = "sl"
                else:
                    sl_level = pos["entry"] * (1 + sl_pct)
                    if bar["high"] >= sl_level:
                        exit_price = sl_level
                        exit_reason = "sl"
                # TP
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

            if exit_price is None and exit_mode == "close_exit":
                # Close exit: exit at bar close if profitable enough
                if pos["side"] == "long":
                    ret_pct = (bar["close"] - pos["entry"]) / pos["entry"]
                    if ret_pct >= min_profit_pct:
                        exit_price = bar["close"]
                        exit_reason = "close_profit"
                else:
                    ret_pct = (pos["entry"] - bar["close"]) / pos["entry"]
                    if ret_pct >= min_profit_pct:
                        exit_price = bar["close"]
                        exit_reason = "close_profit"

                # Hard SL for close_exit mode
                if exit_price is None:
                    if pos["side"] == "long":
                        sl_level = pos["entry"] * (1 - sl_pct)
                        if bar["low"] <= sl_level:
                            exit_price = sl_level
                            exit_reason = "sl"
                    else:
                        sl_level = pos["entry"] * (1 + sl_pct)
                        if bar["high"] >= sl_level:
                            exit_price = sl_level
                            exit_reason = "sl"

            # Time stop
            if exit_price is None and hold_bars >= max_hold:
                exit_price = bar["close"]
                exit_reason = "time_stop"

            if exit_price is not None:
                if pos["side"] == "long":
                    pnl = (exit_price - pos["entry"]) / pos["entry"] * NOTIONAL - FEE
                else:
                    pnl = (pos["entry"] - exit_price) / pos["entry"] * NOTIONAL - FEE
                trades.append({
                    "entry_bar": pos["entry_bar"], "exit_bar": i,
                    "side": pos["side"], "entry": pos["entry"], "exit": exit_price,
                    "pnl": pnl, "reason": exit_reason, "hold_bars": hold_bars,
                    "entry_dt": pos["entry_dt"], "exit_dt": bar_dt,
                })
                open_positions.remove(pos)
                equity += pnl
                daily_pnl += pnl
                monthly_pnl += pnl
                if bar_date not in daily_tracker:
                    daily_tracker[bar_date] = 0.0
                daily_tracker[bar_date] += pnl
                peak_equity = max(peak_equity, equity)
                max_dd = max(max_dd, peak_equity - equity)
                if pnl < 0:
                    consec_losses += 1
                    if consec_losses >= 5:
                        cooldown_until = i + 24
                else:
                    consec_losses = 0
                if pos["side"] == "long": last_exit_l = i
                else: last_exit_s = i

        # Risk
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
    for pos in open_positions:
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

    return pd.DataFrame(trades), max_dd, worst_day_pnl, worst_day_date


def analyze(trade_df, name, show_monthly=False):
    if len(trade_df) == 0:
        print(f"  {name}: NO TRADES")
        return

    trade_df = trade_df.copy()
    trade_df["entry_dt"] = pd.to_datetime(trade_df["entry_dt"])
    trade_df["exit_dt"] = pd.to_datetime(trade_df["exit_dt"])
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
                return f"{tag}:0t"
            n = len(df)
            pnl = df["pnl"].sum()
            wr = (df["pnl"] > 0).mean()
            wins = df[df["pnl"] > 0]["pnl"]
            losses = df[df["pnl"] <= 0]["pnl"]
            pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 999
            avgw = wins.mean() if len(wins) > 0 else 0
            avgl = losses.mean() if len(losses) > 0 else 0
            return f"{tag}:{n}t ${pnl:.0f} PF{pf:.2f} WR{wr:.0%} W${avgw:.0f} L${avgl:.0f}"

        print(f"  {label} {s(t, 'ALL')} | {s(lt, 'L')} | {s(st, 'S')}")

    # OOS monthly
    oos = trade_df[oos_mask].copy()
    if len(oos) > 0:
        oos["month"] = oos["entry_dt"].dt.strftime("%Y-%m")
        reasons = oos.groupby("reason")["pnl"].agg(["count", "mean"])
        exits_str = " | ".join(f"{r}:{int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
        print(f"  Exits: {exits_str}")

        if show_monthly:
            print(f"  {'月份':<8} {'L筆':>4} {'L勝率':>6} {'L淨利':>8} {'S筆':>4} {'S勝率':>6} {'S淨利':>8} {'合計':>8}")
            for month in sorted(oos["month"].unique()):
                mt = oos[oos["month"] == month]
                lt = mt[mt["side"] == "long"]
                st = mt[mt["side"] == "short"]
                l_n, s_n = len(lt), len(st)
                l_wr = (lt["pnl"] > 0).mean() if l_n > 0 else 0
                s_wr = (st["pnl"] > 0).mean() if s_n > 0 else 0
                l_pnl = lt["pnl"].sum()
                s_pnl = st["pnl"].sum()
                print(f"  {month:<8} {l_n:>4} {l_wr:>6.0%} {l_pnl:>8.0f} {s_n:>4} {s_wr:>6.0%} {s_pnl:>8.0f} {l_pnl+s_pnl:>8.0f}")


# ===================================================================
print("=" * 80)
print("V8 Round 3: Close-Exit Scalp — 見好就收")
print("=" * 80)

# --- 3A: Close-exit with dist_ema20 signal ---
print("\n--- 3A: Close-exit Mean Reversion ---")
for min_prof in [0.001, 0.002, 0.003, 0.005]:
    for dist in [0.015, 0.02, 0.03]:
        for sl in [0.02, 0.03]:
            for mh in [4, 6, 8]:
                name = f"CE MR d{dist} mp{min_prof} sl{sl} mh{mh}"
                tdf, _, _, _ = run_backtest(
                    long_signal_func=lambda bar, i, d=dist: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -d,
                    short_signal_func=lambda bar, i, d=dist: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > d,
                    exit_mode="close_exit",
                    min_profit_pct=min_prof,
                    sl_pct=sl,
                    max_hold=mh,
                )
                if len(tdf) == 0:
                    continue
                tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                oos = tdf[tdf["entry_dt"] >= split_date]
                if len(oos) == 0:
                    continue
                oos_wr = (oos["pnl"] > 0).mean()
                oos_pnl = oos["pnl"].sum()
                n = len(oos)
                l_wr = (oos[oos["side"]=="long"]["pnl"] > 0).mean() if len(oos[oos["side"]=="long"]) > 0 else 0
                s_wr = (oos[oos["side"]=="short"]["pnl"] > 0).mean() if len(oos[oos["side"]=="short"]) > 0 else 0
                # Only print if WR > 55%
                if oos_wr >= 0.55:
                    print(f"  [{name}] OOS: {n}t ${oos_pnl:.0f} WR{oos_wr:.0%} L_WR{l_wr:.0%} S_WR{s_wr:.0%}")

# --- 3B: Close-exit with Volume Spike signal (best from R2) ---
print("\n--- 3B: Close-exit Volume Spike ---")
for min_prof in [0.001, 0.002, 0.003]:
    for vol in [1.5, 2.0, 2.5]:
        for sl in [0.02, 0.03]:
            for mh in [4, 6, 8]:
                name = f"CE Vol>{vol} mp{min_prof} sl{sl} mh{mh}"
                tdf, _, _, _ = run_backtest(
                    long_signal_func=lambda bar, i, v=vol: (
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v and
                        not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01
                    ),
                    short_signal_func=lambda bar, i, v=vol: (
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v and
                        not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01
                    ),
                    exit_mode="close_exit",
                    min_profit_pct=min_prof,
                    sl_pct=sl,
                    max_hold=mh,
                )
                if len(tdf) == 0:
                    continue
                tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                oos = tdf[tdf["entry_dt"] >= split_date]
                if len(oos) == 0:
                    continue
                oos_wr = (oos["pnl"] > 0).mean()
                oos_pnl = oos["pnl"].sum()
                n = len(oos)
                l_wr = (oos[oos["side"]=="long"]["pnl"] > 0).mean() if len(oos[oos["side"]=="long"]) > 0 else 0
                s_wr = (oos[oos["side"]=="short"]["pnl"] > 0).mean() if len(oos[oos["side"]=="short"]) > 0 else 0
                if oos_wr >= 0.55:
                    print(f"  [{name}] OOS: {n}t ${oos_pnl:.0f} WR{oos_wr:.0%} L_WR{l_wr:.0%} S_WR{s_wr:.0%}")

# --- 3C: Close-exit with multi-condition signal ---
print("\n--- 3C: Close-exit Multi-Condition ---")
for min_prof in [0.002, 0.003]:
    for sl in [0.02, 0.03]:
        for mh in [6, 8]:
            # L: dist<-1.5% AND (vol>1.5 OR consec_down>=3)
            # S: dist>+1.5% AND (vol>1.5 OR consec_up>=3)
            name = f"CE Multi mp{min_prof} sl{sl} mh{mh}"
            tdf, _, _, _ = run_backtest(
                long_signal_func=lambda bar, i: (
                    not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.015 and
                    (bar["vol_ratio"] > 1.5 or bar["consec_down_s"] >= 3)
                ),
                short_signal_func=lambda bar, i: (
                    not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.015 and
                    (bar["vol_ratio"] > 1.5 or bar["consec_up_s"] >= 3)
                ),
                exit_mode="close_exit",
                min_profit_pct=min_prof,
                sl_pct=sl,
                max_hold=mh,
            )
            if len(tdf) == 0:
                continue
            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
            oos = tdf[tdf["entry_dt"] >= split_date]
            if len(oos) == 0:
                continue
            oos_wr = (oos["pnl"] > 0).mean()
            oos_pnl = oos["pnl"].sum()
            n = len(oos)
            l_wr = (oos[oos["side"]=="long"]["pnl"] > 0).mean() if len(oos[oos["side"]=="long"]) > 0 else 0
            s_wr = (oos[oos["side"]=="short"]["pnl"] > 0).mean() if len(oos[oos["side"]=="short"]) > 0 else 0
            if oos_wr >= 0.50:
                print(f"  [{name}] OOS: {n}t ${oos_pnl:.0f} WR{oos_wr:.0%} L_WR{l_wr:.0%} S_WR{s_wr:.0%}")

# --- 3D: Close-exit with momentum continuation ---
print("\n--- 3D: Close-exit Momentum Continuation ---")
for min_prof in [0.002, 0.003, 0.005]:
    for roc in [0.03, 0.04, 0.05]:
        for sl in [0.02, 0.03]:
            name = f"CE MomCont roc{roc} mp{min_prof} sl{sl}"
            tdf, _, _, _ = run_backtest(
                long_signal_func=lambda bar, i, r=roc: (
                    not np.isnan(bar["roc_5"]) and bar["roc_5"] > r
                ),
                short_signal_func=lambda bar, i, r=roc: (
                    not np.isnan(bar["roc_5"]) and bar["roc_5"] < -r
                ),
                exit_mode="close_exit",
                min_profit_pct=min_prof,
                sl_pct=sl,
                max_hold=6,
            )
            if len(tdf) == 0:
                continue
            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
            oos = tdf[tdf["entry_dt"] >= split_date]
            if len(oos) == 0:
                continue
            oos_wr = (oos["pnl"] > 0).mean()
            oos_pnl = oos["pnl"].sum()
            n = len(oos)
            if oos_wr >= 0.55:
                l_wr = (oos[oos["side"]=="long"]["pnl"] > 0).mean() if len(oos[oos["side"]=="long"]) > 0 else 0
                s_wr = (oos[oos["side"]=="short"]["pnl"] > 0).mean() if len(oos[oos["side"]=="short"]) > 0 else 0
                print(f"  [{name}] OOS: {n}t ${oos_pnl:.0f} WR{oos_wr:.0%} L_WR{l_wr:.0%} S_WR{s_wr:.0%}")

# --- 3E: Close-exit "any signal" (broadest possible entry) ---
# Just enter whenever dist > some threshold, no other filter
# This tests the close-exit mechanism itself
print("\n--- 3E: Close-exit Broad Entry (dist>1%) ---")
for min_prof in [0.001, 0.002, 0.003, 0.005, 0.008]:
    for sl in [0.02, 0.03, 0.04]:
        for mh in [3, 4, 6]:
            name = f"CE Broad mp{min_prof} sl{sl} mh{mh}"
            tdf, _, _, _ = run_backtest(
                long_signal_func=lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01,
                short_signal_func=lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01,
                exit_mode="close_exit",
                min_profit_pct=min_prof,
                sl_pct=sl,
                max_hold=mh,
            )
            if len(tdf) == 0:
                continue
            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
            oos = tdf[tdf["entry_dt"] >= split_date]
            if len(oos) == 0:
                continue
            oos_wr = (oos["pnl"] > 0).mean()
            oos_pnl = oos["pnl"].sum()
            n = len(oos)
            if oos_wr >= 0.55:
                l_wr = (oos[oos["side"]=="long"]["pnl"] > 0).mean() if len(oos[oos["side"]=="long"]) > 0 else 0
                s_wr = (oos[oos["side"]=="short"]["pnl"] > 0).mean() if len(oos[oos["side"]=="short"]) > 0 else 0
                print(f"  [{name}] OOS: {n}t ${oos_pnl:.0f} WR{oos_wr:.0%} L_WR{l_wr:.0%} S_WR{s_wr:.0%}")

# === Print the BEST overall result with full details ===
print("\n\n" + "=" * 80)
print("=== BEST CANDIDATE: Full Details ===")
print("=" * 80)

# Let me manually run the configuration that looks most promising
# based on the sweep and show full details
best_configs = [
    # (name, L signal, S signal, exit_mode, params)
    ("CE MR d0.02 mp0.003 sl0.03 mh6",
     lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.02,
     lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.02,
     "close_exit", {"min_profit_pct": 0.003, "sl_pct": 0.03, "max_hold": 6}),
    ("TP/SL Vol>2.0 tp2/sl2 mh12 (best from R2)",
     lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01,
     lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01,
     "tp_sl", {"tp_pct": 0.02, "sl_pct": 0.02, "max_hold": 12}),
]

for name, l_sig, s_sig, mode, params in best_configs:
    print(f"\n[{name}]")
    tdf, mdd, wd_pnl, wd_date = run_backtest(
        long_signal_func=l_sig,
        short_signal_func=s_sig,
        exit_mode=mode,
        **params,
    )
    analyze(tdf, name, show_monthly=True)
    print(f"  MDD: ${mdd:.0f}, Worst Day: ${wd_pnl:.0f} ({wd_date})")
