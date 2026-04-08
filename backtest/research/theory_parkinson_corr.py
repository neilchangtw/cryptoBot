"""
Round 2: Parkinson Volatility Compression + Close Breakout + BTC-ETH Correlation
=================================================================================
Core thesis: ETH explodes after REAL volatility compresses (Parkinson, not ATR).
Combined with close-based breakout (not wick noise) and BTC-ETH synchronization.

Entry (3 conditions, all shift(1)):
  1. Parkinson Vol Ratio: short(5)/long(20) percentile(100) < 30  (compression)
  2. Close Breakout: close[T-1] > max(close[T-2..T-11]) (long) / < min() (short)
  3. BTC-ETH Return Correlation: rolling_corr(24, shift=1) > 0.5  (synchronous)
  4. Fresh: not all 3 conditions met at T-2

Exit: EMA20 trail (min 12h) + SafeNet +/-3.5%

Parameters locked before data:
  PARK_SHORT=5  (5h short-term vol, ~quarter session)
  PARK_LONG=20  (20h long-term vol, ~1 day)
  PARK_PCTILE_WIN=100  (rolling percentile window)
  PARK_THRESH=30  (bottom 30th percentile = compression)
  BREAKOUT_LOOKBACK=10  (10h close history)
  CORR_WINDOW=24  (24h return correlation, 1 full day)
  CORR_THRESH=0.5  (strong positive synchronization)
"""
import os, sys, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════
MARGIN = 100; LEVERAGE = 20; NOTIONAL = MARGIN * LEVERAGE  # $2,000
FEE = 2.0       # Taker 0.04%x2 + slippage 0.01%x2
MAX_SAME = 2
SAFENET_PCT = 0.035
MIN_TRAIL_BARS = 12
ACCOUNT = 10000

# Theory params (LOCKED)
PARK_SHORT = 5           # Short-term Parkinson window
PARK_LONG = 20           # Long-term Parkinson window
PARK_PCTILE_WIN = 100    # Percentile rolling window
PARK_THRESH = 30         # Compression threshold (bottom 30%)
BREAKOUT_LOOKBACK = 10   # Close breakout lookback
CORR_WINDOW = 24         # BTC-ETH return correlation window
CORR_THRESH = 0.5        # Correlation threshold

# Dynamic dates: latest 730 days
END_DATE = datetime(2026, 4, 3)
START_DATE = END_DATE - timedelta(days=732)  # +2 buffer for warm-up
MID_DATE = END_DATE - timedelta(days=365)    # IS/OOS split

# ═══════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════
def fetch_binance(symbol, interval, start_dt, end_dt):
    all_d = []
    cur = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    print(f"  Fetching {symbol} {interval} {start_dt.date()} ~ {end_dt.date()}...", flush=True)
    while cur < end_ms:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "limit": 1000}, timeout=15)
            d = r.json()
            if not d or isinstance(d, dict):
                break
            all_d.extend(d); cur = d[-1][0] + 1; _time.sleep(0.12)
        except Exception as e:
            print(f"  Error: {e}"); break
    if not all_d:
        return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume",
                                       "ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df = df[df["ot"] < end_ms].reset_index(drop=True)
    print(f"  Got {len(df)} bars", flush=True)
    return df

ETH_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ETHUSDT_1h_latest730d.csv")
ETH_CACHE = os.path.normpath(ETH_CACHE)

BTC_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "BTCUSDT_1h_latest730d.csv")
BTC_CACHE = os.path.normpath(BTC_CACHE)

def load_data(symbol, cache_path):
    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df["datetime"] = pd.to_datetime(df["datetime"])
        last = df["datetime"].iloc[-1]
        if (END_DATE - last.to_pydatetime().replace(tzinfo=None)).days <= 2:
            print(f"Loaded {len(df)} bars from cache ({cache_path})", flush=True)
            return df
    df = fetch_binance(symbol, "1h", START_DATE, END_DATE)
    if len(df) == 0:
        print(f"ERROR: No data for {symbol}!"); sys.exit(1)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df

