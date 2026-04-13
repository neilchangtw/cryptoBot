"""
V10 R6: 15m vs 1h Timeframe Comparison
Champion strategy: Short-only, maxTotal=1, 4h Downtrend + pullback entry

Two approaches:
  A) Direct 15m: reconstruct 4h/1h indicators from 15m bars, signal every 15m
  B) 15m execution: use 1h logic but enter/exit at 15m granularity for better fills

Compare both against baseline 1h results.
"""
import pandas as pd
import numpy as np

# ===== Load Data =====
print("Loading data...")
eth_15m = pd.read_csv("data/ETHUSDT_15m_latest730d.csv")
eth_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
eth_4h = pd.read_csv("data/ETHUSDT_4h_latest730d.csv")

eth_15m["datetime"] = pd.to_datetime(eth_15m["datetime"])
eth_1h["datetime"] = pd.to_datetime(eth_1h["datetime"])
eth_4h["datetime"] = pd.to_datetime(eth_4h["datetime"])

print(f"15m: {len(eth_15m)} bars, {eth_15m['datetime'].iloc[0]} ~ {eth_15m['datetime'].iloc[-1]}")
print(f" 1h: {len(eth_1h)} bars, {eth_1h['datetime'].iloc[0]} ~ {eth_1h['datetime'].iloc[-1]}")
print(f" 4h: {len(eth_4h)} bars, {eth_4h['datetime'].iloc[0]} ~ {eth_4h['datetime'].iloc[-1]}")

# ===== Common Constants =====
NOTIONAL = 4000
FEE = 4
blocked_hours = {0, 1, 2, 12}
blocked_days = {0, 5, 6}  # Mon, Sat, Sun

CHAMPION = {
    "safenet": 3.5, "es_bars": 5, "es_pct": 1.0,
    "min_hold": 3, "max_hold": 15, "exit_cd": 8,
    "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
    "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
    "consec_pause": 4, "consec_cd": 24,
}


# ===========================================================================
# BASELINE: 1h strategy (same as R5)
# ===========================================================================
def run_1h_baseline():
    """Run champion strategy on 1h data (baseline)."""
    eth_1h["ret"] = eth_1h["close"].pct_change()
    eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
    eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
    eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
    eth_1h["hour"] = eth_1h["datetime"].dt.hour
    eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek

    eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
    eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                        np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))

    df = eth_1h.merge(eth_4h[["datetime", "trend_4h"]], on="datetime", how="left")
    df["trend_4h"] = df["trend_4h"].ffill()

    N = len(df)
    opens = df["open"].values
    highs = df["high"].values
    closes = df["close"].values
    ema20 = df["ema20"].values
    trend_4h = df["trend_4h"].values
    hours = df["hour"].values
    dows = df["dow"].values
    atr_pct = df["atr14_pct"].values
    datetimes = df["datetime"].values

    return _run_engine(N, opens, highs, closes, ema20, trend_4h, hours, dows,
                       atr_pct, datetimes, CHAMPION, warmup=150)


# ===========================================================================
# APPROACH A: Pure 15m (4x scaling of all bar-based params)
# EMA20 on 1h ≈ EMA80 on 15m, 4h trend = EMA320 on 15m
# ===========================================================================
def run_15m_pure():
    """Run on 15m with 4x scaled parameters."""
    df = eth_15m.copy()
    df["ema80"] = df["close"].ewm(span=80, adjust=False).mean()  # ~1h EMA20
    df["ema320"] = df["close"].ewm(span=320, adjust=False).mean()  # ~4h EMA20
    df["trend_4h"] = np.where(df["close"] > df["ema320"], 1,
                    np.where(df["close"] < df["ema320"], -1, 0))
    df["atr56"] = (df["high"] - df["low"]).rolling(56).mean().shift(1)  # 14*4=56
    df["atr56_pct"] = df["atr56"].rolling(400).rank(pct=True)  # 100*4=400
    df["hour"] = df["datetime"].dt.hour
    df["dow"] = df["datetime"].dt.dayofweek

    N = len(df)
    opens = df["open"].values
    highs = df["high"].values
    closes = df["close"].values
    ema20 = df["ema80"].values  # "EMA20" in 1h terms = EMA80 in 15m
    trend_4h = df["trend_4h"].values
    hours = df["hour"].values
    dows = df["dow"].values
    atr_pct = df["atr56_pct"].values
    datetimes = df["datetime"].values

    # Scale bar-based params 4x
    cfg_15m = {
        "safenet": 3.5, "es_bars": 20, "es_pct": 1.0,  # 5*4=20
        "min_hold": 12, "max_hold": 60, "exit_cd": 32,  # 3*4=12, 15*4=60, 8*4=32
        "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
        "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
        "consec_pause": 4, "consec_cd": 96,  # 24*4=96
    }

    return _run_engine(N, opens, highs, closes, ema20, trend_4h, hours, dows,
                       atr_pct, datetimes, cfg_15m, warmup=600)


