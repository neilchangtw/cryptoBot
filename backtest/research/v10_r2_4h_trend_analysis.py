"""
V10 R2: 4h Timeframe Analysis + Trend Following Strategy
假說：4h 時框的 move 更大，手續費佔比降至 ~3%
      趨勢跟隨搭配 trailing stop 可以捕捉大行情

分析：
  1. 4h 基本統計（vs 1h 對比）
  2. 4h 趨勢持續性（breakout 後的動量）
  3. 4h EMA trail 回測
  4. 4h + 1h multi-timeframe 結合

同時測試 1h 上的趨勢跟隨策略（EMA trail, 較長持有期）
"""
import pandas as pd
import numpy as np

# ===== Load Data =====
eth_4h = pd.read_csv("data/ETHUSDT_4h_latest730d.csv")
eth_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc_1h = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")

for df in [eth_4h, eth_1h, btc_1h]:
    df["datetime"] = pd.to_datetime(df["datetime"])

eth_4h["ret"] = eth_4h["close"].pct_change()
eth_1h["ret"] = eth_1h["close"].pct_change()
btc_1h["ret"] = btc_1h["close"].pct_change()

# ===== 1. 4h Basic Stats =====
print("=" * 70)
print("V10 R2: 4h Timeframe Analysis")
print(f"4h bars: {len(eth_4h)}, date range: {eth_4h['datetime'].iloc[0]} ~ {eth_4h['datetime'].iloc[-1]}")
print("=" * 70)

ret_4h = eth_4h["ret"].dropna()
ret_1h = eth_1h["ret"].dropna()

print("\n=== 1. 4h vs 1h Basic Stats ===")
print(f"{'':>15} {'4h':>12} {'1h':>12}")
print(f"{'Mean%':>15} {ret_4h.mean()*100:>12.4f} {ret_1h.mean()*100:>12.4f}")
print(f"{'Std%':>15} {ret_4h.std()*100:>12.4f} {ret_1h.std()*100:>12.4f}")
print(f"{'Median%':>15} {ret_4h.median()*100:>12.4f} {ret_1h.median()*100:>12.4f}")
print(f"{'WR%':>15} {(ret_4h>0).mean()*100:>12.1f} {(ret_1h>0).mean()*100:>12.1f}")
print(f"{'Fee/2%move':>15} {'5.0%':>12} {'5.0%':>12}")
print(f"{'Bars/day':>15} {'6':>12} {'24':>12}")
print(f"{'Avg|ret|%':>15} {ret_4h.abs().mean()*100:>12.4f} {ret_1h.abs().mean()*100:>12.4f}")

# Key insight: fee as % of average move
fee_pct_4h = 0.1 / (ret_4h.abs().mean() * 100)
fee_pct_1h = 0.1 / (ret_1h.abs().mean() * 100)
print(f"\nFee as % of avg |move|: 4h={fee_pct_4h*100:.1f}%, 1h={fee_pct_1h*100:.1f}%")

# ===== 2. 4h Breakout Momentum =====
print("\n=== 2. 4h Breakout Momentum ===")
for lookback in [3, 5, 8, 10, 15, 20]:
    eth_4h[f"hi_{lookback}"] = eth_4h["high"].rolling(lookback).max().shift(1)
    eth_4h[f"lo_{lookback}"] = eth_4h["low"].rolling(lookback).min().shift(1)

    bo_up = eth_4h["close"] > eth_4h[f"hi_{lookback}"]
    bo_dn = eth_4h["close"] < eth_4h[f"lo_{lookback}"]

    for fwd in [1, 3, 6]:
        fwd_col = f"fwd_ret_{fwd}"
        if fwd_col not in eth_4h.columns:
            eth_4h[fwd_col] = eth_4h["close"].shift(-fwd) / eth_4h["close"] - 1

        if fwd == 3:
            up_fwd = eth_4h.loc[bo_up, fwd_col].dropna()
            dn_fwd = eth_4h.loc[bo_dn, fwd_col].dropna()
            if len(up_fwd) > 10:
                print(f"  Breakout {lookback}-bar high: {fwd}bar fwd mean={up_fwd.mean()*100:.3f}%, WR={((up_fwd>0).mean()*100):.1f}%, N={len(up_fwd)}")
                print(f"  Breakout {lookback}-bar low:  {fwd}bar fwd mean={dn_fwd.mean()*100:.3f}%, WR_up={((dn_fwd>0).mean()*100):.1f}%, N={len(dn_fwd)}")

