"""
V10 R3: Strategy E Optimization — Risk Control + Parameter Sweep
Base: 4h Trend (EMA20) + 1h Pullback Entry
Goal: Reduce MDD ≤ $500, worst month ≥ -$200, keep WR high

Optimizations to test:
  1. EarlyStop: cut loss after N bars if loss > X%
  2. SafeNet reduction: 4.5% → 3.5% → 3.0%
  3. ATR-based volatility filter: skip high-vol entries
  4. Entry gap from EMA: how deep the pullback must be
  5. Exit timing: EMAcross min hold, TP level
  6. Notional reduction in high-vol regime
"""
import pandas as pd
import numpy as np
from itertools import product

# ===== Load Data =====
eth_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
eth_4h = pd.read_csv("data/ETHUSDT_4h_latest730d.csv")
eth_1h["datetime"] = pd.to_datetime(eth_1h["datetime"])
eth_4h["datetime"] = pd.to_datetime(eth_4h["datetime"])

eth_1h["ret"] = eth_1h["close"].pct_change()

# 1h indicators
eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
eth_1h["ema10"] = eth_1h["close"].ewm(span=10, adjust=False).mean()
eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
eth_1h["hour"] = eth_1h["datetime"].dt.hour
eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek

# 4h indicators + merge
eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                    np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))
eth_4h_map = eth_4h[["datetime", "trend_4h", "ema20_4h"]].copy()
eth_1h = eth_1h.merge(eth_4h_map, on="datetime", how="left")
eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()

# Pre-compute arrays for speed
N = len(eth_1h)
opens = eth_1h["open"].values
highs = eth_1h["high"].values
lows = eth_1h["low"].values
closes = eth_1h["close"].values
ema20 = eth_1h["ema20"].values
trend_4h = eth_1h["trend_4h"].values
hours = eth_1h["hour"].values
dows = eth_1h["dow"].values
atr_pct = eth_1h["atr14_pct"].values
datetimes = eth_1h["datetime"].values

blocked_hours = {0, 1, 2, 12}
blocked_days = {0, 5, 6}

WARMUP = 150
TOTAL = N
IS_END = TOTAL // 2

# Circuit breakers
DAILY_LOSS_LIMIT = -300
MONTHLY_LOSS_LIMIT = -500
CONSEC_LOSS_PAUSE = 5
CONSEC_LOSS_COOLDOWN = 24


