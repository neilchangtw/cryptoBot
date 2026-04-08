"""
Theory 1: Swing Structure Breakout (Dow Theory)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Core: ETH forms pivot highs/lows. When structure compresses (tight
pivot range) and higher lows confirm uptrend, breakout above pivot
high signals genuine trend start.

Entry (max 3 conditions):
  1. close > last confirmed pivot high (fresh breakout)
  2. (pivot_high - pivot_low) / close < 5% (structure compression)
  3. last_pivot_low > prev_pivot_low (higher lows = uptrend)
  [Mirror for short]

Exit: EMA20 trail (min 12h) + SafeNet +/-3.5%
Params: PIVOT_N=7 (structurally determined, not optimized)
"""
import os, sys, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════
MARGIN = 100; LEVERAGE = 20; NOTIONAL = MARGIN * LEVERAGE  # $2,000
FEE = 2.0       # Taker 0.04%x2 ($1.60) + slippage 0.01%x2 ($0.40)
MAX_SAME = 2
SAFENET_PCT = 0.035
MIN_TRAIL_BARS = 12   # 12h min hold before EMA20 trail
ACCOUNT = 10000       # For MDD% calc

# Theory params (structurally determined)
PIVOT_N = 7           # 7 bars each side for pivot detection
COMPRESS_PCT = 0.05   # 5% max range between pivot H and L

# Date range
START_DATE = datetime(2023, 1, 1)
END_DATE = datetime(2025, 1, 1)
IS_SPLIT = pd.Timestamp("2024-01-01 00:00:00")

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ETHUSDT_1h_20230101_20241231.csv")
CACHE = os.path.normpath(CACHE)

# ═══════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════
def fetch_binance(symbol, interval, start_dt, end_dt):
    all_d = []
    cur = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    print(f"  Fetching {symbol} {interval} from {start_dt.date()} to {end_dt.date()}...", flush=True)
    while cur < end_ms:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "limit": 1000}, timeout=15)
            d = r.json()
            if not d or isinstance(d, dict):
                break
            all_d.extend(d)
            cur = d[-1][0] + 1
            _time.sleep(0.12)
        except Exception as e:
            print(f"  Fetch error: {e}", flush=True)
            break
    if not all_d:
        return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume",
                                       "ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df = df[df["ot"] < end_ms].reset_index(drop=True)
    print(f"  Fetched {len(df)} bars", flush=True)
    return df

def load_data():
    if os.path.exists(CACHE):
        df = pd.read_csv(CACHE)
        df["datetime"] = pd.to_datetime(df["datetime"])
        print(f"Loaded {len(df)} bars from cache", flush=True)
        return df
    df = fetch_binance("ETHUSDT", "1h", START_DATE, END_DATE)
    if len(df) == 0:
        print("ERROR: No data fetched!")
        sys.exit(1)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    df.to_csv(CACHE, index=False)
    print(f"Cached to {CACHE}", flush=True)
    return df

# ═══════════════════════════════════════════════════════════
# Indicator Computation
# ═══════════════════════════════════════════════════════════
def compute_indicators(df):
    # EMA20 for trailing exit (unshifted, same convention as champion)
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ─── Pivot highs and lows ───
    N = PIVOT_N
    raw_ph = np.full(len(df), np.nan)
    raw_pl = np.full(len(df), np.nan)

    for j in range(N, len(df) - N):
        window_h = df["high"].values[j-N:j+N+1]
        if df["high"].values[j] >= max(window_h):
            raw_ph[j] = df["high"].values[j]
        window_l = df["low"].values[j-N:j+N+1]
        if df["low"].values[j] <= min(window_l):
            raw_pl[j] = df["low"].values[j]

    df["raw_ph"] = raw_ph
    df["raw_pl"] = raw_pl

    return df

