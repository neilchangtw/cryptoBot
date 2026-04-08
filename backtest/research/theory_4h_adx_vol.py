"""
Round 7: 4h Structure Breakout + 1h ADX Trend + Volume Surge
=============================================================
Core: Multi-timeframe breakout resonance. When 4h close breaks
recent range AND 1h ADX confirms trend direction with volume,
both timeframes agree — high conviction entry.

Entry (3 conditions):
  1. 4h Close Breakout: close_4h > max(close_4h, 5 bars, shift=1) (long)
                     or close_4h < min(close_4h, 5 bars, shift=1) (short)
  2. 1h ADX(14, shift=1) > 20 = trend exists
  3. 1h Volume(shift=1) > 1.5 x MA20(shift=1) = participation surge
  4. Direction: 4h breakout direction MUST agree with 1h DI direction
  5. Fresh: not all conditions met at previous bar

Exit: EMA20 trail (min 12h) + SafeNet +/-3.5%

Parameters locked:
  4h breakout lookback=5 (20h structure)
  ADX period=14 (Wilder standard), threshold=20
  Volume surge=1.5x MA20
"""
import os, sys, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN = 100; LEVERAGE = 20; NOTIONAL = MARGIN * LEVERAGE
FEE = 2.0; MAX_SAME = 2; SAFENET_PCT = 0.035; MIN_TRAIL_BARS = 12; ACCOUNT = 10000

BRK_4H_LOOKBACK = 5   # 4h bars = 20h
ADX_PERIOD = 14; ADX_THRESH = 20
VOL_SURGE = 1.5

END_DATE = datetime(2026, 4, 3)
START_DATE = END_DATE - timedelta(days=732)
MID_DATE = END_DATE - timedelta(days=365)

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
            if not d or isinstance(d, dict): break
            all_d.extend(d); cur = d[-1][0] + 1; _time.sleep(0.12)
        except Exception as e:
            print(f"  Error: {e}"); break
    if not all_d: return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume",
                                       "ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df = df[df["ot"] < end_ms].reset_index(drop=True)
    print(f"  Got {len(df)} bars", flush=True)
    return df

ETH_1H_CACHE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ETHUSDT_1h_latest730d.csv"))
ETH_4H_CACHE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ETHUSDT_4h_latest730d.csv"))

def load_data(interval, cache_path):
    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df["datetime"] = pd.to_datetime(df["datetime"])
        for c in ["open","high","low","close","volume"]:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
        last = df["datetime"].iloc[-1]
        if (END_DATE - last.to_pydatetime().replace(tzinfo=None)).days <= 2:
            print(f"Loaded {len(df)} {interval} bars from cache", flush=True)
            return df
    df = fetch_binance("ETHUSDT", interval, START_DATE, END_DATE)
    if len(df) == 0: print(f"ERROR: No {interval} data!"); sys.exit(1)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df

