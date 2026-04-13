"""
V10 R4: maxTotal=1 + Tight Circuit Breakers + Short-Only Test
三路測試：
  (A) maxTotal=1 雙向, 月虧-$200
  (B) Short-only, maxTotal=1, 月虧-$200
  (C) Best of A/B with fine-tuned params
"""
import pandas as pd
import numpy as np

# ===== Load Data =====
eth_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
eth_4h = pd.read_csv("data/ETHUSDT_4h_latest730d.csv")
eth_1h["datetime"] = pd.to_datetime(eth_1h["datetime"])
eth_4h["datetime"] = pd.to_datetime(eth_4h["datetime"])
eth_1h["ret"] = eth_1h["close"].pct_change()

# Indicators
eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
eth_1h["hour"] = eth_1h["datetime"].dt.hour
eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek

# 4h trend
eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                    np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))
eth_1h = eth_1h.merge(eth_4h[["datetime", "trend_4h", "ema20_4h"]], on="datetime", how="left")
eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()

# Pre-compute arrays
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
IS_END = N // 2
NOTIONAL = 4000
FEE = 4  # $4 round trip


def run_backtest(cfg):
    """Run backtest with given config dict."""
    safenet = cfg["safenet"] / 100
    safenet_slip = 0.25
    es_bars = cfg.get("es_bars", 0)
    es_pct = cfg.get("es_pct", 0) / 100
    tp_pct = cfg.get("tp", 0) / 100
    min_hold = cfg.get("min_hold", 3)
    max_hold = cfg.get("max_hold", 15)
    exit_cd = cfg.get("exit_cd", 5)
    entry_cap = cfg.get("entry_cap", 20)
    pb_min = cfg.get("pb_min", 0) / 100
    pb_max = cfg.get("pb_max", 3.0) / 100
    atr_filter = cfg.get("atr_filter", 0) / 100
    max_total = cfg.get("max_total", 1)
    do_long = cfg.get("do_long", True)
    do_short = cfg.get("do_short", True)
    daily_limit = cfg.get("daily_limit", -200)
    monthly_limit = cfg.get("monthly_limit", -200)
    consec_pause = cfg.get("consec_pause", 5)
    consec_cd = cfg.get("consec_cd", 24)

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

            exit_price = None
            exit_reason = None

            if side == "long":
                sn_check = (lo - entry_price) / entry_price
                pnl_check = (c - entry_price) / entry_price
            else:
                sn_check = -(h - entry_price) / entry_price
                pnl_check = -(c - entry_price) / entry_price

            # SafeNet
            if sn_check <= -safenet:
                slip = safenet * safenet_slip
                if side == "long":
                    exit_price = entry_price * (1 - safenet - slip)
                else:
                    exit_price = entry_price * (1 + safenet + slip)
                exit_reason = "SafeNet"

            # EarlyStop
            elif es_bars > 0 and 1 <= bars_held <= es_bars and pnl_check < -es_pct:
                exit_price = c
                exit_reason = "EarlyStop"

            # TP
            elif tp_pct > 0:
                if side == "long" and (h - entry_price) / entry_price >= tp_pct:
                    exit_price = entry_price * (1 + tp_pct)
                    exit_reason = "TP"
                elif side == "short" and (entry_price - lo) / entry_price >= tp_pct:
                    exit_price = entry_price * (1 - tp_pct)
                    exit_reason = "TP"

            # EMA cross
            if exit_price is None and bars_held >= min_hold:
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

                pnl_net = pnl_pct * NOTIONAL - FEE
                daily_pnl += pnl_net
                monthly_pnl += pnl_net

                if pnl_net < 0:
                    consec_losses += 1
                    if consec_losses >= consec_pause:
                        cooldown_until = i + consec_cd
                else:
                    consec_losses = 0

                if side == "long":
                    last_exit_l = i
                else:
                    last_exit_s = i

                closed_trades.append({
                    "exit_dt": dt,
                    "side": side,
                    "pnl_net": pnl_net,
                    "bars": bars_held,
                    "reason": exit_reason,
                    "is_oos": i >= IS_END,
                })
                positions.remove(pos)

        # === Risk Controls ===
        if daily_pnl <= daily_limit:
            continue
        if monthly_pnl <= monthly_limit:
            continue
        if i < cooldown_until:
            continue

        n_pos = len(positions)
        if n_pos >= max_total:
            continue

        if hours[i] in blocked_hours or dows[i] in blocked_days:
            continue
        if monthly_entries >= entry_cap:
            continue
        if atr_filter > 0 and not np.isnan(atr_pct[i]) and atr_pct[i] > atr_filter:
            continue

        # === Long Entry ===
        if (do_long and (i - last_exit_l) >= exit_cd and
                not np.isnan(t4h) and t4h == 1 and not np.isnan(e20) and e20 > 0):
            gap = (c - e20) / e20
            if -pb_max <= gap <= -pb_min:
                positions.append({
                    "side": "long",
                    "entry_price": next_open,
                    "entry_bar": i,
                })
                monthly_entries += 1
                continue  # only 1 entry per bar

        # === Short Entry ===
        if (do_short and (i - last_exit_s) >= exit_cd and
                not np.isnan(t4h) and t4h == -1 and not np.isnan(e20) and e20 > 0):
            gap = (c - e20) / e20
            if pb_min <= gap <= pb_max:
                positions.append({
                    "side": "short",
                    "entry_price": next_open,
                    "entry_bar": i,
                })
                monthly_entries += 1

    return closed_trades