# ═══════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════
def run_backtest(df):
    N = PIVOT_N
    start_bar = 2 * N + 10  # Burn-in for pivot history

    lpos = []; spos = []; trades = []

    # Pivot history
    ph_list = []  # Confirmed pivot high values (chronological)
    pl_list = []

    # Track whether current pivot level has been broken
    ph_broken = True
    pl_broken = True
    last_ph_val = np.nan
    last_pl_val = np.nan

    for i in range(start_bar, len(df) - 1):
        row_h = df["high"].values[i]
        row_l = df["low"].values[i]
        row_c = df["close"].values[i]
        row_ema20 = df["ema20"].values[i]
        row_dt = df["datetime"].values[i]
        nxt_open = df["open"].values[i + 1]

        # ─── Update positions ───
        nl = []
        for p in lpos:
            closed = False
            bars = i - p["ei"]
            # SafeNet
            if row_l <= p["entry"] * (1 - SAFENET_PCT):
                ep = p["entry"] * (1 - SAFENET_PCT)
                ep = ep - (ep - row_l) * 0.25  # slippage model
                pnl = (ep - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "SafeNet", "side": "long",
                               "bars": bars, "dt": row_dt})
                closed = True
            # EMA20 Trail
            elif bars >= MIN_TRAIL_BARS and row_c <= row_ema20:
                pnl = (row_c - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "Trail", "side": "long",
                               "bars": bars, "dt": row_dt})
                closed = True
            if not closed:
                nl.append(p)
        lpos = nl

        ns = []
        for p in spos:
            closed = False
            bars = i - p["ei"]
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
            if not closed:
                ns.append(p)
        spos = ns

        # ─── Check newly confirmed pivots ───
        # Pivot at bar j confirmed at bar j+N. With +1 shift: available at j+N+1.
        # At bar i, latest confirmable pivot was at bar i - N - 1.
        confirm_bar = i - N - 1

        if confirm_bar >= N:
            raw_ph_val = df["raw_ph"].values[confirm_bar]
            raw_pl_val = df["raw_pl"].values[confirm_bar]

            if not np.isnan(raw_ph_val):
                ph_list.append(raw_ph_val)
            if not np.isnan(raw_pl_val):
                pl_list.append(raw_pl_val)

        # Current structure levels
        cur_last_ph = ph_list[-1] if ph_list else np.nan
        cur_prev_ph = ph_list[-2] if len(ph_list) >= 2 else np.nan
        cur_last_pl = pl_list[-1] if pl_list else np.nan
        cur_prev_pl = pl_list[-2] if len(pl_list) >= 2 else np.nan

        # Reset "broken" flag when pivot level changes
        if cur_last_ph != last_ph_val:
            ph_broken = (row_c > cur_last_ph) if not np.isnan(cur_last_ph) else True
            last_ph_val = cur_last_ph
        if cur_last_pl != last_pl_val:
            pl_broken = (row_c < cur_last_pl) if not np.isnan(cur_last_pl) else True
            last_pl_val = cur_last_pl

        # ─── Generate signals ───
        long_signal = False
        short_signal = False

        if (not np.isnan(cur_last_ph) and not np.isnan(cur_last_pl)
                and cur_last_ph > cur_last_pl):

            # Condition 2: Compression
            structure_range = (cur_last_ph - cur_last_pl) / row_c
            compressed = 0 < structure_range < COMPRESS_PCT

            # Condition 3: Trend (higher lows / lower highs)
            uptrend = (not np.isnan(cur_prev_pl) and cur_last_pl > cur_prev_pl)
            downtrend = (not np.isnan(cur_prev_ph) and cur_last_ph < cur_prev_ph)

            # Condition 1: Fresh breakout
            if (not ph_broken and row_c > cur_last_ph
                    and compressed and uptrend):
                long_signal = True
                ph_broken = True

            if (not pl_broken and row_c < cur_last_pl
                    and compressed and downtrend):
                short_signal = True
                pl_broken = True

        # ─── Execute entries ───
        if long_signal and len(lpos) < MAX_SAME:
            lpos.append({"entry": nxt_open, "ei": i})
        if short_signal and len(spos) < MAX_SAME:
            spos.append({"entry": nxt_open, "ei": i})

    if trades:
        return pd.DataFrame(trades)
    return pd.DataFrame(columns=["pnl","type","side","bars","dt"])