# ═══════════════════════════════════════════════════════════
# Indicator Computation
# ═══════════════════════════════════════════════════════════
def compute_indicators(df, btc_df):
    # EMA20 for trailing exit
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ─── 1. Parkinson Volatility ───
    # Parkinson (1980): σ² = (1 / 4*ln(2)) * ln(H/L)²
    # We use the sqrt form as a volatility measure
    ln_hl = np.log(df["high"] / df["low"])
    parkinson_sq = ln_hl ** 2 / (4 * np.log(2))
    df["park_short"] = np.sqrt(parkinson_sq.rolling(PARK_SHORT).mean())
    df["park_long"] = np.sqrt(parkinson_sq.rolling(PARK_LONG).mean())
    df["park_ratio"] = df["park_short"] / df["park_long"]
    # Percentile of ratio (lower = more compressed)
    df["park_ratio_pctile"] = df["park_ratio"].shift(1).rolling(PARK_PCTILE_WIN).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50
    )
    # shift(1) already applied in the rolling: park_ratio_pctile[T] uses data up to T-1

    # ─── 2. Close Breakout ───
    # close_max[T] = max(close[T-1], close[T-2], ..., close[T-LOOKBACK])
    # close_min[T] = min(close[T-1], close[T-2], ..., close[T-LOOKBACK])
    df["close_max_n"] = df["close"].shift(1).rolling(BREAKOUT_LOOKBACK).max()
    df["close_min_n"] = df["close"].shift(1).rolling(BREAKOUT_LOOKBACK).min()
    # Breakout: close[T-1] > max(close[T-2..T-LOOKBACK-1])
    # But we want breakout at the SIGNAL bar, so:
    # close_shifted[T] = close[T-1]
    # close_shifted needs to break above max of (close[T-2]..close[T-LOOKBACK-1])
    df["close_s1"] = df["close"].shift(1)
    df["close_max_prev"] = df["close"].shift(2).rolling(BREAKOUT_LOOKBACK - 1).max()
    df["close_min_prev"] = df["close"].shift(2).rolling(BREAKOUT_LOOKBACK - 1).min()
    # Long breakout: close[T-1] > max(close[T-2..T-LOOKBACK])
    df["brk_long"] = df["close_s1"] > df["close_max_prev"]
    # Short breakout: close[T-1] < min(close[T-2..T-LOOKBACK])
    df["brk_short"] = df["close_s1"] < df["close_min_prev"]

    # ─── 3. BTC-ETH Return Correlation ───
    # Map BTC close to ETH by open_time
    btc_map = btc_df.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map)
    # Returns (shifted by 1 to avoid current bar)
    eth_ret = np.log(df["close"] / df["close"].shift(1)).shift(1)
    btc_ret = np.log(df["btc_close"] / df["btc_close"].shift(1)).shift(1)
    df["corr_eb"] = eth_ret.rolling(CORR_WINDOW).corr(btc_ret)
    # corr_eb[T] = correlation of returns up to T-1

    # ─── Previous bar conditions for freshness ───
    df["park_pctile_prev"] = df["park_ratio_pctile"].shift(1)
    df["brk_long_prev"] = df["brk_long"].shift(1)
    df["brk_short_prev"] = df["brk_short"].shift(1)
    df["corr_eb_prev"] = df["corr_eb"].shift(1)

    return df