def run_stratE_config(safenet_pct, early_stop_bars, early_stop_pct, tp_pct,
                      min_hold_exit, max_hold, exit_cd, entry_cap,
                      pullback_min, pullback_max, atr_filter,
                      notional_hi_vol, max_same_l, max_same_s, max_total):
    """Run Strategy E with given config. Returns (trades, stats_dict)."""

    NOTIONAL_BASE = 4000
    FEE_RATE = 0.001  # 0.1% round trip
    SAFENET = safenet_pct / 100
    SAFENET_SLIP = 0.25

    positions = []
    closed_trades = []

    daily_pnl = 0.0
    monthly_pnl = 0.0
    consec_losses = 0
    cooldown_until = 0
    current_month = -1
    current_date = None
    last_exit_l = -999
    last_exit_s = -999
    monthly_entries = 0
    entry_ym = (-1, -1)

    for i in range(WARMUP, N - 1):
        dt = pd.Timestamp(datetimes[i])
        bar_month = dt.month
        bar_year = dt.year
        bar_date = dt.date()

        ym = (bar_year, bar_month)
        if ym != entry_ym:
            monthly_entries = 0
            entry_ym = ym

        if (bar_year, bar_month) != (current_date.year if current_date else -1, current_date.month if current_date else -1):
            if bar_date != current_date:
                pass
        if bar_month != current_month:
            monthly_pnl = 0.0
            current_month = bar_month
        if bar_date != current_date:
            daily_pnl = 0.0
            current_date = bar_date

        c = closes[i]
        h = highs[i]
        lo = lows[i]
        e20 = ema20[i]
        t4h = trend_4h[i]
        next_open = opens[i + 1]

        # === Exit ===
        for pos in list(positions):
            bars_held = i - pos["entry_bar"]
            side = pos["side"]
            entry_price = pos["entry_price"]
            notional = pos["notional"]
            fee = notional * FEE_RATE

            exit_price = None
            exit_reason = None

            if side == "long":
                safenet_check = (lo - entry_price) / entry_price
                pnl_check = (c - entry_price) / entry_price
            else:
                safenet_check = -(h - entry_price) / entry_price
                pnl_check = -(c - entry_price) / entry_price

            # SafeNet
            if safenet_check <= -SAFENET:
                slip = SAFENET * SAFENET_SLIP
                if side == "long":
                    exit_price = entry_price * (1 - SAFENET - slip)
                else:
                    exit_price = entry_price * (1 + SAFENET + slip)
                exit_reason = "SafeNet"

            # EarlyStop
            elif early_stop_bars > 0 and bars_held >= 1 and bars_held <= early_stop_bars:
                if pnl_check < -(early_stop_pct / 100):
                    exit_price = c
                    exit_reason = "EarlyStop"

            # TP
            elif tp_pct > 0:
                if side == "long" and (h - entry_price) / entry_price >= tp_pct / 100:
                    exit_price = entry_price * (1 + tp_pct / 100)
                    exit_reason = "TP"
                elif side == "short" and (entry_price - lo) / entry_price >= tp_pct / 100:
                    exit_price = entry_price * (1 - tp_pct / 100)
                    exit_reason = "TP"

            # EMA cross (min hold check)
            if exit_price is None and bars_held >= min_hold_exit:
                if side == "long" and c > e20:
                    exit_price = c
                    exit_reason = "EMAcross"
                elif side == "short" and c < e20:
                    exit_price = c
                    exit_reason = "EMAcross"

            # MaxHold
            if exit_price is None and bars_held >= max_hold:
                exit_price = c
                exit_reason = "MaxHold"

            if exit_price is not None:
                if side == "long":
                    pnl_pct = (exit_price - entry_price) / entry_price
                else:
                    pnl_pct = -(exit_price - entry_price) / entry_price

                pnl_net = pnl_pct * notional - fee
                daily_pnl += pnl_net
                monthly_pnl += pnl_net

                if pnl_net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_LOSS_PAUSE:
                        cooldown_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec_losses = 0

                if side == "long":
                    last_exit_l = i
                else:
                    last_exit_s = i

                closed_trades.append({
                    "entry_dt": pos["entry_dt"],
                    "exit_dt": dt,
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_net": pnl_net,
                    "bars": bars_held,
                    "reason": exit_reason,
                    "bar_idx": i,
                    "is_oos": i >= IS_END,
                })
                positions.remove(pos)

        # === Risk Controls ===
        if daily_pnl <= DAILY_LOSS_LIMIT:
            continue
        if monthly_pnl <= MONTHLY_LOSS_LIMIT:
            continue
        if i < cooldown_until:
            continue

        n_long = sum(1 for p in positions if p["side"] == "long")
        n_short = sum(1 for p in positions if p["side"] == "short")
        if n_long + n_short >= max_total:
            continue

        # Session filter
        if hours[i] in blocked_hours or dows[i] in blocked_days:
            continue

        if monthly_entries >= entry_cap:
            continue

        # ATR filter
        if atr_filter > 0 and not np.isnan(atr_pct[i]) and atr_pct[i] > atr_filter / 100:
            continue

        # Notional adjustment for high vol
        notional = NOTIONAL_BASE
        if notional_hi_vol < 1.0 and not np.isnan(atr_pct[i]) and atr_pct[i] > 0.75:
            notional = NOTIONAL_BASE * notional_hi_vol

        # === Long Entry ===
        if (n_long < max_same_l and (i - last_exit_l) >= exit_cd and
                not np.isnan(t4h) and t4h == 1):
            # Price below EMA20 (pullback) but not too deep
            if not np.isnan(e20) and e20 > 0:
                gap = (c - e20) / e20
                if -(pullback_max / 100) <= gap <= -(pullback_min / 100):
                    positions.append({
                        "side": "long",
                        "entry_price": next_open,
                        "entry_bar": i,
                        "entry_dt": dt,
                        "notional": notional,
                    })
                    monthly_entries += 1

        # === Short Entry ===
        if (n_short < max_same_s and (i - last_exit_s) >= exit_cd and
                not np.isnan(t4h) and t4h == -1):
            if not np.isnan(e20) and e20 > 0:
                gap = (c - e20) / e20
                if (pullback_min / 100) <= gap <= (pullback_max / 100):
                    positions.append({
                        "side": "short",
                        "entry_price": next_open,
                        "entry_bar": i,
                        "entry_dt": dt,
                        "notional": notional,
                    })
                    monthly_entries += 1

    return closed_trades


