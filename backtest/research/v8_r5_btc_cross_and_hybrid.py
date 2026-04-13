"""
V8 Round 5: Final Round — Two last paradigms before feasibility assessment
A) BTC-ETH cross-asset signal: BTC leads ETH, use BTC moves to predict ETH
B) Hybrid exit: close-exit for quick wins + let runners run with trail
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

# Merge BTC data
btc_cols = btc[["datetime", "close", "volume", "tbv"]].rename(
    columns={"close": "btc_close", "volume": "btc_volume", "tbv": "btc_tbv"})
df = eth.merge(btc_cols, on="datetime", how="left")

# ===== ETH Indicators =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)

# ===== BTC-ETH Cross Signals =====
df["btc_ret1"] = df["btc_close"].pct_change().shift(1)
df["eth_ret1"] = df["close"].pct_change().shift(1)
df["btc_ret3"] = df["btc_close"].pct_change(3).shift(1)
df["eth_ret3"] = df["close"].pct_change(3).shift(1)
df["btc_ret5"] = df["btc_close"].pct_change(5).shift(1)
df["eth_ret5"] = df["close"].pct_change(5).shift(1)

# Relative return: ETH - BTC (positive = ETH outperform)
df["rel_ret3"] = (df["eth_ret3"] - df["btc_ret3"]).shift(0)  # already shifted via components
df["rel_ret5"] = (df["eth_ret5"] - df["btc_ret5"]).shift(0)

# BTC momentum (lead signal)
df["btc_roc3"] = df["btc_close"].pct_change(3).shift(1)
df["btc_roc5"] = df["btc_close"].pct_change(5).shift(1)

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
                 exit_mode="tp_sl",
                 tp_pct=0.02, sl_pct=0.02, max_hold=12,
                 min_profit_pct=0.002, trail_pct=0.01,
                 max_same_l=2, max_same_s=2, max_total=3, exit_cd=3,
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

        for pos in list(open_positions):
            exit_price = None
            exit_reason = None
            hold_bars = i - pos["entry_bar"]

            # SafeNet
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
                # SL
                if pos["side"] == "long":
                    if bar["low"] <= pos["entry"] * (1 - sl_pct):
                        exit_price = pos["entry"] * (1 - sl_pct)
                        exit_reason = "sl"
                else:
                    if bar["high"] >= pos["entry"] * (1 + sl_pct):
                        exit_price = pos["entry"] * (1 + sl_pct)
                        exit_reason = "sl"
                # TP
                if exit_price is None:
                    if pos["side"] == "long":
                        if bar["high"] >= pos["entry"] * (1 + tp_pct):
                            exit_price = pos["entry"] * (1 + tp_pct)
                            exit_reason = "tp"
                    else:
                        if bar["low"] <= pos["entry"] * (1 - tp_pct):
                            exit_price = pos["entry"] * (1 - tp_pct)
                            exit_reason = "tp"

            if exit_price is None and exit_mode == "hybrid":
                # Hybrid: close-exit for quick profits, trail for runners
                if pos["side"] == "long":
                    ret_pct = (bar["close"] - pos["entry"]) / pos["entry"]
                    # Track peak
                    if "peak_price" not in pos:
                        pos["peak_price"] = pos["entry"]
                    pos["peak_price"] = max(pos["peak_price"], bar["high"])
                    # SL
                    if bar["low"] <= pos["entry"] * (1 - sl_pct):
                        exit_price = pos["entry"] * (1 - sl_pct)
                        exit_reason = "sl"
                    # Close-exit: take quick profit at bar close
                    elif ret_pct >= min_profit_pct and ret_pct < tp_pct * 0.8:
                        exit_price = bar["close"]
                        exit_reason = "close_profit"
                    # If in big profit territory, trail
                    elif pos["peak_price"] > pos["entry"] * (1 + tp_pct * 0.8):
                        trail_level = pos["peak_price"] * (1 - trail_pct)
                        if bar["low"] <= trail_level:
                            exit_price = max(trail_level, bar["low"])
                            exit_reason = "trail"
                    # TP cap
                    elif bar["high"] >= pos["entry"] * (1 + tp_pct):
                        exit_price = pos["entry"] * (1 + tp_pct)
                        exit_reason = "tp"
                else:
                    ret_pct = (pos["entry"] - bar["close"]) / pos["entry"]
                    if "trough_price" not in pos:
                        pos["trough_price"] = pos["entry"]
                    pos["trough_price"] = min(pos["trough_price"], bar["low"])
                    if bar["high"] >= pos["entry"] * (1 + sl_pct):
                        exit_price = pos["entry"] * (1 + sl_pct)
                        exit_reason = "sl"
                    elif ret_pct >= min_profit_pct and ret_pct < tp_pct * 0.8:
                        exit_price = bar["close"]
                        exit_reason = "close_profit"
                    elif pos["trough_price"] < pos["entry"] * (1 - tp_pct * 0.8):
                        trail_level = pos["trough_price"] * (1 + trail_pct)
                        if bar["high"] >= trail_level:
                            exit_price = min(trail_level, bar["high"])
                            exit_reason = "trail"
                    elif bar["low"] <= pos["entry"] * (1 - tp_pct):
                        exit_price = pos["entry"] * (1 - tp_pct)
                        exit_reason = "tp"

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

    return pd.DataFrame(trades), max_dd


def quick_report(tdf, name, mdd):
    if len(tdf) == 0:
        print(f"  [{name}] NO TRADES")
        return
    tdf = tdf.copy()
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    oos = tdf[tdf["entry_dt"] >= split_date]
    if len(oos) == 0:
        print(f"  [{name}] No OOS trades")
        return

    n = len(oos)
    pnl = oos["pnl"].sum()
    wr = (oos["pnl"] > 0).mean()
    l = oos[oos["side"]=="long"]
    s = oos[oos["side"]=="short"]
    l_wr = (l["pnl"]>0).mean() if len(l)>0 else 0
    s_wr = (s["pnl"]>0).mean() if len(s)>0 else 0
    l_pnl = l["pnl"].sum() if len(l)>0 else 0
    s_pnl = s["pnl"].sum() if len(s)>0 else 0
    wins = oos[oos["pnl"]>0]["pnl"]
    losses = oos[oos["pnl"]<=0]["pnl"]
    pf = wins.sum()/abs(losses.sum()) if losses.sum()!=0 else 999
    avgw = wins.mean() if len(wins)>0 else 0
    avgl = losses.mean() if len(losses)>0 else 0

    reasons = oos.groupby("reason")["pnl"].agg(["count","mean"])
    exits_str = " ".join(f"{r}:{int(row['count'])}" for r, row in reasons.iterrows())

    print(f"  [{name}] {n}t ${pnl:.0f} PF{pf:.2f} WR{wr:.0%} L:{len(l)}t${l_pnl:.0f}/{l_wr:.0%} S:{len(s)}t${s_pnl:.0f}/{s_wr:.0%} W${avgw:.0f}/L${avgl:.0f} [{exits_str}]")


# ===================================================================
print("=" * 80)
print("V8 Round 5: BTC Cross-Asset + Hybrid Exit")
print("=" * 80)

# --- 5A: BTC-ETH Relative Momentum ---
print("\n--- 5A: BTC leads ETH ---")

# Idea 1: BTC dropped but ETH hasn't followed fully → sell ETH (or buy if bouncing)
# Idea 2: BTC-ETH relative return divergence → mean reversion
# Idea 3: BTC momentum as lead signal for ETH direction

for tp, sl in [(0.02, 0.02), (0.025, 0.035), (0.03, 0.04)]:
    for mh in [12, 18]:
        # Signal: BTC dropped big + ETH lagging → buy ETH (ETH will catch up)
        name = f"BTC_lead roc5<-3% tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
        tdf, mdd = run_backtest(
            long_signal_func=lambda bar, i: (
                not np.isnan(bar["btc_roc5"]) and bar["btc_roc5"] > 0.03 and
                not np.isnan(bar["eth_ret5"]) and bar["eth_ret5"] < 0.02
            ),
            short_signal_func=lambda bar, i: (
                not np.isnan(bar["btc_roc5"]) and bar["btc_roc5"] < -0.03 and
                not np.isnan(bar["eth_ret5"]) and bar["eth_ret5"] > -0.02
            ),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        quick_report(tdf, name, mdd)

        # Signal: Relative divergence → mean reversion
        name = f"RelDiv rr5<-2% tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
        tdf, mdd = run_backtest(
            long_signal_func=lambda bar, i: (
                not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] < -0.02
            ),
            short_signal_func=lambda bar, i: (
                not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] > 0.02
            ),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        quick_report(tdf, name, mdd)

# --- 5B: BTC momentum as ETH direction ---
print("\n--- 5B: BTC momentum → ETH direction ---")
for btc_thresh in [0.02, 0.03, 0.04]:
    for tp, sl in [(0.02, 0.02), (0.025, 0.035)]:
        name = f"BTC_mom>{btc_thresh*100:.0f}% tp{tp*100:.0f}/sl{sl*100:.0f}"
        tdf, mdd = run_backtest(
            long_signal_func=lambda bar, i, t=btc_thresh: (
                not np.isnan(bar["btc_roc5"]) and bar["btc_roc5"] > t
            ),
            short_signal_func=lambda bar, i, t=btc_thresh: (
                not np.isnan(bar["btc_roc5"]) and bar["btc_roc5"] < -t
            ),
            tp_pct=tp, sl_pct=sl, max_hold=12,
        )
        quick_report(tdf, name, mdd)

# --- 5C: Hybrid Exit with best signals ---
print("\n--- 5C: Hybrid Exit (close-exit + trail) ---")
for sig_name, l_sig, s_sig in [
    ("Vol2_dist01",
     lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01,
     lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01),
    ("MR_dist02",
     lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.02,
     lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.02),
    ("MomCont_roc3",
     lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.03,
     lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] < -0.03),
]:
    for tp in [0.02, 0.03]:
        for sl in [0.02, 0.03]:
            for mp in [0.002, 0.005]:
                for trail in [0.008, 0.012]:
                    name = f"Hybrid {sig_name} tp{tp*100:.0f}/sl{sl*100:.0f} mp{mp} tr{trail}"
                    tdf, mdd = run_backtest(
                        long_signal_func=l_sig,
                        short_signal_func=s_sig,
                        exit_mode="hybrid",
                        tp_pct=tp, sl_pct=sl, max_hold=12,
                        min_profit_pct=mp, trail_pct=trail,
                    )
                    quick_report(tdf, name, mdd)

# === Print best overall ===
print("\n\n" + "=" * 80)
print("=== ROUND 5 COMPLETE ===")
print("=" * 80)