# ===== 3. Full Backtest: 1h EMA Trend Following =====
print("\n\n=== 3. 1h EMA Trend Following Backtest ===")
print("Testing: Breakout entry + EMA trailing stop (v6-style adapted for $1K)")

# Compute indicators on 1h
eth_1h["ema20"] = eth_1h["close"].ewm(span=20, adjust=False).mean()
eth_1h["ema10"] = eth_1h["close"].ewm(span=10, adjust=False).mean()

# GK volatility
gk = 0.5 * np.log(eth_1h["high"] / eth_1h["low"])**2 - (2*np.log(2)-1) * np.log(eth_1h["close"] / eth_1h["open"])**2
eth_1h["gk"] = gk
eth_1h["gk_ratio"] = eth_1h["gk"].rolling(5).mean() / eth_1h["gk"].rolling(20).mean()
eth_1h["gk_pct"] = eth_1h["gk_ratio"].shift(1).rolling(100).rank(pct=True) * 100

# BTC merge for 1h
eth_1h = eth_1h.merge(btc_1h[["datetime", "ret"]].rename(columns={"ret": "btc_ret"}), on="datetime", how="left")

# Breakout signals
for lb in [8, 10, 12, 15]:
    eth_1h[f"bo_up_{lb}"] = eth_1h["close"] > eth_1h["high"].rolling(lb).max().shift(1)
    eth_1h[f"bo_dn_{lb}"] = eth_1h["close"] < eth_1h["low"].rolling(lb).min().shift(1)

# Session filter (same as v6)
eth_1h["hour"] = eth_1h["datetime"].dt.hour
eth_1h["dow"] = eth_1h["datetime"].dt.dayofweek
blocked_hours = {0, 1, 2, 12}
blocked_days = {0, 5, 6}  # Mon, Sat, Sun

NOTIONAL = 4000
FEE = 4
SAFENET = 0.045
SAFENET_SLIP = 0.25
MAX_SAME_L = 2
MAX_SAME_S = 2
MAX_TOTAL = 3
WARMUP = 150

# IS/OOS split
TOTAL = len(eth_1h)
IS_END = TOTAL // 2

# Circuit breakers
DAILY_LOSS_LIMIT = -300
MONTHLY_LOSS_LIMIT = -500
CONSEC_LOSS_PAUSE = 5
CONSEC_LOSS_COOLDOWN = 24


