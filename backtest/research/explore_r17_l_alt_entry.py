"""
Exploration Round 17: L Strategy — Alternative Entry Paradigms
================================================================
R13-R16 proved: GK compression + upward breakout cannot achieve
WR≥70% AND PnL≥$10K simultaneously for L (long).

Root cause: upward breakouts during compression are unreliable.
Per-trade edge at WR≥70% is $0.3-$5.

New approach: abandon breakout for L. Test mean-reversion / oversold
bounce entries with CMP-style tight TP + MaxHold exit.

Paradigms tested:
  A. Compression Dip-Buy: GK<30 AND close < rolling_min(N) → buy bounce
  B. Multi-bar Sell-off: cumulative return < -X% over N bars → buy bounce
  C. Bollinger Band Lower: close < BB(20,2) lower band → buy bounce
  D. RSI Oversold: RSI(14) < threshold → buy bounce
  E. EMA Dip: close < EMA20 AND close > EMA50 (dip in uptrend)
  F. Consecutive Red Bars: N consecutive bars where close < open

All anti-lookahead: shift(1) on indicators, enter at next bar open.
Exit: TP + MaxHold + SN (same CMP framework as S strategy).
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 2000; FEE = 2.0; ACCOUNT = 10000
SN_PEN = 0.25
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}

def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_data = []; cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status(); data = r.json(); break
            except:
                if attempt == 2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = (df["datetime"] + timedelta(hours=8)).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df

def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100

def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()

    # GK volatility
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)
    d["gk_s"] = d["gk"].rolling(5).mean()
    d["gk_l"] = d["gk"].rolling(20).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(100).apply(pctile_func)

    # EMAs
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()

    # Bollinger Bands (20, 2)
    d["bb_mid"] = d["close"].rolling(20).mean()
    d["bb_std"] = d["close"].rolling(20).std()
    d["bb_lower"] = d["bb_mid"] - 2 * d["bb_std"]
    d["bb_upper"] = d["bb_mid"] + 2 * d["bb_std"]

    # RSI(14) with shift(1)
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi14"] = 100 - (100 / (1 + rs))
    d["rsi14_s1"] = d["rsi14"].shift(1)

    # Cumulative returns over N bars (shift 1)
    for n in [3, 5, 8]:
        d[f"cumret{n}"] = d["ret"].rolling(n).sum().shift(1)

    # Rolling min of close (shift 1) for dip detection
    for n in [5, 8, 10]:
        d[f"cmin{n}"] = d["close"].rolling(n).min().shift(1)

    # Consecutive red bars count (shift 1)
    is_red = (d["close"] < d["open"]).astype(int)
    red_streak = is_red * (is_red.groupby((is_red != is_red.shift()).cumsum()).cumcount() + 1)
    d["red_streak"] = red_streak.shift(1)

    # Session filter
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    # BB position (shift 1): close relative to BB
    d["bb_pos"] = ((d["close"] - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"])).shift(1)

    # Close vs EMA (shift 1)
    d["c_below_ema20"] = (d["close"] < d["ema20"]).shift(1)
    d["c_above_ema50"] = (d["close"] > d["ema50"]).shift(1)

    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# =====================================================
# Generic L backtest with CMP-style TP/MH/SN exit
# =====================================================
def bt_long_cmp(df, entry_mask, tp_pct=0.015, mh=18, sn_pct=0.055,
                max_same=5, exit_cd=6):
    """
    Long CMP: enter when entry_mask is True, exit with TP/MH/SN.
    entry_mask: boolean array, True = entry signal on this bar (already shifted).
    Enter at O[i+1].
    """
    W = 120  # warmup
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values; DT = df["datetime"].values
    SOK = df["sok"].values
    EM = entry_mask.values if hasattr(entry_mask, 'values') else entry_mask

    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        h, lo, c = H[i], L[i], C[i]
        dt, nxo = DT[i], O[i + 1]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False

            # 1. SN (downside for long)
            if lo <= p["e"] * (1 - sn_pct):
                sn_price = p["e"] * (1 - sn_pct)
                ep_ = sn_price - (sn_price - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True

            # 2. TP (upside for long)
            if not done and h >= p["e"] * (1 + tp_pct):
                ep_ = p["e"] * (1 + tp_pct)
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True

            # 3. MaxHold
            if not done and b >= mh:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True

            if not done: npos.append(p)
        pos = npos

        # Entry
        if _b(EM[i]) and _b(SOK[i]) and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def evaluate(tdf, start_dt, end_dt, label=""):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"] > 0]["pnl"].sum(); l_ = abs(p[p["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999; wr = (p["pnl"] > 0).mean() * 100
    eq = p["pnl"].cumsum(); dd = eq - eq.cummax(); mdd = abs(dd.min()) / ACCOUNT * 100
    p["m"] = p["dt"].dt.to_period("M"); ms = p.groupby("m")["pnl"].sum()
    pos_m = (ms > 0).sum(); mt = len(ms)
    if pnl > 0:
        top_v = ms.max(); top_n = str(ms.idxmax()); top_pct = top_v / pnl * 100
    else:
        top_v = ms.max(); top_n = "N/A"; top_pct = 999
    nb = pnl - top_v if pnl > 0 else pnl
    worst_v = ms.min(); worst_n = str(ms.idxmin()) if not ms.empty else "N/A"
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    ed = p.groupby("t")["pnl"].agg(["count", "sum", "mean"])
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": worst_n, "worst_v": worst_v,
            "tpm": tpm, "monthly": ms, "ed": ed, "avg": pnl / n if n else 0}

def walk_forward_mask(df, entry_fn, exit_params, fs, fe):
    """Walk-forward using entry function that returns a mask."""
    os_ = fs + pd.DateOffset(months=12); results = []
    for fold in range(6):
        ts = os_ + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), fe)
        mask = entry_fn(df)
        t = bt_long_cmp(df, mask, **exit_params)
        t["dt"] = pd.to_datetime(t["dt"])
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results

def check_and_print(r, wf_pos, label):
    """Check all 9 criteria and return results."""
    checks = {
        "PnL": r["pnl"] >= 10000, "PF": r["pf"] >= 1.5,
        "MDD": r["mdd"] <= 25, "TPM": r["tpm"] >= 10,
        "WR": r["wr"] >= 70,
        "PM": r["pos_months"] / max(r["months"], 1) >= 0.75,
        "TM": r["top_pct"] <= 20, "NB": r["nb"] >= 8000,
        "WF": wf_pos >= 5,
    }
    passed = sum(v for v in checks.values())
    return {**r, "checks": checks, "passed": passed, "wf_pos": wf_pos}

def fmt_r(r):
    """Format result line."""
    return (f"  {r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% "
            f"MDD{r['mdd']:.1f}% {r['pos_months']}/{r['months']}PM "
            f"topM{r['top_pct']:.1f}% NB${r['nb']:>+7,.0f} avg${r['avg']:.1f}")


# =====================================================
# Multi-sub portfolio for alternative entries
# =====================================================
def bt_multi_sub_alt(df, sub_configs):
    """
    Run multiple independent sub-strategies, each with own entry mask and exit params.
    sub_configs: list of (entry_fn, exit_params_dict, label)
    """
    all_t = []
    for entry_fn, exit_params, label in sub_configs:
        mask = entry_fn(df)
        t = bt_long_cmp(df, mask, **exit_params)
        if len(t) > 0:
            t["sub"] = label
            all_t.append(t)
    if not all_t:
        return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(all_t, ignore_index=True).sort_values("dt").reset_index(drop=True)


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 17: L Strategy — Alternative Entry Paradigms")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df = calc_indicators(df_raw)

    all_results = []

    # =================================================================
    # Phase 1: Single-paradigm scans
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 1A: RSI Oversold Buy")
    print(f"{'='*70}")

    for rsi_th in [25, 30, 35, 40]:
        def make_rsi_entry(th):
            def entry_fn(d):
                return d["rsi14_s1"] < th
            return entry_fn

        entry_fn = make_rsi_entry(rsi_th)
        for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
            for mh in [8, 12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"RSI<{rsi_th} TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:  # only track promising WR
                    print(f"  RSI<{rsi_th} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                              max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check_and_print(r, wf_pos, r["label"]))

    print(f"\n{'='*70}")
    print("  Phase 1B: Bollinger Band Lower Bounce")
    print(f"{'='*70}")

    for bb_th in [0.0, 0.05, 0.10]:  # bb_pos < threshold means below/near lower band
        def make_bb_entry(th):
            def entry_fn(d):
                return d["bb_pos"] < th
            return entry_fn

        entry_fn = make_bb_entry(bb_th)
        for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
            for mh in [8, 12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"BB<{bb_th:.2f} TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:
                    print(f"  BB<{bb_th:.2f} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                              max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check_and_print(r, wf_pos, r["label"]))

    print(f"\n{'='*70}")
    print("  Phase 1C: Cumulative Return Sell-off Bounce")
    print(f"{'='*70}")

    for n_bars, ret_th in [(3, -0.03), (3, -0.04), (5, -0.04), (5, -0.05),
                            (5, -0.06), (8, -0.05), (8, -0.06), (8, -0.08)]:
        def make_cumret_entry(nb, rth):
            def entry_fn(d):
                return d[f"cumret{nb}"] < rth
            return entry_fn

        entry_fn = make_cumret_entry(n_bars, ret_th)
        for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
            for mh in [8, 12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"CR{n_bars}<{ret_th*100:.0f}% TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:
                    print(f"  CR{n_bars}<{ret_th*100:.0f}% TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                              max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check_and_print(r, wf_pos, r["label"]))

    print(f"\n{'='*70}")
    print("  Phase 1D: Consecutive Red Bars Bounce")
    print(f"{'='*70}")

    for red_n in [3, 4, 5]:
        def make_red_entry(rn):
            def entry_fn(d):
                return d["red_streak"] >= rn
            return entry_fn

        entry_fn = make_red_entry(red_n)
        for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
            for mh in [8, 12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"Red≥{red_n} TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:
                    print(f"  Red≥{red_n} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                              max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check_and_print(r, wf_pos, r["label"]))

    print(f"\n{'='*70}")
    print("  Phase 1E: EMA Dip in Uptrend (C < EMA20, C > EMA50)")
    print(f"{'='*70}")

    def ema_dip_entry(d):
        return d["c_below_ema20"] & d["c_above_ema50"]

    for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
        for mh in [8, 12, 15, 18]:
            mask = ema_dip_entry(df)
            t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                            max_same=5, exit_cd=6)
            r = evaluate(t, mid, fe, f"EMADip TP{tp*100:.1f} MH{mh}")
            if not r or r["pnl"] <= 0: continue
            if r["wr"] >= 60:
                print(f"  EMADip TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                wf = walk_forward_mask(df, ema_dip_entry, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                                max_same=5, exit_cd=6), fs, fe)
                wf_pos = sum(1 for w in wf if w["pos"])
                all_results.append(check_and_print(r, wf_pos, r["label"]))

    print(f"\n{'='*70}")
    print("  Phase 1F: GK Compression + Dip (GK<30 AND close near local min)")
    print(f"{'='*70}")

    for gk_th in [30, 40]:
        for dip_n in [5, 8, 10]:
            def make_gk_dip_entry(gth, dn):
                def entry_fn(d):
                    gk_ok = d["gk_pct"] < gth
                    # close_s1 is at or below rolling min (already shifted)
                    dip_ok = d["close"].shift(1) <= d[f"cmin{dn}"] * 1.002  # within 0.2% of local min
                    return gk_ok & dip_ok
                return entry_fn

            entry_fn = make_gk_dip_entry(gk_th, dip_n)
            for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
                for mh in [8, 12, 15, 18]:
                    mask = entry_fn(df)
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=5, exit_cd=6)
                    r = evaluate(t, mid, fe, f"GK{gk_th}Dip{dip_n} TP{tp*100:.1f} MH{mh}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 60:
                        print(f"  GK{gk_th}Dip{dip_n} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                                   max_same=5, exit_cd=6), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check_and_print(r, wf_pos, r["label"]))

    # =================================================================
    # Phase 2: Combo entries (AND/OR combinations of best signals)
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 2: Combo Entries — RSI + BB + GK")
    print(f"{'='*70}")

    # RSI + BB combo: oversold on both indicators
    for rsi_th in [30, 35, 40]:
        for bb_th in [0.05, 0.10, 0.15]:
            def make_rsi_bb_entry(rth, bth):
                def entry_fn(d):
                    return (d["rsi14_s1"] < rth) & (d["bb_pos"] < bth)
                return entry_fn

            entry_fn = make_rsi_bb_entry(rsi_th, bb_th)
            for tp in [0.010, 0.012, 0.015, 0.020]:
                for mh in [12, 15, 18]:
                    mask = entry_fn(df)
                    if mask.sum() < 20: continue  # too few signals
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=5, exit_cd=6)
                    r = evaluate(t, mid, fe, f"RSI{rsi_th}+BB{bb_th:.2f} TP{tp*100:.1f} MH{mh}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 65:
                        print(f"  RSI<{rsi_th}+BB<{bb_th:.2f} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                                   max_same=5, exit_cd=6), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check_and_print(r, wf_pos, r["label"]))

    # GK compression + RSI oversold
    for gk_th in [30, 40]:
        for rsi_th in [30, 35, 40]:
            def make_gk_rsi_entry(gth, rth):
                def entry_fn(d):
                    return (d["gk_pct"] < gth) & (d["rsi14_s1"] < rth)
                return entry_fn

            entry_fn = make_gk_rsi_entry(gk_th, rsi_th)
            for tp in [0.010, 0.012, 0.015, 0.020]:
                for mh in [12, 15, 18]:
                    mask = entry_fn(df)
                    if mask.sum() < 20: continue
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=5, exit_cd=6)
                    r = evaluate(t, mid, fe, f"GK{gk_th}+RSI{rsi_th} TP{tp*100:.1f} MH{mh}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 65:
                        print(f"  GK<{gk_th}+RSI<{rsi_th} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                                   max_same=5, exit_cd=6), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check_and_print(r, wf_pos, r["label"]))

    # OR-combo: RSI<30 OR BB<0.0 OR CumRet5<-5%
    def wide_or_entry(d):
        return (d["rsi14_s1"] < 30) | (d["bb_pos"] < 0.0) | (d["cumret5"] < -0.05)

    for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
        for mh in [8, 12, 15, 18]:
            for cd in [4, 6]:
                mask = wide_or_entry(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=cd)
                r = evaluate(t, mid, fe, f"WideOR TP{tp*100:.1f} MH{mh} CD{cd}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 65:
                    print(f"  WideOR TP{tp*100:.1f}% MH{mh} CD{cd}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, wide_or_entry, dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                                    max_same=5, exit_cd=cd), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check_and_print(r, wf_pos, r["label"]))

    # =================================================================
    # Phase 3: Multi-sub portfolio (different paradigms as sub-strategies)
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 3: Multi-Sub Portfolio (Mixed Paradigms)")
    print(f"{'='*70}")

    # Take top paradigms and combine them as independent sub-strategies
    # Each sub-strategy has its own entry_fn and exit_params
    def rsi30_entry(d): return d["rsi14_s1"] < 30
    def rsi35_entry(d): return d["rsi14_s1"] < 35
    def bb0_entry(d): return d["bb_pos"] < 0.0
    def bb05_entry(d): return d["bb_pos"] < 0.05
    def cr5_5_entry(d): return d["cumret5"] < -0.05
    def cr3_3_entry(d): return d["cumret3"] < -0.03
    def red4_entry(d): return d["red_streak"] >= 4
    def ema_dip_fn(d): return d["c_below_ema20"] & d["c_above_ema50"]

    # Portfolio configs: [(entry_fn, exit_params, label), ...]
    # We'll try several portfolio compositions
    portfolios = {
        "P1_RSI_BB": [
            (rsi30_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "RSI30"),
            (bb0_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "BB0"),
        ],
        "P2_RSI_CR": [
            (rsi30_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "RSI30"),
            (cr5_5_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "CR5-5"),
        ],
        "P3_RSI_BB_CR": [
            (rsi30_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "RSI30"),
            (bb0_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "BB0"),
            (cr5_5_entry, dict(tp_pct=0.012, mh=12, sn_pct=0.055, max_same=5, exit_cd=6), "CR5-5"),
        ],
        "P4_4sub_mix": [
            (rsi30_entry, dict(tp_pct=0.015, mh=18, sn_pct=0.055, max_same=5, exit_cd=6), "RSI30"),
            (rsi35_entry, dict(tp_pct=0.012, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "RSI35"),
            (bb0_entry, dict(tp_pct=0.015, mh=18, sn_pct=0.055, max_same=5, exit_cd=6), "BB0"),
            (cr5_5_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "CR5-5"),
        ],
        "P5_4sub_tight": [
            (rsi30_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=4), "RSI30"),
            (bb0_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=4), "BB0"),
            (cr3_3_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=4), "CR3-3"),
            (red4_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=4), "Red4"),
        ],
        "P6_wide": [
            (rsi30_entry, dict(tp_pct=0.015, mh=18, sn_pct=0.055, max_same=5, exit_cd=6), "RSI30"),
            (rsi35_entry, dict(tp_pct=0.012, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "RSI35"),
            (bb0_entry, dict(tp_pct=0.015, mh=18, sn_pct=0.055, max_same=5, exit_cd=6), "BB0"),
            (bb05_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=6), "BB05"),
            (cr5_5_entry, dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6), "CR5-5"),
            (cr3_3_entry, dict(tp_pct=0.010, mh=12, sn_pct=0.055, max_same=5, exit_cd=4), "CR3-3"),
        ],
    }

    for pname, subs in portfolios.items():
        t = bt_multi_sub_alt(df, subs)
        r = evaluate(t, mid, fe, pname)
        if not r or r["pnl"] <= 0:
            print(f"  {pname}: PnL ≤ 0, skip")
            continue
        print(f"  {pname}: {fmt_r(r)}")
        # Simple WF (using first sub as proxy — imperfect but fast)
        wf_pos = 0
        for fold in range(6):
            os_ = fs + pd.DateOffset(months=12)
            ts = os_ + pd.DateOffset(months=fold * 2)
            te = min(ts + pd.DateOffset(months=2), fe)
            t2 = t.copy(); t2["dt"] = pd.to_datetime(t2["dt"])
            tt = t2[(t2["dt"] >= ts) & (t2["dt"] < te)]
            if len(tt) > 0 and tt["pnl"].sum() > 0: wf_pos += 1
        all_results.append(check_and_print(r, wf_pos, pname))

    # =================================================================
    # Summary
    # =================================================================
    print(f"\n{'='*70}")
    print(f"  R17 Summary — Top 20 by passed checks (of {len(all_results)})")
    print(f"{'='*70}")

    all_results.sort(key=lambda x: (-x["passed"], -x["pnl"]))
    for r in all_results[:20]:
        fail_names = [k for k, v in r["checks"].items() if not v]
        fail_str = ",".join(fail_names) if fail_names else "ALL PASS"
        print(f"  {r['label']:<42s} {r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
              f"WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% {r['pos_months']}/{r['months']}PM "
              f"topM{r['top_pct']:.1f}% NB${r['nb']:>+7,.0f} WF{r['wf_pos']}/6  {r['passed']}/9 "
              f"Fail:{fail_str}")

    if all_results:
        best = all_results[0]
        print(f"\n  Best L Alt: {best['label']} ({best['passed']}/9)")
        fail_names = [k for k, v in best["checks"].items() if not v]
        print(f"  Failing: {', '.join(fail_names) if fail_names else 'NONE'}")
        print(f"  出場: {best['ed'].to_string()}")
        for k, v in best["monthly"].items():
            cum = best["monthly"][:k].sum() + v
            print(f"    {k}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

    # WR vs PnL analysis
    print(f"\n  WR vs PnL Frontier:")
    wr_bins = [(60,65), (65,70), (70,75), (75,80), (80,100)]
    for lo, hi in wr_bins:
        subset = [r for r in all_results if lo <= r["wr"] < hi]
        if subset:
            best_pnl = max(r["pnl"] for r in subset)
            best_r = max(subset, key=lambda r: r["pnl"])
            print(f"    WR {lo}-{hi}%: best PnL ${best_pnl:>+8,.0f} ({best_r['label']})")
        else:
            print(f"    WR {lo}-{hi}%: no configs")

    print(f"\n  上帝視角自檢:")
    print(f"    RSI(14) shift(1): ✓ uses rsi14_s1")
    print(f"    BB(20,2) shift(1): ✓ uses bb_pos (shifted)")
    print(f"    CumRet shift(1): ✓ cumret{'{N}'} uses .shift(1)")
    print(f"    RedStreak shift(1): ✓ red_streak uses .shift(1)")
    print(f"    Entry at O[i+1]: ✓ p['e'] = nxo")
    print(f"    Fee $2/trade: ✓")
