"""
Direction A: 4h Compression Breakout — ETHUSDT
Hypothesis: v6 Champion logic transplanted to 4h timeframe captures larger swings

Logic:
  Entry (all must be true, check on 4h bar close):
    1. MTF ratio: 4h_ATR(14) / Daily_ATR(14) rolling pctile < 40
    2. BB breakout: close > BB_upper (long) or close < BB_lower (short) on 4h
    3. Volume: vol > 1.0x MA20
    4. ETH/BTC ratio Z-score > 1.0 (long) or < -1.0 (short)
    5. Session filter variant: block hours 0,1,2,12 UTC+8 and Mon/Sat/Sun

  Exit:
    SafeNet +/-4% (wider for 4h, 25% overshoot slippage)
    EMA20 trail on 4h (min hold 2 bars = 8h)

  Risk: $10K account, 2%/trade, dynamic sizing
  Cost: 0.10% round trip
  Period: 2023-01-01 ~ 2024-12-31
"""
import os, sys, requests, warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import time as _time

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
ACCOUNT = 10000
RISK_PCT = 0.02
SAFENET_PCT = 0.04       # wider for 4h
FEE_RATE = 0.0010        # 0.10% round trip
MAX_SAME = 2
MIN_HOLD = 2             # 2 bars on 4h = 8h

BB_WIN = 20; BB_STD = 2
PCTILE_WIN = 100
MTF_THRESH = 40
RZ_THRESH = 1.0
WORST_HOURS = {0, 1, 2, 12}  # UTC+8
WORST_DAYS = {0, 5, 6}       # Mon=0, Sat=5, Sun=6

FETCH_START = int(datetime(2022, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
FETCH_END   = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BT_START = datetime(2023, 1, 1)
BT_END   = datetime(2025, 1, 1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# DATA FETCHING
# ══════════════════════════════════════════════════════════════
def fetch_klines(symbol, interval):
    tag = f"{symbol}_{interval}_{FETCH_START}_{FETCH_END}"
    cache = os.path.join(DATA_DIR, f"{tag}.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        df["datetime"] = pd.to_datetime(df["datetime"])
        print(f"  [Cache] {len(df)} bars: {symbol} {interval}")
        return df

    print(f"  Fetching {symbol} {interval}...", end=" ", flush=True)
    rows = []
    cur = FETCH_START
    while cur < FETCH_END:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "limit": 1000}, timeout=10)
            d = r.json()
            if not d: break
            rows.extend(d)
            cur = d[-1][0] + 1
            if len(d) < 1000: break
            _time.sleep(0.1)
        except Exception as e:
            print(f"\n  Error: {e}"); break

    if not rows:
        print("FAILED"); return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume","qv","tbv"]:
        df[c] = pd.to_numeric(df[c])
    df["trades"] = pd.to_numeric(df["trades"])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df.to_csv(cache, index=False)
    print(f"{len(df)} bars")
    return df

def pctile_fn(x):
    if x.max() != x.min():
        return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    return 50

# ══════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════
def compute_indicators(df_4h, btc_4h, df_1d):
    df = df_4h.copy()

    # ATR on 4h
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": abs(df["high"] - df["close"].shift(1)),
        "lc": abs(df["low"] - df["close"].shift(1))
    }).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # Daily ATR mapped to 4h
    tr_d = pd.DataFrame({
        "hl": df_1d["high"] - df_1d["low"],
        "hc": abs(df_1d["high"] - df_1d["close"].shift(1)),
        "lc": abs(df_1d["low"] - df_1d["close"].shift(1))
    }).max(axis=1)
    df_1d["atr_d"] = tr_d.rolling(14).mean()
    mapped = pd.merge_asof(
        df[["ot"]].sort_values("ot"),
        df_1d[["ot","atr_d"]].dropna().sort_values("ot"),
        on="ot", direction="backward")
    df["atr_d"] = mapped["atr_d"].values

    # MTF ratio: 4h / daily
    df["mtf_ratio"] = df["atr"] / df["atr_d"]
    df["mtf_ratio_pctile"] = df["mtf_ratio"].rolling(PCTILE_WIN).apply(pctile_fn, raw=False)

    # BB on 4h
    df["bb_mid"] = df["close"].rolling(BB_WIN).mean()
    bbs = df["close"].rolling(BB_WIN).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * bbs
    df["bb_lower"] = df["bb_mid"] - BB_STD * bbs

    # Volume ratio
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    # EMA20 for trail
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ETH/BTC ratio z-score
    btc_map = btc_4h.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map)
    df["ratio"] = df["close"] / df["btc_close"]
    r_mean = df["ratio"].rolling(50).mean()
    r_std = df["ratio"].rolling(50).std()
    df["ratio_zscore"] = (df["ratio"] - r_mean) / r_std

    # Time fields (entry bar's start hour in UTC+8)
    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday

    return df.dropna().reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════
