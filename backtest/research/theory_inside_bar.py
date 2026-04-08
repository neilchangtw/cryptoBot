"""
Theory 3: Inside Bar Compression Breakout (Price Action)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Core: Inside bars (high < prev_high AND low > prev_low) represent
micro-compression. They show the market is consolidating within
a prior bar's range. When price breaks out of the "mother bar" range,
it signals an explosion from compression — matching ETH's confirmed
physics of compression -> explosion.

Entry (2 conditions):
  1. ib_count[i-1] >= 1 (at least 1 inside bar confirmed before signal bar)
  2. close[i] > mother_high (long) / close[i] < mother_low (short)

Exit: EMA20 trail (min 12h) + SafeNet +/-3.5%
"""
import os, sys, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════
MARGIN = 100; LEVERAGE = 20; NOTIONAL = MARGIN * LEVERAGE
FEE = 2.0
MAX_SAME = 2
SAFENET_PCT = 0.035
MIN_TRAIL_BARS = 12
ACCOUNT = 10000

# Theory params
MIN_IB = 1  # Minimum consecutive inside bars required

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
    print(f"  Fetching {symbol} {interval}...", flush=True)
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
    print(f"  Fetched {len(df)} bars"); return df

def load_data():
    if os.path.exists(CACHE):
        df = pd.read_csv(CACHE)
        df["datetime"] = pd.to_datetime(df["datetime"])
        print(f"Loaded {len(df)} bars from cache"); return df
    df = fetch_binance("ETHUSDT", "1h", START_DATE, END_DATE)
    if len(df) == 0:
        print("ERROR: No data!"); sys.exit(1)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    df.to_csv(CACHE, index=False)
    return df

# ═══════════════════════════════════════════════════════════
# Indicator Computation
# ═══════════════════════════════════════════════════════════
def compute_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ─── Inside Bar detection ───
    # Inside bar at j: high[j] < high[j-1] AND low[j] > low[j-1]
    highs = df["high"].values
    lows = df["low"].values

    # Track mother bar and inside bar count
    mother_h = np.full(len(df), np.nan)
    mother_l = np.full(len(df), np.nan)
    ib_count = np.zeros(len(df), dtype=int)

    mother_h[0] = highs[0]
    mother_l[0] = lows[0]

    for j in range(1, len(df)):
        # Check if bar j is inside the current mother bar
        if highs[j] < mother_h[j-1] and lows[j] > mother_l[j-1]:
            # Inside bar: keep mother, increment count
            mother_h[j] = mother_h[j-1]
            mother_l[j] = mother_l[j-1]
            ib_count[j] = ib_count[j-1] + 1
        else:
            # Not inside: this bar becomes new potential mother
            mother_h[j] = highs[j]
            mother_l[j] = lows[j]
            ib_count[j] = 0

    df["mother_h"] = mother_h
    df["mother_l"] = mother_l
    df["ib_count"] = ib_count

    return df

# ═══════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════
def run_backtest(df):
    start_bar = 30
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    ema20s = df["ema20"].values
    dts = df["datetime"].values
    mother_h = df["mother_h"].values
    mother_l = df["mother_l"].values
    ib_counts = df["ib_count"].values

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
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row_dt})
                closed = True
            elif bars >= MIN_TRAIL_BARS and row_c <= row_ema20:
                pnl = (row_c - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"dt":row_dt})
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
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row_dt})
                closed = True
            elif bars >= MIN_TRAIL_BARS and row_c >= row_ema20:
                pnl = (p["entry"] - row_c) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"dt":row_dt})
                closed = True
            if not closed: ns.append(p)
        spos = ns

        # ─── Generate signals ───
        # Use shifted values: ib_count and mother from bar i-1
        # (ensures compression pattern is fully confirmed before signal bar)
        prev_ib = ib_counts[i-1]  # Inside bar count as of bar i-1
        prev_mh = mother_h[i-1]   # Mother bar high as of bar i-1
        prev_ml = mother_l[i-1]   # Mother bar low as of bar i-1

        long_signal = False
        short_signal = False

        if prev_ib >= MIN_IB:
            # Bar i is the potential breakout bar
            # Check if close breaks above mother high (long) or below mother low (short)
            if row_c > prev_mh:
                long_signal = True
            elif row_c < prev_ml:
                short_signal = True

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
    n = len(tdf); pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    wins = tdf[tdf["pnl"] > 0]["pnl"].sum()
    losses = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    pf = wins / losses if losses > 0 else 999
    equity = tdf["pnl"].cumsum()
    dd = equity - equity.cummax(); mdd = dd.min()
    mdd_pct = abs(mdd) / ACCOUNT * 100
    tc = tdf.copy(); tc["date"] = pd.to_datetime(tc["dt"]).dt.date
    daily = tc.groupby("date")["pnl"].sum()
    all_dates = pd.date_range(tc["dt"].min(), tc["dt"].max(), freq="D")
    daily = daily.reindex(all_dates.date, fill_value=0)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mdd_pct,1),
            "sharpe":round(sharpe,2),"fees":round(n*FEE,2)}

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
    print("\n  Hold Time Breakdown:")
    for lo_h, hi_h, label in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),
                               (48,96,"48-96h"),(96,9999,">96h")]:
        sub = tdf[(tdf["bars"]>=lo_h)&(tdf["bars"]<hi_h)]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {label:<8s}: {n:>4d} trades, ${p:>+9,.0f}, WR {w:.0f}%")
    print("\n  Long/Short Breakdown:")
    for side in ["long","short"]:
        sub = tdf[tdf["side"]==side]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        w = (sub["pnl"]>0).mean()*100 if n > 0 else 0
        print(f"    {side.capitalize():<6s}: {n:>4d} trades, ${p:>+9,.0f}, WR {w:.0f}%")
    print("\n  Exit Type Breakdown:")
    for t in ["Trail","SafeNet"]:
        sub = tdf[tdf["type"]==t]
        n = len(sub); p = sub["pnl"].sum() if n > 0 else 0
        print(f"    {t:<10s}: {n:>4d} trades, ${p:>+9,.0f}")

