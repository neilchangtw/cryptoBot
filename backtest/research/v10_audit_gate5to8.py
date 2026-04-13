"""
V10 Champion1 Independent Audit — Gates 5-8
Gate 5: Parameter Robustness Deep Test
Gate 6: Walk-Forward Deep Analysis
Gate 7: Worst-Case Stress Test
Gate 8: Strategy Logic Verification

Also includes: Gate 2 FOLLOW-UP — can the strategy be salvaged with the 4h fix?
"""
import pandas as pd
import numpy as np
import random
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

random.seed(42)
np.random.seed(42)

# ===== Load Data =====
eth_1h_raw = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
eth_4h_raw = pd.read_csv("data/ETHUSDT_4h_latest730d.csv")
eth_1h_raw["datetime"] = pd.to_datetime(eth_1h_raw["datetime"])
eth_4h_raw["datetime"] = pd.to_datetime(eth_4h_raw["datetime"])

NOTIONAL = 4000
FEE = 4
blocked_hours = {0, 1, 2, 12}
blocked_days = {0, 5, 6}
WARMUP = 150

CHAMPION = {
    "safenet": 3.5, "es_bars": 5, "es_pct": 1.0,
    "min_hold": 3, "max_hold": 15, "exit_cd": 8,
    "entry_cap": 20, "pb_min": 0, "pb_max": 3.0,
    "atr_filter": 90, "daily_limit": -200, "monthly_limit": -200,
    "consec_pause": 4, "consec_cd": 24,
}


def prepare_data_original():
    eth_1h = eth_1h_raw.copy()
    eth_4h = eth_4h_raw.copy()
    eth_1h["ret"] = eth_1h["close"].pct_change()
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
    return eth_1h


def prepare_data_fixed_4h():
    """4h trend with shift(1) — no lookahead."""
    eth_1h = eth_1h_raw.copy()
    eth_4h = eth_4h_raw.copy()
    eth_1h["ret"] = eth_1h["close"].pct_change()
    eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
    eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
    eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
    eth_1h["hour"] = eth_1h["datetime"].dt.hour
    eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek
    eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
    eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                        np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))
    eth_4h["trend_4h"] = eth_4h["trend_4h"].shift(1)
    eth_4h["ema20_4h"] = eth_4h["ema20_4h"].shift(1)
    eth_1h = eth_1h.merge(eth_4h[["datetime", "trend_4h", "ema20_4h"]], on="datetime", how="left")
    eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
    eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()
    return eth_1h


def run_backtest(df, cfg, start_idx=0, end_idx=None):
    N = len(df)
    if end_idx is None:
        end_idx = N
    opens = df["open"].values
    highs = df["high"].values
    closes = df["close"].values
    ema20 = df["ema20"].values
    trend_4h = df["trend_4h"].values
    hours = df["hour"].values
    dows = df["dow"].values
    atr_pct = df["atr14_pct"].values
    datetimes = df["datetime"].values

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
        e20 = ema20[i]
        t4h = trend_4h[i]
        next_open = opens[i + 1] if i + 1 < N else c

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
                    "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "pnl_pct": pnl_pct, "pnl_net": pnl_net,
                    "bars": bars_held, "reason": exit_reason, "bar_idx": i,
                    "entry_bar_idx": pos["entry_bar"],
                })
                positions.remove(pos)

        if daily_pnl <= daily_limit: continue
        if monthly_pnl <= monthly_limit: continue
        if i < cooldown_until: continue
        if len(positions) >= 1: continue
        if hours[i] in blocked_hours or dows[i] in blocked_days: continue
        if monthly_entries >= entry_cap: continue
        if atr_filter > 0 and not np.isnan(atr_pct[i]) and atr_pct[i] > atr_filter: continue
        if (i - last_exit) < exit_cd: continue

        if not np.isnan(t4h) and t4h == -1 and not np.isnan(e20) and e20 > 0:
            gap = (c - e20) / e20
            if pb_min <= gap <= pb_max:
                positions.append({
                    "entry_price": next_open, "entry_bar": i, "entry_dt": dt,
                })
                monthly_entries += 1

    return closed_trades