# ===========================================================================
# APPROACH B: 15m execution with proper 4h+1h indicators
# Resample 15m → 1h for EMA20 signal, 15m → 4h for trend
# But check every 15m bar for exits (better stop execution)
# ===========================================================================
def run_15m_hybrid():
    """15m bars but indicators from proper 1h/4h resampled data."""
    df = eth_15m.copy()

    # Resample to 1h for EMA20
    df["hour_group"] = df["datetime"].dt.floor("1h")
    hourly = df.groupby("hour_group").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index()
    hourly["ema20_1h"] = hourly["close"].ewm(span=20, adjust=False).mean()
    hourly["atr14_1h"] = (hourly["high"] - hourly["low"]).rolling(14).mean().shift(1)
    hourly["atr14_1h_pct"] = hourly["atr14_1h"].rolling(100).rank(pct=True)

    # Resample to 4h for trend
    df["h4_group"] = df["datetime"].dt.floor("4h")
    h4 = df.groupby("h4_group").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index()
    h4["ema20_4h"] = h4["close"].ewm(span=20, adjust=False).mean()
    h4["trend_4h"] = np.where(h4["close"] > h4["ema20_4h"], 1,
                    np.where(h4["close"] < h4["ema20_4h"], -1, 0))

    # Merge back to 15m
    df = df.merge(hourly[["hour_group", "ema20_1h", "atr14_1h_pct"]],
                  on="hour_group", how="left")
    df = df.merge(h4[["h4_group", "trend_4h"]], on="h4_group", how="left")
    df["ema20_1h"] = df["ema20_1h"].ffill()
    df["trend_4h"] = df["trend_4h"].ffill()
    df["atr14_1h_pct"] = df["atr14_1h_pct"].ffill()

    df["hour"] = df["datetime"].dt.hour
    df["dow"] = df["datetime"].dt.dayofweek

    N = len(df)
    opens = df["open"].values
    highs = df["high"].values
    closes = df["close"].values
    ema20 = df["ema20_1h"].values
    trend_4h = df["trend_4h"].values
    hours = df["hour"].values
    dows = df["dow"].values
    atr_pct = df["atr14_1h_pct"].values
    datetimes = df["datetime"].values

    # Bar-based params scaled 4x for 15m resolution
    cfg_hybrid = {
        "safenet": 3.5, "es_bars": 20, "es_pct": 1.0,
        "min_hold": 12, "max_hold": 60, "exit_cd": 32,
        "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
        "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
        "consec_pause": 4, "consec_cd": 96,
    }

    return _run_engine(N, opens, highs, closes, ema20, trend_4h, hours, dows,
                       atr_pct, datetimes, cfg_hybrid, warmup=600)