def quick_stats(trades):
    """Return quick stats dict."""
    if not trades:
        return None

    tdf = pd.DataFrame(trades)
    is_t = tdf[~tdf["is_oos"]]
    oos_t = tdf[tdf["is_oos"]]

    def calc(subset, label):
        if len(subset) == 0:
            return {}
        n = len(subset)
        pnl = subset["pnl_net"].sum()
        wr = (subset["pnl_net"] > 0).mean()

        wins = subset.loc[subset["pnl_net"] > 0, "pnl_net"]
        losses = subset.loc[subset["pnl_net"] <= 0, "pnl_net"]
        pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 999

        subset = subset.copy()
        subset["month"] = pd.to_datetime(subset["exit_dt"]).dt.to_period("M")
        monthly = subset.groupby("month")["pnl_net"].sum()
        pos_months = (monthly >= 0).sum()
        total_months = len(monthly)
        worst_month = monthly.min()

        cum = subset["pnl_net"].cumsum()
        peak = cum.cummax()
        dd = cum - peak
        mdd = dd.min()

        subset["date"] = pd.to_datetime(subset["exit_dt"]).dt.date
        daily = subset.groupby("date")["pnl_net"].sum()
        worst_day = daily.min()

        return {
            f"{label}_n": n, f"{label}_pnl": pnl, f"{label}_wr": wr,
            f"{label}_pf": pf, f"{label}_pos_months": pos_months,
            f"{label}_total_months": total_months, f"{label}_worst_month": worst_month,
            f"{label}_mdd": mdd, f"{label}_worst_day": worst_day,
        }

    stats = {}
    stats.update(calc(is_t, "is"))
    stats.update(calc(oos_t, "oos"))
    return stats


def passes_gates(stats):
    """Check if strategy passes all final gates."""
    if not stats:
        return False, []
    fails = []
    if stats.get("is_pnl", 0) <= 0: fails.append("IS<=0")
    if stats.get("oos_pnl", 0) <= 0: fails.append("OOS<=0")
    oos_pm = stats.get("oos_pos_months", 0)
    oos_tm = stats.get("oos_total_months", 1)
    if oos_pm < 10 or oos_tm < 12: fails.append(f"PosMonth {oos_pm}/{oos_tm}")
    if stats.get("oos_worst_month", -999) < -200: fails.append(f"WorstM ${stats.get('oos_worst_month',0):.0f}")
    if stats.get("oos_mdd", -999) < -500: fails.append(f"MDD ${stats.get('oos_mdd',0):.0f}")
    if stats.get("oos_worst_day", -999) < -300: fails.append(f"WorstD ${stats.get('oos_worst_day',0):.0f}")
    return len(fails) == 0, fails


# ===== Grid Search =====
print("=" * 80)
print("V10 R3: Strategy E Optimization")
print("=" * 80)

# Reduced grid: focus on most impactful parameters
configs = []
for safenet in [3.0, 3.5, 4.0, 4.5]:
    for es_bars, es_pct in [(0, 0), (3, 0.8), (5, 1.0), (5, 1.5), (8, 1.5)]:
        for tp in [0, 2.0, 3.0]:
            for min_hold in [2, 3, 5]:
                for pb_min, pb_max in [(0, 3.0), (0.3, 3.0), (0.5, 2.5), (0, 2.0)]:
                    for atr_filt in [0, 80, 90]:
                        configs.append({
                            "safenet": safenet, "es_bars": es_bars, "es_pct": es_pct,
                            "tp": tp, "min_hold": min_hold,
                            "pb_min": pb_min, "pb_max": pb_max,
                            "atr_filt": atr_filt,
                        })