def run_backtest(data, use_session=True, safenet_pct=SAFENET_PCT,
                 mtf_t=MTF_THRESH, rz_t=RZ_THRESH, min_h=MIN_HOLD):
    equity = ACCOUNT
    peak = ACCOUNT
    mdd = 0.0
    lpos = []; spos = []; trades = []; fees_total = 0.0

    for i in range(1, len(data) - 1):
        row = data.iloc[i]
        nxt = data.iloc[i + 1]
        close = row["close"]; hi = row["high"]; lo = row["low"]

        if row["datetime"] < BT_START or row["datetime"] >= BT_END:
            continue

        # ── EXITS ──
        new_lpos = []
        for p in lpos:
            bars = i - p["ei"]
            p["mf"] = max(p.get("mf", 0), (hi - p["entry"]) * p["qty"])
            closed = False

            sn_price = p["entry"] * (1 - safenet_pct)
            if lo <= sn_price:
                overshoot = sn_price - lo
                exit_p = sn_price - overshoot * 0.25
                pnl = (exit_p - p["entry"]) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "long", "type": "SafeNet", "entry": p["entry"],
                    "exit": exit_p, "pnl": pnl, "fee": fee, "bars": bars,
                    "notional": p["notional"]})
                equity += pnl; closed = True
            elif bars >= min_h and close <= row["ema20"]:
                pnl = (close - p["entry"]) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "long", "type": "Trail", "entry": p["entry"],
                    "exit": close, "pnl": pnl, "fee": fee, "bars": bars,
                    "notional": p["notional"]})
                equity += pnl; closed = True

            if not closed: new_lpos.append(p)
        lpos = new_lpos

        new_spos = []
        for p in spos:
            bars = i - p["ei"]
            p["mf"] = max(p.get("mf", 0), (p["entry"] - lo) * p["qty"])
            closed = False

            sn_price = p["entry"] * (1 + safenet_pct)
            if hi >= sn_price:
                overshoot = hi - sn_price
                exit_p = sn_price + overshoot * 0.25
                pnl = (p["entry"] - exit_p) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "short", "type": "SafeNet", "entry": p["entry"],
                    "exit": exit_p, "pnl": pnl, "fee": fee, "bars": bars,
                    "notional": p["notional"]})
                equity += pnl; closed = True
            elif bars >= min_h and close >= row["ema20"]:
                pnl = (p["entry"] - close) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "short", "type": "Trail", "entry": p["entry"],
                    "exit": close, "pnl": pnl, "fee": fee, "bars": bars,
                    "notional": p["notional"]})
                equity += pnl; closed = True

            if not closed: new_spos.append(p)
        spos = new_spos

        # Track drawdown
        if equity > peak: peak = equity
        dd = (peak - equity) / peak * 100
        if dd > mdd: mdd = dd

        # ── ENTRIES ──
        ep = nxt["open"]
        if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END:
            continue

        # Session filter
        if use_session:
            h = row["hour"]; wd = row["weekday"]
            if h in WORST_HOURS or wd in WORST_DAYS:
                continue

        # Compression filter (previous bar)
        prev = data.iloc[i - 1]
        if prev["mtf_ratio_pctile"] >= mtf_t:
            continue

        # BB breakout
        bb_long = close > row["bb_upper"]
        bb_short = close < row["bb_lower"]
        if not bb_long and not bb_short:
            continue

        # Volume
        if row["vol_ratio"] <= 1.0:
            continue

        # ETH/BTC direction
        rzv = row["ratio_zscore"]
        l_sig = bb_long and rzv > rz_t
        s_sig = bb_short and rzv < -rz_t

        # Position sizing
        notional = (equity * RISK_PCT) / safenet_pct
        qty = notional / ep

        if l_sig and len(lpos) < MAX_SAME:
            lpos.append({"entry": ep, "ei": i + 1, "mf": 0,
                "entry_dt": nxt["datetime"], "qty": qty, "notional": notional})
        if s_sig and len(spos) < MAX_SAME:
            spos.append({"entry": ep, "ei": i + 1, "mf": 0,
                "entry_dt": nxt["datetime"], "qty": qty, "notional": notional})

    return pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl","type","side","bars","fee","notional"]), equity, mdd, fees_total