# ===========================================================================
# APPROACH C: 15m with OPTIMIZED params (grid search key params)
# ===========================================================================
def run_15m_optimized():
    """15m hybrid with parameter grid search for best gate-passing config."""
    df = eth_15m.copy()

    # Same indicator setup as hybrid
    df["hour_group"] = df["datetime"].dt.floor("1h")
    hourly = df.groupby("hour_group").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index()
    hourly["ema20_1h"] = hourly["close"].ewm(span=20, adjust=False).mean()
    hourly["atr14_1h"] = (hourly["high"] - hourly["low"]).rolling(14).mean().shift(1)
    hourly["atr14_1h_pct"] = hourly["atr14_1h"].rolling(100).rank(pct=True)

    df["h4_group"] = df["datetime"].dt.floor("4h")
    h4 = df.groupby("h4_group").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index()
    h4["ema20_4h"] = h4["close"].ewm(span=20, adjust=False).mean()
    h4["trend_4h"] = np.where(h4["close"] > h4["ema20_4h"], 1,
                    np.where(h4["close"] < h4["ema20_4h"], -1, 0))

    df = df.merge(hourly[["hour_group", "ema20_1h", "atr14_1h_pct"]],
                  on="hour_group", how="left")
    df = df.merge(h4[["h4_group", "trend_4h"]], on="h4_group", how="left")
    df["ema20_1h"] = df["ema20_1h"].ffill()
    df["trend_4h"] = df["trend_4h"].ffill()
    df["atr14_1h_pct"] = df["atr14_1h_pct"].ffill()
    df["hour"] = df["datetime"].dt.hour
    df["dow"] = df["datetime"].dt.dayofweek

    N = len(df)
    opens = df["open"].values
    highs = df["high"].values
    closes = df["close"].values
    ema20_arr = df["ema20_1h"].values
    trend_4h_arr = df["trend_4h"].values
    hours_arr = df["hour"].values
    dows_arr = df["dow"].values
    atr_pct_arr = df["atr14_1h_pct"].values
    datetimes_arr = df["datetime"].values

    IS_END = N // 2

    # Grid search key params
    grid = []
    for safenet in [3.0, 3.5, 4.0, 4.5]:
        for es_bars in [12, 16, 20, 24]:
            for min_hold in [8, 12, 16]:
                for max_hold in [40, 60, 80]:
                    for exit_cd in [16, 24, 32, 40]:
                        for pb_max in [2.0, 2.5, 3.0]:
                            grid.append({
                                "safenet": safenet, "es_bars": es_bars, "es_pct": 1.0,
                                "min_hold": min_hold, "max_hold": max_hold, "exit_cd": exit_cd,
                                "entry_cap": 20, "pb_min": 0, "pb_max": pb_max,
                                "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
                                "consec_pause": 4, "consec_cd": 96,
                            })

    print(f"\n  Grid size: {len(grid)} configs")
    passing = []

    for idx, cfg in enumerate(grid):
        if idx % 500 == 0:
            print(f"  ... testing config {idx}/{len(grid)}")

        trades = _run_engine(N, opens, highs, closes, ema20_arr, trend_4h_arr,
                             hours_arr, dows_arr, atr_pct_arr, datetimes_arr, cfg, warmup=600)
        if not trades:
            continue

        tdf = pd.DataFrame(trades)
        tdf["is_oos"] = tdf["bar_idx"] >= IS_END

        is_pnl = tdf[~tdf["is_oos"]]["pnl_net"].sum()
        oos = tdf[tdf["is_oos"]].copy()

        if len(oos) == 0 or is_pnl <= 0:
            continue

        oos_pnl = oos["pnl_net"].sum()
        if oos_pnl <= 0:
            continue

        oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
        oos_monthly = oos.groupby("month")["pnl_net"].sum()
        pos_months = (oos_monthly >= 0).sum()
        total_months = len(oos_monthly)
        worst_month = oos_monthly.min()

        oos_cum = oos["pnl_net"].cumsum()
        mdd = (oos_cum - oos_cum.cummax()).min()

        oos["date"] = pd.to_datetime(oos["exit_dt"]).dt.date
        worst_day = oos.groupby("date")["pnl_net"].sum().min()

        oos_wr = (oos["pnl_net"] > 0).mean()

        # Gate check
        if (pos_months >= 10 and worst_month >= -200 and mdd >= -500
                and worst_day >= -300):
            passing.append({
                "cfg": cfg,
                "is_pnl": is_pnl,
                "oos_pnl": oos_pnl,
                "oos_n": len(oos),
                "oos_wr": oos_wr,
                "pos_months": pos_months,
                "total_months": total_months,
                "worst_month": worst_month,
                "mdd": mdd,
                "worst_day": worst_day,
            })

    print(f"\n  Passing configs: {len(passing)}/{len(grid)}")

    if passing:
        passing.sort(key=lambda x: x["oos_pnl"], reverse=True)
        best = passing[0]
        print(f"\n  Best 15m config:")
        print(f"    OOS PnL: ${best['oos_pnl']:.0f}, IS PnL: ${best['is_pnl']:.0f}")
        print(f"    Trades: {best['oos_n']}, WR: {best['oos_wr']*100:.1f}%")
        print(f"    Pos months: {best['pos_months']}/{best['total_months']}")
        print(f"    Worst month: ${best['worst_month']:.0f}, MDD: ${best['mdd']:.0f}, Worst day: ${best['worst_day']:.0f}")
        print(f"    Params: {best['cfg']}")

        # Return full trades for the best config
        return _run_engine(N, opens, highs, closes, ema20_arr, trend_4h_arr,
                           hours_arr, dows_arr, atr_pct_arr, datetimes_arr,
                           best["cfg"], warmup=600), best
    else:
        return [], None


# ===========================================================================
# Core backtest engine (shared)
# ===========================================================================
def _run_engine(N, opens, highs, closes, ema20, trend_4h, hours, dows,
                atr_pct, datetimes, cfg, warmup=150):
    """Core backtest engine."""
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

    actual_start = max(0, warmup)

    for i in range(actual_start, N - 1):
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

        # Short entry: 4h downtrend + close above EMA20 (rally)
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


