"""
Dual Strategy Parallel Backtest: v6 Champion + C4 Heikin-Ashi
=============================================================
Step 1: Overlap analysis (4 metrics)
Step 2: Capital management selection
Step 3: Parallel backtests (P1-P4)
Step 4: Walk-forward IS/OOS
Step 5: Verdict

v6 entry: MTF<40(prev) + BB breakout + Vol>1 + RatioZ + Session
C4 entry: HA 4-streak + MTF<40(prev) + RatioZ (NO vol/session per user spec)
Both exit: SafeNet 3.5% + EMA20 trail (min 12h)
"""
import os, requests, warnings
import pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
import time as _time

warnings.filterwarnings("ignore")

# ====================================================================
# CONFIG (locked, do not modify)
# ====================================================================
ACCOUNT = 10000
RISK_PCT = 0.02           # 2% = $200 at $10K
SAFENET_PCT = 0.035       # 3.5%
FEE_RATE = 0.001          # 0.10% round trip (taker 0.04%x2 + slip 0.01%x2)
MAX_SAME = 2              # max same-direction per strategy
MIN_HOLD = 12             # bars (1h) before trail can trigger

MTF_THRESH = 40
RZ_THRESH = 1.0
HA_STREAK = 4
WORST_HOURS = {0, 1, 2, 12}   # UTC+8
WORST_DAYS = {0, 5, 6}        # Mon, Sat, Sun