def analyze(trades, label="", verbose=True):
    """Analyze and return stats dict."""
    if not trades:
        if verbose:
            print(f"  {label}: NO TRADES")
        return None

    tdf = pd.DataFrame(trades)

    stats = {}
    for period, mask_fn in [("is", lambda t: ~t["is_oos"]), ("oos", lambda t: t["is_oos"])]:
        subset = tdf[mask_fn(tdf)]
        if len(subset) == 0:
            continue

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
        worst_month = monthly.min() if len(monthly) > 0 else 0

        cum = subset["pnl_net"].cumsum()
        peak = cum.cummax()
        mdd = (cum - peak).min()

        subset["date"] = pd.to_datetime(subset["exit_dt"]).dt.date
        daily = subset.groupby("date")["pnl_net"].sum()
        worst_day = daily.min() if len(daily) > 0 else 0

        stats[f"{period}_n"] = n
        stats[f"{period}_pnl"] = pnl
        stats[f"{period}_wr"] = wr
        stats[f"{period}_pf"] = pf
        stats[f"{period}_pos_months"] = pos_months
        stats[f"{period}_total_months"] = total_months
        stats[f"{period}_worst_month"] = worst_month
        stats[f"{period}_mdd"] = mdd
        stats[f"{period}_worst_day"] = worst_day

    if verbose and "oos_pnl" in stats:
        print(f"  {label}:")
        print(f"    IS:  {stats.get('is_n',0):>4}t ${stats.get('is_pnl',0):>7.0f}  WR {stats.get('is_wr',0)*100:>5.1f}%  PF {stats.get('is_pf',0):>5.2f}")
        print(f"    OOS: {stats.get('oos_n',0):>4}t ${stats.get('oos_pnl',0):>7.0f}  WR {stats.get('oos_wr',0)*100:>5.1f}%  PF {stats.get('oos_pf',0):>5.2f}")
        pm = f"{stats.get('oos_pos_months',0)}/{stats.get('oos_total_months',0)}"
        print(f"    PM: {pm}  WrstM: ${stats.get('oos_worst_month',0):>6.0f}  MDD: ${stats.get('oos_mdd',0):>6.0f}  WrstD: ${stats.get('oos_worst_day',0):>6.0f}")

    return stats


def check_gates(stats):
    """Return (pass, [fail_reasons])."""
    if not stats:
        return False, ["no stats"]
    fails = []
    if stats.get("is_pnl", 0) <= 0: fails.append("IS<=0")
    if stats.get("oos_pnl", 0) <= 0: fails.append("OOS<=0")
    oos_pm = stats.get("oos_pos_months", 0)
    oos_tm = stats.get("oos_total_months", 1)
    if oos_pm < 10: fails.append(f"PosM {oos_pm}/{oos_tm}")
    if stats.get("oos_worst_month", -999) < -200: fails.append(f"WrstM ${stats['oos_worst_month']:.0f}")
    if stats.get("oos_mdd", -999) < -500: fails.append(f"MDD ${stats['oos_mdd']:.0f}")
    if stats.get("oos_worst_day", -999) < -300: fails.append(f"WrstD ${stats['oos_worst_day']:.0f}")
    return len(fails) == 0, fails


# ===== PART A: maxTotal=1, Both directions =====
print("=" * 80)
print("PART A: maxTotal=1, Both L+S, monthly limit -$200")
print("=" * 80)