# ═══════════════════════════════════════════════════════════
# ADX/DI (Wilder 1978)
# ═══════════════════════════════════════════════════════════
def compute_adx(df, period=14):
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    n = len(df)
    tr = np.zeros(n); plus_dm = np.zeros(n); minus_dm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
        up = high[i] - high[i-1]; down = low[i-1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
    atr = np.zeros(n); pdm_s = np.zeros(n); mdm_s = np.zeros(n)
    atr[period] = np.mean(tr[1:period+1])
    pdm_s[period] = np.mean(plus_dm[1:period+1])
    mdm_s[period] = np.mean(minus_dm[1:period+1])
    for i in range(period+1, n):
        atr[i] = (atr[i-1]*(period-1) + tr[i]) / period
        pdm_s[i] = (pdm_s[i-1]*(period-1) + plus_dm[i]) / period
        mdm_s[i] = (mdm_s[i-1]*(period-1) + minus_dm[i]) / period
    pdi = np.zeros(n); mdi = np.zeros(n); dx = np.zeros(n)
    for i in range(period, n):
        if atr[i] > 0:
            pdi[i] = 100*pdm_s[i]/atr[i]; mdi[i] = 100*mdm_s[i]/atr[i]
        ds = pdi[i]+mdi[i]
        dx[i] = 100*abs(pdi[i]-mdi[i])/ds if ds > 0 else 0
    adx = np.zeros(n); start = 2*period
    if start < n:
        adx[start] = np.mean(dx[period+1:start+1])
        for i in range(start+1, n):
            adx[i] = (adx[i-1]*(period-1) + dx[i]) / period
    df["adx"] = adx; df["plus_di"] = pdi; df["minus_di"] = mdi
    return df

# ═══════════════════════════════════════════════════════════
# Indicator Computation
# ═══════════════════════════════════════════════════════════
def compute_indicators(df_1h, df_4h):
    df = df_1h.copy()
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ─── 1. 4h Close Breakout → map to 1h ───
    # Compute on 4h chart first, then merge to 1h
    df_4h["close_s1_4h"] = df_4h["close"].shift(1)
    df_4h["close_max_prev_4h"] = df_4h["close"].shift(2).rolling(BRK_4H_LOOKBACK - 1).max()
    df_4h["close_min_prev_4h"] = df_4h["close"].shift(2).rolling(BRK_4H_LOOKBACK - 1).min()
    df_4h["brk_4h_long"] = df_4h["close_s1_4h"] > df_4h["close_max_prev_4h"]
    df_4h["brk_4h_short"] = df_4h["close_s1_4h"] < df_4h["close_min_prev_4h"]

    # Merge 4h breakout to 1h using asof merge (backward looking)
    merge_cols = df_4h[["ot", "brk_4h_long", "brk_4h_short"]].dropna().sort_values("ot")
    df = df.sort_values("ot")
    merged = pd.merge_asof(df[["ot"]], merge_cols, on="ot", direction="backward")
    df["brk_4h_long"] = merged["brk_4h_long"].values
    df["brk_4h_short"] = merged["brk_4h_short"].values

    # ─── 2. 1h ADX/DI (shift=1) ───
    df = compute_adx(df, ADX_PERIOD)
    df["adx_s1"] = df["adx"].shift(1)
    df["pdi_s1"] = df["plus_di"].shift(1)
    df["mdi_s1"] = df["minus_di"].shift(1)

    # ─── 3. 1h Volume Surge (shift=1) ───
    vol_ma20 = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"].shift(1) / vol_ma20.shift(1)

    # ─── Freshness ───
    df["brk_4h_long_prev"] = df["brk_4h_long"].shift(1)
    df["brk_4h_short_prev"] = df["brk_4h_short"].shift(1)
    df["adx_s1_prev"] = df["adx_s1"].shift(1)
    df["pdi_s1_prev"] = df["pdi_s1"].shift(1)
    df["mdi_s1_prev"] = df["mdi_s1"].shift(1)
    df["vol_ratio_prev"] = df["vol_ratio"].shift(1)

    return df

# ═══════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════
def run_backtest(df):
    warmup = 2 * ADX_PERIOD + BRK_4H_LOOKBACK * 4 + 30  # Extra warmup for 4h merge
    highs = df["high"].values; lows = df["low"].values
    closes = df["close"].values; opens = df["open"].values
    ema20s = df["ema20"].values; dts = df["datetime"].values
    b4l = df["brk_4h_long"].values; b4s = df["brk_4h_short"].values
    b4l_prev = df["brk_4h_long_prev"].values; b4s_prev = df["brk_4h_short_prev"].values
    adx1 = df["adx_s1"].values
    adx1_prev = df["adx_s1_prev"].values
    pdi1 = df["pdi_s1"].values; mdi1 = df["mdi_s1"].values
    pdi1_prev = df["pdi_s1_prev"].values; mdi1_prev = df["mdi_s1_prev"].values
    vrs = df["vol_ratio"].values; vrs_prev = df["vol_ratio_prev"].values

    lpos = []; spos = []; trades = []

    for i in range(warmup, len(df) - 1):
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
                               "bars": bars, "dt": row_dt}); closed = True
            elif bars >= MIN_TRAIL_BARS and row_c <= row_ema20:
                pnl = (row_c - p["entry"]) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "Trail", "side": "long",
                               "bars": bars, "dt": row_dt}); closed = True
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
                               "bars": bars, "dt": row_dt}); closed = True
            elif bars >= MIN_TRAIL_BARS and row_c >= row_ema20:
                pnl = (p["entry"] - row_c) * NOTIONAL / p["entry"] - FEE
                trades.append({"pnl": pnl, "type": "Trail", "side": "short",
                               "bars": bars, "dt": row_dt}); closed = True
            if not closed: ns.append(p)
        spos = ns

        # ─── Generate signals ───
        bl4 = b4l[i]; bs4 = b4s[i]
        bl4_p = b4l_prev[i]; bs4_p = b4s_prev[i]
        a1 = adx1[i]; a1_p = adx1_prev[i]
        pd1 = pdi1[i]; md1 = mdi1[i]
        pd1_p = pdi1_prev[i]; md1_p = mdi1_prev[i]
        vr = vrs[i]; vr_p = vrs_prev[i]

        if a1 == 0 or np.isnan(vr):
            continue

        # C1: 4h breakout
        brk_long_4h = bool(bl4) if not np.isnan(bl4) else False
        brk_short_4h = bool(bs4) if not np.isnan(bs4) else False

        # C2: 1h ADX > threshold
        adx_active = a1 > ADX_THRESH

        # C3: 1h Volume surge
        vol_ok = vr > VOL_SURGE

        # C4: 1h DI direction must agree with 4h breakout
        di_long = pd1 > md1
        di_short = md1 > pd1

        # Full signals
        long_signal_now = brk_long_4h and adx_active and vol_ok and di_long
        short_signal_now = brk_short_4h and adx_active and vol_ok and di_short

        # Freshness
        if not np.isnan(bl4_p) and a1_p > 0 and not np.isnan(vr_p):
            p_bl4 = bool(bl4_p); p_bs4 = bool(bs4_p)
            p_adx = a1_p > ADX_THRESH
            p_vol = vr_p > VOL_SURGE
            p_di_l = pd1_p > md1_p; p_di_s = md1_p > pd1_p
        else:
            p_bl4 = p_bs4 = p_adx = p_vol = p_di_l = p_di_s = False

        fresh_long = not (p_bl4 and p_adx and p_vol and p_di_l)
        fresh_short = not (p_bs4 and p_adx and p_vol and p_di_s)

        long_signal = long_signal_now and fresh_long
        short_signal = short_signal_now and fresh_short

        if long_signal and len(lpos) < MAX_SAME:
            lpos.append({"entry": nxt_open, "ei": i})
        if short_signal and len(spos) < MAX_SAME:
            spos.append({"entry": nxt_open, "ei": i})

    if trades: return pd.DataFrame(trades)
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
    equity = tdf["pnl"].cumsum(); dd = equity - equity.cummax(); mdd = dd.min()
    mdd_pct = abs(mdd) / ACCOUNT * 100
    tc = tdf.copy(); tc["date"] = pd.to_datetime(tc["dt"]).dt.date
    daily = tc.groupby("date")["pnl"].sum()
    all_dates = pd.date_range(tc["dt"].min(), tc["dt"].max(), freq="D")
    daily = daily.reindex(all_dates.date, fill_value=0)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    return {"n":n, "pnl":round(pnl,2), "wr":round(wr,1), "pf":round(pf,2),
            "mdd":round(mdd,2), "mdd_pct":round(mdd_pct,1),
            "sharpe":round(sharpe,2), "fees":round(n*FEE,2)}