FETCH_START = int(datetime(2022, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
FETCH_END   = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BT_START = datetime(2023, 1, 1)
BT_END   = datetime(2025, 1, 1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ====================================================================
# DATA FETCHING
# ====================================================================
def fetch_klines(symbol, interval):
    tag = f"{symbol}_{interval}_{FETCH_START}_{FETCH_END}"
    cache = os.path.join(DATA_DIR, f"{tag}.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        df["datetime"] = pd.to_datetime(df["datetime"])
        print(f"  [Cache] {len(df)} bars: {symbol} {interval}")
        return df
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
    df.to_csv(cache, index=False)
    print(f"{len(df)} bars"); return df

def pctile_fn(x):
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100 if x.max() != x.min() else 50

# ====================================================================
# INDICATORS (shared)
# ====================================================================
def compute_indicators(df_1h, btc_1h, df_4h):
    df = df_1h.copy()

    # ATR 1h
    tr = pd.DataFrame({"hl": df["high"]-df["low"],
        "hc": abs(df["high"]-df["close"].shift(1)),
        "lc": abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # ATR 4h -> map to 1h
    tr4 = pd.DataFrame({"hl": df_4h["high"]-df_4h["low"],
        "hc": abs(df_4h["high"]-df_4h["close"].shift(1)),
        "lc": abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
    df_4h["atr_4h"] = tr4.rolling(14).mean()
    mapped = pd.merge_asof(df[["ot"]].sort_values("ot"),
        df_4h[["ot","atr_4h"]].dropna().sort_values("ot"), on="ot", direction="backward")
    df["atr_4h"] = mapped["atr_4h"].values

    # MTF ratio + pctile
    df["mtf_ratio"] = df["atr"] / df["atr_4h"]
    df["mtf_ratio_pctile"] = df["mtf_ratio"].rolling(100).apply(pctile_fn, raw=False)

    # BB(20,2)
    df["bb_mid"] = df["close"].rolling(20).mean()
    bbs = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bbs
    df["bb_lower"] = df["bb_mid"] - 2 * bbs

    # Vol ratio
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    # EMA20
    df["ema20"] = df["close"].ewm(span=20).mean()

    # ETH/BTC ratio Z-score (rolling 50, matching original champion code)
    btc_map = btc_1h.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map)
    df["ratio"] = df["close"] / df["btc_close"]
    rm = df["ratio"].rolling(50).mean()
    rs = df["ratio"].rolling(50).std()
    df["ratio_zscore"] = (df["ratio"] - rm) / rs

    # HA candles
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = pd.Series(0.0, index=df.index)
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    for idx in range(1, len(df)):
        ha_open.iloc[idx] = (ha_open.iloc[idx-1] + ha_close.iloc[idx-1]) / 2
    df["ha_close"] = ha_close
    df["ha_open"] = ha_open

    # HA streaks
    bull = (ha_close > ha_open).astype(int)
    bear = (ha_close < ha_open).astype(int)
    bs = np.zeros(len(df), dtype=int)
    brs = np.zeros(len(df), dtype=int)
    for idx in range(1, len(df)):
        bs[idx] = (bs[idx-1] + 1) if bull.iloc[idx] else 0
        brs[idx] = (brs[idx-1] + 1) if bear.iloc[idx] else 0
    df["bull_streak"] = bs
    df["bear_streak"] = brs

    # Time
    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday

    return df.dropna().reset_index(drop=True)

# ====================================================================
# SIGNAL CHECKERS
# ====================================================================
def v6_signal(row, prev):
    """v6: MTF<40(prev) + BB breakout + Vol>1 + RatioZ + Session"""
    if row["hour"] in WORST_HOURS or row["weekday"] in WORST_DAYS:
        return None
    if prev["mtf_ratio_pctile"] >= MTF_THRESH:
        return None
    if row["vol_ratio"] <= 1.0:
        return None
    rz = row["ratio_zscore"]
    if row["close"] > row["bb_upper"] and rz > RZ_THRESH: return "long"
    if row["close"] < row["bb_lower"] and rz < -RZ_THRESH: return "short"
    return None

def c4_signal(row, prev):
    """C4: HA 4-streak + MTF<40(prev) + RatioZ (NO vol, NO session)"""
    if prev["mtf_ratio_pctile"] >= MTF_THRESH:
        return None
    rz = row["ratio_zscore"]
    if row["bull_streak"] >= HA_STREAK and rz > RZ_THRESH: return "long"
    if row["bear_streak"] >= HA_STREAK and rz < -RZ_THRESH: return "short"
    return None

# ====================================================================
# EXIT HELPER
# ====================================================================
def process_exits(lpos, spos, row, i):
    """Process exits for all positions. Returns (new_lpos, new_spos, closed, pnl_sum, fee_sum)"""
    hi = row["high"]; lo = row["low"]; close = row["close"]
    closed = []; pnl_sum = 0.0; fee_sum = 0.0

    nl = []
    for p in lpos:
        bars = i - p["ei"]; done = False
        p["mf"] = max(p.get("mf", 0), (hi - p["entry"]) * p["qty"])
        sn = p["entry"] * (1 - SAFENET_PCT)
        if lo <= sn:
            ov = sn - lo; ep = sn - ov * 0.25
            pnl = (ep - p["entry"]) * p["qty"]
            fee = p["notional"] * FEE_RATE; pnl -= fee
            closed.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                "side": "long", "type": "SafeNet", "entry": p["entry"], "exit": ep,
                "pnl": pnl, "fee": fee, "bars": bars, "notional": p["notional"],
                "source": p["source"]})
            pnl_sum += pnl; fee_sum += fee; done = True
        elif bars >= MIN_HOLD and close <= row["ema20"]:
            pnl = (close - p["entry"]) * p["qty"]
            fee = p["notional"] * FEE_RATE; pnl -= fee
            closed.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                "side": "long", "type": "Trail", "entry": p["entry"], "exit": close,
                "pnl": pnl, "fee": fee, "bars": bars, "notional": p["notional"],
                "source": p["source"]})
            pnl_sum += pnl; fee_sum += fee; done = True
        if not done: nl.append(p)

    ns = []
    for p in spos:
        bars = i - p["ei"]; done = False
        p["mf"] = max(p.get("mf", 0), (p["entry"] - lo) * p["qty"])
        sn = p["entry"] * (1 + SAFENET_PCT)
        if hi >= sn:
            ov = hi - sn; ep = sn + ov * 0.25
            pnl = (p["entry"] - ep) * p["qty"]
            fee = p["notional"] * FEE_RATE; pnl -= fee
            closed.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                "side": "short", "type": "SafeNet", "entry": p["entry"], "exit": ep,
                "pnl": pnl, "fee": fee, "bars": bars, "notional": p["notional"],
                "source": p["source"]})
            pnl_sum += pnl; fee_sum += fee; done = True
        elif bars >= MIN_HOLD and close >= row["ema20"]:
            pnl = (p["entry"] - close) * p["qty"]
            fee = p["notional"] * FEE_RATE; pnl -= fee
            closed.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                "side": "short", "type": "Trail", "entry": p["entry"], "exit": close,
                "pnl": pnl, "fee": fee, "bars": bars, "notional": p["notional"],
                "source": p["source"]})
            pnl_sum += pnl; fee_sum += fee; done = True
        if not done: ns.append(p)

    return nl, ns, closed, pnl_sum, fee_sum