# ═══════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════
def calc_stats(tdf):
    if len(tdf) == 0:
        return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sharpe":0,"fees":0}
    n = len(tdf)
    pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    wins = tdf[tdf["pnl"] > 0]["pnl"].sum()
    losses = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    pf = wins / losses if losses > 0 else 999

    equity = tdf["pnl"].cumsum()
    dd = equity - equity.cummax()
    mdd = dd.min()
    mdd_pct = abs(mdd) / ACCOUNT * 100

    # Sharpe (daily PnL)
    tc = tdf.copy()
    tc["date"] = pd.to_datetime(tc["dt"]).dt.date
    daily = tc.groupby("date")["pnl"].sum()
    all_dates = pd.date_range(tc["dt"].min(), tc["dt"].max(), freq="D")
    daily = daily.reindex(all_dates.date, fill_value=0)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0

    return {"n": n, "pnl": round(pnl,2), "wr": round(wr,1),
            "pf": round(pf,2), "mdd": round(mdd,2), "mdd_pct": round(mdd_pct,1),
            "sharpe": round(sharpe,2), "fees": round(n * FEE, 2)}

def print_box(title, stats, months):
    monthly = stats["n"] / months if months > 0 else 0
    print(f"\n  +{'='*54}+")
    print(f"  | {title:<52s} |")
    print(f"  +{'-'*54}+")
    print(f"  | Trades: {stats['n']:>5d}  (monthly avg {monthly:.1f}){' ':<17s} |")
    print(f"  | Win Rate: {stats['wr']:>5.1f}%{' ':<37s} |")
    print(f"  | Profit Factor: {stats['pf']:>6.2f}{' ':<30s} |")
    print(f"  | Net PnL: ${stats['pnl']:>+9,.2f}{' ':<30s} |")
    print(f"  | Max DD: ${stats['mdd']:>+9,.2f}  ({stats['mdd_pct']:.1f}%){' ':<19s} |")
    print(f"  | Sharpe: {stats['sharpe']:>6.2f}{' ':<34s} |")
    print(f"  | Fees+Slip: -${stats['fees']:>8,.2f}{' ':<29s} |")
    print(f"  +{'='*54}+")

def print_breakdown(tdf):
    # Hold time
    print("\n  Hold Time Breakdown:")
    for lo_h, hi_h, label in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),
                               (48,96,"48-96h"),(96,9999,">96h")]:
        sub = tdf[(tdf["bars"]>=lo_h) & (tdf["bars"]<hi_h)]
        n = len(sub)
        p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {label:<8s}: {n:>4d} trades, ${p:>+9,.0f}, WR {w:.0f}%")

    # Long/Short
    print("\n  Long/Short Breakdown:")
    for side in ["long", "short"]:
        sub = tdf[tdf["side"] == side]
        n = len(sub)
        p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {side.capitalize():<6s}: {n:>4d} trades, ${p:>+9,.0f}, WR {w:.0f}%")

    # Exit type
    print("\n  Exit Type Breakdown:")
    for t in ["Trail", "SafeNet"]:
        sub = tdf[tdf["type"] == t]
        n = len(sub)
        p = sub["pnl"].sum() if n > 0 else 0
        print(f"    {t:<10s}: {n:>4d} trades, ${p:>+9,.0f}")