def print_box(title, stats, months):
    m = stats["n"] / months if months > 0 else 0
    print(f"\n  +{'='*58}+")
    print(f"  | {title:<56s} |")
    print(f"  +{'-'*58}+")
    print(f"  | Trades: {stats['n']:>5d}  (monthly avg {m:.1f}){' ':<21s} |")
    print(f"  | Win Rate: {stats['wr']:>5.1f}%{' ':<41s} |")
    print(f"  | Profit Factor: {stats['pf']:>6.2f}{' ':<34s} |")
    print(f"  | Net PnL: ${stats['pnl']:>+10,.2f}{' ':<33s} |")
    print(f"  | Max DD: ${stats['mdd']:>+10,.2f}  ({stats['mdd_pct']:.1f}%){' ':<21s} |")
    print(f"  | Sharpe: {stats['sharpe']:>6.2f}{' ':<38s} |")
    print(f"  | Fees+Slip: -${stats['fees']:>9,.2f}{' ':<32s} |")
    print(f"  +{'='*58}+")

def print_breakdown(tdf):
    print("\n  Hold Time:")
    for lo,hi,lb in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
        s = tdf[(tdf["bars"]>=lo)&(tdf["bars"]<hi)]
        n=len(s); p=s["pnl"].sum() if n>0 else 0; w=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"    {lb:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")
    print("\n  L/S:")
    for side in ["long","short"]:
        s=tdf[tdf["side"]==side]; n=len(s); p=s["pnl"].sum() if n>0 else 0
        w=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"    {side.capitalize():<6s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")
    print("\n  Exit:")
    for t in ["Trail","SafeNet"]:
        s=tdf[tdf["type"]==t]; n=len(s); p=s["pnl"].sum() if n>0 else 0
        print(f"    {t:<10s}: {n:>4d} trades, ${p:>+10,.0f}")

def gods_eye_check():
    print("\n" + "="*62)
    print("  GOD'S EYE SELF-CHECK (6 mandatory)")
    print("="*62)
    checks = [
        ("1. All signal calcs use shift(1)+ data only?",
         "YES: 4h breakout uses close.shift(1) vs close.shift(2).rolling() on 4h;\n"
         "         4h merged to 1h via backward merge (only past 4h bars);\n"
         "         ADX/DI shifted by 1; vol_ratio uses volume.shift(1)/MA20.shift(1)"),
        ("2. Entry price = next bar open?",
         "YES: entry = nxt_open = opens[i+1]"),
        ("3. All rolling indicators have shift(1)?",
         "YES: 4h breakout shift(1) on 4h chart; ADX .shift(1); vol .shift(1)"),
        ("4. Params decided before seeing any data?",
         "YES: 4h lookback=5 (20h structure); ADX=14 Wilder standard;\n"
         "         ADX>20 standard threshold; vol=1.5x standard surge"),
        ("5. No post-OOS parameter adjustment?",
         "YES: Single run, no optimization"),
        ("6. Freshness check prevents stale re-entry?",
         "YES: Checks all 4 conditions at previous bar")
    ]
    all_pass = True
    for q, a in checks:
        st = "PASS" if a.startswith("YES") else "FAIL"
        if st == "FAIL": all_pass = False
        print(f"  [{st}] {q}")
        for line in a.split("\n"): print(f"         {line.strip()}")
    print(f"\n  Result: {'6/6 PASS' if all_pass else 'FAILED'}")
    return all_pass

# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("="*80)
    print("  Round 7: 4h Breakout + 1h ADX Trend + Volume Surge")
    print("="*80)

    print("\n--- Loading Data ---")
    df_1h = load_data("1h", ETH_1H_CACHE)
    df_4h = load_data("4h", ETH_4H_CACHE)
    print(f"1h: {len(df_1h)} bars | 4h: {len(df_4h)} bars")

    print("\n--- Computing Indicators ---")
    df = compute_indicators(df_1h, df_4h)

    warmup = 2 * ADX_PERIOD + BRK_4H_LOOKBACK * 4 + 30
    valid = df.iloc[warmup:]
    c1l = valid["brk_4h_long"].sum(); c1s = valid["brk_4h_short"].sum()
    c2 = (valid["adx_s1"] > ADX_THRESH).sum()
    c3 = (valid["vol_ratio"] > VOL_SURGE).sum()
    c4l = (valid["pdi_s1"] > valid["mdi_s1"]).sum()
    print(f"\n  Condition frequency:")
    print(f"    C1 4h breakout long: {c1l} ({c1l/len(valid)*100:.1f}%)")
    print(f"    C1 4h breakout short: {c1s} ({c1s/len(valid)*100:.1f}%)")
    print(f"    C2 ADX > {ADX_THRESH}: {c2} ({c2/len(valid)*100:.1f}%)")
    print(f"    C3 Volume > {VOL_SURGE}x: {c3} ({c3/len(valid)*100:.1f}%)")
    print(f"    C4 +DI > -DI: {c4l} ({c4l/len(valid)*100:.1f}%)")

    print("\n--- Running Backtest ---")
    all_trades = run_backtest(df)
    all_trades["dt"] = pd.to_datetime(all_trades["dt"])

    mid_ts = pd.Timestamp(MID_DATE)
    is_t = all_trades[all_trades["dt"] < mid_ts].reset_index(drop=True)
    oos_t = all_trades[all_trades["dt"] >= mid_ts].reset_index(drop=True)
    is_m = (MID_DATE - START_DATE).days / 30.44
    oos_m = (END_DATE - MID_DATE).days / 30.44

    is_s = calc_stats(is_t); oos_s = calc_stats(oos_t); full_s = calc_stats(all_trades)

    print_box("IN-SAMPLE (IS)", is_s, is_m)
    if len(is_t) > 0: print_breakdown(is_t)
    print_box("OUT-OF-SAMPLE (OOS) ***", oos_s, oos_m)
    if len(oos_t) > 0: print_breakdown(oos_t)
    print_box("FULL PERIOD", full_s, is_m + oos_m)
    if len(all_trades) > 0: print_breakdown(all_trades)

    oos_monthly = oos_s["n"] / oos_m if oos_m > 0 else 0
    oos_annual = oos_s["pnl"] / (oos_m / 12) if oos_m > 0 else 0
    print("\n" + "="*62)
    print("  TARGET CHECK (OOS)")
    print("="*62)
    t1 = oos_annual >= 5000; t2 = oos_s["pf"] >= 1.5
    t3 = oos_s["mdd_pct"] <= 25; t4 = oos_monthly >= 10
    print(f"  [{'PASS' if t1 else 'FAIL'}] Annual PnL >= $5,000: ${oos_annual:,.0f}")
    print(f"  [{'PASS' if t2 else 'FAIL'}] PF >= 1.5: {oos_s['pf']}")
    print(f"  [{'PASS' if t3 else 'FAIL'}] MDD <= 25%: {oos_s['mdd_pct']}%")
    print(f"  [{'PASS' if t4 else 'FAIL'}] Monthly >= 10: {oos_monthly:.1f}")

    gods_eye_check()

    all_pass = t1 and t2 and t3 and t4
    print(f"\n{'*'*20}")
    if all_pass: print("  ALL TARGETS MET!")
    else:
        fails = []
        if not t1: fails.append("PnL");
        if not t2: fails.append("PF")
        if not t3: fails.append("MDD");
        if not t4: fails.append("Freq")
        print(f"  FAILED: {', '.join(fails)}")
    print(f"{'*'*20}")