def analyze(trades, df):
    if not trades:
        return {"is_pnl": 0, "oos_pnl": 0, "oos_n": 0, "oos_wr": 0,
                "pos_months": 0, "total_months": 0, "worst_month": 0,
                "mdd": 0, "worst_day": 0, "all_pass": False}
    N = len(df)
    IS_END = N // 2
    tdf = pd.DataFrame(trades)
    is_df = tdf[tdf["bar_idx"] < IS_END]
    oos = tdf[tdf["bar_idx"] >= IS_END].copy()
    is_pnl = is_df["pnl_net"].sum() if len(is_df) > 0 else 0
    if len(oos) == 0:
        return {"is_pnl": is_pnl, "oos_pnl": 0, "oos_n": 0, "oos_wr": 0,
                "pos_months": 0, "total_months": 0, "worst_month": 0,
                "mdd": 0, "worst_day": 0, "all_pass": False}
    oos_pnl = oos["pnl_net"].sum()
    oos_wr = (oos["pnl_net"] > 0).mean()
    oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
    monthly = oos.groupby("month")["pnl_net"].sum()
    cum = oos["pnl_net"].cumsum()
    mdd = (cum - cum.cummax()).min()
    oos["date"] = pd.to_datetime(oos["exit_dt"]).dt.date
    daily = oos.groupby("date")["pnl_net"].sum()
    pm = (monthly >= 0).sum()
    tm = len(monthly)
    wm = monthly.min()
    wd = daily.min()
    ap = (is_pnl > 0 and oos_pnl > 0 and pm >= 10 and wm >= -200 and mdd >= -500 and wd >= -300)
    return {"is_pnl": is_pnl, "oos_pnl": oos_pnl, "oos_n": len(oos), "oos_wr": oos_wr,
            "pos_months": pm, "total_months": tm, "worst_month": wm,
            "mdd": mdd, "worst_day": wd, "all_pass": ap}


# =====================================================================
# GATE 5: PARAMETER ROBUSTNESS DEEP TEST
# =====================================================================
print("=" * 80)
print("=== GATE 5: PARAMETER ROBUSTNESS DEEP TEST ===")
print("=" * 80)

df_orig = prepare_data_original()

# 5A. Wide-range 1D sweep
print("\n5A. WIDE-RANGE SINGLE-PARAMETER SWEEP")
print("=" * 60)

