"""
v6 Champion Loss Analysis + Surgical Improvement
=================================================
Step 1: Reproduce v6 with full trade details (MAE/MFE/indicators)
Step 2: Top 20 losers + characteristic comparison
Step 3: Exclusion filter design + backtest
Step 4: MAE/MFE distribution analysis
Step 5: Honest verdict
"""
import os, requests, warnings
import pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
import time as _time

warnings.filterwarnings("ignore")

# ====================================================================
# CONFIG — exact v6 Champion spec (fixed sizing)
# ====================================================================
MARGIN = 100; LEVERAGE = 20  # notional = $2,000
MAX_SAME = 2
FEE = 2.0                    # 0.10% of $2,000 (taker 0.04%x2 + slip 0.01%x2)
SAFENET_PCT = 0.035
MIN_HOLD = 12                # bars before EMA20 trail activates

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

# ====================================================================
# DATA
# ====================================================================
def fetch_klines(symbol, interval):
    tag = f"{symbol}_{interval}_{FETCH_START}_{FETCH_END}"
    cache = os.path.join(DATA_DIR, f"{tag}.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache); df["datetime"] = pd.to_datetime(df["datetime"])
        print(f"  [Cache] {len(df)} bars: {symbol} {interval}"); return df
    print(f"  Fetching {symbol} {interval}...", end=" ", flush=True)
    rows = []; cur = FETCH_START
    while cur < FETCH_END:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "limit": 1000}, timeout=10)
            d = r.json()
            if not d: break
            rows.extend(d); cur = d[-1][0] + 1
            if len(d) < 1000: break
            _time.sleep(0.1)
        except: break
    if not rows: print("FAILED"); return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume","qv","tbv"]:
        df[c] = pd.to_numeric(df[c])
    df["trades"] = pd.to_numeric(df["trades"])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df.to_csv(cache, index=False); print(f"{len(df)} bars"); return df

def pctile_fn(x):
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50

def compute_indicators(df_1h, btc_1h, df_4h):
    df = df_1h.copy()
    # ATR 1h
    tr = pd.DataFrame({"hl":df["high"]-df["low"],
        "hc":abs(df["high"]-df["close"].shift(1)),
        "lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    # ATR 4h -> map
    tr4 = pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],
        "hc":abs(df_4h["high"]-df_4h["close"].shift(1)),
        "lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
    df_4h["atr_4h"] = tr4.rolling(14).mean()
    mapped = pd.merge_asof(df[["ot"]].sort_values("ot"),
        df_4h[["ot","atr_4h"]].dropna().sort_values("ot"), on="ot", direction="backward")
    df["atr_4h"] = mapped["atr_4h"].values
    # MTF ratio
    df["mtf_ratio"] = df["atr"] / df["atr_4h"]
    df["mtf_ratio_pctile"] = df["mtf_ratio"].rolling(100).apply(pctile_fn, raw=False)
    # BB(20,2)
    df["bb_mid"] = df["close"].rolling(20).mean()
    bbs = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2*bbs; df["bb_lower"] = df["bb_mid"] - 2*bbs
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"] * 100
    df["bb_width_pctile"] = df["bb_width"].rolling(100).apply(pctile_fn, raw=False)
    # Vol
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    # EMA
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    # RSI
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    df["rsi"] = 100 - 100/(1+g/l)
    # ETH/BTC ratio Z-score (rolling 50, matching original champion code)
    btc_map = btc_1h.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map)
    df["ratio"] = df["close"] / df["btc_close"]
    rm = df["ratio"].rolling(50).mean(); rs = df["ratio"].rolling(50).std()
    df["ratio_zscore"] = (df["ratio"] - rm) / rs
    # Time
    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday
    return df.dropna().reset_index(drop=True)

