"""
V10 Champion1 Independent Audit — Gates 1-4
Gate 1: Code Audit (line-by-line + manual verification)
Gate 2: 4h EMA20 Lookahead Risk
Gate 3: IS/OOS Gap Analysis
Gate 4: Exit Mechanism Effectiveness
"""
import pandas as pd
import numpy as np
import random

random.seed(42)
np.random.seed(42)

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

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
    """Prepare data using the ORIGINAL code logic (as in v10_r5)."""
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
    # ORIGINAL merge: on datetime (= open time of 4h bar)
    eth_1h = eth_1h.merge(eth_4h[["datetime", "trend_4h", "ema20_4h"]], on="datetime", how="left")
    eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
    eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()
    return eth_1h


def prepare_data_fixed_4h():
    """Prepare data with 4h trend SHIFTED by 1 bar to fix potential lookahead."""
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
    # FIX: shift 4h trend by 1 bar (use completed 4h bar, not forming one)
    eth_4h["trend_4h_fixed"] = eth_4h["trend_4h"].shift(1)
    eth_4h["ema20_4h_fixed"] = eth_4h["ema20_4h"].shift(1)

    eth_1h = eth_1h.merge(
        eth_4h[["datetime", "trend_4h_fixed", "ema20_4h_fixed"]].rename(
            columns={"trend_4h_fixed": "trend_4h", "ema20_4h_fixed": "ema20_4h"}),
        on="datetime", how="left")
    eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
    eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()
    return eth_1h


def prepare_data_no_4h():
    """Prepare data WITHOUT 4h filter (only 1h EMA20)."""
    eth_1h = eth_1h_raw.copy()
    eth_1h["ret"] = eth_1h["close"].pct_change()
    eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
    eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
    eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
    eth_1h["hour"] = eth_1h["datetime"].dt.hour
    eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek
    eth_1h["trend_4h"] = -1.0  # Always "downtrend" = no filter
    eth_1h["ema20_4h"] = 0.0
    return eth_1h


def prepare_data_ema80():
    """Use 1h EMA(80) as alternative 4h trend proxy."""
    eth_1h = eth_1h_raw.copy()
    eth_1h["ret"] = eth_1h["close"].pct_change()
    eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
    eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean().shift(1)
    eth_1h["atr14_pct"] = eth_1h["atr14"].rolling(100).rank(pct=True)
    eth_1h["hour"] = eth_1h["datetime"].dt.hour
    eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek
    ema80 = eth_1h["close"].ewm(span=80, adjust=False).mean()
    eth_1h["trend_4h"] = np.where(eth_1h["close"] > ema80, 1,
                        np.where(eth_1h["close"] < ema80, -1, 0)).astype(float)
    eth_1h["ema20_4h"] = ema80
    return eth_1h


def prepare_data_no_atr_shift():
    """Remove ATR shift(1) to test its impact."""
    eth_1h = eth_1h_raw.copy()
    eth_4h = eth_4h_raw.copy()
    eth_1h["ret"] = eth_1h["close"].pct_change()
    eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
    eth_1h["atr14"] = (eth_1h["high"] - eth_1h["low"]).rolling(14).mean()  # NO shift
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


def run_backtest(df, cfg, start_idx=0, end_idx=None, log_trades=False):
    """Core backtest engine. Returns list of trade dicts."""
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
                trade = {
                    "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "pnl_pct": pnl_pct, "pnl_net": pnl_net,
                    "bars": bars_held, "reason": exit_reason,
                    "bar_idx": i, "entry_bar_idx": pos["entry_bar"],
                }
                if log_trades:
                    trade["entry_close"] = pos.get("entry_close", 0)
                    trade["entry_ema20"] = pos.get("entry_ema20", 0)
                    trade["entry_trend4h"] = pos.get("entry_trend4h", 0)
                    trade["entry_gap"] = pos.get("entry_gap", 0)
                    trade["entry_atr_pct"] = pos.get("entry_atr_pct", 0)
                closed_trades.append(trade)
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

        # Short entry
        if not np.isnan(t4h) and t4h == -1 and not np.isnan(e20) and e20 > 0:
            gap = (c - e20) / e20
            if pb_min <= gap <= pb_max:
                pos_data = {
                    "entry_price": next_open, "entry_bar": i, "entry_dt": dt,
                }
                if log_trades:
                    pos_data["entry_close"] = c
                    pos_data["entry_ema20"] = e20
                    pos_data["entry_trend4h"] = t4h
                    pos_data["entry_gap"] = gap
                    pos_data["entry_atr_pct"] = atr_pct[i]
                positions.append(pos_data)
                monthly_entries += 1

    return closed_trades