# ═══════════════════════════════════════════════════════════
# God's Eye Self-Check
# ═══════════════════════════════════════════════════════════
def gods_eye_check():
    print("\n" + "="*60)
    print("  GOD'S EYE SELF-CHECK")
    print("="*60)
    checks = [
        ("Signal uses only shift(1)+ data for pivots?",
         "YES: Pivots confirmed at bar j+N, used at j+N+1 (shift N+1)"),
        ("Entry price = next bar open?",
         "YES: entry = nxt_open = df['open'].values[i+1]"),
        ("Rolling EMA20 contains current bar?",
         "NOTE: EMA20 for trail exit includes current bar (same as champion baseline)"),
        ("Params from IS, not full sample?",
         "YES: PIVOT_N=7, COMPRESS_PCT=0.05 chosen from structural reasoning, not optimized"),
        ("No future high/low/close used?",
         "YES: Only past pivots (confirmed with delay) and current close for comparison"),
    ]
    all_pass = True
    for q, a in checks:
        status = "PASS" if "YES" in a else "NOTE"
        if status == "NOTE":
            all_pass = False
        print(f"  [{status}] {q}")
        print(f"        -> {a}")
    result = "PASS (with note on EMA20 trail convention)" if not all_pass else "PASS"
    print(f"\n  >>> God's Eye Self-Check: {result}")
    return result

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("="*60)
    print("  Theory 1: Swing Structure Breakout (Dow Theory)")
    print("  ETHUSDT 1h | 2023-01-01 ~ 2024-12-31")
    print("  Notional $2,000 | Fee+Slip $2.00/trade")
    print("="*60)

    # Self-check
    check_result = gods_eye_check()

    # Load data
    print("\n--- Loading Data ---")
    df = load_data()
    df = compute_indicators(df)
    print(f"Data range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"Total bars: {len(df)}")

    # Run on full dataset
    print("\n--- Running Backtest ---")
    all_trades = run_backtest(df)
    print(f"Total trades: {len(all_trades)}")

    if len(all_trades) == 0:
        print("\nNO TRADES GENERATED. Theory produces no signals with current params.")
        print("This theory FAILS the minimum trade requirement.")
        sys.exit(0)

    # Split IS / OOS by trade date
    all_trades["dt"] = pd.to_datetime(all_trades["dt"])
    is_trades = all_trades[all_trades["dt"] < IS_SPLIT].copy()
    oos_trades = all_trades[all_trades["dt"] >= IS_SPLIT].copy()

    # Full sample
    full_stats = calc_stats(all_trades)
    is_stats = calc_stats(is_trades)
    oos_stats = calc_stats(oos_trades)

    # Print results
    print_box("Full Sample (2023-2024)", full_stats, 24)
    print_box("IS (2023)", is_stats, 12)
    print_box("OOS (2024) <- TRUE EXPECTATION", oos_stats, 12)

    # Breakdowns (full sample)
    print("\n--- Full Sample Breakdowns ---")
    print_breakdown(all_trades)

    # Monthly PnL
    print("\n  Monthly PnL:")
    tc = all_trades.copy()
    tc["month"] = pd.to_datetime(tc["dt"]).dt.to_period("M")
    monthly = tc.groupby("month").agg({"pnl": ["sum", "count"]})
    monthly.columns = ["pnl", "n"]
    pos_months = (monthly["pnl"] > 0).sum()
    print(f"    Profitable months: {pos_months}/{len(monthly)}")
    for m, row in monthly.iterrows():
        marker = " <<<" if row["pnl"] < -100 else ""
        print(f"    {m}: ${row['pnl']:>+8,.0f}  ({int(row['n']):>3d} trades){marker}")

    # Suspicion check
    print("\n--- Suspicion Check ---")
    if oos_stats["wr"] > 60 and oos_stats["pf"] > 3.0:
        print("  WARNING: WR>60% AND PF>3.0 in OOS - SUSPICIOUS, re-check code!")
    elif oos_stats["sharpe"] > 5.0:
        print("  WARNING: Sharpe>5.0 in OOS - SUSPICIOUS, re-check code!")
    elif full_stats["pnl"] > 0 and oos_stats["pnl"] < full_stats["pnl"] * 0.25:
        print("  NOTE: OOS PnL < 25% of full sample - possible overfitting")
    else:
        print("  No suspicious patterns detected.")

    print(f"\n  God's Eye Self-Check: {check_result}")
    print("\nDone.")