base = CHAMPION.copy()
params_wide = {
    "safenet":     [2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
    "es_bars":     [0, 3, 4, 5, 6, 8, 10, 12],
    "es_pct":      [0.5, 0.8, 1.0, 1.2, 1.5, 2.0],
    "min_hold":    [1, 2, 3, 4, 5, 7],
    "max_hold":    [8, 10, 12, 15, 18, 24],
    "exit_cd":     [3, 5, 8, 10, 12, 16],
    "pb_max":      [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
    "atr_filter":  [70, 75, 80, 85, 90, 95],
    "entry_cap":   [10, 15, 20, 25, 30],
}

print(f"{'Param':>12} {'Value':>7} {'IS$':>7} {'OOS$':>7} {'WR%':>6} {'PM':>5} {'WrstM':>7} {'MDD':>6} {'WrstD':>6} {'PASS':>6}")
print("-" * 85)

total_configs = 0
pass_configs = 0
oos_positive = 0
cliff_effects = []

for param, values in params_wide.items():
    prev_oos = None
    for val in values:
        cfg = base.copy()
        cfg[param] = val
        trades = run_backtest(df_orig, cfg)
        r = analyze(trades, df_orig)
        total_configs += 1
        if r["oos_pnl"] > 0:
            oos_positive += 1
        if r["all_pass"]:
            pass_configs += 1

        is_base = " <--" if val == base[param] else ""
        p_str = "PASS" if r["all_pass"] else ""
        print(f"{param:>12} {val:>7} {r['is_pnl']:>7.0f} {r['oos_pnl']:>7.0f} "
              f"{r['oos_wr']*100:>5.1f} {r['pos_months']:>2}/{r['total_months']:<2} "
              f"{r['worst_month']:>7.0f} {r['mdd']:>6.0f} {r['worst_day']:>6.0f} {p_str:>6}{is_base}")

        # Cliff detection
        if prev_oos is not None and prev_oos != 0:
            change_pct = abs(r["oos_pnl"] - prev_oos) / abs(prev_oos) * 100
            if change_pct > 50:
                cliff_effects.append((param, val, change_pct))
        prev_oos = r["oos_pnl"]

# 5B. 2D Interaction: SafeNet x ES_pct
print(f"\n\n5B. 2D INTERACTION: SafeNet x ES_pct")
print("=" * 60)

sn_vals = [2.5, 3.0, 3.5, 4.0, 4.5]
es_vals = [0.5, 0.8, 1.0, 1.5, 2.0]

print(f"{'':>10}", end="")
for es in es_vals:
    print(f"  ES={es:>3}", end="")
print()
print("-" * 50)

pass_2d = 0
total_2d = 0
for sn in sn_vals:
    print(f"  SN={sn:>3}%", end="")
    for es in es_vals:
        cfg = base.copy()
        cfg["safenet"] = sn
        cfg["es_pct"] = es
        trades = run_backtest(df_orig, cfg)
        r = analyze(trades, df_orig)
        total_2d += 1
        if r["all_pass"]:
            pass_2d += 1
            print(f" ${r['oos_pnl']:>5.0f}*", end="")
        else:
            print(f" ${r['oos_pnl']:>5.0f} ", end="")
    print()

print(f"\n  2D grid: {pass_2d}/{total_2d} PASS (* = all gates pass)")

# 5C. Aggregate
print(f"\n\n5C. AGGREGATE ROBUSTNESS")
print("=" * 60)
print(f"  Total 1D configs tested: {total_configs}")
print(f"  OOS > $0: {oos_positive}/{total_configs} ({oos_positive/total_configs*100:.0f}%)")
print(f"  ALL GATES PASS: {pass_configs}/{total_configs} ({pass_configs/total_configs*100:.0f}%)")
print(f"  Cliff effects (>50% change): {len(cliff_effects)}")
for param, val, pct in cliff_effects:
    print(f"    {param}={val}: {pct:.0f}% change from previous")


# =====================================================================
# GATE 6: WALK-FORWARD DEEP ANALYSIS
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 6: WALK-FORWARD DEEP ANALYSIS ===")
print("=" * 80)

N = len(df_orig)
IS_END = N // 2
datetimes = df_orig["datetime"].values

# 6A. 6-fold detailed
print("\n6A. 6-FOLD DETAILED BREAKDOWN")
print("=" * 60)

usable = N - WARMUP
fold_size = usable // 6

for fold in range(6):
    oos_start = WARMUP + fold * fold_size
    oos_end = WARMUP + (fold + 1) * fold_size if fold < 5 else N
    trades = run_backtest(df_orig, CHAMPION, oos_start, oos_end)

    start_dt = pd.Timestamp(datetimes[oos_start])
    end_dt = pd.Timestamp(datetimes[min(oos_end - 1, N - 1)])

    # Downtrend ratio
    fold_slice = df_orig.iloc[oos_start:oos_end]
    down_ratio = (fold_slice["trend_4h"] == -1).mean()

    # ETH return
    eth_ret = (fold_slice.iloc[-1]["close"] - fold_slice.iloc[0]["open"]) / fold_slice.iloc[0]["open"]

    if trades:
        tdf = pd.DataFrame(trades)
        pnl = tdf["pnl_net"].sum()
        n = len(tdf)
        wr = (tdf["pnl_net"] > 0).mean()
        reasons = tdf["reason"].value_counts()
        reason_str = ", ".join([f"{r}:{c}" for r, c in reasons.items()])
        status = "+" if pnl > 0 else "-"
    else:
        pnl, n, wr, reason_str, status = 0, 0, 0, "N/A", "-"

    print(f"\n  Fold {fold}: {start_dt.date()} ~ {end_dt.date()}")
    print(f"    ETH: {eth_ret*100:+.1f}%, 4h downtrend: {down_ratio*100:.1f}%")
    print(f"    Trades: {n}, PnL: ${pnl:.0f}, WR: {wr*100:.0f}%, [{status}]")
    print(f"    Exit reasons: {reason_str}")

# 6B. Fold 1/2 failure analysis
print(f"\n\n6B. FOLD 1/2 FAILURE ANALYSIS")
print("=" * 60)
for fold in [1, 2]:
    oos_start = WARMUP + fold * fold_size
    oos_end = WARMUP + (fold + 1) * fold_size
    trades = run_backtest(df_orig, CHAMPION, oos_start, oos_end)
    start_dt = pd.Timestamp(datetimes[oos_start])
    end_dt = pd.Timestamp(datetimes[min(oos_end - 1, N - 1)])

    fold_slice = df_orig.iloc[oos_start:oos_end]
    eth_start = fold_slice.iloc[0]["open"]
    eth_end = fold_slice.iloc[-1]["close"]
    eth_ret = (eth_end - eth_start) / eth_start

    print(f"\n  Fold {fold}: {start_dt.date()} ~ {end_dt.date()}")
    print(f"    ETH: ${eth_start:.0f} -> ${eth_end:.0f} ({eth_ret*100:+.1f}%)")

    if trades:
        tdf = pd.DataFrame(trades)
        print(f"    {len(tdf)} trades, PnL=${tdf['pnl_net'].sum():.0f}")
        for _, t in tdf.iterrows():
            print(f"      {t['entry_dt']:%Y-%m-%d %H:%M} -> {t['exit_dt']:%Y-%m-%d %H:%M} "
                  f"held={int(t['bars'])}b {t['reason']:>9} ${t['pnl_net']:>+7.1f}")

# 6C. 10-fold Walk-Forward
print(f"\n\n6C. 10-FOLD WALK-FORWARD")
print("=" * 60)
fold10_size = usable // 10
wf10_results = []
for fold in range(10):
    oos_start = WARMUP + fold * fold10_size
    oos_end = WARMUP + (fold + 1) * fold10_size if fold < 9 else N
    trades = run_backtest(df_orig, CHAMPION, oos_start, oos_end)
    start_dt = pd.Timestamp(datetimes[oos_start])
    end_dt = pd.Timestamp(datetimes[min(oos_end - 1, N - 1)])
    pnl = sum(t["pnl_net"] for t in trades) if trades else 0
    n = len(trades)
    status = "+" if pnl > 0 else "-"
    wf10_results.append(pnl)
    print(f"  Fold {fold:>2}: {start_dt.date()} ~ {end_dt.date()}  {n:>3}t  ${pnl:>7.0f}  [{status}]")

pos_folds = sum(1 for p in wf10_results if p > 0)
print(f"\n  10-fold result: {pos_folds}/10 positive")

# 6D. Rolling 3-month window
print(f"\n\n6D. ROLLING 3-MONTH WINDOW PnL")
print("=" * 60)
all_trades = run_backtest(df_orig, CHAMPION)
tdf_all = pd.DataFrame(all_trades)
tdf_all["exit_month"] = pd.to_datetime(tdf_all["exit_dt"]).dt.to_period("M")
monthly_pnl = tdf_all.groupby("exit_month")["pnl_net"].sum()

# Rolling 3-month sum
months = list(monthly_pnl.index)
values = list(monthly_pnl.values)
consec_neg = 0
max_consec_neg = 0
print(f"  {'Window':>25} {'PnL':>8}")
for i in range(len(values) - 2):
    window_pnl = sum(values[i:i+3])
    window_name = f"{months[i]}~{months[i+2]}"
    marker = " ***" if window_pnl < 0 else ""
    print(f"  {window_name:>25} ${window_pnl:>7.0f}{marker}")
    if window_pnl < 0:
        consec_neg += 1
        max_consec_neg = max(max_consec_neg, consec_neg)
    else:
        consec_neg = 0

print(f"\n  Consecutive 3-month negative windows: {max_consec_neg}")

# 6E. Circuit breaker effect in fold 1/2
print(f"\n\n6E. CIRCUIT BREAKER EFFECT IN FOLD 1/2")
print("=" * 60)
for fold in [1, 2]:
    oos_start = WARMUP + fold * fold_size
    oos_end = WARMUP + (fold + 1) * fold_size

    # Run with breakers
    trades_with = run_backtest(df_orig, CHAMPION, oos_start, oos_end)
    pnl_with = sum(t["pnl_net"] for t in trades_with)

    # Run without breakers
    cfg_no_brake = CHAMPION.copy()
    cfg_no_brake["daily_limit"] = -99999
    cfg_no_brake["monthly_limit"] = -99999
    cfg_no_brake["consec_pause"] = 999
    trades_without = run_backtest(df_orig, cfg_no_brake, oos_start, oos_end)
    pnl_without = sum(t["pnl_net"] for t in trades_without)

    print(f"  Fold {fold}:")
    print(f"    With breakers:    {len(trades_with):>3}t ${pnl_with:>7.0f}")
    print(f"    Without breakers: {len(trades_without):>3}t ${pnl_without:>7.0f}")
    print(f"    Breaker saved: ${pnl_with - pnl_without:.0f}")


# =====================================================================
# GATE 7: WORST-CASE STRESS TEST
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 7: WORST-CASE STRESS TEST ===")
print("=" * 80)

# 7A. SafeNet penetration analysis
print("\n7A. ALL SAFENET EXITS")
print("=" * 60)
sn_trades = [t for t in all_trades if t["reason"] == "SafeNet"]
print(f"  Total SafeNet exits: {len(sn_trades)}")
print(f"  {'Entry DT':>20} {'Entry$':>8} {'SN Trigger':>10} {'Exit$':>8} {'PnL':>8} {'Bars':>5}")
for t in sn_trades:
    trigger = t["entry_price"] * 1.035
    print(f"  {t['entry_dt']:%Y-%m-%d %H:%M} {t['entry_price']:>8.2f} {trigger:>10.2f} "
          f"{t['exit_price']:>8.2f} ${t['pnl_net']:>7.1f} {int(t['bars']):>5}")
    if t["pnl_net"] < -200:
        print(f"    *** WARNING: Loss > $200!")

max_sn_loss = min(t["pnl_net"] for t in sn_trades) if sn_trades else 0
print(f"\n  Max SafeNet loss: ${max_sn_loss:.1f}")
print(f"  All SafeNet losses < $200: {'YES' if max_sn_loss >= -200 else 'NO'}")

# 7B. Worst day deep dive
print(f"\n\n7B. WORST DAY DEEP DIVE")
print("=" * 60)
tdf_all["date"] = pd.to_datetime(tdf_all["exit_dt"]).dt.date
daily_pnl = tdf_all.groupby("date")["pnl_net"].sum()
worst_date = daily_pnl.idxmin()
worst_pnl = daily_pnl.min()
print(f"  Worst day: {worst_date}, PnL: ${worst_pnl:.1f}")

# Trades on worst day
worst_day_trades = tdf_all[tdf_all["date"] == worst_date]
for _, t in worst_day_trades.iterrows():
    print(f"    {t['entry_dt']:%Y-%m-%d %H:%M} -> {t['exit_dt']:%Y-%m-%d %H:%M} "
          f"{t['reason']:>9} held={int(t['bars'])}b ${t['pnl_net']:>+7.1f}")

# ETH that day
worst_day_bars = df_orig[df_orig["datetime"].dt.date == worst_date]
if len(worst_day_bars) > 0:
    day_open = worst_day_bars.iloc[0]["open"]
    day_high = worst_day_bars["high"].max()
    day_low = worst_day_bars["low"].min()
    day_close = worst_day_bars.iloc[-1]["close"]
    day_ret = (day_close - day_open) / day_open
    print(f"\n  ETH that day: O={day_open:.0f} H={day_high:.0f} L={day_low:.0f} C={day_close:.0f} ({day_ret*100:+.1f}%)")

# 7C. Consecutive loss analysis
print(f"\n\n7C. CONSECUTIVE LOSS STREAKS")
print("=" * 60)
losses = []
streak = 0
streak_pnl = 0
max_streak = 0
max_streak_pnl = 0
streaks = []

for t in all_trades:
    if t["pnl_net"] < 0:
        streak += 1
        streak_pnl += t["pnl_net"]
    else:
        if streak > 0:
            streaks.append((streak, streak_pnl))
            if streak > max_streak:
                max_streak = streak
                max_streak_pnl = streak_pnl
        streak = 0
        streak_pnl = 0

if streak > 0:
    streaks.append((streak, streak_pnl))
    if streak > max_streak:
        max_streak = streak
        max_streak_pnl = streak_pnl

print(f"  Max consecutive losses: {max_streak} trades, ${max_streak_pnl:.0f}")
print(f"\n  Streak distribution:")
from collections import Counter
streak_counts = Counter(s[0] for s in streaks)
for length in sorted(streak_counts.keys()):
    pnls = [s[1] for s in streaks if s[0] == length]
    print(f"    {length} loss streak: {streak_counts[length]} times, avg ${np.mean(pnls):.0f}, worst ${min(pnls):.0f}")

# 7D. Flash crash simulation
print(f"\n\n7D. FLASH CRASH SIMULATION")
print("=" * 60)
sn_pct = 0.035
sn_slip = 0.25
max_loss_pct = sn_pct * (1 + sn_slip)
max_loss_dollar = max_loss_pct * NOTIONAL + FEE
account = 1000
impact = max_loss_dollar / account * 100
print(f"  Scenario: ETH rises 8% in 1 hour (worst case for short)")
print(f"  maxTotal=1 → only 1 position affected")
print(f"  SafeNet: {sn_pct*100}% + {sn_slip*100}% penetration = {max_loss_pct*100:.2f}%")
print(f"  Max loss: ${max_loss_dollar:.1f}")
print(f"  Account impact: {impact:.1f}% of $1,000")

# 7E. Sustained uptrend stress
print(f"\n\n7E. SUSTAINED UPTREND STRESS (worst 3-month period)")
print("=" * 60)
# Find worst 3-month rolling window
months = list(monthly_pnl.index)
values = list(monthly_pnl.values)
worst_3m_pnl = float('inf')
worst_3m_start = None
for i in range(len(values) - 2):
    s = sum(values[i:i+3])
    if s < worst_3m_pnl:
        worst_3m_pnl = s
        worst_3m_start = i

if worst_3m_start is not None:
    w_months = months[worst_3m_start:worst_3m_start+3]
    w_values = values[worst_3m_start:worst_3m_start+3]
    print(f"  Worst 3-month window: {w_months[0]} ~ {w_months[2]}")
    for m, v in zip(w_months, w_values):
        print(f"    {m}: ${v:.0f}")
    print(f"  Total: ${worst_3m_pnl:.0f}")
    print(f"  Account impact: {worst_3m_pnl/1000*100:.1f}% of $1,000")
    print(f"  Survives (> -$500): {'YES' if worst_3m_pnl > -500 else 'NO'}")

# Monthly limit effect
print(f"\n  Monthly -$200 breaker simulation:")
print(f"  Worst case: 3 months × -$200 = -$600")
print(f"  Account impact: -60% of $1,000")
print(f"  But in practice, worst 3-month actual: ${worst_3m_pnl:.0f}")


# =====================================================================
# GATE 8: STRATEGY LOGIC VERIFICATION
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 8: STRATEGY LOGIC VERIFICATION ===")
print("=" * 80)

# 8A. 4h Trend State Distribution
print("\n8A. 4h TREND STATE DISTRIBUTION")
print("=" * 60)
total_bars = len(df_orig)
down_bars = (df_orig["trend_4h"] == -1).sum()
up_bars = (df_orig["trend_4h"] == 1).sum()
zero_bars = (df_orig["trend_4h"] == 0).sum()
nan_bars = df_orig["trend_4h"].isna().sum()
print(f"  Total bars: {total_bars}")
print(f"  4h Downtrend (trend=-1): {down_bars} ({down_bars/total_bars*100:.1f}%)")
print(f"  4h Uptrend (trend=+1):   {up_bars} ({up_bars/total_bars*100:.1f}%)")
print(f"  Neutral/NaN:             {zero_bars + nan_bars} ({(zero_bars+nan_bars)/total_bars*100:.1f}%)")

# 8B. Pullback-then-Drop Statistics
print(f"\n\n8B. PULLBACK-THEN-DROP STATISTICS")
print("=" * 60)
closes = df_orig["close"].values
ema20 = df_orig["ema20"].values
trend_4h_arr = df_orig["trend_4h"].values

# Find all bars where 4h down + close > EMA20
pullback_bars = []
for i in range(WARMUP, N - 20):
    if trend_4h_arr[i] == -1 and closes[i] > ema20[i]:
        gap = (closes[i] - ema20[i]) / ema20[i]
        if 0 <= gap <= 0.03:
            pullback_bars.append(i)

drop_1 = 0
drop_5 = 0
drop_10 = 0
total_pb = len(pullback_bars)

for idx in pullback_bars:
    if idx + 1 < N and closes[idx + 1] < ema20[idx + 1]:
        drop_1 += 1
    found_5 = False
    for j in range(1, min(6, N - idx)):
        if closes[idx + j] < ema20[idx + j]:
            found_5 = True
            break
    if found_5:
        drop_5 += 1
    found_10 = False
    for j in range(1, min(11, N - idx)):
        if closes[idx + j] < ema20[idx + j]:
            found_10 = True
            break
    if found_10:
        drop_10 += 1

print(f"  Pullback events (4h down + close > EMA20, gap 0-3%): {total_pb}")
print(f"  Drops below EMA20 next bar:    {drop_1}/{total_pb} ({drop_1/total_pb*100:.1f}%)")
print(f"  Drops below EMA20 within 5 bar:  {drop_5}/{total_pb} ({drop_5/total_pb*100:.1f}%)")
print(f"  Drops below EMA20 within 10 bar: {drop_10}/{total_pb} ({drop_10/total_pb*100:.1f}%)")

# 8C. Reverse strategy: Long in uptrend
print(f"\n\n8C. REVERSE STRATEGY (Long in 4h uptrend)")
print("=" * 60)

def run_reverse(df, cfg):
    """Long when 4h uptrend + close < EMA20 (dip buy)."""
    N = len(df)
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    ema20 = df["ema20"].values
    trend_4h = df["trend_4h"].values
    hours = df["hour"].values
    dows = df["dow"].values
    atr_pct = df["atr14_pct"].values
    datetimes = df["datetime"].values

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

    for i in range(WARMUP, N - 1):
        dt = pd.Timestamp(datetimes[i])
        ym = (dt.year, dt.month)
        if ym != entry_ym:
            monthly_entries = 0
            entry_ym = ym
        if dt.month != current_month:
            monthly_pnl = 0.0
            current_month = dt.month
        if dt.date() != current_date:
            daily_pnl = 0.0
            current_date = dt.date()

        c = closes[i]
        h = highs[i]
        lo = lows[i]
        e20 = ema20[i]
        t4h = trend_4h[i]
        next_open = opens[i + 1] if i + 1 < N else c

        # Exit (LONG)
        for pos in list(positions):
            bars_held = i - pos["entry_bar"]
            entry_price = pos["entry_price"]
            # Long: SafeNet triggers when LOW drops below threshold
            sn_check = (lo - entry_price) / entry_price
            pnl_check = (c - entry_price) / entry_price
            exit_price = None
            exit_reason = None

            if sn_check <= -safenet:
                slip = safenet * safenet_slip
                exit_price = entry_price * (1 - safenet - slip)
                exit_reason = "SafeNet"
            elif es_bars > 0 and 1 <= bars_held <= es_bars and pnl_check < -es_pct:
                exit_price = c
                exit_reason = "EarlyStop"
            elif bars_held >= min_hold and c > e20:  # Long: close above EMA = profit
                exit_price = c
                exit_reason = "EMAcross"
            elif bars_held >= max_hold:
                exit_price = c
                exit_reason = "MaxHold"

            if exit_price is not None:
                pnl_pct = (exit_price - entry_price) / entry_price
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
                    "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "pnl_pct": pnl_pct, "pnl_net": pnl_net,
                    "bars": bars_held, "reason": exit_reason, "bar_idx": i,
                })
                positions.remove(pos)

        if daily_pnl <= daily_limit: continue
        if monthly_pnl <= monthly_limit: continue
        if i < cooldown_until: continue
        if len(positions) >= 1: continue
        if hours[i] in blocked_hours or dows[i] in blocked_days: continue
        if monthly_entries >= entry_cap: continue
        if atr_filter > 0 and not np.isnan(atr_pct[i]) and atr_pct[i] > atr_filter: continue
        if (i - last_exit) < exit_cd: continue

        # LONG entry: 4h UPTREND + close BELOW EMA20 (dip)
        if not np.isnan(t4h) and t4h == 1 and not np.isnan(e20) and e20 > 0:
            gap = (e20 - c) / e20  # How far below EMA20
            if pb_min <= gap <= pb_max:
                positions.append({
                    "entry_price": next_open, "entry_bar": i, "entry_dt": dt,
                })
                monthly_entries += 1

    return closed_trades