def run_backtest(df, long_entry_fn, short_entry_fn, long_exit_fn, short_exit_fn,
                 config_name="", min_hold_l=7, min_hold_s=0, entry_cap_l=12,
                 exit_cd_l=10, exit_cd_s=6):
    """Generic backtest engine with all risk controls."""

    positions = []
    closed_trades = []

    daily_pnl = 0.0
    monthly_pnl = 0.0
    consec_losses = 0
    cooldown_until = 0
    current_month = None
    current_date = None
    last_exit_l = -999
    last_exit_s = -999
    monthly_entries_l = 0
    entry_month = None

    for i in range(WARMUP, len(df) - 1):
        bar = df.iloc[i]
        bar_dt = bar["datetime"]
        bar_month = bar_dt.month
        bar_year = bar_dt.year
        bar_date = bar_dt.date()

        ym = (bar_year, bar_month)
        if ym != entry_month:
            monthly_entries_l = 0
            entry_month = ym
        if bar_month != current_month:
            monthly_pnl = 0.0
            current_month = bar_month
        if bar_date != current_date:
            daily_pnl = 0.0
            current_date = bar_date

        next_open = df.iloc[i + 1]["open"]

        # === Exit ===
        for pos in list(positions):
            bars_held = i - pos["entry_bar"]
            side = pos["side"]
            entry_price = pos["entry_price"]

            if side == "long":
                pnl_pct = (bar["close"] - entry_price) / entry_price
                safenet_pnl = (bar["low"] - entry_price) / entry_price
            else:
                pnl_pct = -(bar["close"] - entry_price) / entry_price
                safenet_pnl = -(bar["high"] - entry_price) / entry_price

            exit_price = None
            exit_reason = None

            # SafeNet
            if safenet_pnl <= -SAFENET:
                slip = SAFENET * SAFENET_SLIP
                if side == "long":
                    exit_price = entry_price * (1 - SAFENET - slip)
                else:
                    exit_price = entry_price * (1 + SAFENET + slip)
                exit_reason = "SafeNet"
            elif side == "long":
                exit_price, exit_reason = long_exit_fn(df, i, pos, bars_held)
            else:
                exit_price, exit_reason = short_exit_fn(df, i, pos, bars_held)

            if exit_price is not None:
                if side == "long":
                    final_pnl_pct = (exit_price - entry_price) / entry_price
                else:
                    final_pnl_pct = -(exit_price - entry_price) / entry_price

                pnl_net = final_pnl_pct * NOTIONAL - FEE
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
                    "exit_dt": bar_dt,
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": final_pnl_pct,
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
        if n_long + n_short >= MAX_TOTAL:
            continue

        # Session filter
        if bar["hour"] in blocked_hours or bar["dow"] in blocked_days:
            continue

        # === Long Entry ===
        if n_long < MAX_SAME_L and (i - last_exit_l) >= exit_cd_l and monthly_entries_l < entry_cap_l:
            if long_entry_fn(df, i):
                positions.append({
                    "side": "long",
                    "entry_price": next_open,
                    "entry_bar": i,
                    "entry_dt": bar_dt,
                    "trail_stop": None,
                })
                monthly_entries_l += 1

        # === Short Entry ===
        if n_short < MAX_SAME_S and (i - last_exit_s) >= exit_cd_s:
            if short_entry_fn(df, i):
                positions.append({
                    "side": "short",
                    "entry_price": next_open,
                    "entry_bar": i,
                    "entry_dt": bar_dt,
                })

    return closed_trades


def analyze_trades(trades, label=""):
    """Analyze trade list and print report."""
    if not trades:
        print(f"  {label}: NO TRADES")
        return

    tdf = pd.DataFrame(trades)

    for period, mask_fn in [("IS", lambda t: ~t["is_oos"]), ("OOS", lambda t: t["is_oos"]), ("ALL", lambda t: pd.Series(True, index=t.index))]:
        mask = mask_fn(tdf)
        subset = tdf[mask]
        if len(subset) == 0:
            continue

        n = len(subset)
        pnl = subset["pnl_net"].sum()
        wr = (subset["pnl_net"] > 0).mean()
        avg_win = subset.loc[subset["pnl_net"] > 0, "pnl_net"].mean() if (subset["pnl_net"] > 0).any() else 0
        avg_loss = subset.loc[subset["pnl_net"] <= 0, "pnl_net"].mean() if (subset["pnl_net"] <= 0).any() else 0
        pf = abs(subset.loc[subset["pnl_net"] > 0, "pnl_net"].sum() / subset.loc[subset["pnl_net"] <= 0, "pnl_net"].sum()) if (subset["pnl_net"] <= 0).any() and subset.loc[subset["pnl_net"] <= 0, "pnl_net"].sum() != 0 else 999

        # Monthly breakdown
        subset = subset.copy()
        subset["month"] = pd.to_datetime(subset["exit_dt"]).dt.to_period("M")
        monthly = subset.groupby("month")["pnl_net"].sum()
        pos_months = (monthly > 0).sum()
        total_months = len(monthly)
        worst_month = monthly.min()

        # Max drawdown
        cum = subset["pnl_net"].cumsum()
        peak = cum.cummax()
        dd = cum - peak
        mdd = dd.min()

        # Daily worst
        subset["date"] = pd.to_datetime(subset["exit_dt"]).dt.date
        daily = subset.groupby("date")["pnl_net"].sum()
        worst_day = daily.min()

        # Exit reasons
        reasons = subset["reason"].value_counts()
        reason_str = ", ".join(f"{k}:{v}" for k, v in reasons.items())

        print(f"  {label} {period}: {n}t ${pnl:.0f} PF {pf:.2f} WR {wr*100:.1f}% "
              f"AvgW ${avg_win:.0f} AvgL ${avg_loss:.0f}")
        print(f"    Months: {pos_months}/{total_months} positive, worst ${worst_month:.0f}")
        print(f"    MDD ${mdd:.0f}, worst day ${worst_day:.0f}")
        print(f"    Exits: {reason_str}")

    # Monthly table
    print(f"\n  Monthly PnL ({label}):")
    subset_oos = tdf[tdf["is_oos"]].copy()
    if len(subset_oos) > 0:
        subset_oos["month"] = pd.to_datetime(subset_oos["exit_dt"]).dt.to_period("M")
        monthly = subset_oos.groupby("month").agg(
            trades=("pnl_net", "count"),
            pnl=("pnl_net", "sum"),
            wr=("pnl_net", lambda x: (x > 0).mean()),
        )
        cum = 0
        print(f"  {'Month':>10} {'Trades':>7} {'WR%':>6} {'PnL':>8} {'Cum':>8}")
        for m, row in monthly.iterrows():
            cum += row["pnl"]
            print(f"  {str(m):>10} {int(row['trades']):>7} {row['wr']*100:>5.0f}% {row['pnl']:>8.0f} {cum:>8.0f}")