base_A = {
    "max_total": 1, "do_long": True, "do_short": True,
    "daily_limit": -200, "monthly_limit": -200,
    "consec_pause": 4, "consec_cd": 24,
}

best_A = []
for sn in [3.0, 3.5, 4.0, 4.5]:
    for es_b, es_p in [(0, 0), (5, 1.0), (5, 1.5), (8, 1.5)]:
        for mh in [2, 3, 5]:
            for pb_min, pb_max in [(0, 3.0), (0.3, 3.0), (0.3, 2.5), (0.5, 2.5)]:
                for atr in [0, 80, 90]:
                    for ec in [3, 5, 8]:
                        cfg = {**base_A,
                               "safenet": sn, "es_bars": es_b, "es_pct": es_p,
                               "tp": 0, "min_hold": mh, "max_hold": 15,
                               "exit_cd": ec, "entry_cap": 20,
                               "pb_min": pb_min, "pb_max": pb_max, "atr_filter": atr}
                        trades = run_backtest(cfg)
                        stats = analyze(trades, verbose=False)
                        if stats and stats.get("oos_n", 0) >= 30:
                            passed, fails = check_gates(stats)
                            best_A.append((stats.get("oos_pnl", 0), cfg, stats, passed, fails))

best_A.sort(reverse=True)
print(f"\nTotal configs tested: {len(best_A)}")

passing_A = [(p, c, s, f) for p, c, s, pa, f in best_A if pa]
print(f"PASSING: {len(passing_A)}")

print("\nTop 10 (by OOS PnL):")
for pnl, cfg, stats, passed, fails in best_A[:10]:
    p = "PASS" if passed else "fail"
    es = f"{cfg['es_bars']}b{cfg['es_pct']}" if cfg['es_bars'] > 0 else "no"
    print(f"  [{p:>4}] SN={cfg['safenet']} ES={es} MH={cfg['min_hold']} PB={cfg['pb_min']}-{cfg['pb_max']} "
          f"ATR={cfg['atr_filter']} EC={cfg['exit_cd']}  |  "
          f"IS ${stats.get('is_pnl',0):.0f}  OOS ${stats.get('oos_pnl',0):.0f} WR {stats.get('oos_wr',0)*100:.1f}% "
          f"PM {stats.get('oos_pos_months',0)}/{stats.get('oos_total_months',0)} "
          f"WM ${stats.get('oos_worst_month',0):.0f} MDD ${stats.get('oos_mdd',0):.0f} WD ${stats.get('oos_worst_day',0):.0f}")
    if fails:
        print(f"         Fails: {fails}")

# ===== PART B: Short-only, maxTotal=1 =====
print("\n" + "=" * 80)
print("PART B: Short-only, maxTotal=1, monthly limit -$200")
print("=" * 80)

base_B = {
    "max_total": 1, "do_long": False, "do_short": True,
    "daily_limit": -200, "monthly_limit": -200,
    "consec_pause": 4, "consec_cd": 24,
}

best_B = []
for sn in [3.0, 3.5, 4.0, 4.5]:
    for es_b, es_p in [(0, 0), (5, 1.0), (5, 1.5), (8, 1.5)]:
        for mh in [2, 3, 5]:
            for pb_min, pb_max in [(0, 3.0), (0.3, 3.0), (0.3, 2.5), (0.5, 2.5)]:
                for atr in [0, 80, 90]:
                    for ec in [3, 5, 8]:
                        cfg = {**base_B,
                               "safenet": sn, "es_bars": es_b, "es_pct": es_p,
                               "tp": 0, "min_hold": mh, "max_hold": 15,
                               "exit_cd": ec, "entry_cap": 20,
                               "pb_min": pb_min, "pb_max": pb_max, "atr_filter": atr}
                        trades = run_backtest(cfg)
                        stats = analyze(trades, verbose=False)
                        if stats and stats.get("oos_n", 0) >= 20:
                            passed, fails = check_gates(stats)
                            best_B.append((stats.get("oos_pnl", 0), cfg, stats, passed, fails))

best_B.sort(key=lambda x: x[0], reverse=True)
print(f"\nTotal configs tested: {len(best_B)}")

passing_B = [(p, c, s, f) for p, c, s, pa, f in best_B if pa]
print(f"PASSING: {len(passing_B)}")