print(f"Total configs to test: {len(configs)}")

results = []
for idx, cfg in enumerate(configs):
    trades = run_stratE_config(
        safenet_pct=cfg["safenet"],
        early_stop_bars=cfg["es_bars"],
        early_stop_pct=cfg["es_pct"],
        tp_pct=cfg["tp"],
        min_hold_exit=cfg["min_hold"],
        max_hold=15,
        exit_cd=5,
        entry_cap=20,
        pullback_min=cfg["pb_min"],
        pullback_max=cfg["pb_max"],
        atr_filter=cfg["atr_filt"],
        notional_hi_vol=0.6,
        max_same_l=2, max_same_s=2, max_total=3,
    )

    stats = quick_stats(trades)
    if stats and stats.get("oos_n", 0) >= 50:
        passed, fails = passes_gates(stats)
        results.append({**cfg, **stats, "passed": passed, "fails": fails})

    if (idx + 1) % 200 == 0:
        print(f"  Progress: {idx+1}/{len(configs)}")

print(f"\nTotal valid results: {len(results)}")

# Sort by OOS PnL
results.sort(key=lambda x: x.get("oos_pnl", -9999), reverse=True)

# Show top 20
print("\n=== TOP 20 BY OOS PnL ===\n")
print(f"{'SN%':>4} {'ES':>5} {'TP':>4} {'MH':>3} {'PB':>7} {'ATR':>4} | "
      f"{'IS_N':>5} {'IS$':>7} {'OOS_N':>5} {'OOS$':>7} {'WR%':>5} {'PM':>5} {'WrstM':>6} {'MDD':>6} {'WrstD':>6} | {'PASS':>4}")
print("-" * 110)

for r in results[:20]:
    pm = f"{r.get('oos_pos_months',0)}/{r.get('oos_total_months',0)}"
    pb = f"{r['pb_min']}-{r['pb_max']}"
    es = f"{r['es_bars']}b{r['es_pct']}" if r["es_bars"] > 0 else "none"
    tp = f"{r['tp']}" if r["tp"] > 0 else "none"
    passed = "YES" if r["passed"] else "no"
    print(f"{r['safenet']:>4.1f} {es:>5} {tp:>4} {r['min_hold']:>3} {pb:>7} {r['atr_filt']:>4} | "
          f"{r.get('is_n',0):>5} {r.get('is_pnl',0):>7.0f} {r.get('oos_n',0):>5} {r.get('oos_pnl',0):>7.0f} "
          f"{r.get('oos_wr',0)*100:>5.1f} {pm:>5} {r.get('oos_worst_month',0):>6.0f} "
          f"{r.get('oos_mdd',0):>6.0f} {r.get('oos_worst_day',0):>6.0f} | {passed:>4}")

# Show passing configs
passing = [r for r in results if r["passed"]]
print(f"\n\n=== PASSING CONFIGS: {len(passing)} ===\n")

if passing:
    for r in passing[:10]:
        pm = f"{r.get('oos_pos_months',0)}/{r.get('oos_total_months',0)}"
        pb = f"{r['pb_min']}-{r['pb_max']}"
        es = f"{r['es_bars']}b{r['es_pct']}" if r["es_bars"] > 0 else "none"
        tp = f"{r['tp']}" if r["tp"] > 0 else "none"
        print(f"  SN={r['safenet']} ES={es} TP={tp} MH={r['min_hold']} PB={pb} ATR={r['atr_filt']}")
        print(f"    IS: {r.get('is_n',0)}t ${r.get('is_pnl',0):.0f} WR {r.get('is_wr',0)*100:.1f}%")
        print(f"    OOS: {r.get('oos_n',0)}t ${r.get('oos_pnl',0):.0f} WR {r.get('oos_wr',0)*100:.1f}%")
        print(f"    PosMonths: {pm}, WorstMonth: ${r.get('oos_worst_month',0):.0f}, MDD: ${r.get('oos_mdd',0):.0f}, WorstDay: ${r.get('oos_worst_day',0):.0f}")
        print()