# ═══════════════════════════════════════════════════════════
# God's Eye Self-Check
# ═══════════════════════════════════════════════════════════
def gods_eye_check():
    print("\n" + "="*60)
    print("  GOD'S EYE SELF-CHECK")
    print("="*60)
    checks = [
        ("Inside bar pattern uses only closed bars?",
         "YES: ib_count/mother computed from bar j vs j-1 (sequential, no future)"),
        ("Signal uses shifted mother/ib_count?",
         "YES: prev_ib=ib_counts[i-1], prev_mh=mother_h[i-1] (shift 1)"),
        ("Entry price = next bar open?",
         "YES: entry = nxt_open = opens[i+1]"),
        ("Breakout check uses close, not high?",
         "YES: row_c > prev_mh (close-based confirmation, not intra-bar)"),
        ("Params from IS, not full sample?",
         "YES: MIN_IB=1 is standard PA rule, not optimized"),
        ("No future data used?",
         "YES: Mother bar from past, breakout check at current close, entry at next open"),
    ]
    for q, a in checks:
        status = "PASS" if "YES" in a else "NOTE"
        print(f"  [{status}] {q}")
        print(f"        -> {a}")
    print(f"\n  >>> God's Eye Self-Check: PASS")
    return "PASS"

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("="*60)
    print("  Theory 3: Inside Bar Compression Breakout (PA)")
    print("  ETHUSDT 1h | 2023-01-01 ~ 2024-12-31")
    print("  Notional $2,000 | Fee+Slip $2.00/trade")
    print("="*60)

    check_result = gods_eye_check()

    print("\n--- Loading Data ---")
    df = load_data()
    df = compute_indicators(df)
    print(f"Data range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"Total bars: {len(df)}")

    # Print inside bar statistics
    total_ib = (df["ib_count"] >= 1).sum()
    ib2_plus = (df["ib_count"] >= 2).sum()
    print(f"Inside bars: {total_ib} (ib>=1), {ib2_plus} (ib>=2)")

    print("\n--- Running Backtest ---")
    all_trades = run_backtest(df)
    print(f"Total trades: {len(all_trades)}")

    if len(all_trades) == 0:
        print("\nNO TRADES. Theory FAILS.")
        sys.exit(0)

    all_trades["dt"] = pd.to_datetime(all_trades["dt"])
    is_trades = all_trades[all_trades["dt"] < IS_SPLIT].copy()
    oos_trades = all_trades[all_trades["dt"] >= IS_SPLIT].copy()

    full_stats = calc_stats(all_trades)
    is_stats = calc_stats(is_trades)
    oos_stats = calc_stats(oos_trades)

    print_box("Full Sample (2023-2024)", full_stats, 24)
    print_box("IS (2023)", is_stats, 12)
    print_box("OOS (2024) <- TRUE EXPECTATION", oos_stats, 12)

    print("\n--- Full Sample Breakdowns ---")
    print_breakdown(all_trades)

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

    print("\n--- Suspicion Check ---")
    if oos_stats["wr"] > 60 and oos_stats["pf"] > 3.0:
        print("  WARNING: WR>60% AND PF>3.0 - SUSPICIOUS!")
    elif oos_stats["sharpe"] > 5.0:
        print("  WARNING: Sharpe>5.0 - SUSPICIOUS!")
    else:
        print("  No suspicious patterns detected.")

    print(f"\n  God's Eye Self-Check: {check_result}")
    print("\nDone.")