# ══════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════
def calc_stats(tdf, final_equity, mdd, fees):
    if len(tdf) < 1:
        return {"n": 0, "pnl": 0}
    pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999
    avg_w = w["pnl"].mean() if len(w) > 0 else 0
    avg_l = l["pnl"].mean() if len(l) > 0 else 0
    rr = abs(avg_w / avg_l) if avg_l != 0 else 999
    cum = tdf["pnl"].cumsum()
    dd_abs = (cum - cum.cummax()).min()
    sn = tdf[tdf["type"] == "SafeNet"]
    tr = tdf[tdf["type"] == "Trail"]
    bars_mean = tdf["bars"].mean()
    months = 24
    return {
        "n": len(tdf), "pnl": round(pnl, 2), "wr": round(wr, 1),
        "pf": round(pf, 2), "rr": round(rr, 2),
        "mdd_pct": round(mdd, 2), "dd_abs": round(dd_abs, 2),
        "avg_w": round(avg_w, 2), "avg_l": round(avg_l, 2),
        "sn_n": len(sn), "sn_pnl": round(sn["pnl"].sum(), 2),
        "tr_n": len(tr), "tr_pnl": round(tr["pnl"].sum(), 2),
        "fees": round(fees, 2), "bars_mean": round(bars_mean, 1),
        "trades_per_month": round(len(tdf) / months, 1),
        "annual_pnl": round(pnl / 2, 2),
        "final_equity": round(final_equity, 2),
    }

def print_stats(label, s):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    if s["n"] == 0:
        print("  No trades"); return
    print(f"  Trades: {s['n']} ({s['trades_per_month']}/month)")
    print(f"  Total PnL: ${s['pnl']:+,.2f} (annual: ${s['annual_pnl']:+,.2f})")
    print(f"  WR: {s['wr']}%  PF: {s['pf']}  RR: {s['rr']}:1")
    print(f"  Avg Win: ${s['avg_w']:+,.2f}  Avg Loss: ${s['avg_l']:+,.2f}")
    print(f"  Max DD: {s['mdd_pct']:.1f}% (${s['dd_abs']:,.2f})")
    print(f"  Avg Hold: {s['bars_mean']} bars")
    print(f"  SafeNet: {s['sn_n']} trades (${s['sn_pnl']:+,.2f})")
    print(f"  Trail: {s['tr_n']} trades (${s['tr_pnl']:+,.2f})")
    print(f"  Fees: ${s['fees']:,.2f}")
    print(f"  Final Equity: ${s['final_equity']:,.2f}")
    # Side breakdown
    return s