# ===== Strategy A: 1h Breakout + EMA20 Trail (v6 simplified for $1K) =====
def long_entry_A(df, i):
    bar = df.iloc[i]
    return bar["bo_up_10"] and bar["gk_pct"] < 40

def short_entry_A(df, i):
    bar = df.iloc[i]
    return bar["bo_dn_10"] and bar["gk_pct"] < 40

def long_exit_A(df, i, pos, bars_held):
    if bars_held < 7:
        return None, None
    bar = df.iloc[i]
    # EMA20 trail: exit when close < EMA20
    if bar["close"] < bar["ema20"]:
        return bar["close"], "EMA20"
    return None, None

def short_exit_A(df, i, pos, bars_held):
    bar = df.iloc[i]
    # Fixed TP 2% for short
    entry = pos["entry_price"]
    if (entry - bar["low"]) / entry >= 0.02:
        return entry * 0.98, "TP"
    if bars_held >= 12:
        return bar["close"], "MaxHold"
    return None, None

print("\n--- Strategy A: GK Breakout + EMA20 Trail (v6 simplified) ---")
trades_A = run_backtest(eth_1h, long_entry_A, short_entry_A, long_exit_A, short_exit_A,
                        config_name="A", min_hold_l=7, exit_cd_l=10, exit_cd_s=6)
analyze_trades(trades_A, "StratA")


# ===== Strategy B: 1h Dip-Buy in Uptrend (Pullback) =====
eth_1h["ema50"] = eth_1h["close"].ewm(span=50, adjust=False).mean()
eth_1h["rsi14"] = 100 - 100 / (1 + eth_1h["close"].diff().clip(lower=0).rolling(14).mean() /
                                eth_1h["close"].diff().clip(upper=0).abs().rolling(14).mean())

def long_entry_B(df, i):
    """Buy dip in uptrend: close > EMA50, but close < EMA20 (pullback), GK expanding"""
    bar = df.iloc[i]
    if pd.isna(bar.get("ema50")) or pd.isna(bar.get("ema20")):
        return False
    # Uptrend: close > EMA50
    # Pullback: close < EMA20
    # Not too oversold (still in trend)
    return (bar["close"] > bar["ema50"] and
            bar["close"] < bar["ema20"] and
            bar["close"] > bar["ema20"] * 0.97)  # not too deep

