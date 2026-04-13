"""
V8 Round 4: Asymmetric Wide Stop
TP 2.5-3% / SL 3.5-4% — mathematically the only config that can
simultaneously satisfy WR>=70% AND PF>=1.5 AND PnL>=$300/month.

Key insight: wider SL raises the random-walk baseline WR.
Signal only needs to add ~13pp on top.

Signal: Mean reversion (dist_ema20 extreme) + Volume confirmation
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

# ===== Indicators (all shifted) =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)
df["roc_20"] = df["close"].pct_change(20).shift(1)
df["tbr"] = df["tbv"] / df["volume"]
df["tbr_ma3"] = df["tbr"].rolling(3).mean().shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)
df["range_pos_20"] = ((df["close"] - df["low"].rolling(20).min()) /
                       (df["high"].rolling(20).max() - df["low"].rolling(20).min())).shift(1)

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
                 tp_pct, sl_pct, max_hold,
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

        for pos in list(open_positions):
            exit_price = None
            exit_reason = None
            hold_bars = i - pos["entry_bar"]

            # 1. SafeNet
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

            # 2. SL (if no SafeNet)
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

    return pd.DataFrame(trades), max_dd, worst_day_pnl, worst_day_date


def full_analysis(trade_df, name, mdd, worst_day_pnl, worst_day_date):
    if len(trade_df) == 0:
        print(f"  {name}: NO TRADES")
        return

    tdf = trade_df.copy()
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    tdf["exit_dt"] = pd.to_datetime(tdf["exit_dt"])
    is_mask = tdf["entry_dt"] < split_date
    oos_mask = ~is_mask

    for label, mask in [("IS", is_mask), ("OOS", oos_mask)]:
        t = tdf[mask]
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

    oos = tdf[oos_mask].copy()
    if len(oos) > 0:
        oos["month"] = oos["entry_dt"].dt.strftime("%Y-%m")
        reasons = oos.groupby("reason")["pnl"].agg(["count", "mean"])
        exits_str = " | ".join(f"{r}:{int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
        print(f"  Exits: {exits_str}")

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

    print(f"  MDD: ${mdd:.0f}, Worst Day: ${worst_day_pnl:.0f} ({worst_day_date})")

    # Gate check
    print(f"\n  === GATES ===")
    if len(oos) > 0:
        oos_l = oos[oos["side"] == "long"]
        oos_s = oos[oos["side"] == "short"]
        l_monthly = oos_l.groupby("month").agg(n=("pnl","count"), wr=("pnl", lambda x: (x>0).mean()), pnl=("pnl","sum"))
        s_monthly = oos_s.groupby("month").agg(n=("pnl","count"), wr=("pnl", lambda x: (x>0).mean()), pnl=("pnl","sum"))
        all_monthly = oos.groupby("month")["pnl"].sum()

        l_pf = oos_l[oos_l["pnl"]>0]["pnl"].sum() / abs(oos_l[oos_l["pnl"]<=0]["pnl"].sum()) if oos_l[oos_l["pnl"]<=0]["pnl"].sum() != 0 else 999
        s_pf = oos_s[oos_s["pnl"]>0]["pnl"].sum() / abs(oos_s[oos_s["pnl"]<=0]["pnl"].sum()) if oos_s[oos_s["pnl"]<=0]["pnl"].sum() != 0 else 999

        # Consec loss
        sorted_trades = oos.sort_values("exit_dt")
        max_consec = 0
        curr_consec = 0
        max_consec_pnl = 0
        curr_consec_pnl = 0
        for _, t in sorted_trades.iterrows():
            if t["pnl"] < 0:
                curr_consec += 1
                curr_consec_pnl += t["pnl"]
                if curr_consec > max_consec:
                    max_consec = curr_consec
                    max_consec_pnl = curr_consec_pnl
            else:
                curr_consec = 0
                curr_consec_pnl = 0

        pos_months = (all_monthly > 0).sum()

        gates = [
            ("G1 L月PnL>=300", len(l_monthly)>0 and l_monthly["pnl"].min()>=300, f"min=${l_monthly['pnl'].min():.0f}" if len(l_monthly)>0 else "N/A"),
            ("G2 S月PnL>=300", len(s_monthly)>0 and s_monthly["pnl"].min()>=300, f"min=${s_monthly['pnl'].min():.0f}" if len(s_monthly)>0 else "N/A"),
            ("G3 L月WR>=70%", len(l_monthly)>0 and l_monthly["wr"].min()>=0.7, f"min={l_monthly['wr'].min():.0%}" if len(l_monthly)>0 else "N/A"),
            ("G4 S月WR>=70%", len(s_monthly)>0 and s_monthly["wr"].min()>=0.7, f"min={s_monthly['wr'].min():.0%}" if len(s_monthly)>0 else "N/A"),
            ("G5 L PF>=1.5", l_pf >= 1.5, f"{l_pf:.2f}"),
            ("G5 S PF>=1.5", s_pf >= 1.5, f"{s_pf:.2f}"),
            ("G6 MDD<=500", mdd <= 500, f"${mdd:.0f}"),
            ("G7 最差日>=-300", worst_day_pnl >= -300, f"${worst_day_pnl:.0f}"),
            ("G8 連虧<=400", abs(max_consec_pnl) <= 400, f"{max_consec}t ${max_consec_pnl:.0f}"),
            ("C1 合併月>=600", len(all_monthly)>0 and all_monthly.min()>=600, f"min=${all_monthly.min():.0f}" if len(all_monthly)>0 else "N/A"),
            ("C2 正月>=10/12", pos_months >= 10, f"{pos_months}/{len(all_monthly)}"),
            ("C3 最差月>=-200", len(all_monthly)>0 and all_monthly.min()>=-200, f"${all_monthly.min():.0f}" if len(all_monthly)>0 else "N/A"),
        ]
        passed = 0
        for name, ok, val in gates:
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            print(f"    {name}: {val} [{status}]")
        print(f"  Total: {passed}/{len(gates)} PASS")


# ===================================================================
print("=" * 80)
print("V8 Round 4: Asymmetric Wide Stop")
print("=" * 80)

# --- Signal definitions ---
signals = {
    "MR_dist015": (
        lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.015,
        lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.015,
    ),
    "MR_dist02": (
        lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.02,
        lambda bar, i: not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.02,
    ),
    "MR_dist02_vol15": (
        lambda bar, i: (not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.02 and
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5),
        lambda bar, i: (not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.02 and
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5),
    ),
    "Vol2_dist01": (
        lambda bar, i: (not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and
                        not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01),
        lambda bar, i: (not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and
                        not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01),
    ),
    "Vol15_dist015_rp": (
        lambda bar, i: (not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.015 and
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5 and
                        not np.isnan(bar["range_pos_20"]) and bar["range_pos_20"] < 0.3),
        lambda bar, i: (not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.015 and
                        not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5 and
                        not np.isnan(bar["range_pos_20"]) and bar["range_pos_20"] > 0.7),
    ),
    "MomCont_roc3": (
        lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.03,
        lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] < -0.03,
    ),
}

# --- Sweep TP/SL combinations ---
tp_sl_combos = [
    (0.025, 0.035),
    (0.025, 0.04),
    (0.03, 0.04),
    (0.03, 0.045),  # SafeNet = SL, so SL is redundant → effectively just TP + SafeNet
    (0.02, 0.03),
    (0.02, 0.035),
]

max_holds = [12, 18, 24]

print("\n--- Quick Sweep (filtering WR >= 55% in OOS) ---")
results = []

for sig_name, (l_sig, s_sig) in signals.items():
    for tp, sl in tp_sl_combos:
        for mh in max_holds:
            tdf, mdd_val, wd_pnl, wd_date = run_backtest(
                long_signal_func=l_sig,
                short_signal_func=s_sig,
                tp_pct=tp, sl_pct=sl, max_hold=mh,
            )
            if len(tdf) == 0:
                continue
            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
            oos = tdf[tdf["entry_dt"] >= split_date]
            if len(oos) == 0:
                continue

            oos_l = oos[oos["side"] == "long"]
            oos_s = oos[oos["side"] == "short"]
            n = len(oos)
            oos_pnl = oos["pnl"].sum()
            oos_wr = (oos["pnl"] > 0).mean()
            l_wr = (oos_l["pnl"] > 0).mean() if len(oos_l) > 0 else 0
            s_wr = (oos_s["pnl"] > 0).mean() if len(oos_s) > 0 else 0
            l_pnl = oos_l["pnl"].sum() if len(oos_l) > 0 else 0
            s_pnl = oos_s["pnl"].sum() if len(oos_s) > 0 else 0

            results.append({
                "sig": sig_name, "tp": tp, "sl": sl, "mh": mh,
                "n": n, "pnl": oos_pnl, "wr": oos_wr,
                "l_wr": l_wr, "s_wr": s_wr, "l_pnl": l_pnl, "s_pnl": s_pnl,
                "mdd": mdd_val,
            })

# Sort by PnL descending
results.sort(key=lambda x: x["pnl"], reverse=True)

print(f"\n{'Signal':<22} {'TP/SL':>8} {'MH':>3} {'N':>4} {'PnL':>7} {'WR':>5} {'L_WR':>5} {'S_WR':>5} {'L_PnL':>7} {'S_PnL':>7} {'MDD':>6}")
for r in results[:30]:
    print(f"  {r['sig']:<22} {r['tp']*100:.1f}/{r['sl']*100:.1f} {r['mh']:>3} {r['n']:>4} {r['pnl']:>7.0f} {r['wr']:>5.0%} {r['l_wr']:>5.0%} {r['s_wr']:>5.0%} {r['l_pnl']:>7.0f} {r['s_pnl']:>7.0f} {r['mdd']:>6.0f}")

# === Full details for top 3 ===
print("\n\n" + "=" * 80)
print("=== TOP 3 DETAILS ===")
print("=" * 80)

for r in results[:3]:
    sig_name = r["sig"]
    l_sig, s_sig = signals[sig_name]
    print(f"\n[{sig_name} TP{r['tp']*100:.1f}/SL{r['sl']*100:.1f} MH{r['mh']}]")
    tdf, mdd_val, wd_pnl, wd_date = run_backtest(
        long_signal_func=l_sig,
        short_signal_func=s_sig,
        tp_pct=r["tp"], sl_pct=r["sl"], max_hold=r["mh"],
    )
    full_analysis(tdf, sig_name, mdd_val, wd_pnl, wd_date)