def print_side_breakdown(tdf):
    if len(tdf) < 1: return
    for side in ["long", "short"]:
        st = tdf[tdf["side"] == side]
        if len(st) == 0: continue
        pnl = st["pnl"].sum(); wr = (st["pnl"] > 0).mean() * 100
        print(f"  {side.upper()}: {len(st)} trades, ${pnl:+,.2f}, WR {wr:.1f}%")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 70)
    print("  Direction A: 4h Compression Breakout (v6 -> 4h)")
    print("=" * 70)

    print("\n[1/3] Fetching data...")
    df_4h = fetch_klines("ETHUSDT", "4h")
    btc_4h = fetch_klines("BTCUSDT", "4h")
    df_1d = fetch_klines("ETHUSDT", "1d")

    print(f"\n[2/3] Computing indicators...")
    data = compute_indicators(df_4h, btc_4h, df_1d)
    bt_data = data[(data["datetime"] >= BT_START - timedelta(days=30)) &
                   (data["datetime"] < BT_END + timedelta(days=1))].reset_index(drop=True)
    print(f"  Backtest range: {len(bt_data)} bars")

    print(f"\n[3/3] Running backtests...")

    # ── Variant 1: With session filter ──
    tdf1, eq1, mdd1, fee1 = run_backtest(bt_data, use_session=True)
    s1 = calc_stats(tdf1, eq1, mdd1, fee1)
    print_stats("A1: 4h Compression + Session Filter", s1)
    print_side_breakdown(tdf1)

    # ── Variant 2: Without session filter ──
    tdf2, eq2, mdd2, fee2 = run_backtest(bt_data, use_session=False)
    s2 = calc_stats(tdf2, eq2, mdd2, fee2)
    print_stats("A2: 4h Compression (No Session Filter)", s2)
    print_side_breakdown(tdf2)

    # ── Variant 3: Wider SafeNet 5% ──
    tdf3, eq3, mdd3, fee3 = run_backtest(bt_data, use_session=True, safenet_pct=0.05)
    s3 = calc_stats(tdf3, eq3, mdd3, fee3)
    print_stats("A3: 4h Compression + SafeNet 5%", s3)
    print_side_breakdown(tdf3)

    # ── Variant 4: Min hold 3 bars (12h) ──
    tdf4, eq4, mdd4, fee4 = run_backtest(bt_data, use_session=True, min_h=3)
    s4 = calc_stats(tdf4, eq4, mdd4, fee4)
    print_stats("A4: 4h Compression + Min Hold 3 bars (12h)", s4)
    print_side_breakdown(tdf4)

    # ── Hold time analysis for best variant ──
    best_label = "A1"
    best_tdf = tdf1
    best_s = s1
    for lbl, t, s in [("A2", tdf2, s2), ("A3", tdf3, s3), ("A4", tdf4, s4)]:
        if s["pnl"] > best_s["pnl"]:
            best_label = lbl; best_tdf = t; best_s = s

    if len(best_tdf) > 0:
        print(f"\n{'='*70}")
        print(f"  Hold Time Analysis ({best_label})")
        print(f"{'='*70}")
        for lo_b, hi_b, label in [(0, 2, "<8h"), (2, 6, "8-24h"), (6, 12, "24-48h"),
                                   (12, 24, "48-96h"), (24, 999, ">96h")]:
            sub = best_tdf[(best_tdf["bars"] >= lo_b) & (best_tdf["bars"] < hi_b)]
            if len(sub) == 0:
                print(f"  {label:>8s}: 0 trades")
            else:
                wr = (sub["pnl"] > 0).mean() * 100
                print(f"  {label:>8s}: {len(sub):>3d} trades, ${sub['pnl'].sum():>+8,.2f}, WR {wr:.0f}%")

    # ── Monthly breakdown ──
    if len(best_tdf) > 0:
        print(f"\n{'='*70}")
        print(f"  Monthly PnL ({best_label})")
        print(f"{'='*70}")
        best_tdf["month"] = best_tdf["exit_dt"].dt.to_period("M")
        monthly = best_tdf.groupby("month").agg({"pnl": ["sum", "count"]})
        monthly.columns = ["pnl", "n"]
        for m, row in monthly.iterrows():
            bar = "+" * int(max(0, row["pnl"]) / 50) + "-" * int(max(0, -row["pnl"]) / 50)
            print(f"  {str(m):>8s}: {row['n']:>3.0f} trades  ${row['pnl']:>+8,.2f}  {bar}")

    # ── OOS split: IS=2023, OOS=2024 ──
    if len(best_tdf) > 0:
        print(f"\n{'='*70}")
        print(f"  In-Sample vs Out-of-Sample ({best_label})")
        print(f"{'='*70}")
        is_trades = best_tdf[best_tdf["entry_dt"].dt.year == 2023]
        oos_trades = best_tdf[best_tdf["entry_dt"].dt.year == 2024]
        for lbl, sub in [("IS (2023)", is_trades), ("OOS (2024)", oos_trades)]:
            if len(sub) == 0:
                print(f"  {lbl}: No trades"); continue
            pnl = sub["pnl"].sum()
            wr = (sub["pnl"] > 0).mean() * 100
            w = sub[sub["pnl"] > 0]; l = sub[sub["pnl"] <= 0]
            pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999
            print(f"  {lbl}: {len(sub)} trades, ${pnl:+,.2f}, WR {wr:.1f}%, PF {pf:.2f}")

    print(f"\n{'='*70}")
    print(f"  Direction A Summary")
    print(f"{'='*70}")
    for lbl, s in [("A1 Session", s1), ("A2 No Session", s2),
                    ("A3 SafeNet5%", s3), ("A4 MinHold12h", s4)]:
        if s["n"] == 0:
            print(f"  {lbl:<20s}: No trades")
        else:
            print(f"  {lbl:<20s}: {s['n']:>4d} trades  ${s['annual_pnl']:>+8,.2f}/yr  "
                  f"PF {s['pf']:>5.2f}  WR {s['wr']:>5.1f}%  DD {s['mdd_pct']:>5.1f}%  "
                  f"SN {s['sn_n']:>3d}")