trades_reverse = run_reverse(df_orig, CHAMPION)
r_reverse = analyze(trades_reverse, df_orig)
all_trades_orig = run_backtest(df_orig, CHAMPION)
r_orig_fresh = analyze(all_trades_orig, df_orig)

print(f"  {'':>20} {'Short(orig)':>12} {'Long(reverse)':>12}")
print(f"  {'IS PnL':>20} ${r_orig_fresh['is_pnl']:>10.0f} ${r_reverse['is_pnl']:>10.0f}")
print(f"  {'OOS PnL':>20} ${r_orig_fresh['oos_pnl']:>10.0f} ${r_reverse['oos_pnl']:>10.0f}")
print(f"  {'OOS Trades':>20} {r_orig_fresh['oos_n']:>12} {r_reverse['oos_n']:>12}")
print(f"  {'OOS WR':>20} {r_orig_fresh['oos_wr']*100:>11.1f}% {r_reverse['oos_wr']*100:>11.1f}%")

# 8D. Seasonality check
print(f"\n\n8D. SEASONALITY / STRUCTURAL BIAS")
print("=" * 60)
# OOS period ETH return
oos_slice = df_orig.iloc[IS_END:]
oos_ret = (oos_slice.iloc[-1]["close"] - oos_slice.iloc[0]["open"]) / oos_slice.iloc[0]["open"]
print(f"  OOS period ETH return: {oos_ret*100:+.1f}%")
print(f"  IS period ETH return:  {((df_orig.iloc[IS_END-1]['close'] - df_orig.iloc[0]['open']) / df_orig.iloc[0]['open'])*100:+.1f}%")