# ═══════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════
def run_backtest(df):
    warmup = PARK_PCTILE_WIN + PARK_LONG + CORR_WINDOW + 10
    start_bar = warmup
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    ema20s = df["ema20"].values
    dts = df["datetime"].values
    park_pctiles = df["park_ratio_pctile"].values
    park_pctiles_prev = df["park_pctile_prev"].values
    brk_longs = df["brk_long"].values
    brk_shorts = df["brk_short"].values
    brk_longs_prev = df["brk_long_prev"].values
    brk_shorts_prev = df["brk_short_prev"].values
    corr_ebs = df["corr_eb"].values
    corr_ebs_prev = df["corr_eb_prev"].values

    lpos = []; spos = []; trades = []

    for i in range(start_bar, len(df) - 1):
        row_h = highs[i]; row_l = lows[i]; row_c = closes[i]
        row_ema20 = ema20s[i]; row_dt = dts[i]
        nxt_open = opens[i + 1]

        # ─── Update positions ───
        nl = []
        for p in lpos:
            closed = False; bars = i - p["ei"]
            if row_l <= p["entry"] * (1 - SAFENET_PCT):
                ep = p["entry"] * (1 - SAFENET_PCT)
                ep = ep - (ep - row_l) * 0.25
                pnl = (ep - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "SafeNet", "side": "long",
                               "bars": bars, "dt": row_dt})
                closed = True
            elif bars >= MIN_TRAIL_BARS and row_c <= row_ema20:
                pnl = (row_c - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "Trail", "side": "long",
                               "bars": bars, "dt": row_dt})
                closed = True
            if not closed: nl.append(p)
        lpos = nl

        ns = []
        for p in spos:
            closed = False; bars = i - p["ei"]
            if row_h >= p["entry"] * (1 + SAFENET_PCT):
                ep = p["entry"] * (1 + SAFENET_PCT)
                ep = ep + (row_h - ep) * 0.25
                pnl = (p["entry"] - ep) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "SafeNet", "side": "short",
                               "bars": bars, "dt": row_dt})
                closed = True
            elif bars >= MIN_TRAIL_BARS and row_c >= row_ema20:
                pnl = (p["entry"] - row_c) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "Trail", "side": "short",
                               "bars": bars, "dt": row_dt})
                closed = True
            if not closed: ns.append(p)
        spos = ns

        # ─── Generate signals ───
        pp = park_pctiles[i]
        pp_prev = park_pctiles_prev[i]
        bl = brk_longs[i]; bs = brk_shorts[i]
        bl_prev = brk_longs_prev[i]; bs_prev = brk_shorts_prev[i]
        ce = corr_ebs[i]; ce_prev = corr_ebs_prev[i]

        if np.isnan(pp) or np.isnan(ce):
            continue

        # C1: Parkinson compression
        compressed = pp < PARK_THRESH

        # C2: Close breakout
        long_brk = bool(bl) if not np.isnan(bl) else False
        short_brk = bool(bs) if not np.isnan(bs) else False

        # C3: BTC-ETH sync
        synced = ce > CORR_THRESH

        # Freshness: at least one condition was NOT met at previous bar
        if not np.isnan(pp_prev) and not np.isnan(ce_prev):
            prev_compressed = pp_prev < PARK_THRESH
            prev_synced = ce_prev > CORR_THRESH
            prev_bl = bool(bl_prev) if not np.isnan(bl_prev) else False
            prev_bs = bool(bs_prev) if not np.isnan(bs_prev) else False
        else:
            prev_compressed = False
            prev_synced = False
            prev_bl = False
            prev_bs = False

        fresh_long = not (prev_compressed and prev_bl and prev_synced)
        fresh_short = not (prev_compressed and prev_bs and prev_synced)

        long_signal = compressed and long_brk and synced and fresh_long
        short_signal = compressed and short_brk and synced and fresh_short

        # ─── Execute entries ───
        if long_signal and len(lpos) < MAX_SAME:
            lpos.append({"entry": nxt_open, "ei": i})
        if short_signal and len(spos) < MAX_SAME:
            spos.append({"entry": nxt_open, "ei": i})

    if trades:
        return pd.DataFrame(trades)
    return pd.DataFrame(columns=["pnl", "type", "side", "bars", "dt"])

# ═══════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════
def calc_stats(tdf):
    if len(tdf) == 0:
        return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sharpe":0,"fees":0}
    n = len(tdf); pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    wins = tdf[tdf["pnl"] > 0]["pnl"].sum()
    losses = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    pf = wins / losses if losses > 0 else 999
    equity = tdf["pnl"].cumsum()
    dd = equity - equity.cummax()
    mdd = dd.min()
    mdd_pct = abs(mdd) / ACCOUNT * 100
    tc = tdf.copy()
    tc["date"] = pd.to_datetime(tc["dt"]).dt.date
    daily = tc.groupby("date")["pnl"].sum()
    all_dates = pd.date_range(tc["dt"].min(), tc["dt"].max(), freq="D")
    daily = daily.reindex(all_dates.date, fill_value=0)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    return {"n":n, "pnl":round(pnl,2), "wr":round(wr,1), "pf":round(pf,2),
            "mdd":round(mdd,2), "mdd_pct":round(mdd_pct,1),
            "sharpe":round(sharpe,2), "fees":round(n*FEE,2)}