# ====================================================================
# SOLO BACKTEST (for overlap analysis)
# ====================================================================
def run_solo(data, strategy):
    """Run one strategy independently. Returns (trades_df, signal_bars, bar_long, bar_short, equity, mdd, fees)"""
    equity = ACCOUNT; peak = ACCOUNT; mdd = 0.0
    lpos = []; spos = []; trades = []; fees = 0.0
    n = len(data)
    bar_long = np.zeros(n, dtype=int)
    bar_short = np.zeros(n, dtype=int)
    signal_bars = []  # (bar_index, direction)
    sig_fn = v6_signal if strategy == "v6" else c4_signal

    for i in range(1, n - 1):
        row = data.iloc[i]; prev = data.iloc[i-1]; nxt = data.iloc[i+1]
        if row["datetime"] < BT_START or row["datetime"] >= BT_END:
            bar_long[i] = len(lpos); bar_short[i] = len(spos); continue

        # Exits
        lpos, spos, cl, pnl_d, fee_d = process_exits(lpos, spos, row, i)
        trades.extend(cl); equity += pnl_d; fees += fee_d

        if equity > peak: peak = equity
        dd = (peak - equity) / peak * 100
        if dd > mdd: mdd = dd

        bar_long[i] = len(lpos); bar_short[i] = len(spos)

        # Entry
        ep = nxt["open"]
        if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END: continue
        sig = sig_fn(row, prev)
        if sig is None: continue

        signal_bars.append((i, sig))
        notional = (equity * RISK_PCT) / SAFENET_PCT
        qty = notional / ep
        pos = {"entry": ep, "ei": i+1, "mf": 0, "entry_dt": nxt["datetime"],
               "qty": qty, "notional": notional, "source": strategy}

        if sig == "long" and len(lpos) < MAX_SAME:
            lpos.append(pos)
        elif sig == "short" and len(spos) < MAX_SAME:
            spos.append(pos)

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl","type","side","bars","fee","notional","source","entry_dt","exit_dt"])
    return tdf, signal_bars, bar_long, bar_short, equity, mdd, fees

# ====================================================================
# PARALLEL BACKTEST
# ====================================================================
def run_parallel(data, scheme):
    """Run both strategies on shared equity pool.
    scheme: P1 (full parallel), P2 (reduced risk), P3 (mutual exclusion), P4 (merge same-dir)
    """
    equity = ACCOUNT; peak = ACCOUNT; mdd = 0.0; mdd_abs = 0.0
    v6_lpos = []; v6_spos = []; c4_lpos = []; c4_spos = []
    trades = []; fees = 0.0
    max_pos = 0; max_risk_pct = 0.0

    # Daily tracking for Sharpe
    current_date = None; day_start_eq = ACCOUNT; daily_returns = []

    for i in range(1, len(data) - 1):
        row = data.iloc[i]; prev = data.iloc[i-1]; nxt = data.iloc[i+1]
        if row["datetime"] < BT_START or row["datetime"] >= BT_END:
            continue

        # Day change tracking
        bar_date = row["datetime"].date()
        if bar_date != current_date:
            if current_date is not None and day_start_eq > 0:
                daily_returns.append((equity - day_start_eq) / day_start_eq)
            day_start_eq = equity; current_date = bar_date

        # ── EXITS ──
        v6_lpos, v6_spos, cl1, p1, f1 = process_exits(v6_lpos, v6_spos, row, i)
        c4_lpos, c4_spos, cl2, p2, f2 = process_exits(c4_lpos, c4_spos, row, i)
        trades.extend(cl1); trades.extend(cl2)
        equity += p1 + p2; fees += f1 + f2

        if equity > peak: peak = equity
        dd_pct = (peak - equity) / peak * 100
        dd_abs = peak - equity
        if dd_pct > mdd: mdd = dd_pct
        if dd_abs > mdd_abs: mdd_abs = dd_abs

        # Track exposure
        all_pos = v6_lpos + v6_spos + c4_lpos + c4_spos
        npos = len(all_pos)
        if npos > max_pos: max_pos = npos
        total_risk = sum(p["notional"] * SAFENET_PCT for p in all_pos)
        risk_pct = total_risk / equity * 100 if equity > 0 else 0
        if risk_pct > max_risk_pct: max_risk_pct = risk_pct

        # ── ENTRIES ──
        ep = nxt["open"]
        if nxt["datetime"] < BT_START or nxt["datetime"] >= BT_END: continue

        v6s = v6_signal(row, prev)
        c4s = c4_signal(row, prev)

        entries = []  # (source, direction, risk_pct_override)
        if scheme == "P1":
            if v6s: entries.append(("v6", v6s, RISK_PCT))
            if c4s: entries.append(("c4", c4s, RISK_PCT))

        elif scheme == "P2":
            if v6s: entries.append(("v6", v6s, 0.015))
            if c4s: entries.append(("c4", c4s, 0.015))

        elif scheme == "P3":
            if v6s: entries.append(("v6", v6s, RISK_PCT))
            v6_has = len(v6_lpos) > 0 or len(v6_spos) > 0
            # Also block C4 if v6 just entered (v6s is not None)
            if c4s and not v6_has and not v6s:
                entries.append(("c4", c4s, RISK_PCT))

        elif scheme == "P4":
            if v6s and c4s and v6s == c4s:
                entries.append(("merged", v6s, RISK_PCT))
            elif v6s and c4s and v6s != c4s:
                entries.append(("v6", v6s, RISK_PCT / 2))
                entries.append(("c4", c4s, RISK_PCT / 2))
            elif v6s:
                entries.append(("v6", v6s, RISK_PCT))
            elif c4s:
                entries.append(("c4", c4s, RISK_PCT))

        for source, direction, rpct in entries:
            notional = (equity * rpct) / SAFENET_PCT
            qty = notional / ep
            pos = {"entry": ep, "ei": i+1, "mf": 0, "entry_dt": nxt["datetime"],
                   "qty": qty, "notional": notional, "source": source}

            if scheme == "P4":
                # Combined MAX_SAME
                all_l = len(v6_lpos) + len(c4_lpos)
                all_s = len(v6_spos) + len(c4_spos)
                if direction == "long" and all_l >= MAX_SAME: continue
                if direction == "short" and all_s >= MAX_SAME: continue
            else:
                if source in ("v6", "merged"):
                    if direction == "long" and len(v6_lpos) >= MAX_SAME: continue
                    if direction == "short" and len(v6_spos) >= MAX_SAME: continue
                elif source == "c4":
                    if direction == "long" and len(c4_lpos) >= MAX_SAME: continue
                    if direction == "short" and len(c4_spos) >= MAX_SAME: continue

            if source in ("v6", "merged"):
                if direction == "long": v6_lpos.append(pos)
                else: v6_spos.append(pos)
            elif source == "c4":
                if direction == "long": c4_lpos.append(pos)
                else: c4_spos.append(pos)

    # Final daily return
    if current_date is not None and day_start_eq > 0:
        daily_returns.append((equity - day_start_eq) / day_start_eq)

    dr = np.array(daily_returns)
    sharpe = np.mean(dr) / np.std(dr) * np.sqrt(365) if len(dr) > 1 and np.std(dr) > 0 else 0

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl","type","side","bars","fee","notional","source","entry_dt","exit_dt"])
    return tdf, equity, mdd, mdd_abs, fees, max_pos, max_risk_pct, sharpe, daily_returns

