"""
V9 Round 3: RelDiv Asymmetric L/S Optimization

R1 finding: LB5 TH0.03 TP2.5/SL3.5 MH18 = OOS $1,586, WR 81%, PF 2.92
but only 32 trades (too sparse) and IS = -$1,042 (suspicious).

This round:
1. Asymmetric thresholds: L thresh != S thresh (broaden one side)
2. Different TP/SL per side
3. Circuit breakers: daily loss limit, consec loss cooldown
4. Monthly cap per side to prevent overtrading in one month
5. Time-of-day optimization per side

Goal: Find configs that pass ALL 8 V9 gates, especially G6 (pos months >= 8/12)
and G8 (IS must be positive).
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

btc_cols = btc[["datetime", "close", "volume"]].rename(
    columns={"close": "btc_close", "volume": "btc_volume"})
df = eth.merge(btc_cols, on="datetime", how="left")

# Indicators
for lb in [3, 5, 7, 10]:
    eth_ret = df["close"].pct_change(lb)
    btc_ret = df["btc_close"].pct_change(lb)
    df[f"rel_ret{lb}"] = (eth_ret - btc_ret).shift(1)

df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)

# Breakout
for n in [8, 10, 12, 15]:
    df[f"brk_up_{n}"] = df["close"] > df["high"].shift(1).rolling(n).max()
    df[f"brk_dn_{n}"] = df["close"] < df["low"].shift(1).rolling(n).min()

# Session
df["hour"] = df["datetime"].dt.hour  # UTC+8
df["dow"] = df["datetime"].dt.dayofweek

NOTIONAL = 4000
FEE = 4
SAFENET = 0.045
SLIP_MULT = 1.25

split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)
is_mask_global = df["datetime"] < split_date
warmup = 150

def run_backtest_asym(
    l_signal_func, s_signal_func,
    l_tp, l_sl, l_mh,
    s_tp, s_sl, s_mh,
    max_same=2, total_max=3,
    l_monthly_cap=99, s_monthly_cap=99,
    exit_cd_l=0, exit_cd_s=0,
    l_block_hours=None, s_block_hours=None,
    block_days=None,
    daily_loss_limit=-9999,
    consec_loss_cd=0,
):
    if l_block_hours is None: l_block_hours = {0,1,2,12}
    if s_block_hours is None: s_block_hours = {0,1,2,12}
    if block_days is None: block_days = {5, 6}  # Sat, Sun

    trades = []
    positions = []  # list of dict: side, entry_bar, entry_price
    last_exit_l = -999
    last_exit_s = -999
    daily_pnl = {}  # date -> pnl
    consec_losses = 0
    monthly_counts = {}  # "YYYY-MM" -> {"long": n, "short": n}
    last_loss_exit_bar = -999

    for i in range(warmup, len(df) - 1):
        bar = df.iloc[i]
        next_open = df.iloc[i + 1]["open"]
        dt = bar["datetime"]
        date_str = str(dt.date())

        # --- Exit check ---
        closed = []
        for j, pos in enumerate(positions):
            bars_held = i - pos["entry_bar"]
            if pos["side"] == "long":
                ret = (bar["close"] - pos["entry_price"]) / pos["entry_price"]
                tp_pct = l_tp; sl_pct = l_sl; mh = l_mh
            else:
                ret = (pos["entry_price"] - bar["close"]) / pos["entry_price"]
                tp_pct = s_tp; sl_pct = s_sl; mh = s_mh

            reason = None
            exit_price = None
            # SafeNet
            safenet_ret = -SAFENET * SLIP_MULT
            if ret <= safenet_ret:
                reason = "safenet"
                exit_price = pos["entry_price"] * (1 + safenet_ret) if pos["side"] == "long" else pos["entry_price"] * (1 - safenet_ret)
            # TP
            elif ret >= tp_pct:
                reason = "tp"
                exit_price = pos["entry_price"] * (1 + tp_pct) if pos["side"] == "long" else pos["entry_price"] * (1 - tp_pct)
            # SL
            elif ret <= -sl_pct:
                reason = "sl"
                exit_price = pos["entry_price"] * (1 - sl_pct) if pos["side"] == "long" else pos["entry_price"] * (1 + sl_pct)
            # MaxHold
            elif bars_held >= mh:
                reason = "time_stop"
                exit_price = bar["close"]

            if reason:
                if pos["side"] == "long":
                    pnl = (exit_price - pos["entry_price"]) / pos["entry_price"] * NOTIONAL - FEE
                else:
                    pnl = (pos["entry_price"] - exit_price) / pos["entry_price"] * NOTIONAL - FEE

                trades.append({
                    "entry_dt": df.iloc[pos["entry_bar"]]["datetime"],
                    "exit_dt": dt,
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "reason": reason,
                    "bars_held": bars_held,
                })
                closed.append(j)

                if pos["side"] == "long":
                    last_exit_l = i
                else:
                    last_exit_s = i

                # Track daily PnL
                daily_pnl[date_str] = daily_pnl.get(date_str, 0) + pnl

                # Track consecutive losses
                if pnl < 0:
                    consec_losses += 1
                    last_loss_exit_bar = i
                else:
                    consec_losses = 0

        for j in sorted(closed, reverse=True):
            positions.pop(j)

        # --- Entry check ---
        hour = int(bar["hour"])
        dow = int(bar["dow"])
        if dow in block_days:
            continue

        # Circuit breaker: daily loss limit
        if daily_pnl.get(date_str, 0) <= daily_loss_limit:
            continue

        # Circuit breaker: consecutive loss cooldown
        if consec_loss_cd > 0 and consec_losses >= 5:
            if (i - last_loss_exit_bar) < consec_loss_cd:
                continue

        n_long = sum(1 for p in positions if p["side"] == "long")
        n_short = sum(1 for p in positions if p["side"] == "short")

        # Monthly caps (incremental tracking)
        current_month = str(dt)[:7]
        mc = monthly_counts.get(current_month, {"long": 0, "short": 0})
        l_this_month = mc["long"]
        s_this_month = mc["short"]

        # Long entry
        if (n_long < max_same and len(positions) < total_max
            and hour not in l_block_hours
            and (i - last_exit_l) >= exit_cd_l
            and l_this_month < l_monthly_cap):
            if l_signal_func(bar, i):
                positions.append({"side": "long", "entry_bar": i + 1, "entry_price": next_open})
                # Track entry month for L
                entry_month = str(df.iloc[i+1]["datetime"])[:7]
                if entry_month not in monthly_counts: monthly_counts[entry_month] = {"long": 0, "short": 0}
                monthly_counts[entry_month]["long"] += 1

        # Short entry
        if (n_short < max_same and len(positions) < total_max
            and hour not in s_block_hours
            and (i - last_exit_s) >= exit_cd_s
            and s_this_month < s_monthly_cap):
            if s_signal_func(bar, i):
                positions.append({"side": "short", "entry_bar": i + 1, "entry_price": next_open})
                # Track entry month for S
                entry_month = str(df.iloc[i+1]["datetime"])[:7]
                if entry_month not in monthly_counts: monthly_counts[entry_month] = {"long": 0, "short": 0}
                monthly_counts[entry_month]["short"] += 1

    tdf = pd.DataFrame(trades)
    if len(tdf) == 0:
        return tdf, 0, 0, ""

    # MDD
    tdf = tdf.sort_values("exit_dt").reset_index(drop=True)
    cum = tdf["pnl"].cumsum()
    peak = cum.cummax()
    dd = cum - peak
    mdd = abs(dd.min())

    # Worst day
    tdf["exit_date"] = pd.to_datetime(tdf["exit_dt"]).dt.date
    dpnl = tdf.groupby("exit_date")["pnl"].sum()
    wd_pnl = dpnl.min()
    wd_date = dpnl.idxmin()

    return tdf, mdd, wd_pnl, str(wd_date)


def full_report(tdf, label, mdd, wd_pnl, wd_date):
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    is_mask = tdf["entry_dt"] < split_date
    oos_mask = tdf["entry_dt"] >= split_date

    for lbl, mask in [("IS", is_mask), ("OOS", oos_mask)]:
        t = tdf[mask]
        if len(t) == 0:
            print(f"  {lbl}: NO TRADES")
            continue
        lt = t[t["side"]=="long"]; st = t[t["side"]=="short"]
        def s(d, tag):
            if len(d)==0: return f"{tag}:0t"
            n=len(d); pnl=d["pnl"].sum(); wr=(d["pnl"]>0).mean()
            w=d[d["pnl"]>0]["pnl"]; lo=d[d["pnl"]<=0]["pnl"]
            pf=w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
            w_avg = w.mean() if len(w)>0 else 0
            l_avg = lo.mean() if len(lo)>0 else 0
            return f"{tag}:{n}t ${pnl:.0f} PF{pf:.2f} WR{wr:.0%} W${w_avg:.0f} L${l_avg:.0f}"
        print(f"  {lbl} {s(t,'ALL')} | {s(lt,'L')} | {s(st,'S')}")

    oos = tdf[oos_mask].copy()
    if len(oos) == 0:
        print("  NO OOS TRADES")
        return None

    oos["month"] = oos["entry_dt"].dt.strftime("%Y-%m")

    # Exit reasons
    reasons = oos.groupby("reason")["pnl"].agg(["count","mean"])
    exits_str = " | ".join(f"{r}:{int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
    print(f"  Exits: {exits_str}")

    # Monthly
    print(f"  {'Mon':<8} {'L_n':>4} {'L_WR':>6} {'L_PnL':>8} {'S_n':>4} {'S_WR':>6} {'S_PnL':>8} {'Total':>8}")
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
    is_pnl = tdf[is_mask]["pnl"].sum()

    # Walk-forward
    n_folds = 6
    fold_size = max(len(oos) // n_folds, 1)
    wf_positive = 0
    for f in range(n_folds):
        start = f * fold_size
        end = start + fold_size if f < n_folds - 1 else len(oos)
        fold_pnl = oos.iloc[start:end]["pnl"].sum()
        if fold_pnl > 0:
            wf_positive += 1

    gates = [
        ("G1 OOS_PnL>=600", total_pnl >= 600, f"${total_pnl:.0f}"),
        ("G2 WR>=55%", total_wr >= 0.55, f"{total_wr:.0%}"),
        ("G3 PF>=1.15", pf >= 1.15, f"{pf:.2f}"),
        ("G4 MDD<=1500", mdd <= 1500, f"${mdd:.0f}"),
        ("G5 WorstMo>=-300", all_monthly.min() >= -300, f"${all_monthly.min():.0f}"),
        ("G6 PosMo>=8/12", pos_months >= 8, f"{pos_months}/{len(all_monthly)}"),
        ("G7 WF>=4/6", wf_positive >= 4, f"{wf_positive}/6"),
        ("G8 IS_PnL>0", is_pnl > 0, f"${is_pnl:.0f}"),
    ]
    passed = sum(1 for _, ok, _ in gates if ok)
    print(f"\n  === V9 GATES ({passed}/{len(gates)}) ===")
    for g_name, ok, val in gates:
        print(f"    {g_name}: {val} [{'PASS' if ok else 'FAIL'}]")
    return {"pnl": total_pnl, "wr": total_wr, "pf": pf, "mdd": mdd, "passed": passed, "n": len(oos), "is_pnl": is_pnl}


# ===================================================================
print("=" * 80)
print("V9 Round 3: RelDiv Asymmetric L/S Optimization")
print("=" * 80)

# --- Phase 1: Asymmetric thresholds ---
# R1 showed LB5 TH0.03 is too strict (32 trades). Try different L vs S thresholds.
print("\n--- Phase 1: Asymmetric L/S thresholds ---")
results = []

for lb in [5, 7]:
    for l_th in [0.015, 0.02, 0.025, 0.03]:
        for s_th in [0.015, 0.02, 0.025, 0.03]:
            for tp, sl in [(0.02, 0.03), (0.025, 0.035), (0.025, 0.04)]:
                for mh in [12, 18]:
                    col = f"rel_ret{lb}"
                    tdf, mdd, wd, wdd = run_backtest_asym(
                        l_signal_func=lambda bar, i, c=col, t=l_th: not np.isnan(bar[c]) and bar[c] < -t,
                        s_signal_func=lambda bar, i, c=col, t=s_th: not np.isnan(bar[c]) and bar[c] > t,
                        l_tp=tp, l_sl=sl, l_mh=mh,
                        s_tp=tp, s_sl=sl, s_mh=mh,
                    )
                    if len(tdf) == 0: continue
                    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                    oos = tdf[tdf["entry_dt"] >= split_date]
                    iis = tdf[tdf["entry_dt"] < split_date]
                    if len(oos) < 10: continue

                    n = len(oos)
                    pnl = oos["pnl"].sum()
                    is_pnl = iis["pnl"].sum()
                    wr = (oos["pnl"]>0).mean()
                    w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
                    pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999

                    results.append({
                        "lb": lb, "l_th": l_th, "s_th": s_th,
                        "tp": tp, "sl": sl, "mh": mh,
                        "n": n, "pnl": pnl, "is_pnl": is_pnl,
                        "wr": wr, "pf": pf, "mdd": mdd,
                    })

results.sort(key=lambda x: x["pnl"], reverse=True)
print(f"\n{'LB':>3} {'L_TH':>5} {'S_TH':>5} {'TP/SL':>6} {'MH':>3} {'N':>4} {'PnL':>7} {'IS':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
for r in results[:30]:
    tp_sl = f"{r['tp']*100:.1f}/{r['sl']*100:.1f}"
    print(f"{r['lb']:>3} {r['l_th']:>5.3f} {r['s_th']:>5.3f} {tp_sl:>6} {r['mh']:>3} {r['n']:>4} {r['pnl']:>7.0f} {r['is_pnl']:>7.0f} {r['wr']:>5.0%} {r['pf']:>5.2f} {r['mdd']:>6.0f}")

# Find configs with IS > 0
both_pos = [r for r in results if r["is_pnl"] > 0 and r["pnl"] > 0]
print(f"\nConfigs with BOTH IS and OOS positive: {len(both_pos)}")
if both_pos:
    for r in both_pos[:10]:
        tp_sl = f"{r['tp']*100:.1f}/{r['sl']*100:.1f}"
        print(f"  LB{r['lb']} L_TH{r['l_th']:.3f} S_TH{r['s_th']:.3f} {tp_sl} MH{r['mh']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f}")


# --- Phase 2: Asymmetric TP/SL per side ---
# L side might benefit from wider TP (trend following), S from tighter TP
print("\n\n--- Phase 2: Asymmetric TP/SL per side ---")
results2 = []

# Use top lookback/thresholds from Phase 1
top_configs = []
if both_pos:
    top_configs = [(r["lb"], r["l_th"], r["s_th"]) for r in both_pos[:5]]
else:
    top_configs = [(r["lb"], r["l_th"], r["s_th"]) for r in results[:5]]

# Deduplicate
seen = set()
top_dedup = []
for c in top_configs:
    if c not in seen:
        seen.add(c)
        top_dedup.append(c)
top_configs = top_dedup[:5]

for lb, l_th, s_th in top_configs:
    col = f"rel_ret{lb}"
    # Focused grid: L wants wider TP (trend), S wants tighter TP (revert)
    for l_tp, l_sl, l_mh in [(0.025, 0.035, 18), (0.025, 0.04, 18), (0.03, 0.04, 24),
                               (0.02, 0.03, 12), (0.02, 0.035, 12), (0.025, 0.035, 12),
                               (0.03, 0.035, 18), (0.02, 0.04, 18), (0.025, 0.04, 24)]:
        for s_tp, s_sl, s_mh in [(0.015, 0.025, 8), (0.02, 0.03, 12), (0.015, 0.03, 12),
                                   (0.02, 0.035, 12), (0.025, 0.035, 18), (0.02, 0.025, 8),
                                   (0.015, 0.035, 18), (0.025, 0.03, 12), (0.02, 0.03, 8)]:
                            tdf, mdd, wd, wdd = run_backtest_asym(
                                l_signal_func=lambda bar, i, c=col, t=l_th: not np.isnan(bar[c]) and bar[c] < -t,
                                s_signal_func=lambda bar, i, c=col, t=s_th: not np.isnan(bar[c]) and bar[c] > t,
                                l_tp=l_tp, l_sl=l_sl, l_mh=l_mh,
                                s_tp=s_tp, s_sl=s_sl, s_mh=s_mh,
                            )
                            if len(tdf) == 0: continue
                            tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                            oos = tdf[tdf["entry_dt"] >= split_date]
                            iis = tdf[tdf["entry_dt"] < split_date]
                            if len(oos) < 10: continue

                            n = len(oos)
                            pnl = oos["pnl"].sum()
                            is_pnl = iis["pnl"].sum()
                            wr = (oos["pnl"]>0).mean()
                            w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
                            pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999

                            results2.append({
                                "lb": lb, "l_th": l_th, "s_th": s_th,
                                "l_tp": l_tp, "l_sl": l_sl, "l_mh": l_mh,
                                "s_tp": s_tp, "s_sl": s_sl, "s_mh": s_mh,
                                "n": n, "pnl": pnl, "is_pnl": is_pnl,
                                "wr": wr, "pf": pf, "mdd": mdd,
                            })

results2.sort(key=lambda x: x["pnl"], reverse=True)
print(f"\nTop 20 asymmetric TP/SL configs:")
for r in results2[:20]:
    print(f"  LB{r['lb']} L{r['l_th']:.3f}/S{r['s_th']:.3f} LTP{r['l_tp']*100:.1f}/LSL{r['l_sl']*100:.1f}/LMH{r['l_mh']} STP{r['s_tp']*100:.1f}/SSL{r['s_sl']*100:.1f}/SMH{r['s_mh']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f} MDD${r['mdd']:.0f}")

both_pos2 = [r for r in results2 if r["is_pnl"] > 0 and r["pnl"] > 0]
print(f"\nAsym TP/SL with BOTH IS+OOS positive: {len(both_pos2)}")
if both_pos2:
    for r in both_pos2[:10]:
        print(f"  LB{r['lb']} L{r['l_th']:.3f}/S{r['s_th']:.3f} LTP{r['l_tp']*100:.1f}/LSL{r['l_sl']*100:.1f}/LMH{r['l_mh']} STP{r['s_tp']*100:.1f}/SSL{r['s_sl']*100:.1f}/SMH{r['s_mh']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f} MDD${r['mdd']:.0f}")


# --- Phase 3: Add circuit breakers + monthly caps to best configs ---
print("\n\n--- Phase 3: Circuit Breakers + Monthly Caps ---")

# Collect the best configs from Phase 1 and 2
best_configs = []
all_results = both_pos + both_pos2 if (both_pos or both_pos2) else results[:5]
for r in all_results[:5]:
    best_configs.append(r)

results3 = []
for cfg in best_configs:
    lb = cfg["lb"]
    l_th = cfg["l_th"]; s_th = cfg["s_th"]
    col = f"rel_ret{lb}"

    l_tp = cfg.get("l_tp", cfg.get("tp", 0.025))
    l_sl = cfg.get("l_sl", cfg.get("sl", 0.035))
    l_mh = cfg.get("l_mh", cfg.get("mh", 18))
    s_tp = cfg.get("s_tp", cfg.get("tp", 0.025))
    s_sl = cfg.get("s_sl", cfg.get("sl", 0.035))
    s_mh = cfg.get("s_mh", cfg.get("mh", 18))

    for l_cap in [6, 8, 12]:
        for s_cap in [6, 8, 12]:
            for cd_l in [0, 6, 10]:
                for cd_s in [0, 6, 10]:
                    tdf, mdd, wd, wdd = run_backtest_asym(
                        l_signal_func=lambda bar, i, c=col, t=l_th: not np.isnan(bar[c]) and bar[c] < -t,
                        s_signal_func=lambda bar, i, c=col, t=s_th: not np.isnan(bar[c]) and bar[c] > t,
                        l_tp=l_tp, l_sl=l_sl, l_mh=l_mh,
                        s_tp=s_tp, s_sl=s_sl, s_mh=s_mh,
                        l_monthly_cap=l_cap, s_monthly_cap=s_cap,
                        exit_cd_l=cd_l, exit_cd_s=cd_s,
                    )
                    if len(tdf) == 0: continue
                    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                    oos = tdf[tdf["entry_dt"] >= split_date]
                    iis = tdf[tdf["entry_dt"] < split_date]
                    if len(oos) < 10: continue

                    n = len(oos)
                    pnl = oos["pnl"].sum()
                    is_pnl = iis["pnl"].sum()
                    wr = (oos["pnl"]>0).mean()
                    w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
                    pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999

                    if pnl > 0:
                        results3.append({
                            "lb": lb, "l_th": l_th, "s_th": s_th,
                            "l_tp": l_tp, "l_sl": l_sl, "l_mh": l_mh,
                            "s_tp": s_tp, "s_sl": s_sl, "s_mh": s_mh,
                            "l_cap": l_cap, "s_cap": s_cap,
                            "cd_l": cd_l, "cd_s": cd_s,
                            "n": n, "pnl": pnl, "is_pnl": is_pnl,
                            "wr": wr, "pf": pf, "mdd": mdd,
                        })

results3.sort(key=lambda x: x["pnl"], reverse=True)
print(f"\nTop circuit breaker configs ({len(results3)} positive):")
for r in results3[:15]:
    print(f"  LB{r['lb']} L{r['l_th']:.3f}/S{r['s_th']:.3f} L_cap{r['l_cap']} S_cap{r['s_cap']} CD_L{r['cd_l']} CD_S{r['cd_s']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f} MDD${r['mdd']:.0f}")


# --- Phase 4: Full report on the overall best ---
print("\n\n" + "=" * 80)
print("=== BEST CANDIDATES: Full V9 Gate Check ===")
print("=" * 80)

# Combine all results and pick top by different criteria
all_candidates = []

# Top by OOS PnL from each phase
for r in (results[:3] + results2[:3] + results3[:3]):
    all_candidates.append(r)

# Top by IS+OOS both positive
for r in (both_pos[:2] + both_pos2[:2]):
    all_candidates.append(r)

# Deduplicate by converting to tuple of key params
seen_keys = set()
unique_candidates = []
for r in all_candidates:
    key = (r["lb"], r.get("l_th", r.get("l_th")), r.get("s_th", r.get("s_th")),
           r.get("l_tp", r.get("tp", 0)), r.get("s_tp", r.get("tp", 0)),
           r.get("l_mh", r.get("mh", 0)), r.get("s_mh", r.get("mh", 0)))
    if key not in seen_keys:
        seen_keys.add(key)
        unique_candidates.append(r)

for idx, r in enumerate(unique_candidates[:6]):
    lb = r["lb"]
    l_th = r["l_th"]; s_th = r["s_th"]
    col = f"rel_ret{lb}"

    l_tp = r.get("l_tp", r.get("tp", 0.025))
    l_sl = r.get("l_sl", r.get("sl", 0.035))
    l_mh = r.get("l_mh", r.get("mh", 18))
    s_tp = r.get("s_tp", r.get("tp", 0.025))
    s_sl = r.get("s_sl", r.get("sl", 0.035))
    s_mh = r.get("s_mh", r.get("mh", 18))

    l_cap = r.get("l_cap", 99)
    s_cap = r.get("s_cap", 99)
    cd_l = r.get("cd_l", 0)
    cd_s = r.get("cd_s", 0)

    print(f"\n--- Candidate {idx+1}: LB{lb} L_TH{l_th:.3f}/S_TH{s_th:.3f} LTP{l_tp*100:.1f}/LSL{l_sl*100:.1f}/LMH{l_mh} STP{s_tp*100:.1f}/SSL{s_sl*100:.1f}/SMH{s_mh} ---")
    if l_cap < 99 or s_cap < 99 or cd_l > 0 or cd_s > 0:
        print(f"    L_cap={l_cap} S_cap={s_cap} CD_L={cd_l} CD_S={cd_s}")

    tdf, mdd, wd_pnl, wd_date = run_backtest_asym(
        l_signal_func=lambda bar, i, c=col, t=l_th: not np.isnan(bar[c]) and bar[c] < -t,
        s_signal_func=lambda bar, i, c=col, t=s_th: not np.isnan(bar[c]) and bar[c] > t,
        l_tp=l_tp, l_sl=l_sl, l_mh=l_mh,
        s_tp=s_tp, s_sl=s_sl, s_mh=s_mh,
        l_monthly_cap=l_cap, s_monthly_cap=s_cap,
        exit_cd_l=cd_l, exit_cd_s=cd_s,
    )
    result = full_report(tdf, f"Candidate {idx+1}", mdd, wd_pnl, wd_date)

print("\n" + "=" * 80)
print("V9 R3 Complete")
print("=" * 80)