def analyze_oos(trades, df):
    """Compute OOS metrics for a set of trades."""
    if not trades:
        return {"oos_pnl": 0, "oos_n": 0, "oos_wr": 0, "pos_months": 0,
                "total_months": 0, "worst_month": 0, "mdd": 0, "worst_day": 0}
    N = len(df)
    IS_END = N // 2
    tdf = pd.DataFrame(trades)
    oos = tdf[tdf["bar_idx"] >= IS_END].copy()
    is_df = tdf[tdf["bar_idx"] < IS_END]
    if len(oos) == 0:
        return {"is_pnl": is_df["pnl_net"].sum(), "oos_pnl": 0, "oos_n": 0,
                "oos_wr": 0, "pos_months": 0, "total_months": 0,
                "worst_month": 0, "mdd": 0, "worst_day": 0}

    oos_pnl = oos["pnl_net"].sum()
    oos_wr = (oos["pnl_net"] > 0).mean()
    oos["month"] = pd.to_datetime(oos["exit_dt"]).dt.to_period("M")
    monthly = oos.groupby("month")["pnl_net"].sum()
    cum = oos["pnl_net"].cumsum()
    mdd = (cum - cum.cummax()).min()
    oos["date"] = pd.to_datetime(oos["exit_dt"]).dt.date
    daily = oos.groupby("date")["pnl_net"].sum()

    return {
        "is_pnl": is_df["pnl_net"].sum(),
        "oos_pnl": oos_pnl,
        "oos_n": len(oos),
        "oos_wr": oos_wr,
        "pos_months": (monthly >= 0).sum(),
        "total_months": len(monthly),
        "worst_month": monthly.min(),
        "mdd": mdd,
        "worst_day": daily.min(),
    }


# =====================================================================
# GATE 1: CODE AUDIT
# =====================================================================
print("=" * 80)
print("=== GATE 1: CODE AUDIT (Line-by-Line + Manual Verification) ===")
print("=" * 80)

print("""
1A. LINE-BY-LINE CHECK
======================

✓ Line 24: 1h EMA20 = close.ewm(span=20, adjust=False).mean()
  → EMA is recursive, uses current + past closes. No shift needed. CORRECT.

✓ Line 25: ATR(14) = (high - low).rolling(14).mean().shift(1)
  → shift(1) ensures bar i's ATR uses data up to bar i-1. CORRECT.

✓ Line 26: ATR percentile = atr14.rolling(100).rank(pct=True)
  → Rolling rank on already-shifted ATR values. No additional lookahead. CORRECT.

⚠ Lines 30-35: 4h EMA20 + trend calculation and merge
  → eth_4h["ema20_4h"] computed on 4h data. OK.
  → Merged to 1h on "datetime" (= OPEN TIME of bars). POTENTIAL ISSUE.
  → See Gate 2 for detailed analysis.

✓ Line 88: Loop range(actual_start, min(end_idx, N-1))
  → N-1 prevents accessing opens[N] on the last bar. CORRECT.

✓ Line 110: next_open = opens[i+1]
  → Entry at NEXT bar's open. Anti-lookahead confirmed. CORRECT.

✓ Lines 117-118: Short PnL checks
  → sn_check = -(h - entry)/entry: checks if HIGH exceeds SafeNet for SHORT. CORRECT.
  → pnl_check = -(c - entry)/entry: unrealized PnL at close. CORRECT.

✓ Lines 123-125: SafeNet exit
  → Triggers when high ≥ entry × (1 + safenet). CORRECT for short.
  → exit_price = entry × (1 + safenet + slip). Includes 25% penetration. CORRECT.
  → Max loss = -(1.04375 - 1) × $4000 - $4 = -$179. MATCHES reported value.

✓ Line 127: EarlyStop: 1 ≤ bars_held ≤ 5 (inclusive). CORRECT.
  → pnl_check < -es_pct means close is > 1% above entry. CORRECT for short loss.

✓ Line 130: EMAcross: bars_held ≥ 3 AND close < EMA20.
  → For short: close below EMA20 means profit. CORRECT.

✓ Line 133: MaxHold: bars_held ≥ 15.

✓ Line 138: pnl_pct = -(exit - entry) / entry. CORRECT for short.

✓ Lines 143-148: Consecutive loss counter
  → Increments on loss, resets to 0 on win. CORRECT.
  → cooldown_until = i + 24 when consec_losses >= 4. CORRECT.

✓ Lines 165-179: Risk controls are AFTER exit loop → existing positions can still exit. CORRECT.

✓ Lines 98-100: Monthly PnL reset uses month number. Sequential data makes this OK.
✓ Lines 94-96: Entry cap uses (year, month) tuple. More precise. CORRECT.

ISSUES FOUND:
  1. 4h EMA merge uses open-time datetime → POTENTIAL LOOKAHEAD (see Gate 2)
  2. No other logic errors found.
""")

# 1B. Manual Verification of 3 OOS trades
print("\n1B. MANUAL VERIFICATION OF 3 OOS TRADES")
print("=" * 60)