# ====================================================================
# STATISTICS
# ====================================================================
def calc_basic(tdf):
    if len(tdf) < 1:
        return {"n": 0, "pnl": 0, "annual": 0, "wr": 0, "pf": 0}
    pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999
    sn = tdf[tdf["type"] == "SafeNet"]
    tr = tdf[tdf["type"] == "Trail"]
    return {"n": len(tdf), "pnl": round(pnl, 2), "annual": round(pnl/2, 2),
            "wr": round(wr, 1), "pf": round(pf, 2),
            "sn_n": len(sn), "sn_pnl": round(sn["pnl"].sum(), 2),
            "tr_n": len(tr), "tr_pnl": round(tr["pnl"].sum(), 2),
            "fees": round(tdf["fee"].sum(), 2),
            "avg_bars": round(tdf["bars"].mean(), 1) if len(tdf) > 0 else 0}


# ====================================================================
# MAIN
# ====================================================================
if __name__ == "__main__":
    SEP = "=" * 70

    print(SEP)
    print("  Dual Strategy: v6 Champion + C4 Heikin-Ashi")
    print(SEP)

    # ── Data ──
    print("\n[DATA] Fetching...")
    df_1h = fetch_klines("ETHUSDT", "1h")
    btc_1h = fetch_klines("BTCUSDT", "1h")
    df_4h = fetch_klines("ETHUSDT", "4h")

    print("[DATA] Computing indicators...")
    data = compute_indicators(df_1h, btc_1h, df_4h)
    print(f"  Total bars: {len(data)}")

    # ================================================================
    # STEP 1: SOLO RUNS + OVERLAP ANALYSIS
    # ================================================================
    print(f"\n{SEP}")
    print("  STEP 1: Overlap Analysis")
    print(SEP)

    print("\n  Running v6 solo...", end=" ", flush=True)
    v6_tdf, v6_sigs, v6_bl, v6_bs, v6_eq, v6_mdd, v6_fees = run_solo(data, "v6")
    v6_stats = calc_basic(v6_tdf)
    print(f"{v6_stats['n']} trades, ${v6_stats['pnl']:+,.2f}")

    print("  Running C4 solo...", end=" ", flush=True)
    c4_tdf, c4_sigs, c4_bl, c4_bs, c4_eq, c4_mdd, c4_fees = run_solo(data, "c4")
    c4_stats = calc_basic(c4_tdf)
    print(f"{c4_stats['n']} trades, ${c4_stats['pnl']:+,.2f}")

    # v6 solo performance
    print(f"\n  --- v6 Solo (dynamic sizing, $10K) ---")
    print(f"  Trades: {v6_stats['n']}  PnL: ${v6_stats['pnl']:+,.2f} (${v6_stats['annual']:+,.2f}/yr)")
    print(f"  WR: {v6_stats['wr']}%  PF: {v6_stats['pf']}  MDD: {v6_mdd:.1f}%")
    print(f"  SafeNet: {v6_stats['sn_n']}x (${v6_stats['sn_pnl']:+,.2f})  Trail: {v6_stats['tr_n']}x (${v6_stats['tr_pnl']:+,.2f})")
    if len(v6_tdf) > 0:
        for s in ["long", "short"]:
            sub = v6_tdf[v6_tdf["side"] == s]
            if len(sub) > 0:
                print(f"  {s.upper()}: {len(sub)} trades, ${sub['pnl'].sum():+,.2f}, WR {(sub['pnl']>0).mean()*100:.1f}%")

    print(f"\n  --- C4 Solo (dynamic sizing, $10K) ---")
    print(f"  Trades: {c4_stats['n']}  PnL: ${c4_stats['pnl']:+,.2f} (${c4_stats['annual']:+,.2f}/yr)")
    print(f"  WR: {c4_stats['wr']}%  PF: {c4_stats['pf']}  MDD: {c4_mdd:.1f}%")
    print(f"  SafeNet: {c4_stats['sn_n']}x (${c4_stats['sn_pnl']:+,.2f})  Trail: {c4_stats['tr_n']}x (${c4_stats['tr_pnl']:+,.2f})")
    if len(c4_tdf) > 0:
        for s in ["long", "short"]:
            sub = c4_tdf[c4_tdf["side"] == s]
            if len(sub) > 0:
                print(f"  {s.upper()}: {len(sub)} trades, ${sub['pnl'].sum():+,.2f}, WR {(sub['pnl']>0).mean()*100:.1f}%")

    # ── 1.1 Same-bar entry overlap ──
    print(f"\n  [1.1] Same-bar entry overlap")
    v6_sig_bars = set(s[0] for s in v6_sigs)
    c4_sig_bars = set(s[0] for s in c4_sigs)
    overlap_bars = v6_sig_bars & c4_sig_bars
    min_sigs = min(len(v6_sig_bars), len(c4_sig_bars))
    overlap_rate = len(overlap_bars) / min_sigs * 100 if min_sigs > 0 else 0

    # Same direction overlap
    v6_sig_dict = {}
    for b, d in v6_sigs:
        v6_sig_dict[b] = d
    c4_sig_dict = {}
    for b, d in c4_sigs:
        c4_sig_dict[b] = d
    same_dir_overlap = sum(1 for b in overlap_bars if v6_sig_dict.get(b) == c4_sig_dict.get(b))
    opp_dir_overlap = len(overlap_bars) - same_dir_overlap

    print(f"    v6 signals: {len(v6_sig_bars)},  C4 signals: {len(c4_sig_bars)}")
    print(f"    Same-bar overlap: {len(overlap_bars)} bars ({overlap_rate:.1f}%)")
    print(f"      Same direction: {same_dir_overlap},  Opposite: {opp_dir_overlap}")

    # ── 1.2 Holding period overlap ──
    print(f"\n  [1.2] Holding period overlap")
    bt_mask = np.zeros(len(data), dtype=bool)
    for idx in range(len(data)):
        dt = data.iloc[idx]["datetime"]
        if BT_START <= dt < BT_END:
            bt_mask[idx] = True
    bt_bars = bt_mask.sum()

    v6_has_long = (v6_bl > 0) & bt_mask
    v6_has_short = (v6_bs > 0) & bt_mask
    c4_has_long = (c4_bl > 0) & bt_mask
    c4_has_short = (c4_bs > 0) & bt_mask

    both_long_bars = (v6_has_long & c4_has_long).sum()
    both_short_bars = (v6_has_short & c4_has_short).sum()
    any_overlap_bars = ((v6_has_long | v6_has_short) & (c4_has_long | c4_has_short)).sum()

    v6_hold_bars = (v6_has_long | v6_has_short).sum()
    c4_hold_bars = (c4_has_long | c4_has_short).sum()
    min_hold = min(v6_hold_bars, c4_hold_bars)
    hold_overlap_pct = any_overlap_bars / min_hold * 100 if min_hold > 0 else 0

    long_overlap_pct = both_long_bars / min(v6_has_long.sum(), c4_has_long.sum()) * 100 \
        if min(v6_has_long.sum(), c4_has_long.sum()) > 0 else 0
    short_overlap_pct = both_short_bars / min(v6_has_short.sum(), c4_has_short.sum()) * 100 \
        if min(v6_has_short.sum(), c4_has_short.sum()) > 0 else 0

    print(f"    v6 holding: {v6_hold_bars} bars ({v6_hold_bars/bt_bars*100:.1f}% of BT period)")
    print(f"    C4 holding: {c4_hold_bars} bars ({c4_hold_bars/bt_bars*100:.1f}% of BT period)")
    print(f"    Both long:  {both_long_bars} bars ({long_overlap_pct:.1f}% of min)")
    print(f"    Both short: {both_short_bars} bars ({short_overlap_pct:.1f}% of min)")
    print(f"    Any overlap: {any_overlap_bars} bars ({hold_overlap_pct:.1f}% of min)")

    # ── 1.3 Entry characteristics ──
    print(f"\n  [1.3] Entry characteristics comparison")
    print(f"    {'Feature':<25s} {'v6':>12s} {'C4':>12s}")
    print(f"    {'-'*49}")
    if len(v6_tdf) > 0 and len(c4_tdf) > 0:
        v6_long_n = len(v6_tdf[v6_tdf["side"]=="long"])
        v6_short_n = len(v6_tdf[v6_tdf["side"]=="short"])
        c4_long_n = len(c4_tdf[c4_tdf["side"]=="long"])
        c4_short_n = len(c4_tdf[c4_tdf["side"]=="short"])
        print(f"    {'Trades (total)':<25s} {v6_stats['n']:>12d} {c4_stats['n']:>12d}")
        print(f"    {'Long / Short':<25s} {f'{v6_long_n}/{v6_short_n}':>12s} {f'{c4_long_n}/{c4_short_n}':>12s}")
        print(f"    {'WR %':<25s} {v6_stats['wr']:>11.1f}% {c4_stats['wr']:>11.1f}%")
        print(f"    {'PF':<25s} {v6_stats['pf']:>12.2f} {c4_stats['pf']:>12.2f}")
        print(f"    {'Avg hold (bars)':<25s} {v6_stats['avg_bars']:>12.1f} {c4_stats['avg_bars']:>12.1f}")
        print(f"    {'SafeNet count':<25s} {v6_stats['sn_n']:>12d} {c4_stats['sn_n']:>12d}")
        v6a = f"${v6_stats['annual']:+,.0f}"; c4a = f"${c4_stats['annual']:+,.0f}"
        print(f"    {'Annual PnL':<25s} {v6a:>12s} {c4a:>12s}")

    # ── 1.4 Daily PnL correlation ──
    print(f"\n  [1.4] Daily PnL Pearson correlation")
    dates = pd.date_range(BT_START, BT_END - timedelta(days=1), freq='D')
    v6_daily = pd.Series(0.0, index=dates)
    c4_daily = pd.Series(0.0, index=dates)

    if len(v6_tdf) > 0:
        for _, t in v6_tdf.iterrows():
            d = pd.Timestamp(t["exit_dt"]).normalize()
            if d in v6_daily.index:
                v6_daily[d] += t["pnl"]
    if len(c4_tdf) > 0:
        for _, t in c4_tdf.iterrows():
            d = pd.Timestamp(t["exit_dt"]).normalize()
            if d in c4_daily.index:
                c4_daily[d] += t["pnl"]

    # Only compute on days where at least one strategy has trades
    active_mask = (v6_daily != 0) | (c4_daily != 0)
    if active_mask.sum() > 2:
        corr_all = v6_daily.corr(c4_daily)
        corr_active = v6_daily[active_mask].corr(c4_daily[active_mask])
    else:
        corr_all = 0; corr_active = 0

    print(f"    All days: {corr_all:.3f}")
    print(f"    Active days only: {corr_active:.3f}")
    if corr_all < 0.3:
        corr_label = "LOW (true diversification)"
    elif corr_all < 0.6:
        corr_label = "MEDIUM (partial diversification)"
    else:
        corr_label = "HIGH (effectively doubling down)"
    print(f"    Interpretation: {corr_label}")

    # ── 1.5 Overlap verdict ──
    print(f"\n  [1.5] Overlap Verdict")
    print(f"    Same-bar overlap: {overlap_rate:.1f}% {'< 20% OK' if overlap_rate < 20 else '>= 20% CAUTION' if overlap_rate < 40 else '>= 40% HIGH'}")
    print(f"    Daily corr: {corr_all:.3f} {'< 0.3 OK' if corr_all < 0.3 else '< 0.6 MEDIUM' if corr_all < 0.6 else '>= 0.6 HIGH'}")

    if overlap_rate < 20 and corr_all < 0.4:
        recommended = "P1"
        print(f"    --> Low overlap. Recommended: P1 (full parallel)")
    elif overlap_rate >= 40 or corr_all >= 0.6:
        recommended = "P3"
        print(f"    --> High overlap. Recommended: P3 (mutual exclusion)")
    else:
        recommended = "P2"
        print(f"    --> Medium overlap. Recommended: P2 (reduced risk)")

    # ================================================================
    # STEP 2: CAPITAL MANAGEMENT
    # ================================================================
    print(f"\n{SEP}")
    print(f"  STEP 2: Capital Management Selection")
    print(SEP)
    print(f"\n  Based on overlap analysis: {recommended}")
    print(f"  Running all 4 schemes for comparison...")

    # ================================================================
    # STEP 3: PARALLEL BACKTESTS
    # ================================================================
    print(f"\n{SEP}")
    print(f"  STEP 3: Parallel Backtests (P1-P4)")
    print(SEP)

    scheme_results = {}
    for scheme in ["P1", "P2", "P3", "P4"]:
        tdf, eq, mdd_pct, mdd_abs, fee_total, max_p, max_r, sharpe, dr = \
            run_parallel(data, scheme)

        if len(tdf) == 0:
            scheme_results[scheme] = None
            continue

        total_pnl = tdf["pnl"].sum()
        total_n = len(tdf)
        annual_pnl = total_pnl / 2

        v6_trades = tdf[tdf["source"].isin(["v6", "merged"])]
        c4_trades = tdf[tdf["source"] == "c4"]

        wr = (tdf["pnl"] > 0).mean() * 100
        w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
        pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999

        # IS/OOS split
        is_trades = tdf[tdf["entry_dt"].dt.year == 2023]
        oos_trades = tdf[tdf["entry_dt"].dt.year == 2024]
        is_pnl = is_trades["pnl"].sum() if len(is_trades) > 0 else 0
        oos_pnl = oos_trades["pnl"].sum() if len(oos_trades) > 0 else 0

        result = {
            "tdf": tdf, "equity": eq, "mdd_pct": mdd_pct, "mdd_abs": mdd_abs,
            "fees": fee_total, "max_pos": max_p, "max_risk_pct": max_r,
            "sharpe": sharpe, "total_pnl": total_pnl, "annual_pnl": annual_pnl,
            "total_n": total_n, "wr": wr, "pf": pf,
            "v6_n": len(v6_trades), "v6_pnl": v6_trades["pnl"].sum() if len(v6_trades) > 0 else 0,
            "c4_n": len(c4_trades), "c4_pnl": c4_trades["pnl"].sum() if len(c4_trades) > 0 else 0,
            "is_pnl": is_pnl, "oos_pnl": oos_pnl,
            "is_n": len(is_trades), "oos_n": len(oos_trades),
        }
        scheme_results[scheme] = result

        # Print scheme results
        desc = {"P1": "Full Parallel ($200 each)",
                "P2": "Reduced Risk ($150 each)",
                "P3": "Mutual Exclusion (v6 priority)",
                "P4": "Same-Dir Merge"}

        print(f"\n  {'~'*60}")
        print(f"  Scheme: {scheme} - {desc[scheme]}")
        print(f"  Period: 2023-01-01 ~ 2024-12-31")
        print(f"  {'~'*60}")
        print(f"  -- v6 contribution --")
        print(f"  Trades: {result['v6_n']}    PnL: ${result['v6_pnl']:+,.2f}")
        print(f"  -- C4 contribution --")
        print(f"  Trades: {result['c4_n']}    PnL: ${result['c4_pnl']:+,.2f}")
        print(f"  -- Combined --")
        print(f"  Total trades: {total_n} (avg {total_n/24:.1f}/month)")
        print(f"  Combined annual PnL: ${annual_pnl:+,.2f}")
        print(f"  Combined PF: {pf:.2f}")
        print(f"  Combined WR: {wr:.1f}%")
        print(f"  Combined MDD: {mdd_pct:.1f}% (${mdd_abs:,.2f})")
        print(f"  Sharpe ratio: {sharpe:.2f}")
        print(f"  Fees total: ${fee_total:,.2f}")
        print(f"  Max simultaneous positions: {max_p}")
        print(f"  Max risk exposure: {max_r:.1f}%")
        print(f"  Final equity: ${eq:,.2f}")

    # ================================================================
    # STEP 4: WALK-FORWARD
    # ================================================================
    print(f"\n{SEP}")
    print(f"  STEP 4: Walk-Forward Validation")
    print(SEP)

    for scheme in ["P1", "P2", "P3", "P4"]:
        r = scheme_results.get(scheme)
        if r is None: continue
        decay = (1 - r["oos_pnl"] / r["is_pnl"]) * 100 if r["is_pnl"] != 0 else 0
        print(f"\n  {scheme}:")
        print(f"    IS (2023): {r['is_n']} trades, ${r['is_pnl']:+,.2f}")
        print(f"    OOS(2024): {r['oos_n']} trades, ${r['oos_pnl']:+,.2f}")
        if r["is_pnl"] > 0:
            print(f"    OOS/IS decay: {decay:.0f}%")
        else:
            print(f"    IS negative, no decay calc")

    # ================================================================
    # STEP 5: VERDICT
    # ================================================================
    print(f"\n{SEP}")
    print(f"  STEP 5: Honest Verdict")
    print(SEP)

    print(f"\n  Target checklist:")
    print(f"  {'Metric':<35s} {'Target':>12s} | ", end="")
    for s in ["P1", "P2", "P3", "P4"]:
        print(f" {s:>10s}", end="")
    print()
    print(f"  {'-'*85}")

    metrics = [
        ("Annual PnL", ">= $5,000"),
        ("MDD", "<= 25%"),
        ("OOS PnL", "> $0"),
        ("Max risk exposure", "<= 6%"),
    ]

    for name, target in metrics:
        print(f"  {name:<35s} {target:>12s} | ", end="")
        for s in ["P1", "P2", "P3", "P4"]:
            r = scheme_results.get(s)
            if r is None:
                print(f" {'N/A':>10s}", end=""); continue
            if name == "Annual PnL":
                val = r["annual_pnl"]
                ok = val >= 5000
                print(f" {'$'+f'{val:+,.0f}':>10s}", end="")
            elif name == "MDD":
                val = r["mdd_pct"]
                ok = val <= 25
                print(f" {f'{val:.1f}%':>10s}", end="")
            elif name == "OOS PnL":
                val = r["oos_pnl"]
                ok = val > 0
                print(f" {'$'+f'{val:+,.0f}':>10s}", end="")
            elif name == "Max risk exposure":
                val = r["max_risk_pct"]
                ok = val <= 6
                print(f" {f'{val:.1f}%':>10s}", end="")
        print()

    # Best scheme
    best_scheme = None
    best_pnl = -999999
    for s in ["P1", "P2", "P3", "P4"]:
        r = scheme_results.get(s)
        if r is None: continue
        if r["annual_pnl"] > best_pnl and r["mdd_pct"] <= 25 and r["oos_pnl"] > 0:
            best_pnl = r["annual_pnl"]
            best_scheme = s

    # Check if any scheme meets ALL targets
    all_pass = False
    if best_scheme:
        r = scheme_results[best_scheme]
        all_pass = (r["annual_pnl"] >= 5000 and r["mdd_pct"] <= 25
                    and r["oos_pnl"] > 0 and r["max_risk_pct"] <= 6)

    print(f"\n  Best scheme: {best_scheme if best_scheme else 'None'}")

    if all_pass:
        r = scheme_results[best_scheme]
        print(f"\n  ALL TARGETS MET with {best_scheme}!")
        print(f"  Annual PnL: ${r['annual_pnl']:+,.2f}")
        print(f"  MDD: {r['mdd_pct']:.1f}%")
        print(f"  OOS: ${r['oos_pnl']:+,.2f}")
        print(f"  Max risk: {r['max_risk_pct']:.1f}%")
        print(f"\n  Implementation notes:")
        print(f"  - v6 runs on every 1h bar with full conditions (BB+Vol+Session)")
        print(f"  - C4 runs on every 1h bar with HA+MTF+RatioZ only")
        print(f"  - Both share SafeNet 3.5% + EMA20 trail (min 12h)")
        print(f"  - Pause C4 if v6 has persistent SafeNet losses (3+ consecutive)")
        print(f"  - Min account: $10,000 for this risk profile")
    else:
        print(f"\n  NOT ALL TARGETS MET.")
        # Identify which targets failed
        if best_scheme:
            r = scheme_results[best_scheme]
            if r["annual_pnl"] < 5000:
                print(f"  FAIL: Annual PnL ${r['annual_pnl']:+,.2f} < $5,000")
            if r["mdd_pct"] > 25:
                print(f"  FAIL: MDD {r['mdd_pct']:.1f}% > 25%")
            if r["oos_pnl"] <= 0:
                print(f"  FAIL: OOS PnL ${r['oos_pnl']:+,.2f} <= $0")
            if r["max_risk_pct"] > 6:
                print(f"  FAIL: Max risk {r['max_risk_pct']:.1f}% > 6%")
        else:
            print(f"  No scheme met basic MDD + OOS requirements.")

        print(f"\n  Root cause analysis:")
        # Sum simple addition
        simple_sum = v6_stats["annual"] + c4_stats["annual"]
        print(f"  v6 annual (solo): ${v6_stats['annual']:+,.2f}")
        print(f"  C4 annual (solo): ${c4_stats['annual']:+,.2f}")
        print(f"  Simple sum: ${simple_sum:+,.2f}")
        if best_scheme:
            r = scheme_results[best_scheme]
            print(f"  {best_scheme} actual: ${r['annual_pnl']:+,.2f}")
            synergy = r['annual_pnl'] - simple_sum
            print(f"  Synergy/drag: ${synergy:+,.2f}")

    # Monthly breakdown for best scheme
    if best_scheme and len(scheme_results[best_scheme]["tdf"]) > 0:
        tdf = scheme_results[best_scheme]["tdf"].copy()
        print(f"\n  Monthly PnL ({best_scheme}):")
        tdf["month"] = tdf["exit_dt"].dt.to_period("M")
        monthly = tdf.groupby("month").agg({"pnl": ["sum", "count"]})
        monthly.columns = ["pnl", "n"]
        for m, row in monthly.iterrows():
            bar = "+" * int(max(0, row["pnl"]) / 100) + "-" * int(max(0, -row["pnl"]) / 100)
            print(f"    {str(m):>8s}: {row['n']:>3.0f} trades  ${row['pnl']:>+9,.2f}  {bar}")

    print(f"\n{SEP}")
    print(f"  END OF ANALYSIS")
    print(SEP)