# ====================================================================
# BACKTEST ENGINE — records full trade details
# ====================================================================
def run_backtest(data, extra_filter=None):
    """
    Run v6 Champion backtest. extra_filter is a function(row, prev) -> bool.
    If extra_filter returns True, trade is SKIPPED.
    Returns trades list with full details.
    """
    lpos = []; spos = []; trades = []
    NOT = MARGIN * LEVERAGE  # $2,000

    for i in range(1, len(data) - 1):
        row = data.iloc[i]; nxt = data.iloc[i+1]; prev = data.iloc[i-1]
        hi = row["high"]; lo = row["low"]; close = row["close"]

        if row["datetime"] < BT_START or row["datetime"] >= BT_END:
            continue

        # ── EXITS ──
        nl = []
        for p in lpos:
            bars = i - p["ei"]; done = False
            # Track MAE/MFE
            p["mae"] = min(p.get("mae", 0), (lo - p["entry"]) / p["entry"] * 100)
            p["mfe"] = max(p.get("mfe", 0), (hi - p["entry"]) / p["entry"] * 100)

            sn_price = p["entry"] * (1 - SAFENET_PCT)
            if lo <= sn_price:
                ov = sn_price - lo
                ep = sn_price - ov * 0.25
                pnl = (ep - p["entry"]) * NOT / p["entry"] - FEE
                trades.append({**p["info"], "exit_time": row["datetime"],
                    "exit_price": ep, "pnl": pnl, "exit_type": "SafeNet",
                    "hold_hours": bars, "mae_pct": p["mae"], "mfe_pct": p["mfe"]})
                done = True
            elif bars >= MIN_HOLD and close <= row["ema20"]:
                pnl = (close - p["entry"]) * NOT / p["entry"] - FEE
                trades.append({**p["info"], "exit_time": row["datetime"],
                    "exit_price": close, "pnl": pnl, "exit_type": "EMA20_trail",
                    "hold_hours": bars, "mae_pct": p["mae"], "mfe_pct": p["mfe"]})
                done = True
            if not done: nl.append(p)
        lpos = nl

        ns = []
        for p in spos:
            bars = i - p["ei"]; done = False
            p["mae"] = min(p.get("mae", 0), (p["entry"] - hi) / p["entry"] * 100)
            p["mfe"] = max(p.get("mfe", 0), (p["entry"] - lo) / p["entry"] * 100)

            sn_price = p["entry"] * (1 + SAFENET_PCT)
            if hi >= sn_price:
                ov = hi - sn_price
                ep = sn_price + ov * 0.25
                pnl = (p["entry"] - ep) * NOT / p["entry"] - FEE
                trades.append({**p["info"], "exit_time": row["datetime"],
                    "exit_price": ep, "pnl": pnl, "exit_type": "SafeNet",
                    "hold_hours": bars, "mae_pct": p["mae"], "mfe_pct": p["mfe"]})
                done = True
            elif bars >= MIN_HOLD and close >= row["ema20"]:
                pnl = (p["entry"] - close) * NOT / p["entry"] - FEE
                trades.append({**p["info"], "exit_time": row["datetime"],
                    "exit_price": close, "pnl": pnl, "exit_type": "EMA20_trail",
                    "hold_hours": bars, "mae_pct": p["mae"], "mfe_pct": p["mfe"]})
                done = True
            if not done: ns.append(p)
        spos = ns

        # ── ENTRY ──
        ep = nxt["open"]
        if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END: continue

        h = row["hour"]; wd = row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS: continue
        if prev["mtf_ratio_pctile"] >= MTF_THRESH: continue

        bb_long = close > row["bb_upper"]
        bb_short = close < row["bb_lower"]
        if not bb_long and not bb_short: continue
        if row["vol_ratio"] <= 1.0: continue

        rzv = row["ratio_zscore"]
        l_sig = bb_long and rzv > RZ_THRESH
        s_sig = bb_short and rzv < -RZ_THRESH
        if not l_sig and not s_sig: continue

        # Extra exclusion filter
        if extra_filter and extra_filter(row, prev): continue

        direction = "long" if l_sig else "short"
        info = {
            "entry_time": nxt["datetime"], "entry_price": ep,
            "direction": direction,
            "mtf_pctile": round(prev["mtf_ratio_pctile"], 1),
            "ratio_z": round(rzv, 2),
            "entry_hour": h, "entry_weekday": wd,
            "vol_ratio": round(row["vol_ratio"], 2),
            "bb_width_pctile": round(row["bb_width_pctile"], 1),
            "atr": round(row["atr"], 2),
            "atr_pct": round(row["atr"] / close * 100, 3),
            "rsi": round(row["rsi"], 1),
        }

        if l_sig and len(lpos) < MAX_SAME:
            lpos.append({"entry": ep, "ei": i+1, "mae": 0, "mfe": 0, "info": info})
        elif s_sig and len(spos) < MAX_SAME:
            spos.append({"entry": ep, "ei": i+1, "mae": 0, "mfe": 0, "info": info})

    return pd.DataFrame(trades)