if oos_ret < -0.1:
    print(f"  WARNING: OOS ETH dropped >10% — short strategy naturally benefits")
elif oos_ret > 0.1:
    print(f"  NOTE: OOS ETH rose >10% — short strategy had headwind but still profited")
else:
    print(f"  OOS ETH relatively flat — no strong directional bias")


# =====================================================================
# GATE 2 FOLLOW-UP: CAN THE STRATEGY BE SALVAGED?
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 2 FOLLOW-UP: SALVAGE ATTEMPT WITH 4h FIX ===")
print("=" * 80)
print("\nThe 4h lookahead fix (shift(1)) destroyed the strategy.")
print("Testing: can parameter re-optimization save it?\n")

df_fixed = prepare_data_fixed_4h()

# Grid search with fixed 4h
grid_configs = []
for sn in [3.0, 3.5, 4.0, 4.5, 5.0]:
    for es_b in [0, 5, 8, 12]:
        for es_p in [0.8, 1.0, 1.5]:
            for mh in [2, 3, 5]:
                for mx in [10, 15, 20]:
                    for ec in [5, 8, 12]:
                        for pb in [2.0, 3.0, 5.0]:
                            grid_configs.append({
                                "safenet": sn, "es_bars": es_b, "es_pct": es_p,
                                "min_hold": mh, "max_hold": mx, "exit_cd": ec,
                                "entry_cap": 20, "pb_min": 0, "pb_max": pb,
                                "atr_filter": 90, "daily_limit": -200,
                                "monthly_limit": -200, "consec_pause": 4, "consec_cd": 24,
                            })