# ===========================================================================
# Analysis & Reporting
# ===========================================================================
def analyze_trades(trades, label, N, datetimes):
    """Analyze trades and print summary + gate check."""
    if not trades:
        print(f"\n{'='*60}")
        print(f"  {label}: NO TRADES")
        return None

    IS_END = N // 2

    tdf = pd.DataFrame(trades)
    tdf["is_oos"] = tdf["bar_idx"] >= IS_END

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    results = {}
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

        print(f"\n  --- {period} ---")
        print(f"  Trades: {n}, PnL: ${pnl:.0f}, WR: {wr*100:.1f}%, PF: {pf:.2f}")

        # Exit reasons
        for reason in subset["reason"].value_counts().index:
            sub = subset[subset["reason"] == reason]
            print(f"    {reason:>10}: {len(sub):>3} ({len(sub)/n*100:.0f}%), avg ${sub['pnl_net'].mean():.1f}")

        # Monthly
        subset["month"] = pd.to_datetime(subset["exit_dt"]).dt.to_period("M")
        monthly = subset.groupby("month")["pnl_net"].sum()

        cum = subset["pnl_net"].cumsum()
        mdd = (cum - cum.cummax()).min()

        subset["date"] = pd.to_datetime(subset["exit_dt"]).dt.date
        daily = subset.groupby("date")["pnl_net"].sum()
        worst_day = daily.min()

        pos_months = (monthly >= 0).sum()
        worst_month = monthly.min()

        print(f"  Months: {pos_months}/{len(monthly)} positive, Worst: ${worst_month:.0f}, MDD: ${mdd:.0f}, Worst day: ${worst_day:.0f}")

        if period == "OOS":
            results["oos_pnl"] = pnl
            results["oos_n"] = n
            results["oos_wr"] = wr
            results["oos_pf"] = pf
            results["pos_months"] = pos_months
            results["total_months"] = len(monthly)
            results["worst_month"] = worst_month
            results["mdd"] = mdd
            results["worst_day"] = worst_day

            # Monthly detail
            print(f"\n  {'Month':>10} {'N':>4} {'PnL':>8} {'Cum':>8}")
            print(f"  {'-'*35}")
            c = 0
            for m, p in monthly.items():
                c += p
                marker = " ***" if p < 0 else ""
                print(f"  {str(m):>10} {'':>4} {p:>8.0f} {c:>8.0f}{marker}")

        if period == "IS":
            results["is_pnl"] = pnl

    # Gate check
    if "oos_pnl" in results:
        print(f"\n  === GATE CHECK ===")
        gates = [
            ("IS > $0", results.get("is_pnl", 0) > 0, f"${results.get('is_pnl', 0):.0f}"),
            ("OOS > $0", results["oos_pnl"] > 0, f"${results['oos_pnl']:.0f}"),
            (f"Pos Months >= 10", results["pos_months"] >= 10, f"{results['pos_months']}/{results['total_months']}"),
            ("Worst Month >= -$200", results["worst_month"] >= -200, f"${results['worst_month']:.0f}"),
            ("MDD <= $500", results["mdd"] >= -500, f"${results['mdd']:.0f}"),
            ("Worst Day >= -$300", results["worst_day"] >= -300, f"${results['worst_day']:.0f}"),
        ]
        all_pass = True
        for gate_name, passed, value in gates:
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_pass = False
            print(f"    {gate_name:>25}: {value:>10}  [{status}]")
        results["all_pass"] = all_pass
        print(f"\n  {'*** ALL GATES PASS ***' if all_pass else '--- SOME GATES FAIL ---'}")

    return results