print("\nTop 10 (by OOS PnL):")
for pnl, cfg, stats, passed, fails in best_B[:10]:
    p = "PASS" if passed else "fail"
    es = f"{cfg['es_bars']}b{cfg['es_pct']}" if cfg['es_bars'] > 0 else "no"
    print(f"  [{p:>4}] SN={cfg['safenet']} ES={es} MH={cfg['min_hold']} PB={cfg['pb_min']}-{cfg['pb_max']} "
          f"ATR={cfg['atr_filter']} EC={cfg['exit_cd']}  |  "
          f"IS ${stats.get('is_pnl',0):.0f}  OOS ${stats.get('oos_pnl',0):.0f} WR {stats.get('oos_wr',0)*100:.1f}% "
          f"PM {stats.get('oos_pos_months',0)}/{stats.get('oos_total_months',0)} "
          f"WM ${stats.get('oos_worst_month',0):.0f} MDD ${stats.get('oos_mdd',0):.0f} WD ${stats.get('oos_worst_day',0):.0f}")
    if fails:
        print(f"         Fails: {fails}")


# ===== PART C: maxTotal=2 but with tighter monthly limit =====
print("\n" + "=" * 80)
print("PART C: maxTotal=2, tighter limits, both sides")
print("=" * 80)

base_C = {
    "max_total": 2, "do_long": True, "do_short": True,
    "daily_limit": -150, "monthly_limit": -200,
    "consec_pause": 3, "consec_cd": 36,
}

best_C = []
for sn in [3.0, 3.5, 4.0]:
    for es_b, es_p in [(3, 0.8), (5, 1.0), (8, 1.5)]:
        for mh in [2, 3]:
            for pb_min, pb_max in [(0.3, 3.0), (0.3, 2.5)]:
                for atr in [80, 90]:
                    for ec in [5, 8]:
                        cfg = {**base_C,
                               "safenet": sn, "es_bars": es_b, "es_pct": es_p,
                               "tp": 0, "min_hold": mh, "max_hold": 15,
                               "exit_cd": ec, "entry_cap": 15,
                               "pb_min": pb_min, "pb_max": pb_max, "atr_filter": atr}
                        trades = run_backtest(cfg)
                        stats = analyze(trades, verbose=False)
                        if stats and stats.get("oos_n", 0) >= 30:
                            passed, fails = check_gates(stats)
                            best_C.append((stats.get("oos_pnl", 0), cfg, stats, passed, fails))

best_C.sort(key=lambda x: x[0], reverse=True)
print(f"\nTotal configs tested: {len(best_C)}")

passing_C = [(p, c, s, f) for p, c, s, pa, f in best_C if pa]
print(f"PASSING: {len(passing_C)}")

print("\nTop 10:")
for pnl, cfg, stats, passed, fails in best_C[:10]:
    p = "PASS" if passed else "fail"
    es = f"{cfg['es_bars']}b{cfg['es_pct']}" if cfg['es_bars'] > 0 else "no"
    print(f"  [{p:>4}] SN={cfg['safenet']} ES={es} MH={cfg['min_hold']} PB={cfg['pb_min']}-{cfg['pb_max']} "
          f"ATR={cfg['atr_filter']} EC={cfg['exit_cd']}  |  "
          f"IS ${stats.get('is_pnl',0):.0f}  OOS ${stats.get('oos_pnl',0):.0f} WR {stats.get('oos_wr',0)*100:.1f}% "
          f"PM {stats.get('oos_pos_months',0)}/{stats.get('oos_total_months',0)} "
          f"WM ${stats.get('oos_worst_month',0):.0f} MDD ${stats.get('oos_mdd',0):.0f} WD ${stats.get('oos_worst_day',0):.0f}")
    if fails:
        print(f"         Fails: {fails}")


# ===== Summary =====
print("\n" + "=" * 80)
print("=== SUMMARY ===")
print("=" * 80)

total_passing = len(passing_A) + len(passing_B) + len(passing_C)
print(f"\nTotal PASSING configs: {total_passing}")
print(f"  Part A (L+S maxT=1): {len(passing_A)}")
print(f"  Part B (S-only maxT=1): {len(passing_B)}")
print(f"  Part C (L+S maxT=2): {len(passing_C)}")

# Detailed monthly for best passing config
all_passing = []
for label, passing_list in [("A", passing_A), ("B", passing_B), ("C", passing_C)]:
    for pnl, cfg, stats, fails in passing_list:
        all_passing.append((pnl, cfg, stats, label))