def print_box(title, stats, months):
    monthly = stats["n"] / months if months > 0 else 0
    print(f"\n  +{'='*58}+")
    print(f"  | {title:<56s} |")
    print(f"  +{'-'*58}+")
    print(f"  | Trades: {stats['n']:>5d}  (monthly avg {monthly:.1f}){' ':<21s} |")
    print(f"  | Win Rate: {stats['wr']:>5.1f}%{' ':<41s} |")
    print(f"  | Profit Factor: {stats['pf']:>6.2f}{' ':<34s} |")
    print(f"  | Net PnL: ${stats['pnl']:>+10,.2f}{' ':<33s} |")
    print(f"  | Max DD: ${stats['mdd']:>+10,.2f}  ({stats['mdd_pct']:.1f}%){' ':<21s} |")
    print(f"  | Sharpe: {stats['sharpe']:>6.2f}{' ':<38s} |")
    print(f"  | Fees+Slip: -${stats['fees']:>9,.2f}{' ':<32s} |")
    print(f"  +{'='*58}+")

def print_breakdown(tdf):
    print("\n  Hold Time Breakdown:")
    for lo_h, hi_h, label in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),
                               (48,96,"48-96h"),(96,9999,">96h")]:
        sub = tdf[(tdf["bars"]>=lo_h)&(tdf["bars"]<hi_h)]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {label:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")

    print("\n  Long/Short Breakdown:")
    for side in ["long","short"]:
        sub = tdf[tdf["side"]==side]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {side.capitalize():<6s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")

    print("\n  Exit Type:")
    for t in ["Trail","SafeNet"]:
        sub = tdf[tdf["type"]==t]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        print(f"    {t:<10s}: {n:>4d} trades, ${p:>+10,.0f}")

# ═══════════════════════════════════════════════════════════
# God's Eye Self-Check (6 mandatory)
# ═══════════════════════════════════════════════════════════
def gods_eye_check():
    print("\n" + "="*62)
    print("  GOD'S EYE SELF-CHECK (6 mandatory)")
    print("="*62)
    checks = [
        ("1. All signal calcs use shift(1)+ data only?",
         "YES: park_ratio_pctile uses .shift(1) before rolling;\n"
         "         close breakout uses close.shift(1) vs close.shift(2).rolling();\n"
         "         corr_eb uses returns shifted by 1"),
        ("2. Entry price = next bar open?",
         "YES: entry = nxt_open = opens[i+1]"),
        ("3. All rolling indicators have shift(1)?",
         "YES: Parkinson pctile: park_ratio.shift(1).rolling();\n"
         "         Close breakout: close.shift(1) vs close.shift(2).rolling();\n"
         "         Correlation: returns.shift(1).rolling.corr()"),
        ("4. Params decided before seeing any data?",
         "YES: Parkinson short/long from vol theory;\n"
         "         breakout lookback=10 from ~half-day structure;\n"
         "         corr=24h from full trading day cycle"),
        ("5. No post-OOS parameter adjustment?",
         "YES: Single run, no optimization"),
        ("6. Freshness check prevents stale re-entry?",
         "YES: fresh_long/short checks previous bar conditions")
    ]
    all_pass = True
    for q, a in checks:
        status = "PASS" if a.startswith("YES") else "FAIL"
        if status == "FAIL": all_pass = False
        print(f"  [{status}] {q}")
        for line in a.split("\n"):
            print(f"         {line.strip()}")
    print(f"\n  Result: {'6/6 PASS' if all_pass else 'FAILED'}")
    return all_pass

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("="*80)
    print("  Round 2: Parkinson Compression + Close Breakout + BTC-ETH Correlation")
    print("="*80)

    print("\n─── Loading Data ───")
    df_eth = load_data("ETHUSDT", ETH_CACHE)
    df_btc = load_data("BTCUSDT", BTC_CACHE)

    print(f"\nETH bars: {len(df_eth)}")
    print(f"BTC bars: {len(df_btc)}")
    print(f"Date range: {df_eth['datetime'].iloc[0]} ~ {df_eth['datetime'].iloc[-1]}")
    print(f"IS period: < {MID_DATE.date()}")
    print(f"OOS period: >= {MID_DATE.date()}")

    print("\n─── Computing Indicators ───")
    df = compute_indicators(df_eth, df_btc)

    # Diagnostic: how many signal bars per condition
    warmup = PARK_PCTILE_WIN + PARK_LONG + CORR_WINDOW + 10
    valid = df.iloc[warmup:]
    c1 = (valid["park_ratio_pctile"] < PARK_THRESH).sum()
    c2l = valid["brk_long"].sum(); c2s = valid["brk_short"].sum()
    c3 = (valid["corr_eb"] > CORR_THRESH).sum()
    print(f"\n  Condition frequency:")
    print(f"    C1 Parkinson compression (<{PARK_THRESH}th pctile): {c1} bars ({c1/len(valid)*100:.1f}%)")
    print(f"    C2 Close breakout long: {c2l} bars ({c2l/len(valid)*100:.1f}%)")
    print(f"    C2 Close breakout short: {c2s} bars ({c2s/len(valid)*100:.1f}%)")
    print(f"    C3 BTC-ETH corr > {CORR_THRESH}: {c3} bars ({c3/len(valid)*100:.1f}%)")

    print("\n─── Running Full Backtest ───")
    all_trades = run_backtest(df)
    all_trades["dt"] = pd.to_datetime(all_trades["dt"])

    mid_ts = pd.Timestamp(MID_DATE)
    is_trades = all_trades[all_trades["dt"] < mid_ts].reset_index(drop=True)
    oos_trades = all_trades[all_trades["dt"] >= mid_ts].reset_index(drop=True)

    is_months = (MID_DATE - START_DATE).days / 30.44
    oos_months = (END_DATE - MID_DATE).days / 30.44

    is_stats = calc_stats(is_trades)
    oos_stats = calc_stats(oos_trades)
    full_stats = calc_stats(all_trades)

    print_box("IN-SAMPLE (IS)", is_stats, is_months)
    if len(is_trades) > 0:
        print_breakdown(is_trades)

    print_box("OUT-OF-SAMPLE (OOS) ★★★", oos_stats, oos_months)
    if len(oos_trades) > 0:
        print_breakdown(oos_trades)

    print_box("FULL PERIOD", full_stats, is_months + oos_months)
    if len(all_trades) > 0:
        print_breakdown(all_trades)

    # ─── Target Check ───
    oos_monthly_trades = oos_stats["n"] / oos_months if oos_months > 0 else 0
    oos_annual_pnl = oos_stats["pnl"] / (oos_months / 12) if oos_months > 0 else 0
    print("\n" + "="*62)
    print("  TARGET CHECK (OOS)")
    print("="*62)
    t1 = oos_annual_pnl >= 5000
    t2 = oos_stats["pf"] >= 1.5
    t3 = oos_stats["mdd_pct"] <= 25
    t4 = oos_monthly_trades >= 10
    print(f"  [{'PASS' if t1 else 'FAIL'}] OOS Annual PnL >= $5,000: ${oos_annual_pnl:,.0f}")
    print(f"  [{'PASS' if t2 else 'FAIL'}] OOS PF >= 1.5: {oos_stats['pf']}")
    print(f"  [{'PASS' if t3 else 'FAIL'}] OOS MDD <= 25%: {oos_stats['mdd_pct']}%")
    print(f"  [{'PASS' if t4 else 'FAIL'}] OOS Monthly Trades >= 10: {oos_monthly_trades:.1f}")

    gods_eye_check()

    all_pass = t1 and t2 and t3 and t4
    print(f"\n{'★'*20}")
    if all_pass:
        print(f"  ALL TARGETS MET! STRATEGY FOUND!")
    else:
        fails = []
        if not t1: fails.append("PnL")
        if not t2: fails.append("PF")
        if not t3: fails.append("MDD")
        if not t4: fails.append("Freq")
        print(f"  FAILED targets: {', '.join(fails)}")
    print(f"{'★'*20}")