# ====================================================================
# MAIN
# ====================================================================
if __name__ == "__main__":
    SEP = "=" * 75
    print(SEP)
    print("  v6 Champion Loss Analysis + Surgical Improvement")
    print(SEP)

    # ── Fetch ──
    print("\n[DATA]")
    df_1h = fetch_klines("ETHUSDT", "1h")
    btc_1h = fetch_klines("BTCUSDT", "1h")
    df_4h = fetch_klines("ETHUSDT", "4h")

    print("Computing indicators (ratio Z rolling=50)...")
    data = compute_indicators(df_1h, btc_1h, df_4h)
    print(f"  Total bars: {len(data)}")

    # ================================================================
    # STEP 1: Reproduce v6 baseline
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 1: v6 Champion Baseline")
    print(SEP)

    tdf = run_backtest(data)
    n = len(tdf); pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999
    sn = tdf[tdf["exit_type"] == "SafeNet"]
    tr = tdf[tdf["exit_type"] == "EMA20_trail"]

    print(f"\n  2-year: {n} trades ({n/2:.0f}/yr)")
    print(f"  PnL: ${pnl:+,.2f} (${pnl/2:+,.2f}/yr)")
    print(f"  WR: {wr:.1f}%  PF: {pf:.2f}")
    print(f"  SafeNet: {len(sn)} (${sn['pnl'].sum():+,.2f})  Trail: {len(tr)} (${tr['pnl'].sum():+,.2f})")
    cum = tdf["pnl"].cumsum(); dd = (cum - cum.cummax()).min()
    print(f"  Max DD: ${dd:,.2f}")
    for s in ["long", "short"]:
        sub = tdf[tdf["direction"] == s]
        if len(sub) > 0:
            print(f"  {s.upper()}: {len(sub)} trades, ${sub['pnl'].sum():+,.2f}, WR {(sub['pnl']>0).mean()*100:.1f}%")

    # IS/OOS
    is_t = tdf[tdf["entry_time"].dt.year == 2023]
    oos_t = tdf[tdf["entry_time"].dt.year == 2024]
    for lbl, sub in [("IS(2023)", is_t), ("OOS(2024)", oos_t)]:
        if len(sub) > 0:
            sp = sub["pnl"].sum()
            sw = sub[sub["pnl"]>0]; sl = sub[sub["pnl"]<=0]
            spf = sw["pnl"].sum()/abs(sl["pnl"].sum()) if len(sl)>0 and sl["pnl"].sum()!=0 else 999
            print(f"  {lbl}: {len(sub)} trades, ${sp:+,.2f}, PF {spf:.2f}")

    # ================================================================
    # STEP 2: Loss Analysis
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 2: Loss Deep Analysis")
    print(SEP)

    # 2a. Top 20 losers
    losers = tdf.nsmallest(20, "pnl")
    print(f"\n  [2a] Top 20 Worst Trades")
    print(f"  {'#':>3s} {'Entry Time':>17s} {'Dir':>5s} {'Hold':>6s} {'PnL':>8s} {'Exit':>10s} {'MAE%':>7s} {'MFE%':>7s} {'MTF':>5s} {'RatioZ':>7s} {'Vol':>5s}")
    print(f"  {'-'*90}")
    for rank, (_, t) in enumerate(losers.iterrows(), 1):
        et = t["entry_time"].strftime("%Y-%m-%d %H:%M")
        print(f"  {rank:>3d} {et:>17s} {t['direction']:>5s} {t['hold_hours']:>5.0f}h "
              f"${t['pnl']:>+7.1f} {t['exit_type']:>10s} {t['mae_pct']:>+6.2f}% "
              f"{t['mfe_pct']:>+6.2f}% {t['mtf_pctile']:>5.1f} {t['ratio_z']:>+6.2f} "
              f"{t['vol_ratio']:>5.2f}")

    # 2b. Characteristic comparison: top 20 losers vs all winners
    winners = tdf[tdf["pnl"] > 0]
    top20_losers = losers
    all_losers = tdf[tdf["pnl"] <= 0]

    print(f"\n  [2b] Characteristic Comparison")
    print(f"  {'Feature':<30s} {'Top20 Losers':>14s} {'All Winners':>14s} {'All Losers':>14s} {'Delta(L-W)':>12s}")
    print(f"  {'-'*84}")

    def pct_str(series, val):
        return f"{(series==val).mean()*100:.0f}%"

    features = [
        ("Direction: Long%",
         f"{(top20_losers['direction']=='long').mean()*100:.0f}%",
         f"{(winners['direction']=='long').mean()*100:.0f}%",
         f"{(all_losers['direction']=='long').mean()*100:.0f}%"),
        ("Avg MTF pctile",
         f"{top20_losers['mtf_pctile'].mean():.1f}",
         f"{winners['mtf_pctile'].mean():.1f}",
         f"{all_losers['mtf_pctile'].mean():.1f}"),
        ("Avg Ratio Z (abs)",
         f"{top20_losers['ratio_z'].abs().mean():.2f}",
         f"{winners['ratio_z'].abs().mean():.2f}",
         f"{all_losers['ratio_z'].abs().mean():.2f}"),
        ("Avg Vol Ratio",
         f"{top20_losers['vol_ratio'].mean():.2f}",
         f"{winners['vol_ratio'].mean():.2f}",
         f"{all_losers['vol_ratio'].mean():.2f}"),
        ("Avg BB Width Pctile",
         f"{top20_losers['bb_width_pctile'].mean():.1f}",
         f"{winners['bb_width_pctile'].mean():.1f}",
         f"{all_losers['bb_width_pctile'].mean():.1f}"),
        ("Avg ATR%",
         f"{top20_losers['atr_pct'].mean():.3f}%",
         f"{winners['atr_pct'].mean():.3f}%",
         f"{all_losers['atr_pct'].mean():.3f}%"),
        ("Avg RSI",
         f"{top20_losers['rsi'].mean():.1f}",
         f"{winners['rsi'].mean():.1f}",
         f"{all_losers['rsi'].mean():.1f}"),
        ("Avg Hold Hours",
         f"{top20_losers['hold_hours'].mean():.1f}",
         f"{winners['hold_hours'].mean():.1f}",
         f"{all_losers['hold_hours'].mean():.1f}"),
        ("Exit: SafeNet%",
         f"{(top20_losers['exit_type']=='SafeNet').mean()*100:.0f}%",
         f"{(winners['exit_type']=='SafeNet').mean()*100:.0f}%",
         f"{(all_losers['exit_type']=='SafeNet').mean()*100:.0f}%"),
        ("Avg MAE%",
         f"{top20_losers['mae_pct'].mean():.2f}%",
         f"{winners['mae_pct'].mean():.2f}%",
         f"{all_losers['mae_pct'].mean():.2f}%"),
        ("Avg MFE%",
         f"{top20_losers['mfe_pct'].mean():.2f}%",
         f"{winners['mfe_pct'].mean():.2f}%",
         f"{all_losers['mfe_pct'].mean():.2f}%"),
    ]

    for name, t20, win, allL in features:
        # Compute delta for the raw number
        print(f"  {name:<30s} {t20:>14s} {win:>14s} {allL:>14s}")

    # 2c. Detailed breakdowns
    print(f"\n  [2c] Detailed Breakdowns")

    # Hour distribution
    print(f"\n  Entry Hour (UTC+8) breakdown:")
    print(f"  {'Hour':>6s} {'Total':>6s} {'Winners':>8s} {'Losers':>8s} {'WR%':>6s} {'Avg PnL':>8s}")
    print(f"  {'-'*44}")
    for h in sorted(tdf["entry_hour"].unique()):
        sub = tdf[tdf["entry_hour"] == h]
        w_h = sub[sub["pnl"]>0]; l_h = sub[sub["pnl"]<=0]
        print(f"  {h:>6d} {len(sub):>6d} {len(w_h):>8d} {len(l_h):>8d} "
              f"{len(w_h)/len(sub)*100:>5.0f}% ${sub['pnl'].mean():>+7.1f}")

    # Weekday distribution
    day_names = {1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri"}
    print(f"\n  Entry Weekday breakdown:")
    print(f"  {'Day':>6s} {'Total':>6s} {'Winners':>8s} {'Losers':>8s} {'WR%':>6s} {'Avg PnL':>8s}")
    print(f"  {'-'*44}")
    for wd in sorted(tdf["entry_weekday"].unique()):
        sub = tdf[tdf["entry_weekday"] == wd]
        w_d = sub[sub["pnl"]>0]; l_d = sub[sub["pnl"]<=0]
        dn = day_names.get(wd, f"d{wd}")
        print(f"  {dn:>6s} {len(sub):>6d} {len(w_d):>8d} {len(l_d):>8d} "
              f"{len(w_d)/len(sub)*100:>5.0f}% ${sub['pnl'].mean():>+7.1f}")

    # Direction × exit type
    print(f"\n  Direction x Exit Type:")
    for d in ["long", "short"]:
        for et in ["SafeNet", "EMA20_trail"]:
            sub = tdf[(tdf["direction"]==d) & (tdf["exit_type"]==et)]
            if len(sub) > 0:
                print(f"  {d:>5s} x {et:<12s}: {len(sub):>3d} trades, "
                      f"${sub['pnl'].sum():>+8.1f}, avg ${sub['pnl'].mean():>+6.1f}")

    # Hold time brackets
    print(f"\n  Hold Time brackets:")
    print(f"  {'Bracket':>10s} {'N':>5s} {'PnL':>9s} {'WR%':>6s} {'Avg PnL':>8s}")
    print(f"  {'-'*40}")
    for lo_b, hi_b, lbl in [(0,6,"<6h"),(6,12,"6-12h"),(12,24,"12-24h"),
                             (24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
        sub = tdf[(tdf["hold_hours"]>=lo_b) & (tdf["hold_hours"]<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>10s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}% ${sub['pnl'].mean():>+7.1f}")

    # MTF pctile brackets
    print(f"\n  MTF Pctile brackets:")
    print(f"  {'Bracket':>10s} {'N':>5s} {'PnL':>9s} {'WR%':>6s}")
    print(f"  {'-'*35}")
    for lo_b, hi_b, lbl in [(0,10,"0-10"),(10,20,"10-20"),(20,30,"20-30"),(30,40,"30-40")]:
        sub = tdf[(tdf["mtf_pctile"]>=lo_b) & (tdf["mtf_pctile"]<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>10s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}%")

    # Ratio Z brackets
    print(f"\n  Ratio Z (abs) brackets:")
    print(f"  {'Bracket':>10s} {'N':>5s} {'PnL':>9s} {'WR%':>6s}")
    print(f"  {'-'*35}")
    for lo_b, hi_b, lbl in [(1.0,1.5,"1.0-1.5"),(1.5,2.0,"1.5-2.0"),(2.0,3.0,"2.0-3.0"),(3.0,99,"3.0+")]:
        sub = tdf[(tdf["ratio_z"].abs()>=lo_b) & (tdf["ratio_z"].abs()<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>10s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}%")

    # Vol ratio brackets
    print(f"\n  Vol Ratio brackets:")
    print(f"  {'Bracket':>10s} {'N':>5s} {'PnL':>9s} {'WR%':>6s}")
    print(f"  {'-'*35}")
    for lo_b, hi_b, lbl in [(1.0,1.5,"1.0-1.5"),(1.5,2.0,"1.5-2.0"),(2.0,3.0,"2.0-3.0"),(3.0,99,"3.0+")]:
        sub = tdf[(tdf["vol_ratio"]>=lo_b) & (tdf["vol_ratio"]<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>10s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}%")

    # ATR% brackets
    print(f"\n  ATR% brackets:")
    print(f"  {'Bracket':>12s} {'N':>5s} {'PnL':>9s} {'WR%':>6s}")
    print(f"  {'-'*37}")
    atr_pcts = sorted(tdf["atr_pct"].unique())
    q33 = tdf["atr_pct"].quantile(0.33)
    q66 = tdf["atr_pct"].quantile(0.66)
    for lo_b, hi_b, lbl in [(0,q33,f"<{q33:.3f}"),(q33,q66,f"{q33:.3f}-{q66:.3f}"),(q66,99,f">{q66:.3f}")]:
        sub = tdf[(tdf["atr_pct"]>=lo_b) & (tdf["atr_pct"]<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>12s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}%")

    # BB width pctile brackets
    print(f"\n  BB Width Pctile brackets:")
    print(f"  {'Bracket':>10s} {'N':>5s} {'PnL':>9s} {'WR%':>6s}")
    print(f"  {'-'*35}")
    for lo_b, hi_b, lbl in [(0,10,"0-10"),(10,20,"10-20"),(20,40,"20-40"),(40,60,"40-60"),(60,100,"60-100")]:
        sub = tdf[(tdf["bb_width_pctile"]>=lo_b) & (tdf["bb_width_pctile"]<hi_b)]
        if len(sub) > 0:
            print(f"  {lbl:>10s} {len(sub):>5d} ${sub['pnl'].sum():>+8.1f} "
                  f"{(sub['pnl']>0).mean()*100:>5.0f}%")

    # ================================================================
    # STEP 3: Filter Design + Backtest
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 3: Exclusion Filter Design + Backtest")
    print(SEP)

    baseline_n = n; baseline_pnl = pnl; baseline_pf = pf

    # Collect filter candidates from the data
    filters = []

    # F1: Skip high MTF pctile (e.g. > 30, > 25)
    for thresh in [30, 25, 20]:
        filters.append((f"F1: MTF < {thresh} (was <40)",
            lambda row, prev, t=thresh: prev["mtf_ratio_pctile"] >= t))

    # F2: Skip weak ratio Z (close to threshold)
    for thresh in [1.3, 1.5, 1.2]:
        filters.append((f"F2: |RatioZ| > {thresh} (was >1.0)",
            lambda row, prev, t=thresh: abs(row["ratio_zscore"]) < t))

    # F3: Skip low vol ratio
    for thresh in [1.5, 2.0, 1.3]:
        filters.append((f"F3: Vol > {thresh} (was >1.0)",
            lambda row, prev, t=thresh: row["vol_ratio"] < t))

    # F4: Skip high BB width pctile (already expanded)
    for thresh in [40, 50, 30]:
        filters.append((f"F4: BB_width_pctile < {thresh}",
            lambda row, prev, t=thresh: row["bb_width_pctile"] >= t))

    # F5: Skip high ATR% (too volatile)
    atr_p75 = tdf["atr_pct"].quantile(0.75)
    atr_p60 = tdf["atr_pct"].quantile(0.60)
    for thresh in [atr_p75, atr_p60]:
        filters.append((f"F5: ATR% < {thresh:.3f}",
            lambda row, prev, t=thresh: (row["atr"] / row["close"] * 100) >= t))

    # F6: Skip specific bad hours
    for bad_h in [{3, 4}, {3}, {10, 11}]:
        filters.append((f"F6: Also block hours {bad_h}",
            lambda row, prev, bh=bad_h: row["hour"] in bh))

    # F7: Skip short trades only in weak conditions
    filters.append(("F7: No shorts when RatioZ > -1.5",
        lambda row, prev: row["close"] < row["bb_lower"] and row["ratio_zscore"] > -1.5))

    # F8: Short-only filter: skip shorts with vol < 1.5
    filters.append(("F8: No shorts when vol < 1.5",
        lambda row, prev: row["close"] < row["bb_lower"] and row["vol_ratio"] < 1.5))

    # F9: Skip if RSI is extreme (overbought entering long, oversold entering short)
    # Actually skip if RSI near midrange (weak momentum)
    filters.append(("F9: Skip RSI 40-60 (no conviction)",
        lambda row, prev: 40 < row["rsi"] < 60))

    print(f"\n  Baseline: {baseline_n} trades, ${baseline_pnl:+,.2f}, PF {baseline_pf:.2f}")
    print(f"\n  {'Filter':<40s} {'N':>5s} {'PnL':>9s} {'PF':>6s} {'WR':>6s} "
          f"{'Cut-L':>6s} {'Cut-W':>6s} {'Net':>9s} {'OOS PnL':>9s}")
    print(f"  {'-'*100}")

    filter_results = []
    for fname, ffunc in filters:
        ftdf = run_backtest(data, extra_filter=ffunc)
        if len(ftdf) == 0:
            print(f"  {fname:<40s} {'0 trades':>5s}")
            continue

        fn = len(ftdf); fpnl = ftdf["pnl"].sum()
        fwr = (ftdf["pnl"] > 0).mean() * 100
        fw = ftdf[ftdf["pnl"] > 0]; fl = ftdf[ftdf["pnl"] <= 0]
        fpf = fw["pnl"].sum() / abs(fl["pnl"].sum()) if len(fl) > 0 and fl["pnl"].sum() != 0 else 999

        # What was filtered out
        # Find trades in baseline but not in filtered (by entry_time)
        base_entries = set(tdf["entry_time"].astype(str))
        filt_entries = set(ftdf["entry_time"].astype(str))
        cut_entries = base_entries - filt_entries
        cut_trades = tdf[tdf["entry_time"].astype(str).isin(cut_entries)]
        cut_w = cut_trades[cut_trades["pnl"] > 0]
        cut_l = cut_trades[cut_trades["pnl"] <= 0]
        net = fpnl - baseline_pnl

        # OOS
        foos = ftdf[ftdf["entry_time"].dt.year == 2024]
        foos_pnl = foos["pnl"].sum() if len(foos) > 0 else 0

        print(f"  {fname:<40s} {fn:>5d} ${fpnl:>+8,.0f} {fpf:>5.2f} {fwr:>5.1f}% "
              f"{len(cut_l):>5d}L {len(cut_w):>5d}W ${net:>+8,.0f} ${foos_pnl:>+8,.0f}")

        filter_results.append({
            "name": fname, "n": fn, "pnl": fpnl, "pf": fpf, "wr": fwr,
            "cut_l": len(cut_l), "cut_w": len(cut_w), "net": net,
            "oos_pnl": foos_pnl, "annual": fpnl/2,
            "tdf": ftdf,
        })

    # ================================================================
    # STEP 4: MAE/MFE Analysis
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 4: MAE/MFE Analysis")
    print(SEP)

    # 4a. MAE distribution for all losing trades
    losing = tdf[tdf["pnl"] <= 0]
    print(f"\n  [4a] MAE Distribution (all {len(losing)} losing trades)")
    mae_bins = [(-0.5, 0, ">-0.5%"), (-1.0, -0.5, "-1% to -0.5%"),
                (-2.0, -1.0, "-2% to -1%"), (-3.0, -2.0, "-3% to -2%"),
                (-3.5, -3.0, "-3.5% to -3%"), (-99, -3.5, "< -3.5%")]
    for lo_b, hi_b, lbl in mae_bins:
        sub = losing[(losing["mae_pct"] >= lo_b) & (losing["mae_pct"] < hi_b)]
        bar = "#" * len(sub)
        print(f"  {lbl:>16s}: {len(sub):>3d} trades  {bar}")

    # Also for winners
    winning = tdf[tdf["pnl"] > 0]
    print(f"\n  MAE Distribution (all {len(winning)} winning trades)")
    for lo_b, hi_b, lbl in mae_bins:
        sub = winning[(winning["mae_pct"] >= lo_b) & (winning["mae_pct"] < hi_b)]
        bar = "#" * len(sub)
        print(f"  {lbl:>16s}: {len(sub):>3d} trades  {bar}")

    # 4b. MFE vs final PnL (profit retention)
    print(f"\n  [4b] MFE vs Final PnL")
    if len(winning) > 0:
        # For winners: MFE in $ vs final PnL in $
        win_mfe_dollar = winning["mfe_pct"] / 100 * winning["entry_price"] * MARGIN * LEVERAGE / winning["entry_price"]
        # Simplified: mfe$ = mfe_pct/100 * notional
        win_mfe_d = winning["mfe_pct"] / 100 * (MARGIN * LEVERAGE)
        retention = winning["pnl"].sum() / win_mfe_d.sum() * 100 if win_mfe_d.sum() > 0 else 0
        print(f"  Winners: avg MFE ${win_mfe_d.mean():,.1f}, avg PnL ${winning['pnl'].mean():,.1f}")
        print(f"  Profit retention: {retention:.1f}% (PnL / peak MFE)")

    # Trades that had MFE > $50 but ended as loss
    if len(losing) > 0:
        lose_mfe_d = losing["mfe_pct"] / 100 * (MARGIN * LEVERAGE)
        had_profit = losing[lose_mfe_d > 50]
        print(f"\n  Losers that had MFE > $50 (missed profit): {len(had_profit)} / {len(losing)}")
        if len(had_profit) > 0:
            print(f"    Their total PnL: ${had_profit['pnl'].sum():+,.2f}")
            print(f"    Their avg peak MFE$: ${lose_mfe_d[lose_mfe_d>50].mean():,.1f}")
            print(f"    Lost profit: ${(lose_mfe_d[lose_mfe_d>50].sum() + had_profit['pnl'].sum()):+,.2f}")

    # 4c. SafeNet analysis by subset
    print(f"\n  [4c] SafeNet Optimality by Subset")

    for subset_name, subset in [("Long", tdf[tdf["direction"]=="long"]),
                                 ("Short", tdf[tdf["direction"]=="short"])]:
        sn_sub = subset[subset["exit_type"] == "SafeNet"]
        tr_sub = subset[subset["exit_type"] == "EMA20_trail"]
        print(f"\n  {subset_name}:")
        print(f"    SafeNet: {len(sn_sub)} trades, ${sn_sub['pnl'].sum():+,.2f}, avg MAE {sn_sub['mae_pct'].mean():.2f}%")
        print(f"    Trail:   {len(tr_sub)} trades, ${tr_sub['pnl'].sum():+,.2f}, avg MAE {tr_sub['mae_pct'].mean():.2f}%")

        # MAE distribution of SafeNet exits vs Trail exits
        if len(sn_sub) > 0:
            print(f"    SafeNet avg MFE before stop: {sn_sub['mfe_pct'].mean():.2f}% "
                  f"(= ${sn_sub['mfe_pct'].mean()/100*MARGIN*LEVERAGE:.1f})")

    # Different SafeNet levels simulation
    print(f"\n  SafeNet level sensitivity (full strategy re-run):")
    for sn_pct in [0.025, 0.030, 0.035, 0.040, 0.045, 0.050]:
        # Quick re-run with different SafeNet
        test_trades = []
        test_lpos = []; test_spos = []
        NOT = MARGIN * LEVERAGE

        for i in range(1, len(data) - 1):
            row = data.iloc[i]; nxt = data.iloc[i+1]; prev = data.iloc[i-1]
            hi = row["high"]; lo = row["low"]; close = row["close"]
            if row["datetime"] < BT_START or row["datetime"] >= BT_END: continue

            nl = []
            for p in test_lpos:
                bars = i - p["ei"]; done = False
                sn = p["entry"] * (1 - sn_pct)
                if lo <= sn:
                    ov = sn - lo; ep = sn - ov * 0.25
                    pnl_v = (ep - p["entry"]) * NOT / p["entry"] - FEE
                    test_trades.append({"pnl": pnl_v, "type": "SN", "side": "long"})
                    done = True
                elif bars >= MIN_HOLD and close <= row["ema20"]:
                    pnl_v = (close - p["entry"]) * NOT / p["entry"] - FEE
                    test_trades.append({"pnl": pnl_v, "type": "Trail", "side": "long"})
                    done = True
                if not done: nl.append(p)
            test_lpos = nl

            ns = []
            for p in test_spos:
                bars = i - p["ei"]; done = False
                sn = p["entry"] * (1 + sn_pct)
                if hi >= sn:
                    ov = hi - sn; ep = sn + ov * 0.25
                    pnl_v = (p["entry"] - ep) * NOT / p["entry"] - FEE
                    test_trades.append({"pnl": pnl_v, "type": "SN", "side": "short"})
                    done = True
                elif bars >= MIN_HOLD and close >= row["ema20"]:
                    pnl_v = (p["entry"] - close) * NOT / p["entry"] - FEE
                    test_trades.append({"pnl": pnl_v, "type": "Trail", "side": "short"})
                    done = True
                if not done: ns.append(p)
            test_spos = ns

            ep_v = nxt["open"]
            if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END: continue
            h = row["hour"]; wd = row["weekday"]
            if h in WORST_HOURS or wd in WORST_DAYS: continue
            if prev["mtf_ratio_pctile"] >= MTF_THRESH: continue
            bb_l = close > row["bb_upper"]; bb_s = close < row["bb_lower"]
            if not bb_l and not bb_s: continue
            if row["vol_ratio"] <= 1.0: continue
            rzv = row["ratio_zscore"]
            if bb_l and rzv > RZ_THRESH and len(test_lpos) < MAX_SAME:
                test_lpos.append({"entry": ep_v, "ei": i+1})
            if bb_s and rzv < -RZ_THRESH and len(test_spos) < MAX_SAME:
                test_spos.append({"entry": ep_v, "ei": i+1})

        if test_trades:
            ttdf = pd.DataFrame(test_trades)
            tp = ttdf["pnl"].sum()
            tn = len(ttdf)
            tsn = len(ttdf[ttdf["type"] == "SN"])
            tw = ttdf[ttdf["pnl"] > 0]; tl = ttdf[ttdf["pnl"] <= 0]
            tpf = tw["pnl"].sum() / abs(tl["pnl"].sum()) if len(tl) > 0 and tl["pnl"].sum() != 0 else 999
            mark = " <-- current" if sn_pct == 0.035 else ""
            print(f"  SN {sn_pct*100:.1f}%: {tn:>4d} trades, ${tp:>+8,.0f}, PF {tpf:>5.2f}, "
                  f"SN triggers: {tsn:>3d}{mark}")

    # ================================================================
    # STEP 5: Final Verdict
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 5: Honest Verdict")
    print(SEP)

    # Sort filter results by PnL
    filter_results.sort(key=lambda x: x["pnl"], reverse=True)

    # OOS for baseline
    oos_base = oos_t["pnl"].sum() if len(oos_t) > 0 else 0
    is_base = is_t["pnl"].sum() if len(is_t) > 0 else 0

    print(f"\n  {'Strategy':<40s} {'N':>5s} {'2yr PnL':>9s} {'Annual':>9s} {'PF':>6s} "
          f"{'IS PnL':>9s} {'OOS PnL':>9s}")
    print(f"  {'-'*88}")
    print(f"  {'v6 Original':<40s} {baseline_n:>5d} ${baseline_pnl:>+8,.0f} ${baseline_pnl/2:>+8,.0f} "
          f"{baseline_pf:>5.2f} ${is_base:>+8,.0f} ${oos_base:>+8,.0f}")

    for fr in filter_results[:10]:
        ftdf = fr["tdf"]
        fis = ftdf[ftdf["entry_time"].dt.year == 2023]["pnl"].sum()
        print(f"  {fr['name']:<40s} {fr['n']:>5d} ${fr['pnl']:>+8,.0f} ${fr['annual']:>+8,.0f} "
              f"{fr['pf']:>5.2f} ${fis:>+8,.0f} ${fr['oos_pnl']:>+8,.0f}")

    # Find best that improves both IS and OOS
    best = None
    for fr in filter_results:
        if fr["pnl"] > baseline_pnl and fr["oos_pnl"] > oos_base and fr["n"] >= 120:
            best = fr; break

    if best:
        print(f"\n  BEST IMPROVEMENT: {best['name']}")
        print(f"    PnL: ${baseline_pnl:+,.2f} -> ${best['pnl']:+,.2f} ({best['pnl']-baseline_pnl:+,.2f})")
        print(f"    Annual: ${baseline_pnl/2:+,.2f} -> ${best['annual']:+,.2f}")
        print(f"    PF: {baseline_pf:.2f} -> {best['pf']:.2f}")
        print(f"    OOS: ${oos_base:+,.2f} -> ${best['oos_pnl']:+,.2f}")
        print(f"    Trades: {baseline_n} -> {best['n']} ({best['n']/2:.0f}/yr)")
        if best['annual'] >= 2500:
            print(f"    Target $5,000/yr: {'MET' if best['annual'] >= 5000 else 'NOT MET'} (${best['annual']:+,.2f})")
    else:
        print(f"\n  NO FILTER IMPROVES BOTH PnL AND OOS.")
        # Find best by PnL that doesn't destroy OOS
        for fr in filter_results:
            if fr["oos_pnl"] > 0 and fr["n"] >= 120:
                print(f"  Best with positive OOS: {fr['name']}")
                print(f"    PnL: ${fr['pnl']:+,.2f}, OOS: ${fr['oos_pnl']:+,.2f}")
                break

    print(f"\n{SEP}")
    print("  END OF ANALYSIS")
    print(SEP)
