"""
V9 Round 1: BTC-ETH Relative Divergence Deep Optimization
Base: V8 R5 best — rel_ret5 > ±2%, TP 2.5% / SL 3.5%, MH 18, OOS +$787

Optimization axes:
1. Divergence threshold (1.5%, 2%, 2.5%, 3%)
2. Divergence lookback (3, 5, 7, 10 bars)
3. TP/SL combinations
4. MaxHold (12, 18, 24)
5. Session filters (hours, days)
6. Additional confirmations (vol, dist_ema20, TBR)
7. Exit cooldown (3, 5, 8)
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

btc_cols = btc[["datetime", "close", "volume", "tbv"]].rename(
    columns={"close": "btc_close", "volume": "btc_volume", "tbv": "btc_tbv"})
df = eth.merge(btc_cols, on="datetime", how="left")

# ===== Indicators (all shifted) =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)
df["tbr"] = df["tbv"] / df["volume"]
df["tbr_ma3"] = df["tbr"].rolling(3).mean().shift(1)

# BTC-ETH relative returns at various lookbacks
for n in [3, 5, 7, 10]:
    df[f"eth_ret{n}"] = df["close"].pct_change(n).shift(1)
    df[f"btc_ret{n}"] = df["btc_close"].pct_change(n).shift(1)
    df[f"rel_ret{n}"] = df[f"eth_ret{n}"] - df[f"btc_ret{n}"]

# ETH momentum
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_20"] = df["close"].pct_change(20).shift(1)

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
        s_allow_hours = set(range(24)) - BLOCK_DAYS  # all hours for S (session filter via s_allow_hours)
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

            # SafeNet
            if pos["side"] == "long":
                sn = pos["entry"] * (1 - SAFENET_PCT)
                if bar["low"] <= sn:
                    exit_price = sn - (sn - bar["low"]) * SLIPPAGE_FACTOR
                    exit_reason = "safenet"
            else:
                sn = pos["entry"] * (1 + SAFENET_PCT)
                if bar["high"] >= sn:
                    exit_price = sn + (bar["high"] - sn) * SLIPPAGE_FACTOR
                    exit_reason = "safenet"

            # SL
            if exit_price is None:
                if pos["side"] == "long":
                    sl = pos["entry"] * (1 - sl_pct)
                    if bar["low"] <= sl:
                        exit_price = sl
                        exit_reason = "sl"
                else:
                    sl = pos["entry"] * (1 + sl_pct)
                    if bar["high"] >= sl:
                        exit_price = sl
                        exit_reason = "sl"

            # TP
            if exit_price is None:
                if pos["side"] == "long":
                    tp = pos["entry"] * (1 + tp_pct)
                    if bar["high"] >= tp:
                        exit_price = tp
                        exit_reason = "tp"
                else:
                    tp = pos["entry"] * (1 - tp_pct)
                    if bar["low"] <= tp:
                        exit_price = tp
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


def full_report(tdf, name, mdd, wd_pnl, wd_date):
    if len(tdf) == 0:
        print(f"  {name}: NO TRADES")
        return None
    tdf = tdf.copy()
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    tdf["exit_dt"] = pd.to_datetime(tdf["exit_dt"])
    is_mask = tdf["entry_dt"] < split_date
    oos_mask = ~is_mask

    for label, mask in [("IS", is_mask), ("OOS", oos_mask)]:
        t = tdf[mask]
        if len(t) == 0:
            continue
        lt = t[t["side"]=="long"]
        st = t[t["side"]=="short"]
        def s(d, tag):
            if len(d)==0: return f"{tag}:0t"
            n=len(d); pnl=d["pnl"].sum(); wr=(d["pnl"]>0).mean()
            w=d[d["pnl"]>0]["pnl"]; lo=d[d["pnl"]<=0]["pnl"]
            pf=w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
            w_avg = w.mean() if len(w)>0 else 0; l_avg = lo.mean() if len(lo)>0 else 0
            return f"{tag}:{n}t ${pnl:.0f} PF{pf:.2f} WR{wr:.0%} W${w_avg:.0f} L${l_avg:.0f}"
        print(f"  {label} {s(t,'ALL')} | {s(lt,'L')} | {s(st,'S')}")

    oos = tdf[oos_mask].copy()
    if len(oos) == 0:
        return None

    oos["month"] = oos["entry_dt"].dt.strftime("%Y-%m")

    # Exit reasons
    reasons = oos.groupby("reason")["pnl"].agg(["count","mean"])
    exits_str = " | ".join(f"{r}:{int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
    print(f"  Exits: {exits_str}")

    # Monthly
    print(f"  {'月份':<8} {'L筆':>4} {'L勝率':>6} {'L淨利':>8} {'S筆':>4} {'S勝率':>6} {'S淨利':>8} {'合計':>8}")
    for month in sorted(oos["month"].unique()):
        mt = oos[oos["month"]==month]
        lt = mt[mt["side"]=="long"]; st = mt[mt["side"]=="short"]
        l_n,s_n = len(lt),len(st)
        l_wr = (lt["pnl"]>0).mean() if l_n>0 else 0
        s_wr = (st["pnl"]>0).mean() if s_n>0 else 0
        l_pnl = lt["pnl"].sum(); s_pnl = st["pnl"].sum()
        print(f"  {month:<8} {l_n:>4} {l_wr:>6.0%} {l_pnl:>8.0f} {s_n:>4} {s_wr:>6.0%} {s_pnl:>8.0f} {l_pnl+s_pnl:>8.0f}")

    print(f"  MDD: ${mdd:.0f}, Worst Day: ${wd_pnl:.0f} ({wd_date})")

    # V9 Gates
    all_monthly = oos.groupby("month")["pnl"].sum()
    pos_months = (all_monthly > 0).sum()
    total_pnl = oos["pnl"].sum()
    total_wr = (oos["pnl"]>0).mean()
    w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
    pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999

    # Consec loss
    sorted_t = oos.sort_values("exit_dt")
    mc=0; cc=0; mcp=0; ccp=0
    for _,t in sorted_t.iterrows():
        if t["pnl"]<0: cc+=1; ccp+=t["pnl"]
        else:
            if cc>mc: mc=cc; mcp=ccp
            cc=0; ccp=0
    if cc>mc: mc=cc; mcp=ccp

    # Walk-forward 6-fold
    oos_bars = oos.copy()
    oos_bars["bar_idx"] = range(len(oos_bars))
    n_folds = 6
    fold_size = len(oos_bars) // n_folds
    wf_positive = 0
    for f in range(n_folds):
        start = f * fold_size
        end = start + fold_size if f < n_folds - 1 else len(oos_bars)
        fold_pnl = oos_bars.iloc[start:end]["pnl"].sum()
        if fold_pnl > 0:
            wf_positive += 1

    gates = [
        ("G1 OOS年PnL>=600", total_pnl >= 600, f"${total_pnl:.0f}"),
        ("G2 OOS WR>=55%", total_wr >= 0.55, f"{total_wr:.0%}"),
        ("G3 PF>=1.15", pf >= 1.15, f"{pf:.2f}"),
        ("G4 MDD<=1500", mdd <= 1500, f"${mdd:.0f}"),
        ("G5 最差月>=-300", all_monthly.min() >= -300, f"${all_monthly.min():.0f}"),
        ("G6 正月>=8/12", pos_months >= 8, f"{pos_months}/{len(all_monthly)}"),
        ("G7 WF>=4/6", wf_positive >= 4, f"{wf_positive}/6"),
        ("G8 IS也正PnL", tdf[is_mask]["pnl"].sum() > 0, f"${tdf[is_mask]['pnl'].sum():.0f}"),
    ]
    passed = 0
    print(f"\n  === V9 GATES ===")
    for g_name, ok, val in gates:
        status = "PASS" if ok else "FAIL"
        if ok: passed += 1
        print(f"    {g_name}: {val} [{status}]")
    print(f"  Total: {passed}/{len(gates)} PASS")
    return {"pnl": total_pnl, "wr": total_wr, "pf": pf, "mdd": mdd, "passed": passed}


# ===================================================================
print("=" * 80)
print("V9 Round 1: BTC-ETH Relative Divergence Deep Optimization")
print("=" * 80)

# --- Phase 1: Lookback + Threshold sweep ---
print("\n--- Phase 1: Lookback x Threshold x TP/SL ---")
results = []

for lookback in [3, 5, 7, 10]:
    for thresh in [0.015, 0.02, 0.025, 0.03]:
        for tp, sl in [(0.02, 0.03), (0.02, 0.035), (0.025, 0.035), (0.025, 0.04), (0.03, 0.04)]:
            for mh in [12, 18, 24]:
                col = f"rel_ret{lookback}"
                tdf, mdd, wd, wdd = run_backtest(
                    long_signal_func=lambda bar, i, c=col, t=thresh: not np.isnan(bar[c]) and bar[c] < -t,
                    short_signal_func=lambda bar, i, c=col, t=thresh: not np.isnan(bar[c]) and bar[c] > t,
                    tp_pct=tp, sl_pct=sl, max_hold=mh,
                )
                if len(tdf) == 0: continue
                tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                oos = tdf[tdf["entry_dt"] >= split_date]
                iis = tdf[tdf["entry_dt"] < split_date]
                if len(oos) == 0: continue

                n = len(oos)
                pnl = oos["pnl"].sum()
                is_pnl = iis["pnl"].sum()
                wr = (oos["pnl"]>0).mean()
                l = oos[oos["side"]=="long"]; s = oos[oos["side"]=="short"]
                l_pnl = l["pnl"].sum() if len(l)>0 else 0
                s_pnl = s["pnl"].sum() if len(s)>0 else 0
                l_wr = (l["pnl"]>0).mean() if len(l)>0 else 0
                s_wr = (s["pnl"]>0).mean() if len(s)>0 else 0
                w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
                pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999

                results.append({
                    "lb": lookback, "th": thresh, "tp": tp, "sl": sl, "mh": mh,
                    "n": n, "pnl": pnl, "is_pnl": is_pnl, "wr": wr, "pf": pf,
                    "l_pnl": l_pnl, "s_pnl": s_pnl, "l_wr": l_wr, "s_wr": s_wr,
                    "mdd": mdd,
                })

# Sort by PnL
results.sort(key=lambda x: x["pnl"], reverse=True)

# Print top 20
print(f"\n{'LB':>3} {'TH':>5} {'TP/SL':>8} {'MH':>3} {'N':>4} {'PnL':>7} {'IS_PnL':>7} {'WR':>5} {'PF':>5} {'L_PnL':>7} {'S_PnL':>7} {'MDD':>6}")
for r in results[:25]:
    flag = " *" if r["is_pnl"] > 0 and r["pnl"] > 0 else ""
    print(f"  {r['lb']:>3} {r['th']:>5.3f} {r['tp']*100:.1f}/{r['sl']*100:.1f} {r['mh']:>3} {r['n']:>4} {r['pnl']:>7.0f} {r['is_pnl']:>7.0f} {r['wr']:>5.0%} {r['pf']:>5.2f} {r['l_pnl']:>7.0f} {r['s_pnl']:>7.0f} {r['mdd']:>6.0f}{flag}")

# --- Phase 2: Add confirmations to best base ---
print("\n\n--- Phase 2: Add Confirmations to Top Configs ---")

# Find configs where both IS and OOS are positive
both_positive = [r for r in results if r["is_pnl"] > 0 and r["pnl"] > 0]
print(f"\nConfigs with BOTH IS and OOS positive: {len(both_positive)}")

if both_positive:
    for r in both_positive[:5]:
        print(f"  LB{r['lb']} TH{r['th']} TP{r['tp']*100:.1f}/SL{r['sl']*100:.1f} MH{r['mh']}: IS ${r['is_pnl']:.0f}, OOS ${r['pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f}")

# Take top 3 OOS configs and add volume/dist/session confirmations
print("\n--- Phase 2a: Volume confirmation ---")
top_configs = results[:5]
for r in top_configs:
    lb = r["lb"]; th = r["th"]; tp = r["tp"]; sl = r["sl"]; mh = r["mh"]
    col = f"rel_ret{lb}"

    for vol_th in [1.0, 1.3, 1.5]:
        name = f"LB{lb} TH{th} TP{tp*100:.0f}/SL{sl*100:.0f} MH{mh} Vol>{vol_th}"
        tdf, mdd, wd, wdd = run_backtest(
            long_signal_func=lambda bar, i, c=col, t=th, v=vol_th: (
                not np.isnan(bar[c]) and bar[c] < -t and
                not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v),
            short_signal_func=lambda bar, i, c=col, t=th, v=vol_th: (
                not np.isnan(bar[c]) and bar[c] > t and
                not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > v),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        if len(tdf) == 0: continue
        tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
        oos = tdf[tdf["entry_dt"] >= split_date]
        iis = tdf[tdf["entry_dt"] < split_date]
        if len(oos) == 0: continue
        n = len(oos); pnl = oos["pnl"].sum(); is_pnl = iis["pnl"].sum()
        wr = (oos["pnl"]>0).mean()
        w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
        pf_val = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
        flag = " **" if is_pnl > 0 and pnl > 600 else ""
        print(f"  [{name}] {n}t OOS ${pnl:.0f} IS ${is_pnl:.0f} WR{wr:.0%} PF{pf_val:.2f} MDD${mdd:.0f}{flag}")

# Phase 2b: dist_ema20 confirmation
print("\n--- Phase 2b: dist_ema20 confirmation ---")
for r in top_configs[:3]:
    lb = r["lb"]; th = r["th"]; tp = r["tp"]; sl = r["sl"]; mh = r["mh"]
    col = f"rel_ret{lb}"

    for dist_th in [0.005, 0.01, 0.015]:
        name = f"LB{lb} TH{th} TP{tp*100:.0f}/SL{sl*100:.0f} MH{mh} Dist>{dist_th*100:.1f}%"
        tdf, mdd, wd, wdd = run_backtest(
            long_signal_func=lambda bar, i, c=col, t=th, d=dist_th: (
                not np.isnan(bar[c]) and bar[c] < -t and
                not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -d),
            short_signal_func=lambda bar, i, c=col, t=th, d=dist_th: (
                not np.isnan(bar[c]) and bar[c] > t and
                not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > d),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        if len(tdf) == 0: continue
        tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
        oos = tdf[tdf["entry_dt"] >= split_date]
        iis = tdf[tdf["entry_dt"] < split_date]
        if len(oos) == 0: continue
        n = len(oos); pnl = oos["pnl"].sum(); is_pnl = iis["pnl"].sum()
        wr = (oos["pnl"]>0).mean()
        w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
        pf_val = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
        flag = " **" if is_pnl > 0 and pnl > 600 else ""
        print(f"  [{name}] {n}t OOS ${pnl:.0f} IS ${is_pnl:.0f} WR{wr:.0%} PF{pf_val:.2f} MDD${mdd:.0f}{flag}")

# Phase 2c: Exit cooldown
print("\n--- Phase 2c: Exit cooldown ---")
for r in top_configs[:3]:
    lb = r["lb"]; th = r["th"]; tp = r["tp"]; sl = r["sl"]; mh = r["mh"]
    col = f"rel_ret{lb}"

    for cd in [5, 8, 12]:
        name = f"LB{lb} TH{th} TP{tp*100:.0f}/SL{sl*100:.0f} MH{mh} CD{cd}"
        tdf, mdd, wd, wdd = run_backtest(
            long_signal_func=lambda bar, i, c=col, t=th: not np.isnan(bar[c]) and bar[c] < -t,
            short_signal_func=lambda bar, i, c=col, t=th: not np.isnan(bar[c]) and bar[c] > t,
            tp_pct=tp, sl_pct=sl, max_hold=mh, exit_cd=cd,
        )
        if len(tdf) == 0: continue
        tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
        oos = tdf[tdf["entry_dt"] >= split_date]
        iis = tdf[tdf["entry_dt"] < split_date]
        if len(oos) == 0: continue
        n = len(oos); pnl = oos["pnl"].sum(); is_pnl = iis["pnl"].sum()
        wr = (oos["pnl"]>0).mean()
        w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
        pf_val = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
        flag = " **" if is_pnl > 0 and pnl > 600 else ""
        print(f"  [{name}] {n}t OOS ${pnl:.0f} IS ${is_pnl:.0f} WR{wr:.0%} PF{pf_val:.2f} MDD${mdd:.0f}{flag}")

# Phase 2d: Different L/S hours
print("\n--- Phase 2d: Session optimization ---")
for r in top_configs[:3]:
    lb = r["lb"]; th = r["th"]; tp = r["tp"]; sl = r["sl"]; mh = r["mh"]
    col = f"rel_ret{lb}"

    # Try different L block hours
    for l_block in [{0,1,2,3}, {0,1,2,3,4,5}, set()]:
        for s_allow in [set(range(11,22)), set(range(8,22)), set(range(24))]:
            name = f"LB{lb} TH{th} LB_hrs={len(l_block)} S_hrs={len(s_allow)}"
            tdf, mdd, wd, wdd = run_backtest(
                long_signal_func=lambda bar, i, c=col, t=th: not np.isnan(bar[c]) and bar[c] < -t,
                short_signal_func=lambda bar, i, c=col, t=th: not np.isnan(bar[c]) and bar[c] > t,
                tp_pct=tp, sl_pct=sl, max_hold=mh,
                l_block_hours=l_block, s_allow_hours=s_allow,
            )
            if len(tdf) == 0: continue
            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
            oos = tdf[tdf["entry_dt"] >= split_date]
            iis = tdf[tdf["entry_dt"] < split_date]
            if len(oos) == 0: continue
            n = len(oos); pnl = oos["pnl"].sum(); is_pnl = iis["pnl"].sum()
            wr = (oos["pnl"]>0).mean()
            w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
            pf_val = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
            flag = " **" if is_pnl > 0 and pnl > 600 else ""
            if pnl > 400:  # only print promising
                print(f"  [{name}] {n}t OOS ${pnl:.0f} IS ${is_pnl:.0f} WR{wr:.0%} PF{pf_val:.2f}{flag}")


# === Full report for overall best ===
print("\n\n" + "=" * 80)
print("=== BEST CANDIDATE: Full V9 Gate Check ===")
print("=" * 80)

# Find best config where both IS and OOS positive
if both_positive:
    best = both_positive[0]
else:
    best = results[0]

lb = best["lb"]; th = best["th"]; tp = best["tp"]; sl = best["sl"]; mh = best["mh"]
col = f"rel_ret{lb}"
print(f"\nBest: LB{lb} TH{th} TP{tp*100:.1f}/SL{sl*100:.1f} MH{mh}")
tdf, mdd, wd_pnl, wd_date = run_backtest(
    long_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
    short_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
    tp_pct=tp, sl_pct=sl, max_hold=mh,
)
full_report(tdf, "Best", mdd, wd_pnl, wd_date)

# Also try the best OOS regardless of IS
if results[0] != best:
    best2 = results[0]
    lb2 = best2["lb"]; th2 = best2["th"]; tp2 = best2["tp"]; sl2 = best2["sl"]; mh2 = best2["mh"]
    col2 = f"rel_ret{lb2}"
    print(f"\nBest OOS: LB{lb2} TH{th2} TP{tp2*100:.1f}/SL{sl2*100:.1f} MH{mh2}")
    tdf2, mdd2, wd_pnl2, wd_date2 = run_backtest(
        long_signal_func=lambda bar, i: not np.isnan(bar[col2]) and bar[col2] < -th2,
        short_signal_func=lambda bar, i: not np.isnan(bar[col2]) and bar[col2] > th2,
        tp_pct=tp2, sl_pct=sl2, max_hold=mh2,
    )
    full_report(tdf2, "Best OOS", mdd2, wd_pnl2, wd_date2)