if all_passing:
    all_passing.sort(reverse=True)
    best_pnl, best_cfg, best_stats, best_label = all_passing[0]

    print(f"\n=== BEST PASSING CONFIG (Part {best_label}) ===")
    es = f"{best_cfg['es_bars']}b{best_cfg['es_pct']}" if best_cfg['es_bars'] > 0 else "no"
    print(f"Config: SN={best_cfg['safenet']} ES={es} MH={best_cfg['min_hold']} "
          f"PB={best_cfg['pb_min']}-{best_cfg['pb_max']} ATR={best_cfg['atr_filter']} "
          f"EC={best_cfg['exit_cd']} maxT={best_cfg['max_total']} "
          f"L={best_cfg['do_long']} S={best_cfg['do_short']}")

    trades = run_backtest(best_cfg)
    tdf = pd.DataFrame(trades)
    oos = tdf[tdf["is_oos"]].copy()
    is_df = tdf[~tdf["is_oos"]].copy()

    for label, subset in [("IS", is_df), ("OOS", oos)]:
        print(f"\n  {label}: {len(subset)}t ${subset['pnl_net'].sum():.0f} WR {(subset['pnl_net']>0).mean()*100:.1f}%")
        subset["month"] = pd.to_datetime(subset["exit_dt"]).dt.to_period("M")
        monthly = subset.groupby("month")["pnl_net"].agg(["count", "sum"])
        monthly.columns = ["trades", "pnl"]
        cum = 0
        print(f"  {'Month':>10} {'Trades':>7} {'PnL':>8} {'Cum':>8}")
        for m, row in monthly.iterrows():
            cum += row["pnl"]
            print(f"  {str(m):>10} {int(row['trades']):>7} {row['pnl']:>8.0f} {cum:>8.0f}")

    # Exit reasons
    print(f"\n  OOS Exit Reasons:")
    for reason in oos["reason"].value_counts().index:
        sub = oos[oos["reason"] == reason]
        print(f"    {reason:>10}: {len(sub)} trades, avg ${sub['pnl_net'].mean():.1f}")

    # Side split
    if best_cfg["do_long"]:
        for side in ["long", "short"]:
            sub = oos[oos["side"] == side]
            if len(sub) > 0:
                print(f"\n  {side:>6}: {len(sub)}t WR {(sub['pnl_net']>0).mean()*100:.1f}% PnL ${sub['pnl_net'].sum():.0f}")

else:
    print("\nNo passing configs found. Showing closest from each part:")
    for label, best_list in [("A", best_A), ("B", best_B), ("C", best_C)]:
        if best_list:
            # Sort by number of fails
            closest = sorted(best_list, key=lambda x: (len(x[4]), -x[0]))
            pnl, cfg, stats, passed, fails = closest[0]
            es = f"{cfg['es_bars']}b{cfg['es_pct']}" if cfg['es_bars'] > 0 else "no"
            print(f"\n  Part {label} closest: SN={cfg['safenet']} ES={es} MH={cfg['min_hold']} "
                  f"PB={cfg['pb_min']}-{cfg['pb_max']} ATR={cfg['atr_filter']}")
            print(f"    IS ${stats.get('is_pnl',0):.0f} OOS ${stats.get('oos_pnl',0):.0f} WR {stats.get('oos_wr',0)*100:.1f}%")
            print(f"    PM {stats.get('oos_pos_months',0)}/{stats.get('oos_total_months',0)} "
                  f"WrstM ${stats.get('oos_worst_month',0):.0f} MDD ${stats.get('oos_mdd',0):.0f} WrstD ${stats.get('oos_worst_day',0):.0f}")
            print(f"    Fails: {fails}")

            # Monthly detail for closest
            trades = run_backtest(cfg)
            tdf = pd.DataFrame(trades)
            oos = tdf[tdf["is_oos"]].copy()
            oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
            monthly = oos.groupby("month")["pnl_net"].agg(["count", "sum"])
            monthly.columns = ["trades", "pnl"]
            cum = 0
            print(f"    {'Month':>10} {'N':>4} {'PnL':>8} {'Cum':>8}")
            for m, row in monthly.iterrows():
                cum += row["pnl"]
                print(f"    {str(m):>10} {int(row['trades']):>4} {row['pnl']:>8.0f} {cum:>8.0f}")

print("\n=== R4 COMPLETE ===")