def short_entry_B(df, i):
    """Sell rally in downtrend: close < EMA50, close > EMA20"""
    bar = df.iloc[i]
    if pd.isna(bar.get("ema50")) or pd.isna(bar.get("ema20")):
        return False
    return (bar["close"] < bar["ema50"] and
            bar["close"] > bar["ema20"] and
            bar["close"] < bar["ema20"] * 1.03)

def long_exit_B(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    # TP: 3% or close below EMA50
    if (bar["high"] - entry) / entry >= 0.03:
        return entry * 1.03, "TP3%"
    if bars_held >= 5 and bar["close"] < bar.get("ema50", bar["close"]):
        return bar["close"], "TrendEnd"
    if bars_held >= 15:
        return bar["close"], "MaxHold"
    return None, None

def short_exit_B(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    if (entry - bar["low"]) / entry >= 0.03:
        return entry * 0.97, "TP3%"
    if bars_held >= 5 and bar["close"] > bar.get("ema50", bar["close"]):
        return bar["close"], "TrendEnd"
    if bars_held >= 15:
        return bar["close"], "MaxHold"
    return None, None

print("\n\n--- Strategy B: Pullback in Trend (EMA50 trend + EMA20 pullback) ---")
trades_B = run_backtest(eth_1h, long_entry_B, short_entry_B, long_exit_B, short_exit_B,
                        config_name="B", min_hold_l=0, exit_cd_l=5, exit_cd_s=5)
analyze_trades(trades_B, "StratB")


# ===== Strategy C: BTC-ETH RelDiv Improved (v9-style) =====
eth_1h["btc_cum5"] = eth_1h["btc_ret"].rolling(5).sum().shift(1)
eth_1h["eth_cum5"] = eth_1h["ret"].rolling(5).sum().shift(1)
eth_1h["reldiv5"] = eth_1h["btc_cum5"] - eth_1h["eth_cum5"]

# Also test 3-bar and 8-bar lookbacks
eth_1h["btc_cum3"] = eth_1h["btc_ret"].rolling(3).sum().shift(1)
eth_1h["eth_cum3"] = eth_1h["ret"].rolling(3).sum().shift(1)
eth_1h["reldiv3"] = eth_1h["btc_cum3"] - eth_1h["eth_cum3"]

eth_1h["btc_cum8"] = eth_1h["btc_ret"].rolling(8).sum().shift(1)
eth_1h["eth_cum8"] = eth_1h["ret"].rolling(8).sum().shift(1)
eth_1h["reldiv8"] = eth_1h["btc_cum8"] - eth_1h["eth_cum8"]

def long_entry_C(df, i):
    """Buy ETH when it significantly underperforms BTC (mean reversion)"""
    bar = df.iloc[i]
    # ETH lagged BTC by > 3% over 5 bars
    return bar.get("reldiv5", 0) > 0.03

def short_entry_C(df, i):
    """Short ETH when it significantly outperforms BTC"""
    bar = df.iloc[i]
    return bar.get("reldiv5", 0) < -0.03

def long_exit_C(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    # TP 2.5%
    if (bar["high"] - entry) / entry >= 0.025:
        return entry * 1.025, "TP"
    # Time stop
    if bars_held >= 12:
        return bar["close"], "MaxHold"
    return None, None

def short_exit_C(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    if (entry - bar["low"]) / entry >= 0.025:
        return entry * 0.975, "TP"
    if bars_held >= 12:
        return bar["close"], "MaxHold"
    return None, None

print("\n\n--- Strategy C: BTC-ETH RelDiv 5-bar > 3% (v9-style) ---")
trades_C = run_backtest(eth_1h, long_entry_C, short_entry_C, long_exit_C, short_exit_C,
                        config_name="C", min_hold_l=0, exit_cd_l=3, exit_cd_s=3)
analyze_trades(trades_C, "StratC")


# ===== Strategy D: Adaptive RelDiv with different thresholds =====
for lb, thresh in [(3, 0.025), (5, 0.025), (5, 0.035), (8, 0.03), (8, 0.04)]:
    col = f"reldiv{lb}"

    def make_long_entry(c, t):
        def fn(df, i):
            return df.iloc[i].get(c, 0) > t
        return fn

    def make_short_entry(c, t):
        def fn(df, i):
            return df.iloc[i].get(c, 0) < -t
        return fn

    trades = run_backtest(eth_1h, make_long_entry(col, thresh), make_short_entry(col, thresh),
                         long_exit_C, short_exit_C,
                         config_name=f"D_{lb}_{thresh}", min_hold_l=0, exit_cd_l=3, exit_cd_s=3)
    if trades:
        tdf = pd.DataFrame(trades)
        is_trades = tdf[~tdf["is_oos"]]
        oos_trades = tdf[tdf["is_oos"]]
        is_pnl = is_trades["pnl_net"].sum() if len(is_trades) > 0 else 0
        oos_pnl = oos_trades["pnl_net"].sum() if len(oos_trades) > 0 else 0
        wr = (tdf["pnl_net"] > 0).mean()
        print(f"\n  RelDiv lb={lb} thresh={thresh}: {len(tdf)}t IS ${is_pnl:.0f} OOS ${oos_pnl:.0f} WR {wr*100:.1f}%")

        if oos_pnl > 0 and is_pnl > 0:
            print("    *** BOTH IS AND OOS POSITIVE ***")
            analyze_trades(trades, f"D_{lb}_{thresh}")


# ===== Strategy E: 4h Trend + 1h Entry =====
print("\n\n=== Strategy E: 4h Trend + 1h Entry ===")

# Map 4h trend to 1h bars
eth_4h["ema20_4h"] = eth_4h["close"].ewm(span=20, adjust=False).mean()
eth_4h["trend_4h"] = np.where(eth_4h["close"] > eth_4h["ema20_4h"], 1,
                    np.where(eth_4h["close"] < eth_4h["ema20_4h"], -1, 0))

# Map to 1h by forward-filling 4h values
eth_4h_map = eth_4h[["datetime", "trend_4h", "ema20_4h"]].copy()
eth_1h = eth_1h.merge(eth_4h_map, on="datetime", how="left")
eth_1h["trend_4h"] = eth_1h["trend_4h"].ffill()
eth_1h["ema20_4h"] = eth_1h["ema20_4h"].ffill()

def long_entry_E(df, i):
    """Buy on 1h dip when 4h trend is up"""
    bar = df.iloc[i]
    if pd.isna(bar.get("trend_4h")):
        return False
    # 4h uptrend
    if bar["trend_4h"] != 1:
        return False
    # 1h pullback: close dipped below EMA20 1h
    if bar["close"] >= bar["ema20"]:
        return False
    # But not too deep
    if bar["close"] < bar["ema20"] * 0.97:
        return False
    return True

def short_entry_E(df, i):
    """Short on 1h rally when 4h trend is down"""
    bar = df.iloc[i]
    if pd.isna(bar.get("trend_4h")):
        return False
    if bar["trend_4h"] != -1:
        return False
    if bar["close"] <= bar["ema20"]:
        return False
    if bar["close"] > bar["ema20"] * 1.03:
        return False
    return True

def long_exit_E(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    # Exit when price breaks above EMA20 + some profit, OR time stop
    if bars_held >= 3 and bar["close"] > bar["ema20"]:
        return bar["close"], "EMAcross"
    if (bar["high"] - entry) / entry >= 0.03:
        return entry * 1.03, "TP3%"
    if bars_held >= 12:
        return bar["close"], "MaxHold"
    return None, None

def short_exit_E(df, i, pos, bars_held):
    bar = df.iloc[i]
    entry = pos["entry_price"]
    if bars_held >= 3 and bar["close"] < bar["ema20"]:
        return bar["close"], "EMAcross"
    if (entry - bar["low"]) / entry >= 0.03:
        return entry * 0.97, "TP3%"
    if bars_held >= 12:
        return bar["close"], "MaxHold"
    return None, None

trades_E = run_backtest(eth_1h, long_entry_E, short_entry_E, long_exit_E, short_exit_E,
                        config_name="E", min_hold_l=0, exit_cd_l=5, exit_cd_s=5)
analyze_trades(trades_E, "StratE")


print("\n\n=== R2 COMPLETE ===")