print(f"Grid size: {len(grid_configs)} configs")
passing_fixed = []
oos_positive_count = 0

for idx, cfg in enumerate(grid_configs):
    if idx % 5000 == 0:
        print(f"  ... config {idx}/{len(grid_configs)}")
    trades = run_backtest(df_fixed, cfg)
    r = analyze(trades, df_fixed)
    if r["oos_pnl"] > 0:
        oos_positive_count += 1
    if r["all_pass"]:
        passing_fixed.append((r["oos_pnl"], r, cfg))

print(f"\nWith 4h fix applied:")
print(f"  OOS > $0: {oos_positive_count}/{len(grid_configs)} ({oos_positive_count/len(grid_configs)*100:.1f}%)")
print(f"  ALL GATES PASS: {len(passing_fixed)}/{len(grid_configs)} ({len(passing_fixed)/len(grid_configs)*100:.2f}%)")

if passing_fixed:
    passing_fixed.sort(key=lambda x: x[0], reverse=True)
    best = passing_fixed[0]
    print(f"\n  Best fixed config:")
    print(f"    OOS PnL: ${best[1]['oos_pnl']:.0f}, IS: ${best[1]['is_pnl']:.0f}")
    print(f"    OOS WR: {best[1]['oos_wr']*100:.1f}%, Pos months: {best[1]['pos_months']}/{best[1]['total_months']}")
    print(f"    Worst month: ${best[1]['worst_month']:.0f}, MDD: ${best[1]['mdd']:.0f}")
    print(f"    Params: {best[2]}")