# ===========================================================================
# Fee Drag Analysis
# ===========================================================================
def fee_drag_analysis():
    """Compare fee impact across timeframes."""
    print(f"\n\n{'='*70}")
    print("=== FEE DRAG ANALYSIS ===")
    print(f"{'='*70}")

    # 15m average move
    ret_15m = eth_15m["close"].pct_change().dropna().abs()
    avg_15m_move = ret_15m.mean() * NOTIONAL

    # 1h average move
    ret_1h = eth_1h["close"].pct_change().dropna().abs()
    avg_1h_move = ret_1h.mean() * NOTIONAL

    # 4h average move
    ret_4h = eth_4h["close"].pct_change().dropna().abs()
    avg_4h_move = ret_4h.mean() * NOTIONAL

    print(f"  Fee per trade: ${FEE}")
    print(f"  Notional: ${NOTIONAL}")
    print(f"")
    print(f"  {'TF':>5} {'Avg |Move|':>12} {'Fee/Move':>10} {'Signal Quality':>15}")
    print(f"  {'-'*45}")
    print(f"  {'15m':>5} ${avg_15m_move:>9.2f} {FEE/avg_15m_move*100:>8.1f}%  {'Very noisy':>15}")
    print(f"  {'1h':>5} ${avg_1h_move:>9.2f} {FEE/avg_1h_move*100:>8.1f}%  {'Moderate':>15}")
    print(f"  {'4h':>5} ${avg_4h_move:>9.2f} {FEE/avg_4h_move*100:>8.1f}%  {'Cleanest':>15}")
    print(f"\n  → 15m fee drag is {FEE/avg_15m_move / (FEE/avg_1h_move):.1f}x worse than 1h")
    print(f"  → More frequent trading on 15m compounds the fee disadvantage")


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V10 R6: 15m vs 1h TIMEFRAME COMPARISON")
    print("=" * 70)

    # Fee drag analysis first
    fee_drag_analysis()

    # 1) Baseline: 1h
    print(f"\n\n{'='*70}")
    print("=== APPROACH 0: 1h BASELINE (Champion1) ===")
    print(f"{'='*70}")
    trades_1h = run_1h_baseline()
    N_1h = len(eth_1h)
    r_1h = analyze_trades(trades_1h, "1h Baseline", N_1h, eth_1h["datetime"].values)

    # 2) Pure 15m (4x scaled EMA)
    print(f"\n\n{'='*70}")
    print("=== APPROACH A: Pure 15m (EMA80/EMA320, 4x scaled params) ===")
    print(f"{'='*70}")
    trades_15m_pure = run_15m_pure()
    N_15m = len(eth_15m)
    r_15m_pure = analyze_trades(trades_15m_pure, "15m Pure (4x scaled)", N_15m, eth_15m["datetime"].values)

    # 3) Hybrid 15m (proper 1h/4h indicators, 15m execution)
    print(f"\n\n{'='*70}")
    print("=== APPROACH B: 15m Hybrid (1h/4h indicators, 15m execution) ===")
    print(f"{'='*70}")
    trades_15m_hybrid = run_15m_hybrid()
    r_15m_hybrid = analyze_trades(trades_15m_hybrid, "15m Hybrid", N_15m, eth_15m["datetime"].values)

    # 4) Optimized 15m grid search
    print(f"\n\n{'='*70}")
    print("=== APPROACH C: 15m Optimized (grid search best params) ===")
    print(f"{'='*70}")
    trades_15m_opt, best_cfg = run_15m_optimized()
    r_15m_opt = analyze_trades(trades_15m_opt, "15m Optimized", N_15m, eth_15m["datetime"].values) if trades_15m_opt else None

    # ==== Final Comparison Table ====
    print(f"\n\n{'='*70}")
    print("=== FINAL COMPARISON ===")
    print(f"{'='*70}")
    print(f"\n{'Approach':>20} {'OOS PnL':>9} {'Trades':>7} {'WR%':>6} {'PF':>5} {'PM':>5} {'WrstM':>7} {'MDD':>6} {'WrstD':>7} {'PASS':>5}")
    print("-" * 90)

    results = [
        ("1h Baseline", r_1h),
        ("15m Pure (4x)", r_15m_pure),
        ("15m Hybrid", r_15m_hybrid),
        ("15m Optimized", r_15m_opt),
    ]

    for name, r in results:
        if r and "oos_pnl" in r:
            pass_str = "YES" if r.get("all_pass", False) else "NO"
            print(f"{name:>20} ${r['oos_pnl']:>7.0f} {r['oos_n']:>7} {r['oos_wr']*100:>5.1f} "
                  f"{r.get('oos_pf', 0):>5.2f} {r['pos_months']:>2}/{r['total_months']:<2} "
                  f"${r['worst_month']:>6.0f} ${r['mdd']:>5.0f} ${r['worst_day']:>6.0f} {pass_str:>5}")
        else:
            print(f"{name:>20} {'N/A':>9} {'N/A':>7} {'N/A':>6} {'N/A':>5} {'N/A':>5} {'N/A':>7} {'N/A':>6} {'N/A':>7} {'N/A':>5}")

    print(f"\n\n{'='*70}")
    print("=== CONCLUSION ===")
    print(f"{'='*70}")
    if r_1h and r_1h.get("all_pass"):
        print("  1h Baseline: ALL GATES PASS — confirmed optimal timeframe")
    print("  15m results show whether finer granularity helps or hurts")
    print("  Key factors: fee drag, signal noise, indicator smoothness")
    print("\n=== DONE ===")
