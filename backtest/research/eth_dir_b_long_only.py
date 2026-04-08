"""
Direction B: Long-Only v6 Variant — ETHUSDT 1h
Hypothesis: v6 Champion long side is stronger (48% WR vs short 38%),
            removing shorts + adding momentum confirmation improves edge

Logic (1h timeframe, same as v6):
  Entry (long only):
    1. MTF ratio: 1h_ATR(14) / 4h_ATR(14) pctile < 40 (compression)
    2. BB breakout: close > BB_upper (long only)
    3. Volume: vol > 1.0x MA20
    4. ETH/BTC ratio Z-score > 1.0 (relative strength)
    5. Session filter: block hours 0,1,2,12 UTC+8 and Mon/Sat/Sun
    Momentum confirmations (tested as variants):
    B2: + EMA20 slope > 0 (price trending up)
    B3: + RSI > 50 (momentum above midline)
    B4: + close > EMA50 (above trend)

  Exit:
    SafeNet -3.5% (25% overshoot slippage)
    EMA20 trail on 1h (min hold 12h)

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

ACCOUNT = 10000
RISK_PCT = 0.02
SAFENET_PCT = 0.035
FEE_RATE = 0.0010
MAX_SAME = 2
MIN_HOLD = 12  # 1h bars

BB_WIN = 20; BB_STD = 2
PCTILE_WIN = 100
MTF_THRESH = 40
RZ_THRESH = 1.0
WORST_HOURS = {0, 1, 2, 12}
WORST_DAYS = {0, 5, 6}

FETCH_START = int(datetime(2022, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
FETCH_END   = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BT_START = datetime(2023, 1, 1)
BT_END   = datetime(2025, 1, 1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

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

def compute_indicators(df_1h, btc_1h, df_4h):
    df = df_1h.copy()

    # ATR on 1h
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": abs(df["high"] - df["close"].shift(1)),
        "lc": abs(df["low"] - df["close"].shift(1))
    }).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # 4h ATR mapped to 1h
    tr_4h = pd.DataFrame({
        "hl": df_4h["high"] - df_4h["low"],
        "hc": abs(df_4h["high"] - df_4h["close"].shift(1)),
        "lc": abs(df_4h["low"] - df_4h["close"].shift(1))
    }).max(axis=1)
    df_4h["atr_4h"] = tr_4h.rolling(14).mean()
    mapped = pd.merge_asof(
        df[["ot"]].sort_values("ot"),
        df_4h[["ot","atr_4h"]].dropna().sort_values("ot"),
        on="ot", direction="backward")
    df["atr_4h"] = mapped["atr_4h"].values

    # MTF ratio
    df["mtf_ratio"] = df["atr"] / df["atr_4h"]
    df["mtf_ratio_pctile"] = df["mtf_ratio"].rolling(PCTILE_WIN).apply(pctile_fn, raw=False)

    # BB
    df["bb_mid"] = df["close"].rolling(BB_WIN).mean()
    bbs = df["close"].rolling(BB_WIN).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * bbs
    df["bb_lower"] = df["bb_mid"] - BB_STD * bbs

    # Volume
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    # EMA20, EMA50
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    # EMA20 slope (5-bar)
    df["ema20_slope"] = df["ema20"] - df["ema20"].shift(5)

    # RSI
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    l_val = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + g / l_val)

    # ETH/BTC ratio z-score
    btc_map = btc_1h.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map)
    df["ratio"] = df["close"] / df["btc_close"]
    r_mean = df["ratio"].rolling(50).mean()
    r_std = df["ratio"].rolling(50).std()
    df["ratio_zscore"] = (df["ratio"] - r_mean) / r_std

    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday

    return df.dropna().reset_index(drop=True)


def run_backtest(data, momentum="none"):
    """
    momentum: "none" | "ema_slope" | "rsi50" | "above_ema50"
    """
    equity = ACCOUNT; peak = ACCOUNT; mdd = 0.0
    lpos = []; trades = []; fees_total = 0.0

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

            sn_price = p["entry"] * (1 - SAFENET_PCT)
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
            elif bars >= MIN_HOLD and close <= row["ema20"]:
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

        if equity > peak: peak = equity
        dd = (peak - equity) / peak * 100
        if dd > mdd: mdd = dd

        # ── ENTRIES (LONG ONLY) ──
        ep = nxt["open"]
        if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END:
            continue

        h = row["hour"]; wd = row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:
            continue

        prev = data.iloc[i - 1]
        if prev["mtf_ratio_pctile"] >= MTF_THRESH:
            continue

        if close <= row["bb_upper"]:
            continue
        if row["vol_ratio"] <= 1.0:
            continue
        if row["ratio_zscore"] <= RZ_THRESH:
            continue

        # Momentum confirmation
        if momentum == "ema_slope" and row["ema20_slope"] <= 0:
            continue
        elif momentum == "rsi50" and row["rsi"] <= 50:
            continue
        elif momentum == "above_ema50" and close <= row["ema50"]:
            continue

        notional = (equity * RISK_PCT) / SAFENET_PCT
        qty = notional / ep

        if len(lpos) < MAX_SAME:
            lpos.append({"entry": ep, "ei": i + 1, "mf": 0,
                "entry_dt": nxt["datetime"], "qty": qty, "notional": notional})

    return pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl","type","side","bars","fee","notional"]), equity, mdd, fees_total


def calc_stats(tdf, final_equity, mdd, fees):
    if len(tdf) < 1:
        return {"n": 0, "pnl": 0, "annual_pnl": 0}
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
    months = 24
    return {
        "n": len(tdf), "pnl": round(pnl, 2), "wr": round(wr, 1),
        "pf": round(pf, 2), "rr": round(rr, 2),
        "mdd_pct": round(mdd, 2), "dd_abs": round(dd_abs, 2),
        "avg_w": round(avg_w, 2), "avg_l": round(avg_l, 2),
        "sn_n": len(sn), "sn_pnl": round(sn["pnl"].sum(), 2),
        "tr_n": len(tr), "tr_pnl": round(tr["pnl"].sum(), 2),
        "fees": round(fees, 2), "bars_mean": round(tdf["bars"].mean(), 1),
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
    print(f"  Avg Hold: {s['bars_mean']} bars ({s['bars_mean']:.0f}h)")
    print(f"  SafeNet: {s['sn_n']} trades (${s['sn_pnl']:+,.2f})")
    print(f"  Trail: {s['tr_n']} trades (${s['tr_pnl']:+,.2f})")
    print(f"  Fees: ${s['fees']:,.2f}")
    print(f"  Final Equity: ${s['final_equity']:,.2f}")


if __name__ == "__main__":
    print("=" * 70)
    print("  Direction B: Long-Only v6 Variant")
    print("=" * 70)

    print("\n[1/3] Fetching data...")
    df_1h = fetch_klines("ETHUSDT", "1h")
    btc_1h = fetch_klines("BTCUSDT", "1h")
    df_4h = fetch_klines("ETHUSDT", "4h")

    print(f"\n[2/3] Computing indicators...")
    data = compute_indicators(df_1h, btc_1h, df_4h)
    bt_data = data[(data["datetime"] >= BT_START - timedelta(days=30)) &
                   (data["datetime"] < BT_END + timedelta(days=1))].reset_index(drop=True)
    print(f"  Backtest range: {len(bt_data)} bars")

    print(f"\n[3/3] Running backtests...")

    variants = [
        ("B1: Long-Only (no momentum)", "none"),
        ("B2: Long-Only + EMA20 Slope > 0", "ema_slope"),
        ("B3: Long-Only + RSI > 50", "rsi50"),
        ("B4: Long-Only + Close > EMA50", "above_ema50"),
    ]

    results = {}
    for label, mom in variants:
        tdf, eq, mdd, fee = run_backtest(bt_data, momentum=mom)
        s = calc_stats(tdf, eq, mdd, fee)
        print_stats(label, s)
        results[label] = (tdf, s)

    # Best variant analysis
    best_label = max(results, key=lambda k: results[k][1]["pnl"])
    best_tdf, best_s = results[best_label]

    if len(best_tdf) > 0:
        # Hold time analysis
        print(f"\n{'='*70}")
        print(f"  Hold Time Analysis ({best_label})")
        print(f"{'='*70}")
        for lo_b, hi_b, label in [(0, 6, "<6h"), (6, 12, "6-12h"), (12, 24, "12-24h"),
                                   (24, 48, "24-48h"), (48, 96, "48-96h"), (96, 999, ">96h")]:
            sub = best_tdf[(best_tdf["bars"] >= lo_b) & (best_tdf["bars"] < hi_b)]
            if len(sub) == 0:
                print(f"  {label:>8s}: 0 trades")
            else:
                wr = (sub["pnl"] > 0).mean() * 100
                print(f"  {label:>8s}: {len(sub):>3d} trades, ${sub['pnl'].sum():>+8,.2f}, WR {wr:.0f}%")

        # Monthly PnL
        print(f"\n{'='*70}")
        print(f"  Monthly PnL ({best_label})")
        print(f"{'='*70}")
        best_tdf["month"] = best_tdf["exit_dt"].dt.to_period("M")
        monthly = best_tdf.groupby("month").agg({"pnl": ["sum", "count"]})
        monthly.columns = ["pnl", "n"]
        for m, row in monthly.iterrows():
            bar = "+" * int(max(0, row["pnl"]) / 50) + "-" * int(max(0, -row["pnl"]) / 50)
            print(f"  {str(m):>8s}: {row['n']:>3.0f} trades  ${row['pnl']:>+8,.2f}  {bar}")

        # IS/OOS
        print(f"\n{'='*70}")
        print(f"  In-Sample vs Out-of-Sample ({best_label})")
        print(f"{'='*70}")
        is_t = best_tdf[best_tdf["entry_dt"].dt.year == 2023]
        oos_t = best_tdf[best_tdf["entry_dt"].dt.year == 2024]
        for lbl, sub in [("IS (2023)", is_t), ("OOS (2024)", oos_t)]:
            if len(sub) == 0:
                print(f"  {lbl}: No trades"); continue
            pnl = sub["pnl"].sum()
            wr = (sub["pnl"] > 0).mean() * 100
            w = sub[sub["pnl"] > 0]; l_val = sub[sub["pnl"] <= 0]
            pf = w["pnl"].sum() / abs(l_val["pnl"].sum()) if len(l_val) > 0 and l_val["pnl"].sum() != 0 else 999
            print(f"  {lbl}: {len(sub)} trades, ${pnl:+,.2f}, WR {wr:.1f}%, PF {pf:.2f}")

    print(f"\n{'='*70}")
    print(f"  Direction B Summary")
    print(f"{'='*70}")
    for lbl in results:
        s = results[lbl][1]
        if s["n"] == 0:
            print(f"  {lbl:<40s}: No trades")
        else:
            print(f"  {lbl:<40s}: {s['n']:>4d} trades  ${s['annual_pnl']:>+8,.2f}/yr  "
                  f"PF {s['pf']:>5.2f}  WR {s['wr']:>5.1f}%  DD {s['mdd_pct']:>5.1f}%  "
                  f"SN {s['sn_n']:>3d}")