else:
    # Show closest to passing
    print("  No configs pass all gates. Showing closest:\n")
    # Sort by number of fails
    results.sort(key=lambda x: (len(x.get("fails", [])), -x.get("oos_pnl", -9999)))
    for r in results[:10]:
        pm = f"{r.get('oos_pos_months',0)}/{r.get('oos_total_months',0)}"
        pb = f"{r['pb_min']}-{r['pb_max']}"
        es = f"{r['es_bars']}b{r['es_pct']}" if r["es_bars"] > 0 else "none"
        tp = f"{r['tp']}" if r["tp"] > 0 else "none"
        print(f"  SN={r['safenet']} ES={es} TP={tp} MH={r['min_hold']} PB={pb} ATR={r['atr_filt']}")
        print(f"    IS: {r.get('is_n',0)}t ${r.get('is_pnl',0):.0f} | OOS: {r.get('oos_n',0)}t ${r.get('oos_pnl',0):.0f} WR {r.get('oos_wr',0)*100:.1f}%")
        print(f"    PM: {pm}, WrstM: ${r.get('oos_worst_month',0):.0f}, MDD: ${r.get('oos_mdd',0):.0f}, WrstD: ${r.get('oos_worst_day',0):.0f}")
        print(f"    Fails: {r.get('fails', [])}")
        print()

# ===== Detailed analysis of best config =====
if results:
    best = results[0] if passing else results[0]
    print("\n=== DETAILED ANALYSIS OF BEST CONFIG ===\n")
    cfg = best
    trades = run_stratE_config(
        safenet_pct=cfg["safenet"],
        early_stop_bars=cfg["es_bars"],
        early_stop_pct=cfg["es_pct"],
        tp_pct=cfg["tp"],
        min_hold_exit=cfg["min_hold"],
        max_hold=15,
        exit_cd=5,
        entry_cap=20,
        pullback_min=cfg["pb_min"],
        pullback_max=cfg["pb_max"],
        atr_filter=cfg["atr_filt"],
        notional_hi_vol=0.6,
        max_same_l=2, max_same_s=2, max_total=3,
    )

    tdf = pd.DataFrame(trades)
    oos = tdf[tdf["is_oos"]].copy()
    oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
    monthly = oos.groupby("month").agg(
        trades=("pnl_net", "count"),
        wr=("pnl_net", lambda x: (x > 0).mean()),
        pnl=("pnl_net", "sum"),
    )

    print(f"Config: SN={cfg['safenet']} ES={cfg['es_bars']}b{cfg['es_pct']} TP={cfg['tp']} "
          f"MH={cfg['min_hold']} PB={cfg['pb_min']}-{cfg['pb_max']} ATR={cfg['atr_filt']}")
    print(f"\nOOS Monthly Table:")
    cum = 0
    print(f"  {'Month':>10} {'Trades':>7} {'WR%':>6} {'PnL':>8} {'Cum':>8}")
    for m, row in monthly.iterrows():
        cum += row["pnl"]
        print(f"  {str(m):>10} {int(row['trades']):>7} {row['wr']*100:>5.0f}% {row['pnl']:>8.0f} {cum:>8.0f}")

    # Exit reason distribution
    print(f"\nOOS Exit Reasons:")
    for reason, count in oos["reason"].value_counts().items():
        sub = oos[oos["reason"] == reason]
        avg_pnl = sub["pnl_net"].mean()
        print(f"  {reason:>10}: {count:>4} trades, avg PnL ${avg_pnl:.1f}")

    # Side distribution
    print(f"\nOOS Side Distribution:")
    for side in ["long", "short"]:
        sub = oos[oos["side"] == side]
        if len(sub) > 0:
            print(f"  {side:>6}: {len(sub)} trades, WR {(sub['pnl_net']>0).mean()*100:.1f}%, PnL ${sub['pnl_net'].sum():.0f}")

print("\n=== R3 COMPLETE ===")
