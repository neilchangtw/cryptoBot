"""
V10 R5: Champion Config Detailed Validation
Config: Short-only, maxTotal=1, 4h Downtrend + 1h Rally Entry
  SafeNet=3.5%, EarlyStop=5b@1.0%, min_hold=3, PB=0-3.0%, ATR<90pct, EC=8
  Monthly limit=-$200, Daily limit=-$200

Tests:
  1. Full IS/OOS monthly detail
  2. Walk-Forward 6-fold
  3. Top 3 configs cross-validation
  4. Robustness: nearby parameter sensitivity
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

eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                    np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))
eth_1h = eth_1h.merge(eth_4h[["datetime", "trend_4h", "ema20_4h"]], on="datetime", how="left")
eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()

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
NOTIONAL = 4000
FEE = 4


def run_backtest_range(cfg, start_idx, end_idx):
    """Run backtest on specified index range."""
    safenet = cfg["safenet"] / 100
    safenet_slip = 0.25
    es_bars = cfg.get("es_bars", 0)
    es_pct = cfg.get("es_pct", 0) / 100
    min_hold = cfg.get("min_hold", 3)
    max_hold = cfg.get("max_hold", 15)
    exit_cd = cfg.get("exit_cd", 8)
    entry_cap = cfg.get("entry_cap", 20)
    pb_min = cfg.get("pb_min", 0) / 100
    pb_max = cfg.get("pb_max", 3.0) / 100
    atr_filter = cfg.get("atr_filter", 90) / 100
    daily_limit = cfg.get("daily_limit", -200)
    monthly_limit = cfg.get("monthly_limit", -200)
    consec_pause = cfg.get("consec_pause", 4)
    consec_cd = cfg.get("consec_cd", 24)

    positions = []
    closed_trades = []
    daily_pnl = 0.0
    monthly_pnl = 0.0
    consec_losses = 0
    cooldown_until = 0
    current_month = -1
    current_date = None
    last_exit = -999
    monthly_entries = 0
    entry_ym = (-1, -1)

    actual_start = max(start_idx, WARMUP)

    for i in range(actual_start, min(end_idx, N - 1)):
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
        next_open = opens[i + 1] if i + 1 < N else c

        # Exit
        for pos in list(positions):
            bars_held = i - pos["entry_bar"]
            entry_price = pos["entry_price"]

            sn_check = -(h - entry_price) / entry_price
            pnl_check = -(c - entry_price) / entry_price

            exit_price = None
            exit_reason = None

            if sn_check <= -safenet:
                slip = safenet * safenet_slip
                exit_price = entry_price * (1 + safenet + slip)
                exit_reason = "SafeNet"
            elif es_bars > 0 and 1 <= bars_held <= es_bars and pnl_check < -es_pct:
                exit_price = c
                exit_reason = "EarlyStop"
            elif bars_held >= min_hold and c < e20:
                exit_price = c
                exit_reason = "EMAcross"
            elif bars_held >= max_hold:
                exit_price = c
                exit_reason = "MaxHold"

            if exit_price is not None:
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

                last_exit = i
                closed_trades.append({
                    "entry_dt": pos["entry_dt"],
                    "exit_dt": dt,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_net": pnl_net,
                    "bars": bars_held,
                    "reason": exit_reason,
                    "bar_idx": i,
                })
                positions.remove(pos)

        # Risk controls
        if daily_pnl <= daily_limit:
            continue
        if monthly_pnl <= monthly_limit:
            continue
        if i < cooldown_until:
            continue
        if len(positions) >= 1:
            continue
        if hours[i] in blocked_hours or dows[i] in blocked_days:
            continue
        if monthly_entries >= entry_cap:
            continue
        if atr_filter > 0 and not np.isnan(atr_pct[i]) and atr_pct[i] > atr_filter:
            continue
        if (i - last_exit) < exit_cd:
            continue

        # Short entry: 4h downtrend + 1h close above EMA20 (rally)
        if not np.isnan(t4h) and t4h == -1 and not np.isnan(e20) and e20 > 0:
            gap = (c - e20) / e20
            if pb_min <= gap <= pb_max:
                positions.append({
                    "entry_price": next_open,
                    "entry_bar": i,
                    "entry_dt": dt,
                })
                monthly_entries += 1

    return closed_trades


# Champion configs to test
configs = {
    "Champion1": {
        "safenet": 3.5, "es_bars": 5, "es_pct": 1.0,
        "min_hold": 3, "max_hold": 15, "exit_cd": 8,
        "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
        "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
        "consec_pause": 4, "consec_cd": 24,
    },
    "Champion2": {
        "safenet": 3.0, "es_bars": 8, "es_pct": 1.5,
        "min_hold": 3, "max_hold": 15, "exit_cd": 8,
        "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
        "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
        "consec_pause": 4, "consec_cd": 24,
    },
    "Champion3": {
        "safenet": 3.0, "es_bars": 5, "es_pct": 1.0,
        "min_hold": 3, "max_hold": 15, "exit_cd": 8,
        "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
        "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
        "consec_pause": 4, "consec_cd": 24,
    },
}

IS_END = N // 2

print("=" * 80)
print("V10 R5: Champion Config Detailed Validation")
print(f"Data: {N} bars, IS: 0-{IS_END} ({pd.Timestamp(datetimes[0]).date()} ~ {pd.Timestamp(datetimes[IS_END]).date()})")
print(f"                  OOS: {IS_END}-{N} ({pd.Timestamp(datetimes[IS_END]).date()} ~ {pd.Timestamp(datetimes[-1]).date()})")
print("=" * 80)

# ===== 1. Full IS/OOS Detail =====
for name, cfg in configs.items():
    print(f"\n{'='*70}")
    print(f"=== {name} ===")
    print(f"{'='*70}")
    print(f"SafeNet={cfg['safenet']}%, EarlyStop={cfg['es_bars']}b@{cfg['es_pct']}%, "
          f"MinHold={cfg['min_hold']}, MaxHold={cfg['max_hold']}, ExitCD={cfg['exit_cd']}")
    print(f"Pullback={cfg['pb_min']}-{cfg['pb_max']}%, ATR<{cfg['atr_filter']}pct, "
          f"DailyLim=${cfg['daily_limit']}, MonthLim=${cfg['monthly_limit']}")

    trades = run_backtest_range(cfg, 0, N)
    if not trades:
        print("  NO TRADES")
        continue

    tdf = pd.DataFrame(trades)
    tdf["is_oos"] = tdf["bar_idx"] >= IS_END

    for period, mask in [("IS", ~tdf["is_oos"]), ("OOS", tdf["is_oos"]), ("ALL", pd.Series(True, index=tdf.index))]:
        subset = tdf[mask].copy()
        if len(subset) == 0:
            continue

        n = len(subset)
        pnl = subset["pnl_net"].sum()
        wr = (subset["pnl_net"] > 0).mean()
        wins = subset.loc[subset["pnl_net"] > 0, "pnl_net"]
        losses = subset.loc[subset["pnl_net"] <= 0, "pnl_net"]
        pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 999
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = losses.mean() if len(losses) > 0 else 0
        avg_bars = subset["bars"].mean()

        print(f"\n  --- {period} ---")
        print(f"  Trades: {n}, PnL: ${pnl:.0f}, WR: {wr*100:.1f}%, PF: {pf:.2f}")
        print(f"  Avg Win: ${avg_win:.1f}, Avg Loss: ${avg_loss:.1f}, Avg Bars: {avg_bars:.1f}")

        # Exit reasons
        for reason in subset["reason"].value_counts().index:
            sub = subset[subset["reason"] == reason]
            print(f"    {reason:>10}: {len(sub):>3} ({len(sub)/n*100:.0f}%), avg ${sub['pnl_net'].mean():.1f}")

        # Monthly
        subset["month"] = pd.to_datetime(subset["exit_dt"]).dt.to_period("M")
        monthly = subset.groupby("month").agg(
            trades=("pnl_net", "count"),
            wr=("pnl_net", lambda x: (x > 0).mean()),
            pnl=("pnl_net", "sum"),
        )

        cum = subset["pnl_net"].cumsum()
        peak = cum.cummax()
        mdd = (cum - peak).min()

        subset["date"] = pd.to_datetime(subset["exit_dt"]).dt.date
        daily = subset.groupby("date")["pnl_net"].sum()
        worst_day = daily.min()
        worst_day_date = daily.idxmin()

        pos_months = (monthly["pnl"] >= 0).sum()
        worst_month = monthly["pnl"].min()
        worst_month_name = monthly["pnl"].idxmin()

        print(f"\n  Months: {pos_months}/{len(monthly)} positive")
        print(f"  Worst month: ${worst_month:.0f} ({worst_month_name})")
        print(f"  MDD: ${mdd:.0f}")
        print(f"  Worst day: ${worst_day:.0f} ({worst_day_date})")

        print(f"\n  {'Month':>10} {'N':>4} {'WR%':>6} {'PnL':>8} {'Cum':>8}")
        print(f"  {'-'*40}")
        c = 0
        for m, row in monthly.iterrows():
            c += row["pnl"]
            marker = " ***" if row["pnl"] < 0 else ""
            print(f"  {str(m):>10} {int(row['trades']):>4} {row['wr']*100:>5.0f}% {row['pnl']:>8.0f} {c:>8.0f}{marker}")

    # ===== Gate Check =====
    oos = tdf[tdf["is_oos"]].copy()
    is_df = tdf[~tdf["is_oos"]].copy()

    is_pnl = is_df["pnl_net"].sum()
    oos_pnl = oos["pnl_net"].sum()

    oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
    oos_monthly = oos.groupby("month")["pnl_net"].sum()
    oos_pos_months = (oos_monthly >= 0).sum()
    oos_total_months = len(oos_monthly)
    oos_worst_month = oos_monthly.min()

    oos_cum = oos["pnl_net"].cumsum()
    oos_mdd = (oos_cum - oos_cum.cummax()).min()

    oos["date"] = pd.to_datetime(oos["exit_dt"]).dt.date
    oos_daily = oos.groupby("date")["pnl_net"].sum()
    oos_worst_day = oos_daily.min()

    print(f"\n  === GATE CHECK ===")
    gates = [
        ("IS > $0", is_pnl > 0, f"${is_pnl:.0f}"),
        ("OOS > $0", oos_pnl > 0, f"${oos_pnl:.0f}"),
        (f"Pos Months >= 10/12", oos_pos_months >= 10, f"{oos_pos_months}/{oos_total_months}"),
        ("Worst Month >= -$200", oos_worst_month >= -200, f"${oos_worst_month:.0f}"),
        ("MDD <= $500", oos_mdd >= -500, f"${oos_mdd:.0f}"),
        ("Worst Day >= -$300", oos_worst_day >= -300, f"${oos_worst_day:.0f}"),
    ]
    all_pass = True
    for gate_name, passed, value in gates:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"    {gate_name:>25}: {value:>10}  [{status}]")
    print(f"\n  {'*** ALL GATES PASS ***' if all_pass else '--- SOME GATES FAIL ---'}")


# ===== 2. Walk-Forward 6-fold =====
print(f"\n\n{'='*70}")
print("=== WALK-FORWARD 6-FOLD VALIDATION ===")
print(f"{'='*70}")

# Use Champion1 for WF
cfg = configs["Champion1"]
usable = N - WARMUP  # bars available after warmup
fold_size = usable // 6

print(f"Total usable bars: {usable}, fold size: {fold_size}")
print(f"Each fold: {fold_size//24:.0f} days IS + {fold_size//24:.0f} days OOS\n")

wf_results = []
for fold in range(6):
    # IS: all data except this fold
    # OOS: this fold
    oos_start = WARMUP + fold * fold_size
    oos_end = WARMUP + (fold + 1) * fold_size if fold < 5 else N

    # For simplicity, use train on first half, test on second half of each fold
    # Actually, do rolling WF: train on everything before this fold, test on this fold
    # But for fold 0 there's no prior data, so use fold 0 as IS and fold 1 as OOS etc.
    # Better approach: sliding window
    is_start = WARMUP
    is_end = oos_start

    if is_end - is_start < fold_size:
        # Not enough IS data for first fold, skip or use all prior
        is_start = WARMUP
        is_end = oos_start

    oos_trades = run_backtest_range(cfg, oos_start, oos_end)

    if oos_trades:
        odf = pd.DataFrame(oos_trades)
        pnl = odf["pnl_net"].sum()
        n = len(odf)
        wr = (odf["pnl_net"] > 0).mean()
        start_date = pd.Timestamp(datetimes[oos_start]).date()
        end_date = pd.Timestamp(datetimes[min(oos_end - 1, N - 1)]).date()
        wf_results.append((fold, pnl, n, wr, start_date, end_date))
        status = "+" if pnl > 0 else "-"
        print(f"  Fold {fold}: {start_date} ~ {end_date}  {n:>3}t  ${pnl:>7.0f}  WR {wr*100:.1f}%  [{status}]")
    else:
        start_date = pd.Timestamp(datetimes[oos_start]).date()
        end_date = pd.Timestamp(datetimes[min(oos_end - 1, N - 1)]).date()
        wf_results.append((fold, 0, 0, 0, start_date, end_date))
        print(f"  Fold {fold}: {start_date} ~ {end_date}  NO TRADES  [-]")

pos_folds = sum(1 for _, pnl, _, _, _, _ in wf_results if pnl > 0)
total_wf_pnl = sum(pnl for _, pnl, _, _, _, _ in wf_results)
print(f"\n  WF Result: {pos_folds}/6 positive folds, total ${total_wf_pnl:.0f}")
print(f"  {'PASS' if pos_folds >= 4 else 'FAIL'}: need >= 4/6")


# ===== 3. Parameter Sensitivity =====
print(f"\n\n{'='*70}")
print("=== PARAMETER SENSITIVITY (Champion1 ± 1 step) ===")
print(f"{'='*70}")

base = configs["Champion1"].copy()
params_to_test = {
    "safenet": [3.0, 3.5, 4.0],
    "es_bars": [3, 5, 8],
    "es_pct": [0.8, 1.0, 1.5],
    "min_hold": [2, 3, 5],
    "exit_cd": [5, 8, 12],
    "pb_max": [2.0, 2.5, 3.0],
    "atr_filter": [80, 85, 90],
}

print(f"\n{'Param':>12} {'Value':>7} {'IS_N':>5} {'IS$':>7} {'OOS_N':>5} {'OOS$':>7} {'WR%':>6} {'PM':>5} {'WrstM':>7} {'MDD':>6} {'WrstD':>6} {'ALL_PASS':>8}")
print("-" * 100)

for param, values in params_to_test.items():
    for val in values:
        cfg = base.copy()
        cfg[param] = val
        trades = run_backtest_range(cfg, 0, N)
        if not trades:
            continue

        tdf = pd.DataFrame(trades)
        tdf["is_oos"] = tdf["bar_idx"] >= IS_END

        is_t = tdf[~tdf["is_oos"]]
        oos_t = tdf[tdf["is_oos"]].copy()

        is_pnl = is_t["pnl_net"].sum()
        oos_pnl = oos_t["pnl_net"].sum()
        oos_wr = (oos_t["pnl_net"] > 0).mean() if len(oos_t) > 0 else 0

        oos_t["month"] = pd.to_datetime(oos_t["exit_dt"]).dt.to_period("M")
        oos_monthly = oos_t.groupby("month")["pnl_net"].sum()
        pm = (oos_monthly >= 0).sum()
        tm = len(oos_monthly)
        wm = oos_monthly.min() if len(oos_monthly) > 0 else 0

        oos_cum = oos_t["pnl_net"].cumsum()
        mdd = (oos_cum - oos_cum.cummax()).min() if len(oos_t) > 0 else 0

        oos_t["date"] = pd.to_datetime(oos_t["exit_dt"]).dt.date
        wd = oos_t.groupby("date")["pnl_net"].sum().min() if len(oos_t) > 0 else 0

        all_pass = (is_pnl > 0 and oos_pnl > 0 and pm >= 10 and wm >= -200 and mdd >= -500 and wd >= -300)
        marker = "PASS" if all_pass else ""
        is_base = " <--" if val == base[param] else ""

        print(f"{param:>12} {val:>7} {len(is_t):>5} {is_pnl:>7.0f} {len(oos_t):>5} {oos_pnl:>7.0f} "
              f"{oos_wr*100:>5.1f} {pm:>2}/{tm:<2} {wm:>7.0f} {mdd:>6.0f} {wd:>6.0f} {marker:>8}{is_base}")


# ===== 4. Final Output =====
print(f"\n\n{'='*70}")
print("=== V10 R5 FINAL OUTPUT FORMAT ===")
print(f"{'='*70}")

print("""
=== [V10 Short-only 4h-1h Pullback] Round 5 ===
Timeframe: 1h (with 4h trend filter)
Paradigm: Counter-trend pullback shorting (short rallies in 4h downtrend)
Hypothesis: In 4h downtrends, 1h rallies above EMA20 are shorting opportunities
            that revert within 3-15 bars. Single position eliminates correlated loss risk.

Entry:
  1. 4h EMA20 trend = DOWN (close < 4h EMA20)
  2. 1h close > 1h EMA20 (rally/pullback up)
  3. Gap: 0% to 3.0% above EMA20
  4. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  5. ATR(14) percentile < 90 (skip extreme vol)
  6. Exit cooldown: 8 bars since last exit
  7. Monthly entry cap: 20
  8. maxTotal: 1 (only 1 position at a time)

Exit (priority):
  1. SafeNet: +3.5% (25% slip model, max loss ~$175)
  2. EarlyStop: bars 1-5, loss > 1.0% → cut
  3. EMAcross: min hold 3 bars, close < EMA20 → take profit
  4. MaxHold: 15 bars

Risk:
  Daily loss limit: -$200
  Monthly loss limit: -$200
  Consecutive loss pause: 4 losses → 24 bar cooldown
  Max simultaneous: 1 position
""")

print("=== DONE ===")
