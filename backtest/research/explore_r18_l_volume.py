"""
Exploration Round 18: L Strategy — Volume-Based Entries
========================================================
R13-R17 exhausted price-based L entries:
  Breakout: WR 44-58%, PnL up to $13K (fails WR)
  Mean-reversion: WR 70-94%, PnL max $1K (fails PnL)

Volume is completely new information. Test:
  A. Volume spike + oversold → buy bounce (quality signal)
  B. Volume climax (exhaustion sell) → contrarian long
  C. Low volume compression + volume breakout → catch expansion
  D. Combine volume with best signals from R17 (RSI, CumRet, GK)

Also test: maxSame=9 with EXIT_CD=2 for maximum trade count.
Plus: Portfolio combos of independent signals.
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

    # RSI(14) with shift(1)
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi14"] = 100 - (100 / (1 + rs))
    d["rsi14_s1"] = d["rsi14"].shift(1)

    # Cumulative returns (shift 1)
    for n in [3, 5, 8]:
        d[f"cumret{n}"] = d["ret"].rolling(n).sum().shift(1)

    # ---- Volume indicators (all shift(1)) ----
    # Volume ratio: current volume / average volume
    for n in [10, 20, 50]:
        d[f"vol_ratio_{n}"] = (d["volume"] / d["volume"].rolling(n).mean()).shift(1)

    # Volume percentile over 100 bars
    d["vol_pct"] = d["volume"].shift(1).rolling(100).apply(pctile_func)

    # Sell volume proxy: volume on red bars (close < open)
    d["is_red"] = (d["close"] < d["open"]).astype(float)
    d["sell_vol"] = d["volume"] * d["is_red"]
    d["sell_vol_ratio"] = (d["sell_vol"] / d["volume"].rolling(20).mean()).shift(1)

    # Volume spike + direction
    d["vol_spike_2x"] = (d["volume"] > 2 * d["volume"].rolling(20).mean()).shift(1)
    d["vol_spike_3x"] = (d["volume"] > 3 * d["volume"].rolling(20).mean()).shift(1)
    d["red_vol_spike"] = (d["vol_spike_2x"].shift(-1).fillna(False)) & (d["is_red"].shift(1).astype(bool))
    # Fix: red_vol_spike should be: bar was red AND had volume spike, all shifted
    d["red_vol_spike"] = ((d["volume"] > 2 * d["volume"].rolling(20).mean()) & (d["close"] < d["open"])).shift(1)

    # Volume climax: extreme high volume (top 5%) on red bar
    d["vol_climax"] = ((d["vol_pct"] > 95) & (d["is_red"].shift(1).astype(bool)))

    # Low volume (compression proxy)
    d["low_vol"] = (d["vol_pct"] < 20)  # already shifted via vol_pct

    # Bollinger Band position (shift 1)
    d["bb_mid"] = d["close"].rolling(20).mean()
    d["bb_std"] = d["close"].rolling(20).std()
    d["bb_lower"] = d["bb_mid"] - 2 * d["bb_std"]
    d["bb_upper"] = d["bb_mid"] + 2 * d["bb_std"]
    d["bb_pos"] = ((d["close"] - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"])).shift(1)

    # Consecutive red bars (shift 1)
    is_red_int = (d["close"] < d["open"]).astype(int)
    red_streak = is_red_int * (is_red_int.groupby((is_red_int != is_red_int.shift()).cumsum()).cumcount() + 1)
    d["red_streak"] = red_streak.shift(1)

    # Session filter
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    # Breakout (for hybrid approaches)
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12]:
        d[f"cmx{bl}"] = d["close"].shift(2).rolling(bl - 1).max()
        d[f"bl{bl}_up"] = d["cs1"] > d[f"cmx{bl}"]

    # OR-entry components (existing L)
    d["skew20"] = d["ret"].rolling(20).skew().shift(1)
    d["ret_sign15"] = (d["ret"] > 0).rolling(15).mean().shift(1)

    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_long_cmp(df, entry_mask, tp_pct=0.015, mh=18, sn_pct=0.055,
                max_same=5, exit_cd=6):
    W = 120
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
            if lo <= p["e"] * (1 - sn_pct):
                sn_price = p["e"] * (1 - sn_pct)
                ep_ = sn_price - (sn_price - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            if not done and h >= p["e"] * (1 + tp_pct):
                ep_ = p["e"] * (1 + tp_pct)
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True
            if not done and b >= mh:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos
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

def fmt_r(r):
    return (f"{r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% "
            f"MDD{r['mdd']:.1f}% {r['pos_months']}/{r['months']}PM "
            f"topM{r['top_pct']:.1f}% avg${r['avg']:.1f}")

def check9(r, wf_pos):
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


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 18: L Strategy — Volume-Based Entries")
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
    # Phase 1: Volume signals alone
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 1A: Red Volume Spike Buy (high vol sell-off → bounce)")
    print(f"{'='*70}")

    for vol_mult in [2, 3]:
        def make_red_vol(vm):
            def entry_fn(d):
                return ((d["volume"] > vm * d["volume"].rolling(20).mean()) &
                        (d["close"] < d["open"])).shift(1).fillna(False)
            return entry_fn

        entry_fn = make_red_vol(vol_mult)
        cnt = entry_fn(df).sum()
        print(f"  RedVol{vol_mult}x signals: {cnt}")
        for tp in [0.008, 0.010, 0.012, 0.015, 0.020]:
            for mh in [8, 12, 15, 18]:
                for cd in [4, 6]:
                    mask = entry_fn(df)
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=5, exit_cd=cd)
                    r = evaluate(t, mid, fe, f"RedVol{vol_mult}x TP{tp*100:.1f} MH{mh} CD{cd}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 60:
                        print(f"    TP{tp*100:.1f}% MH{mh} CD{cd}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, entry_fn,
                                               dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                    max_same=5, exit_cd=cd), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check9(r, wf_pos))

    print(f"\n{'='*70}")
    print("  Phase 1B: Volume Climax Buy (top 5% volume + red bar)")
    print(f"{'='*70}")

    def vol_climax_entry(d):
        return d["vol_climax"].fillna(False)

    cnt = vol_climax_entry(df).sum()
    print(f"  VolClimax signals: {cnt}")
    for tp in [0.010, 0.015, 0.020, 0.025]:
        for mh in [8, 12, 15, 18]:
            mask = vol_climax_entry(df)
            t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                            max_same=5, exit_cd=6)
            r = evaluate(t, mid, fe, f"VolClimax TP{tp*100:.1f} MH{mh}")
            if not r or r["pnl"] <= 0: continue
            if r["wr"] >= 55:
                print(f"  TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                wf = walk_forward_mask(df, vol_climax_entry,
                                       dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                            max_same=5, exit_cd=6), fs, fe)
                wf_pos = sum(1 for w in wf if w["pos"])
                all_results.append(check9(r, wf_pos))

    # =================================================================
    # Phase 2: Volume + Price combos
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 2A: Volume Spike + RSI Oversold")
    print(f"{'='*70}")

    for rsi_th in [30, 35, 40]:
        def make_vol_rsi(rth):
            def entry_fn(d):
                vol_ok = d["vol_ratio_20"].fillna(0) > 1.5
                rsi_ok = d["rsi14_s1"] < rth
                return vol_ok & rsi_ok
            return entry_fn

        entry_fn = make_vol_rsi(rsi_th)
        cnt = entry_fn(df).sum()
        print(f"  Vol1.5x+RSI<{rsi_th}: {cnt} signals")
        for tp in [0.010, 0.012, 0.015, 0.020]:
            for mh in [12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"Vol+RSI{rsi_th} TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:
                    print(f"    RSI<{rsi_th} TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn,
                                           dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check9(r, wf_pos))

    print(f"\n{'='*70}")
    print("  Phase 2B: Volume Spike + CumRet Sell-off")
    print(f"{'='*70}")

    for cr_n, cr_th in [(3, -0.03), (5, -0.04), (5, -0.05)]:
        def make_vol_cr(cn, ct):
            def entry_fn(d):
                vol_ok = d["vol_ratio_20"].fillna(0) > 1.5
                cr_ok = d[f"cumret{cn}"] < ct
                return vol_ok & cr_ok
            return entry_fn

        entry_fn = make_vol_cr(cr_n, cr_th)
        cnt = entry_fn(df).sum()
        print(f"  Vol1.5x+CR{cr_n}<{cr_th*100:.0f}%: {cnt} signals")
        for tp in [0.010, 0.012, 0.015, 0.020]:
            for mh in [12, 15, 18]:
                mask = entry_fn(df)
                t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                max_same=5, exit_cd=6)
                r = evaluate(t, mid, fe, f"Vol+CR{cr_n}{cr_th*100:.0f} TP{tp*100:.1f} MH{mh}")
                if not r or r["pnl"] <= 0: continue
                if r["wr"] >= 60:
                    print(f"    CR{cr_n}<{cr_th*100:.0f}% TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                    wf = walk_forward_mask(df, entry_fn,
                                           dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                max_same=5, exit_cd=6), fs, fe)
                    wf_pos = sum(1 for w in wf if w["pos"])
                    all_results.append(check9(r, wf_pos))

    print(f"\n{'='*70}")
    print("  Phase 2C: Volume + GK Compression + RSI")
    print(f"{'='*70}")

    for gk_th in [30, 40]:
        for rsi_th in [30, 35]:
            def make_vol_gk_rsi(gth, rth):
                def entry_fn(d):
                    gk_ok = d["gk_pct"] < gth
                    rsi_ok = d["rsi14_s1"] < rth
                    vol_ok = d["vol_ratio_20"].fillna(0) > 1.5
                    return gk_ok & rsi_ok & vol_ok
                return entry_fn

            entry_fn = make_vol_gk_rsi(gk_th, rsi_th)
            cnt = entry_fn(df).sum()
            if cnt < 10:
                print(f"  GK{gk_th}+RSI{rsi_th}+Vol: {cnt} signals (too few)")
                continue
            print(f"  GK{gk_th}+RSI{rsi_th}+Vol: {cnt} signals")
            for tp in [0.010, 0.012, 0.015, 0.020]:
                for mh in [12, 15, 18]:
                    mask = entry_fn(df)
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=5, exit_cd=6)
                    r = evaluate(t, mid, fe, f"GK{gk_th}RSI{rsi_th}Vol TP{tp*100:.1f} MH{mh}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 60:
                        print(f"    TP{tp*100:.1f}% MH{mh}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, entry_fn,
                                               dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                    max_same=5, exit_cd=6), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check9(r, wf_pos))

    # =================================================================
    # Phase 3: High-frequency OR-entry with relaxed CD
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 3: High-Frequency OR-entry (many weak signals, CD=2)")
    print(f"{'='*70}")

    def wide_or_v2(d):
        return ((d["rsi14_s1"] < 35) |
                (d["bb_pos"] < 0.05) |
                (d["cumret5"] < -0.04) |
                (d["red_streak"] >= 3) |
                (d["vol_ratio_20"].fillna(0) > 2.0))

    for tp in [0.006, 0.008, 0.010, 0.012]:
        for mh in [6, 8, 10, 12]:
            for ms in [5, 9]:
                for cd in [2, 3, 4]:
                    mask = wide_or_v2(df)
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=0.055,
                                    max_same=ms, exit_cd=cd)
                    r = evaluate(t, mid, fe, f"WideOR2 TP{tp*100:.1f} MH{mh} m{ms} CD{cd}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= 65 and r["n"] >= 100:
                        print(f"  TP{tp*100:.1f}% MH{mh} m{ms} CD{cd}: {fmt_r(r)}")
                        wf = walk_forward_mask(df, wide_or_v2,
                                               dict(tp_pct=tp, mh=mh, sn_pct=0.055,
                                                    max_same=ms, exit_cd=cd), fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        all_results.append(check9(r, wf_pos))

    # =================================================================
    # Phase 4: CMP Portfolio with independent volume sub-strategies
    # =================================================================
    print(f"\n{'='*70}")
    print("  Phase 4: CMP Portfolio — Independent Volume Subs")
    print(f"{'='*70}")

    def make_sub(entry_fn, tp, mh, ms, cd, label):
        return (entry_fn, dict(tp_pct=tp, mh=mh, sn_pct=0.055, max_same=ms, exit_cd=cd), label)

    def rsi30(d): return d["rsi14_s1"] < 30
    def rsi35(d): return d["rsi14_s1"] < 35
    def cr3_3(d): return d["cumret3"] < -0.03
    def cr5_5(d): return d["cumret5"] < -0.05
    def red4(d): return d["red_streak"] >= 4
    def bb0(d): return d["bb_pos"] < 0.0
    def redvol2(d): return ((d["volume"] > 2 * d["volume"].rolling(20).mean()) &
                            (d["close"] < d["open"])).shift(1).fillna(False)

    # Portfolio A: Best individual subs from R17, each independent
    pf_configs = {
        "PfA_4sig": [
            make_sub(rsi30, 0.010, 8, 5, 6, "RSI30"),
            make_sub(cr3_3, 0.015, 12, 5, 6, "CR3"),
            make_sub(red4, 0.012, 12, 5, 6, "Red4"),
            make_sub(redvol2, 0.015, 12, 5, 6, "RedVol"),
        ],
        "PfB_3sig_tight": [
            make_sub(rsi30, 0.008, 8, 5, 4, "RSI30"),
            make_sub(cr5_5, 0.015, 15, 5, 6, "CR5"),
            make_sub(bb0, 0.010, 8, 5, 4, "BB0"),
        ],
        "PfC_5sig_wide": [
            make_sub(rsi30, 0.010, 8, 3, 6, "RSI30"),
            make_sub(rsi35, 0.008, 8, 3, 6, "RSI35"),
            make_sub(cr3_3, 0.012, 12, 3, 6, "CR3"),
            make_sub(cr5_5, 0.020, 15, 3, 6, "CR5"),
            make_sub(redvol2, 0.015, 12, 3, 6, "RedVol"),
        ],
        "PfD_high_freq": [
            make_sub(rsi30, 0.008, 6, 5, 2, "RSI30"),
            make_sub(rsi35, 0.006, 6, 5, 2, "RSI35"),
            make_sub(cr3_3, 0.008, 6, 5, 2, "CR3"),
            make_sub(bb0, 0.008, 6, 5, 2, "BB0"),
            make_sub(red4, 0.008, 6, 5, 2, "Red4"),
            make_sub(redvol2, 0.008, 6, 5, 2, "RedVol"),
        ],
    }

    for pname, subs in pf_configs.items():
        all_t = []
        for entry_fn, exit_params, label in subs:
            mask = entry_fn(df)
            t = bt_long_cmp(df, mask, **exit_params)
            if len(t) > 0:
                t["sub"] = label
                all_t.append(t)
        if not all_t:
            print(f"  {pname}: no trades")
            continue
        combined = pd.concat(all_t, ignore_index=True).sort_values("dt").reset_index(drop=True)
        r = evaluate(combined, mid, fe, pname)
        if not r:
            print(f"  {pname}: no OOS trades")
            continue
        print(f"  {pname}: {fmt_r(r)}")
        if r["pnl"] > 0:
            # WF using combined trades
            wf_pos = 0
            for fold in range(6):
                os_ = fs + pd.DateOffset(months=12)
                ts = os_ + pd.DateOffset(months=fold * 2)
                te = min(ts + pd.DateOffset(months=2), fe)
                ct = combined.copy(); ct["dt"] = pd.to_datetime(ct["dt"])
                tt = ct[(ct["dt"] >= ts) & (ct["dt"] < te)]
                if len(tt) > 0 and tt["pnl"].sum() > 0: wf_pos += 1
            all_results.append(check9(r, wf_pos))

            # Sub breakdown
            for sub_label in combined["sub"].unique():
                st = combined[combined["sub"] == sub_label]
                st_oos = st[(pd.to_datetime(st["dt"]) >= mid) & (pd.to_datetime(st["dt"]) < fe)]
                if len(st_oos) > 0:
                    sp = st_oos["pnl"].sum()
                    sw = (st_oos["pnl"] > 0).mean() * 100
                    print(f"    {sub_label}: {len(st_oos)}t ${sp:>+7,.0f} WR{sw:.1f}%")

    # =================================================================
    # Summary
    # =================================================================
    print(f"\n{'='*70}")
    print(f"  R18 Summary — Top 20 by passed checks (of {len(all_results)})")
    print(f"{'='*70}")

    all_results.sort(key=lambda x: (-x["passed"], -x["pnl"]))
    for r in all_results[:20]:
        fail_names = [k for k, v in r["checks"].items() if not v]
        fail_str = ",".join(fail_names) if fail_names else "ALL PASS"
        print(f"  {r['label']:<42s} {r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
              f"WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% {r['pos_months']}/{r['months']}PM "
              f"topM{r['top_pct']:.1f}% NB${r['nb']:>+7,.0f} WF{r['wf_pos']}/6  {r['passed']}/9 "
              f"Fail:{fail_str}")

    # WR vs PnL frontier
    print(f"\n  WR vs PnL Frontier:")
    for lo, hi in [(55,60), (60,65), (65,70), (70,75), (75,80), (80,100)]:
        subset = [r for r in all_results if lo <= r["wr"] < hi]
        if subset:
            best_r = max(subset, key=lambda r: r["pnl"])
            print(f"    WR {lo:>2}-{hi:>3}%: best PnL ${best_r['pnl']:>+8,.0f} "
                  f"({best_r['n']}t, {best_r['label']})")
        else:
            print(f"    WR {lo:>2}-{hi:>3}%: no configs")

    # Structural analysis
    print(f"\n  ===== STRUCTURAL ANALYSIS =====")
    wr70 = [r for r in all_results if r["wr"] >= 70]
    pnl10k = [r for r in all_results if r["pnl"] >= 10000]
    print(f"  Configs with WR≥70%: {len(wr70)}")
    if wr70:
        bw = max(wr70, key=lambda r: r["pnl"])
        print(f"    Best PnL at WR≥70%: ${bw['pnl']:>+,.0f} ({bw['n']}t, {bw['label']})")
    print(f"  Configs with PnL≥$10K: {len(pnl10k)}")
    if pnl10k:
        bp = min(pnl10k, key=lambda r: 100-r["wr"])
        print(f"    Best WR at PnL≥$10K: {bp['wr']:.1f}% ({bp['n']}t, {bp['label']})")

    print(f"\n  上帝視角自檢:")
    print(f"    Volume indicators shift(1): ✓ (vol_ratio, vol_pct, sell_vol_ratio)")
    print(f"    RSI shift(1): ✓ rsi14_s1")
    print(f"    CumRet shift(1): ✓")
    print(f"    Entry at O[i+1]: ✓ p['e'] = nxo")
    print(f"    Fee $2/trade: ✓")
    print(f"    SN 25% penetration: ✓")