df_orig = prepare_data_original()
N = len(df_orig)
IS_END = N // 2

trades_logged = run_backtest(df_orig, CHAMPION, log_trades=True)
tdf = pd.DataFrame(trades_logged)
oos_trades = tdf[tdf["bar_idx"] >= IS_END].copy()

# Pick 1 EMAcross win, 1 EarlyStop loss, 1 SafeNet loss
ema_wins = oos_trades[(oos_trades["reason"] == "EMAcross") & (oos_trades["pnl_net"] > 0)]
es_losses = oos_trades[(oos_trades["reason"] == "EarlyStop") & (oos_trades["pnl_net"] < 0)]
sn_losses = oos_trades[(oos_trades["reason"] == "SafeNet") & (oos_trades["pnl_net"] < 0)]

samples = []
if len(ema_wins) > 0:
    samples.append(("EMAcross Win", ema_wins.iloc[len(ema_wins)//2]))
if len(es_losses) > 0:
    samples.append(("EarlyStop Loss", es_losses.iloc[0]))
if len(sn_losses) > 0:
    samples.append(("SafeNet Loss", sn_losses.iloc[0]))

for label, trade in samples:
    entry_idx = int(trade["entry_bar_idx"])
    exit_idx = int(trade["bar_idx"])
    print(f"\n  --- {label} ---")
    print(f"  Entry bar: {entry_idx} ({trade['entry_dt']})")
    print(f"  Exit bar:  {exit_idx} ({trade['exit_dt']})")
    print(f"  Entry conditions at bar {entry_idx}:")
    print(f"    Close:     {df_orig.iloc[entry_idx]['close']:.2f}")
    print(f"    EMA20:     {df_orig.iloc[entry_idx]['ema20']:.2f}")
    print(f"    Gap:       {trade['entry_gap']*100:.2f}%")
    print(f"    Trend4h:   {trade['entry_trend4h']}")
    print(f"    ATR pct:   {trade['entry_atr_pct']:.4f}" if not np.isnan(trade['entry_atr_pct']) else "    ATR pct:   NaN")
    print(f"    Hour:      {df_orig.iloc[entry_idx]['hour']}")
    print(f"    DOW:       {df_orig.iloc[entry_idx]['dow']}")
    print(f"  Entry price: {trade['entry_price']:.2f} (= open of bar {entry_idx+1}: {df_orig.iloc[entry_idx+1]['open']:.2f})")
    match = abs(trade['entry_price'] - df_orig.iloc[entry_idx+1]['open']) < 0.01
    print(f"  Entry price MATCH: {'YES' if match else 'NO !!!'}")

    # Verify exit bar by bar
    entry_price = trade["entry_price"]
    safenet = 0.035
    safenet_slip = 0.25
    print(f"\n  Bar-by-bar exit check (entry={entry_price:.2f}):")
    for b in range(entry_idx + 1, min(exit_idx + 2, N)):
        bars_held = b - entry_idx
        hi = df_orig.iloc[b]["high"]
        cl = df_orig.iloc[b]["close"]
        em = df_orig.iloc[b]["ema20"]
        sn = -(hi - entry_price) / entry_price
        pnl_c = -(cl - entry_price) / entry_price

        triggers = []
        if sn <= -safenet:
            triggers.append("SafeNet")
        if 1 <= bars_held <= 5 and pnl_c < -0.01:
            triggers.append("EarlyStop")
        if bars_held >= 3 and cl < em:
            triggers.append("EMAcross")
        if bars_held >= 15:
            triggers.append("MaxHold")

        trigger_str = ", ".join(triggers) if triggers else "-"
        print(f"    Bar {b} (held={bars_held}): H={hi:.2f} C={cl:.2f} EMA={em:.2f} "
              f"sn%={sn*100:.2f}% pnl%={pnl_c*100:.2f}% → {trigger_str}")
        if triggers:
            break

    print(f"\n  Reported: reason={trade['reason']}, bars={trade['bars']}, PnL=${trade['pnl_net']:.1f}")
    expected_pnl = trade["pnl_pct"] * NOTIONAL - FEE
    print(f"  Calculated: pnl_pct={trade['pnl_pct']*100:.3f}%, pnl_net=${expected_pnl:.1f}")

# 1C. First 3 and last 3 trades
print("\n\n1C. FIRST 3 AND LAST 3 TRADES (full signal chain)")
print("=" * 60)
all_trades = tdf
for label, subset in [("FIRST 3", all_trades.head(3)), ("LAST 3", all_trades.tail(3))]:
    print(f"\n  --- {label} ---")
    for _, t in subset.iterrows():
        print(f"  Entry: {t['entry_dt']} bar={int(t['entry_bar_idx'])} "
              f"close={t['entry_close']:.2f} ema={t['entry_ema20']:.2f} "
              f"gap={t['entry_gap']*100:.1f}% trend4h={t['entry_trend4h']:.0f} "
              f"atr_p={t['entry_atr_pct']:.3f}")
        print(f"  Exit:  {t['exit_dt']} bar={int(t['bar_idx'])} "
              f"price={t['exit_price']:.2f} reason={t['reason']} "
              f"held={int(t['bars'])}b PnL=${t['pnl_net']:.1f}")


# =====================================================================
# GATE 2: 4h EMA20 LOOKAHEAD RISK
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 2: 4h EMA20 LOOKAHEAD RISK ===")
print("=" * 80)

# 2A. 4h Candle Aggregation Verification
print("\n2A. 4h CANDLE TIME ALIGNMENT")
print("=" * 60)
print("\n  First 5 x 4h bars:")
for i in range(5):
    row = eth_4h_raw.iloc[i]
    print(f"  4h bar {i}: datetime={row['datetime']}  O={row['open']:.2f} H={row['high']:.2f} "
          f"L={row['low']:.2f} C={row['close']:.2f}")

print(f"\n  First 20 x 1h bars:")
for i in range(20):
    row = eth_1h_raw.iloc[i]
    print(f"  1h bar {i}: datetime={row['datetime']}  O={row['open']:.2f} H={row['high']:.2f} "
          f"L={row['low']:.2f} C={row['close']:.2f}")

# Check: does 4h bar datetime match its first 1h bar?
print(f"\n  4h bar 0 datetime: {eth_4h_raw.iloc[0]['datetime']}")
print(f"  1h bar 0 datetime: {eth_1h_raw.iloc[0]['datetime']}")
print(f"  Match: {eth_4h_raw.iloc[0]['datetime'] == eth_1h_raw.iloc[0]['datetime']}")

# Verify 4h OHLC matches 1h bars
print(f"\n  Verifying 4h bar 0 aggregation from 1h bars:")
h4_dt = pd.Timestamp(eth_4h_raw.iloc[0]["datetime"])
h1_subset = eth_1h_raw[(eth_1h_raw["datetime"] >= h4_dt) &
                        (eth_1h_raw["datetime"] < h4_dt + pd.Timedelta(hours=4))]
print(f"  4h bar: O={eth_4h_raw.iloc[0]['open']:.2f} H={eth_4h_raw.iloc[0]['high']:.2f} "
      f"L={eth_4h_raw.iloc[0]['low']:.2f} C={eth_4h_raw.iloc[0]['close']:.2f}")
print(f"  1h agg: O={h1_subset.iloc[0]['open']:.2f} H={h1_subset['high'].max():.2f} "
      f"L={h1_subset['low'].min():.2f} C={h1_subset.iloc[-1]['close']:.2f}")
print(f"  1h bars in this 4h: {len(h1_subset)} ({list(h1_subset['datetime'].dt.hour)})")

# 2B. Lookahead Analysis
print("\n\n2B. FORWARD-FILL TIME ALIGNMENT (LOOKAHEAD CHECK)")
print("=" * 60)
print("""
  The 4h bar's datetime = its OPEN time.
  A 4h bar opening at T covers T to T+3:59.
  Its CLOSE is at T+4h.

  The merge on datetime aligns 4h bar (open=T) with 1h bar (open=T).
  After ffill, 1h bars T, T+1, T+2, T+3 all get this 4h bar's trend.

  PROBLEM: The 4h bar at T hasn't CLOSED yet at time T.
  Its close occurs at T+4h. But we use trend_4h (computed from that close)
  at 1h bars T through T+3.

  Degree of lookahead per 1h bar within each 4h period:
    1st bar (T+0): 3-4 hours lookahead
    2nd bar (T+1): 2-3 hours
    3rd bar (T+2): 1-2 hours
    4th bar (T+3): 0-1 hours (almost simultaneous)

  → Average ~1.5 hours of lookahead per entry signal.
""")

# Count how many entries used each position within the 4h bar
trades_orig = run_backtest(df_orig, CHAMPION, log_trades=True)
tdf_orig = pd.DataFrame(trades_orig)
# Determine which hour within the 4h block each entry falls
entry_hours = []
for _, t in tdf_orig.iterrows():
    edt = pd.Timestamp(t["entry_dt"])
    h = edt.hour
    pos_in_4h = h % 4  # 0=first, 1=second, 2=third, 3=fourth
    entry_hours.append(pos_in_4h)
tdf_orig["pos_in_4h"] = entry_hours

print(f"  Distribution of entries within 4h blocks:")
for pos in range(4):
    n = (tdf_orig["pos_in_4h"] == pos).sum()
    pct = n / len(tdf_orig) * 100
    lookahead_h = 3 - pos
    print(f"    Position {pos} (lookahead ~{lookahead_h}h): {n} entries ({pct:.1f}%)")

# 2C. Test: Remove 4h filter
print("\n\n2C. REMOVE 4h EMA20 (no trend filter)")
print("=" * 60)
df_no4h = prepare_data_no_4h()
trades_no4h = run_backtest(df_no4h, CHAMPION)
r_no4h = analyze_oos(trades_no4h, df_no4h)
r_orig = analyze_oos(trades_orig, df_orig)

print(f"  {'':>20} {'Original':>12} {'No 4h':>12} {'Delta':>10}")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_no4h['oos_pnl']:>10.0f} {(r_no4h['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS Trades':>20} {r_orig['oos_n']:>12} {r_no4h['oos_n']:>12}")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_no4h['oos_wr']*100:>11.1f}%")
print(f"  {'Pos Months':>20} {r_orig['pos_months']:>4}/{r_orig['total_months']:<7} {r_no4h['pos_months']:>4}/{r_no4h['total_months']:<7}")
print(f"  {'Worst Month':>20} ${r_orig['worst_month']:>10.0f} ${r_no4h['worst_month']:>10.0f}")
print(f"  {'MDD':>20} ${r_orig['mdd']:>10.0f} ${r_no4h['mdd']:>10.0f}")

# 2D. Test: 1h EMA(80) as 4h trend proxy
print("\n\n2D. 1h EMA(80) AS 4h TREND PROXY")
print("=" * 60)
df_ema80 = prepare_data_ema80()
trades_ema80 = run_backtest(df_ema80, CHAMPION)
r_ema80 = analyze_oos(trades_ema80, df_ema80)

print(f"  {'':>20} {'Original':>12} {'EMA80':>12} {'Delta':>10}")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_ema80['oos_pnl']:>10.0f} {(r_ema80['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS Trades':>20} {r_orig['oos_n']:>12} {r_ema80['oos_n']:>12}")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_ema80['oos_wr']*100:>11.1f}%")
print(f"  {'Pos Months':>20} {r_orig['pos_months']:>4}/{r_orig['total_months']:<7} {r_ema80['pos_months']:>4}/{r_ema80['total_months']:<7}")

# 2D-FIX. Test: 4h trend with shift(1) fix
print("\n\n2D-FIX. 4h TREND WITH SHIFT(1) FIX (no lookahead)")
print("=" * 60)
df_fixed = prepare_data_fixed_4h()
trades_fixed = run_backtest(df_fixed, CHAMPION)
r_fixed = analyze_oos(trades_fixed, df_fixed)

print(f"  {'':>20} {'Original':>12} {'Fixed(shift)':>12} {'Delta':>10}")
print(f"  {'IS PnL':>20} ${r_orig['is_pnl']:>10.0f} ${r_fixed['is_pnl']:>10.0f} {(r_fixed['is_pnl']-r_orig['is_pnl'])/max(1,abs(r_orig['is_pnl']))*100:>+9.1f}%")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_fixed['oos_pnl']:>10.0f} {(r_fixed['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS Trades':>20} {r_orig['oos_n']:>12} {r_fixed['oos_n']:>12}")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_fixed['oos_wr']*100:>11.1f}%")
print(f"  {'Pos Months':>20} {r_orig['pos_months']:>4}/{r_orig['total_months']:<7} {r_fixed['pos_months']:>4}/{r_fixed['total_months']:<7}")
print(f"  {'Worst Month':>20} ${r_orig['worst_month']:>10.0f} ${r_fixed['worst_month']:>10.0f}")
print(f"  {'MDD':>20} ${r_orig['mdd']:>10.0f} ${r_fixed['mdd']:>10.0f}")
print(f"  {'Worst Day':>20} ${r_orig['worst_day']:>10.0f} ${r_fixed['worst_day']:>10.0f}")

# Gate check on fixed version
fixed_gates = [
    ("IS > $0", r_fixed["is_pnl"] > 0),
    ("OOS > $0", r_fixed["oos_pnl"] > 0),
    ("Pos Months >= 10", r_fixed["pos_months"] >= 10),
    ("Worst Month >= -$200", r_fixed["worst_month"] >= -200),
    ("MDD <= $500", r_fixed["mdd"] >= -500),
    ("Worst Day >= -$300", r_fixed["worst_day"] >= -300),
]
print(f"\n  GATE CHECK on fixed version:")
all_pass_fixed = True
for name, passed in fixed_gates:
    if not passed:
        all_pass_fixed = False
    print(f"    {name:>25}: {'PASS' if passed else 'FAIL'}")
print(f"  {'ALL GATES PASS' if all_pass_fixed else 'SOME GATES FAIL'}")

# 2E. Remove ATR shift
print("\n\n2E. REMOVE ATR shift(1)")
print("=" * 60)
df_noshift = prepare_data_no_atr_shift()
trades_noshift = run_backtest(df_noshift, CHAMPION)
r_noshift = analyze_oos(trades_noshift, df_noshift)

print(f"  {'':>20} {'Original':>12} {'No ATR shift':>12} {'Delta':>10}")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_noshift['oos_pnl']:>10.0f} {(r_noshift['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_noshift['oos_wr']*100:>11.1f}%")


# =====================================================================
# GATE 3: IS/OOS GAP ANALYSIS
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 3: IS/OOS GAP ANALYSIS ===")
print("=" * 80)

# 3A. ETH Price Action
print("\n3A. ETH PRICE ACTION IS vs OOS")
print("=" * 60)
is_start = df_orig.iloc[0]["datetime"]
is_end_dt = df_orig.iloc[IS_END]["datetime"]
oos_end_dt = df_orig.iloc[-1]["datetime"]

is_prices = df_orig.iloc[:IS_END]
oos_prices = df_orig.iloc[IS_END:]

is_ret = (is_prices.iloc[-1]["close"] - is_prices.iloc[0]["open"]) / is_prices.iloc[0]["open"]
oos_ret = (oos_prices.iloc[-1]["close"] - oos_prices.iloc[0]["open"]) / oos_prices.iloc[0]["open"]

print(f"  IS  ({is_start.date()} ~ {is_end_dt.date()}): ETH {is_ret*100:+.1f}% (${is_prices.iloc[0]['open']:.0f} → ${is_prices.iloc[-1]['close']:.0f})")
print(f"  OOS ({is_end_dt.date()} ~ {oos_end_dt.date()}): ETH {oos_ret*100:+.1f}% (${oos_prices.iloc[0]['open']:.0f} → ${oos_prices.iloc[-1]['close']:.0f})")

# 3B. 4h Downtrend Ratio
print("\n3B. 4h DOWNTREND RATIO")
print("=" * 60)
is_down = (df_orig.iloc[:IS_END]["trend_4h"] == -1).mean()
oos_down = (df_orig.iloc[IS_END:]["trend_4h"] == -1).mean()
print(f"  IS  4h downtrend ratio: {is_down*100:.1f}%")
print(f"  OOS 4h downtrend ratio: {oos_down*100:.1f}%")
print(f"  Difference: {(oos_down - is_down)*100:+.1f}pp")

# 3C. Monthly Breakdown
print("\n3C. MONTHLY BREAKDOWN (IS)")
print("=" * 60)
is_trades = tdf_orig[tdf_orig["bar_idx"] < IS_END].copy()
if len(is_trades) > 0:
    is_trades["month"] = pd.to_datetime(is_trades["exit_dt"]).dt.to_period("M")
    is_monthly = is_trades.groupby("month").agg(
        n=("pnl_net", "count"),
        wr=("pnl_net", lambda x: (x > 0).mean()),
        pnl=("pnl_net", "sum"),
    )
    print(f"  {'Month':>10} {'N':>4} {'WR%':>6} {'PnL':>8} {'Cum':>8}")
    print(f"  {'-'*40}")
    c = 0
    neg_months = 0
    for m, row in is_monthly.iterrows():
        c += row["pnl"]
        marker = " ***" if row["pnl"] < 0 else ""
        if row["pnl"] < 0:
            neg_months += 1
        print(f"  {str(m):>10} {int(row['n']):>4} {row['wr']*100:>5.0f}% {row['pnl']:>8.0f} {c:>8.0f}{marker}")
    print(f"\n  IS positive months: {len(is_monthly) - neg_months}/{len(is_monthly)}")

print(f"\n  MONTHLY BREAKDOWN (OOS)")
oos_t = tdf_orig[tdf_orig["bar_idx"] >= IS_END].copy()
if len(oos_t) > 0:
    oos_t["month"] = pd.to_datetime(oos_t["exit_dt"]).dt.to_period("M")
    oos_monthly = oos_t.groupby("month").agg(
        n=("pnl_net", "count"),
        wr=("pnl_net", lambda x: (x > 0).mean()),
        pnl=("pnl_net", "sum"),
    )
    print(f"  {'Month':>10} {'N':>4} {'WR%':>6} {'PnL':>8} {'Cum':>8}")
    print(f"  {'-'*40}")
    c = 0
    for m, row in oos_monthly.iterrows():
        c += row["pnl"]
        marker = " ***" if row["pnl"] < 0 else ""
        print(f"  {str(m):>10} {int(row['n']):>4} {row['wr']*100:>5.0f}% {row['pnl']:>8.0f} {c:>8.0f}{marker}")

# 3D. Monthly downtrend ratio per month
print(f"\n  4h DOWNTREND % PER MONTH:")
df_orig["ym"] = df_orig["datetime"].dt.to_period("M")
monthly_down = df_orig.groupby("ym").apply(lambda x: (x["trend_4h"] == -1).mean())
print(f"  {'Month':>10} {'Down%':>7}")
for m, d in monthly_down.items():
    period = "IS " if m < df_orig.iloc[IS_END]["datetime"].to_period("M") else "OOS"
    print(f"  {str(m):>10} {d*100:>6.1f}%  [{period}]")

# 3E. Conservative estimate
print(f"\n3D. CONSERVATIVE ANNUALIZED ESTIMATE")
print("=" * 60)
is_pnl = r_orig["is_pnl"]
oos_pnl = r_orig["oos_pnl"]
weighted = (is_pnl + oos_pnl) / 2
conservative = weighted * 0.7
print(f"  IS annual:  ${is_pnl:.0f}")
print(f"  OOS annual: ${oos_pnl:.0f}")
print(f"  50/50 weighted: ${weighted:.0f}")
print(f"  × 0.7 safety:  ${conservative:.0f}")


# =====================================================================
# GATE 4: EXIT MECHANISM EFFECTIVENESS
# =====================================================================
print("\n\n" + "=" * 80)
print("=== GATE 4: EXIT MECHANISM EFFECTIVENESS ===")
print("=" * 80)

# 4A. EMAcross vs Random Exit (100 simulations)
print("\n4A. EMAcross vs RANDOM EXIT (100 sims)")
print("=" * 60)

# Run backtest that logs entry bars, then simulate random exits
def run_random_exit_sim(df, cfg, n_sims=100):
    """Keep all entries from original, but exit at random hold 3-15 bars."""
    N = len(df)
    IS_END = N // 2
    closes_arr = df["close"].values
    opens_arr = df["open"].values
    highs_arr = df["high"].values
    datetimes_arr = df["datetime"].values

    # First get all entry points from original backtest
    orig_trades = run_backtest(df, cfg)
    entries = [(t["entry_bar_idx"], t["entry_price"]) for t in orig_trades]
    oos_entries = [(idx, price) for idx, price in entries if idx >= IS_END]

    safenet = cfg["safenet"] / 100
    safenet_slip = 0.25

    sim_pnls = []
    sim_wrs = []

    for sim in range(n_sims):
        pnl_total = 0
        wins = 0
        total = 0
        for entry_idx, entry_price in oos_entries:
            hold = random.randint(3, 15)
            exit_idx = min(entry_idx + hold, N - 1)
            # Still apply SafeNet
            sn_triggered = False
            for b in range(int(entry_idx) + 1, exit_idx + 1):
                if b >= N:
                    break
                sn_check = -(highs_arr[b] - entry_price) / entry_price
                if sn_check <= -safenet:
                    slip = safenet * safenet_slip
                    exit_price = entry_price * (1 + safenet + slip)
                    sn_triggered = True
                    break
            if not sn_triggered:
                exit_price = closes_arr[min(exit_idx, N-1)]

            pnl_pct = -(exit_price - entry_price) / entry_price
            pnl_net = pnl_pct * NOTIONAL - FEE
            pnl_total += pnl_net
            total += 1
            if pnl_net > 0:
                wins += 1

        sim_pnls.append(pnl_total)
        sim_wrs.append(wins / total if total > 0 else 0)

    return sim_pnls, sim_wrs

sim_pnls, sim_wrs = run_random_exit_sim(df_orig, CHAMPION)
orig_oos_pnl = r_orig["oos_pnl"]
orig_oos_wr = r_orig["oos_wr"]

print(f"  EMAcross exit:  OOS PnL = ${orig_oos_pnl:.0f}, WR = {orig_oos_wr*100:.1f}%")
print(f"  Random exit (100 sims):")
print(f"    Mean PnL: ${np.mean(sim_pnls):.0f}")
print(f"    Std PnL:  ${np.std(sim_pnls):.0f}")
print(f"    Min PnL:  ${np.min(sim_pnls):.0f}")
print(f"    Max PnL:  ${np.max(sim_pnls):.0f}")
print(f"    Mean WR:  {np.mean(sim_wrs)*100:.1f}%")
print(f"  EMAcross outperforms random by: ${orig_oos_pnl - np.mean(sim_pnls):.0f} ({(orig_oos_pnl - np.mean(sim_pnls))/max(1,abs(np.mean(sim_pnls)))*100:+.0f}%)")
beat_pct = sum(1 for p in sim_pnls if orig_oos_pnl > p) / len(sim_pnls)
print(f"  EMAcross beats {beat_pct*100:.0f}% of random simulations")

# 4B. Post-exit price action
print("\n\n4B. POST-EXIT PRICE ACTION (EMAcross exits)")
print("=" * 60)
ema_exits = tdf_orig[(tdf_orig["reason"] == "EMAcross") & (tdf_orig["bar_idx"] >= IS_END)].copy()
post_5 = []
post_10 = []
post_20 = []
continues_down = 0

for _, t in ema_exits.iterrows():
    exit_idx = int(t["bar_idx"])
    exit_price = t["exit_price"]
    entry_price = t["entry_price"]
    # Short position: profit if price goes DOWN after entry
    # After EMAcross exit: does price continue down (would have been more profit)?
    if exit_idx + 5 < N:
        ret5 = (df_orig.iloc[exit_idx + 5]["close"] - exit_price) / exit_price
        post_5.append(ret5)
    if exit_idx + 10 < N:
        ret10 = (df_orig.iloc[exit_idx + 10]["close"] - exit_price) / exit_price
        post_10.append(ret10)
    if exit_idx + 20 < N:
        ret20 = (df_orig.iloc[exit_idx + 20]["close"] - exit_price) / exit_price
        post_20.append(ret20)
    # Does price continue falling? (favorable for held short)
    if exit_idx + 5 < N and df_orig.iloc[exit_idx + 5]["close"] < exit_price:
        continues_down += 1

total_ema = len(ema_exits)
print(f"  EMAcross OOS exits: {total_ema}")
print(f"  After exit, price movement (positive = price goes UP = short would lose):")
print(f"    5-bar avg:  {np.mean(post_5)*100:+.2f}% (favorable to exit: {(np.array(post_5) > 0).mean()*100:.0f}%)")
print(f"    10-bar avg: {np.mean(post_10)*100:+.2f}% (favorable to exit: {(np.array(post_10) > 0).mean()*100:.0f}%)")
print(f"    20-bar avg: {np.mean(post_20)*100:+.2f}% (favorable to exit: {(np.array(post_20) > 0).mean()*100:.0f}%)")
print(f"  Price continues falling after exit (5 bar): {continues_down}/{total_ema} ({continues_down/total_ema*100:.0f}%)")

# 4B extra: If held 5 more bars
held5_pnls = []
for _, t in ema_exits.iterrows():
    exit_idx = int(t["bar_idx"])
    entry_price = t["entry_price"]
    if exit_idx + 5 < N:
        alt_exit = df_orig.iloc[exit_idx + 5]["close"]
        alt_pnl = -(alt_exit - entry_price) / entry_price * NOTIONAL - FEE
        held5_pnls.append(alt_pnl)
actual_pnls = ema_exits["pnl_net"].values[:len(held5_pnls)]
print(f"\n  If held 5 MORE bars after EMAcross:")
print(f"    Original avg PnL:  ${np.mean(actual_pnls):.1f}")
print(f"    Held-5-more avg:   ${np.mean(held5_pnls):.1f}")
print(f"    Difference:        ${np.mean(held5_pnls) - np.mean(actual_pnls):.1f}")

# 4C. Remove EarlyStop
print("\n\n4C. REMOVE EARLYSTOP")
print("=" * 60)
cfg_no_es = CHAMPION.copy()
cfg_no_es["es_bars"] = 0
trades_no_es = run_backtest(df_orig, cfg_no_es)
r_no_es = analyze_oos(trades_no_es, df_orig)

print(f"  {'':>20} {'With ES':>12} {'No ES':>12} {'Delta':>10}")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_no_es['oos_pnl']:>10.0f} {(r_no_es['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_no_es['oos_wr']*100:>11.1f}%")
print(f"  {'MDD':>20} ${r_orig['mdd']:>10.0f} ${r_no_es['mdd']:>10.0f}")
print(f"  {'Worst Month':>20} ${r_orig['worst_month']:>10.0f} ${r_no_es['worst_month']:>10.0f}")
print(f"  {'Worst Day':>20} ${r_orig['worst_day']:>10.0f} ${r_no_es['worst_day']:>10.0f}")

# 4D. SafeNet 3.5% vs 4.5%
print("\n\n4D. SAFENET 3.5% vs 4.5%")
print("=" * 60)
cfg_sn45 = CHAMPION.copy()
cfg_sn45["safenet"] = 4.5
trades_sn45 = run_backtest(df_orig, cfg_sn45)
r_sn45 = analyze_oos(trades_sn45, df_orig)

print(f"  {'':>20} {'SN=3.5%':>12} {'SN=4.5%':>12} {'Delta':>10}")
print(f"  {'OOS PnL':>20} ${r_orig['oos_pnl']:>10.0f} ${r_sn45['oos_pnl']:>10.0f} {(r_sn45['oos_pnl']-r_orig['oos_pnl'])/max(1,abs(r_orig['oos_pnl']))*100:>+9.1f}%")
print(f"  {'OOS WR':>20} {r_orig['oos_wr']*100:>11.1f}% {r_sn45['oos_wr']*100:>11.1f}%")
print(f"  {'MDD':>20} ${r_orig['mdd']:>10.0f} ${r_sn45['mdd']:>10.0f}")
print(f"  {'Worst Month':>20} ${r_orig['worst_month']:>10.0f} ${r_sn45['worst_month']:>10.0f}")

print("\n\n=== GATES 1-4 COMPLETE ===")