else:
    print(f"\n  NO CONFIG PASSES ALL GATES WITH 4h FIX.")
    print(f"  → The strategy's entire edge comes from the 4h lookahead bias.")


# =====================================================================
# FINAL SUMMARY
# =====================================================================
print("\n\n" + "=" * 80)
print("=== V10 CHAMPION1 AUDIT FINAL SUMMARY ===")
print("=" * 80)

print("""
| Gate | Name                        | Verdict          | Key Finding |
|------|-----------------------------|------------------|-------------|
|  1   | Code Audit                  | CONDITIONAL PASS | Logic correct except 4h merge |
|  2   | 4h EMA Lookahead            | *** FAIL ***     | 3h lookahead in 4h trend; fix kills strategy |
|  3   | IS/OOS Gap Analysis         | CONDITIONAL PASS | IS 5/13 pos months is weak |
|  4   | Exit Effectiveness          | PASS             | EMAcross beats 95% random sims |
|  5   | Parameter Robustness        | (see above)      | Robust within lookahead framework |
|  6   | Walk-Forward                | (see above)      | 4/6 (6-fold), details above |
|  7   | Stress Test                 | (see above)      | maxTotal=1 limits to $179/trade |
|  8   | Strategy Logic              | (see above)      | Pullback-drop stats above |

CRITICAL FINDING: Gate 2 FAIL
==============================
The 4h EMA20 merge uses the OPEN TIME of the 4h bar as join key.
This gives every 1h bar access to the 4h bar that hasn't closed yet.
Average lookahead: ~1.5 hours per entry signal.
38.6% of entries have 3-hour lookahead.

Impact: Fixing the lookahead (shift 4h trend by 1 bar):
  - OOS PnL: $3,586 → $149 (-95.9%)
  - IS PnL:  $660 → -$1,363
  - Fails 4/6 gates
""")

print("=== DONE ===")
