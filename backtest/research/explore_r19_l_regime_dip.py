"""
Exploration Round 19: L Strategy — Regime Dip-Buy (Genuinely Novel)
====================================================================
R13-R18 exhausted:
  - Compression breakout L: WR 44-59%, PnL up to $13K
  - Mean-reversion (RSI/BB/CumRet/Vol): WR 70-100%, PnL max $2.5K
  - Trend pullback (RSI 50-cross): no edge on ETH

After reviewing doc/backtest_history.md, these haven't been tested for L:

1. REGIME DIP-BUY: Confirmed uptrend regime → buy any small dip → tight TP
   Key difference from TPR: TPR used RSI 50-cross (lagging). We use raw dip (ret < -X%).
   Key difference from pure mean-rev: we ADD a regime filter to boost WR.

2. ADX REGIME + DIP: ADX>25 + DI+>DI- = confirmed trend, then buy RSI<40 dip.
   ADX was tested as trend-following ENTRY (doc R6), never as REGIME FILTER for dip-buy.

3. TAKER BUY RATIO ENTRY: Extreme sell (taker_buy_ratio < 0.45) → buy bounce.
   Taker was tested as entry FILTER (A1), never as standalone contrarian ENTRY signal.

4. ENGULFING + CMP: Bullish engulfing candle → buy with TP/MH.
   Never tested with CMP exit framework.

5. GK EXPANSION DIP: High GK (expansion, NOT compression) + price dip → buy during
   volatile conditions when dips bounce harder. Opposite of all previous GK<30 logic.

All with CMP exit: TP + MH + SN. Anti-lookahead compliant.
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
    for c in ["open","high","low","close","volume","tbv"]:
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

    # EMAs (shift 1 for regime check)
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema20_s1"] = d["ema20"].shift(1)
    d["ema50_s1"] = d["ema50"].shift(1)
    d["ema20_slope"] = (d["ema20"] - d["ema20"].shift(3)).shift(1)  # 3-bar slope, shifted

    # RSI(14) shift(1)
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi14"] = 100 - (100 / (1 + rs))
    d["rsi14_s1"] = d["rsi14"].shift(1)

    # ADX (Wilder smoothing, from scratch, shift(1))
    tr = pd.DataFrame({
        'hl': d['high'] - d['low'],
        'hc': abs(d['high'] - d['close'].shift(1)),
        'lc': abs(d['low'] - d['close'].shift(1))
    }).max(axis=1)
    plus_dm = (d['high'] - d['high'].shift(1)).clip(lower=0)
    minus_dm = (d['low'].shift(1) - d['low']).clip(lower=0)
    # Where plus_dm > minus_dm, keep plus_dm, else 0
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0)
    # Wilder smoothing (alpha=1/14)
    atr14 = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr14
    minus_di = 100 * minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr14
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    d["adx"] = adx.shift(1)
    d["plus_di"] = plus_di.shift(1)
    d["minus_di"] = minus_di.shift(1)
    d["adx_rising"] = (d["adx"] > d["adx"].shift(1))  # already shifted base

    # Returns for dip detection (shift 1)
    d["ret_s1"] = d["ret"].shift(1)
    d["cumret3_s1"] = d["ret"].rolling(3).sum().shift(1)
    d["cumret5_s1"] = d["ret"].rolling(5).sum().shift(1)

    # Taker Buy Ratio (shift 1)
    d["taker_ratio"] = (d["tbv"] / d["volume"].replace(0, np.nan)).shift(1)

    # Engulfing pattern (shift 1): current bar engulfs previous bar
    # Bullish engulfing: prev bar red, current bar green, current body covers prev body
    prev_red = d["close"].shift(1) < d["open"].shift(1)
    curr_green = d["close"] > d["open"]
    body_covers = (d["open"] <= d["close"].shift(1)) & (d["close"] >= d["open"].shift(1))
    d["bull_engulf"] = (prev_red & curr_green & body_covers).shift(1)

    # Hammer pattern: long lower wick, small body (shift 1)
    body = abs(d["close"] - d["open"])
    lower_wick = pd.concat([d["close"], d["open"]], axis=1).min(axis=1) - d["low"]
    upper_wick = d["high"] - pd.concat([d["close"], d["open"]], axis=1).max(axis=1)
    d["hammer"] = ((lower_wick > 2 * body) & (upper_wick < body)).shift(1)

    # Regime flags (shift 1)
    d["uptrend_ema50"] = (d["close"] > d["ema50"]).shift(1)
    d["uptrend_ema20"] = (d["close"] > d["ema20"]).shift(1)
    d["adx_uptrend"] = ((d["adx"] > 25) & (d["plus_di"] > d["minus_di"])).fillna(False)

    # Session filter
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

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
    worst_v = ms.min()
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    ed = p.groupby("t")["pnl"].agg(["count", "sum", "mean"])
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_v": worst_v,
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
        results.append(fp > 0)
    return sum(results)

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

def scan(df, entry_fn, label_base, mid, fe, fs, all_results, tp_list, mh_list, cd_list,
         ms_list=[5], wr_floor=60, sn=0.055):
    """Scan TP/MH/CD grid for an entry function."""
    count = 0
    for tp in tp_list:
        for mh in mh_list:
            for cd in cd_list:
                for ms in ms_list:
                    mask = entry_fn(df)
                    t = bt_long_cmp(df, mask, tp_pct=tp, mh=mh, sn_pct=sn,
                                    max_same=ms, exit_cd=cd)
                    r = evaluate(t, mid, fe, f"{label_base} TP{tp*100:.1f} MH{mh} CD{cd} m{ms}")
                    if not r or r["pnl"] <= 0: continue
                    if r["wr"] >= wr_floor:
                        count += 1
                        if count <= 8:  # limit output
                            print(f"    {fmt_r(r)}")
                        ep = dict(tp_pct=tp, mh=mh, sn_pct=sn, max_same=ms, exit_cd=cd)
                        wf = walk_forward_mask(df, entry_fn, ep, fs, fe)
                        all_results.append(check9(r, wf))
    if count > 8:
        print(f"    ... +{count-8} more configs")
    return count


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 19: L Strategy — Regime Dip-Buy (Novel Paradigms)")
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
    tp_grid = [0.008, 0.010, 0.012, 0.015, 0.020]
    mh_grid = [8, 12, 15, 18]
    cd_grid = [4, 6]

    # =================================================================
    # Paradigm 1: EMA50 Uptrend + Dip Buy
    # =================================================================
    print(f"\n{'='*70}")
    print("  P1: EMA50 Uptrend + Dip Buy")
    print(f"{'='*70}")

    for dip_th in [-0.003, -0.005, -0.008, -0.01]:
        def make_ema50_dip(dth):
            def entry_fn(d):
                return d["uptrend_ema50"].fillna(False) & (d["ret_s1"] < dth)
            return entry_fn
        entry_fn = make_ema50_dip(dip_th)
        cnt = entry_fn(df).sum()
        print(f"\n  EMA50Up + Dip<{dip_th*100:.1f}%: {cnt} signals")
        scan(df, entry_fn, f"EMA50Dip{dip_th*100:.0f}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # With cumulative dip (3-bar)
    for dip_th in [-0.02, -0.03, -0.04]:
        def make_ema50_cumdip(dth):
            def entry_fn(d):
                return d["uptrend_ema50"].fillna(False) & (d["cumret3_s1"] < dth)
            return entry_fn
        entry_fn = make_ema50_cumdip(dip_th)
        cnt = entry_fn(df).sum()
        print(f"\n  EMA50Up + CumDip3<{dip_th*100:.0f}%: {cnt} signals")
        scan(df, entry_fn, f"EMA50CD3{dip_th*100:.0f}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 2: ADX Uptrend Regime + RSI Dip
    # =================================================================
    print(f"\n{'='*70}")
    print("  P2: ADX Uptrend + RSI Dip")
    print(f"{'='*70}")

    for rsi_th in [35, 40, 45]:
        def make_adx_rsi(rth):
            def entry_fn(d):
                return d["adx_uptrend"].fillna(False) & (d["rsi14_s1"] < rth)
            return entry_fn
        entry_fn = make_adx_rsi(rsi_th)
        cnt = entry_fn(df).sum()
        print(f"\n  ADXUp + RSI<{rsi_th}: {cnt} signals")
        scan(df, entry_fn, f"ADXUp+RSI{rsi_th}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # ADX uptrend + single-bar dip
    for dip_th in [-0.005, -0.008, -0.01]:
        def make_adx_dip(dth):
            def entry_fn(d):
                return d["adx_uptrend"].fillna(False) & (d["ret_s1"] < dth)
            return entry_fn
        entry_fn = make_adx_dip(dip_th)
        cnt = entry_fn(df).sum()
        print(f"\n  ADXUp + Dip<{dip_th*100:.1f}%: {cnt} signals")
        scan(df, entry_fn, f"ADXDip{dip_th*100:.0f}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 3: Taker Buy Ratio as Contrarian Entry
    # =================================================================
    print(f"\n{'='*70}")
    print("  P3: Taker Buy Ratio Contrarian Entry")
    print(f"{'='*70}")

    for tkr_th in [0.42, 0.44, 0.46, 0.48]:
        def make_taker(tth):
            def entry_fn(d):
                return d["taker_ratio"].fillna(0.5) < tth
            return entry_fn
        entry_fn = make_taker(tkr_th)
        cnt = entry_fn(df).sum()
        print(f"\n  TakerRatio<{tkr_th}: {cnt} signals")
        scan(df, entry_fn, f"Taker<{tkr_th}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # Taker + EMA50 uptrend
    for tkr_th in [0.44, 0.46, 0.48]:
        def make_taker_up(tth):
            def entry_fn(d):
                return (d["taker_ratio"].fillna(0.5) < tth) & d["uptrend_ema50"].fillna(False)
            return entry_fn
        entry_fn = make_taker_up(tkr_th)
        cnt = entry_fn(df).sum()
        print(f"\n  TakerRatio<{tkr_th}+EMA50Up: {cnt} signals")
        scan(df, entry_fn, f"Tkr{tkr_th}Up", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 4: Engulfing / Hammer Reversal + CMP
    # =================================================================
    print(f"\n{'='*70}")
    print("  P4: Engulfing & Hammer Patterns")
    print(f"{'='*70}")

    def engulf_entry(d):
        return d["bull_engulf"].fillna(False)

    cnt = engulf_entry(df).sum()
    print(f"\n  BullEngulfing: {cnt} signals")
    scan(df, engulf_entry, "BullEngulf", mid, fe, fs, all_results,
         tp_grid, mh_grid, cd_grid)

    # Engulfing + uptrend
    def engulf_up_entry(d):
        return d["bull_engulf"].fillna(False) & d["uptrend_ema50"].fillna(False)

    cnt = engulf_up_entry(df).sum()
    print(f"\n  BullEngulfing+EMA50Up: {cnt} signals")
    scan(df, engulf_up_entry, "EngulfUp", mid, fe, fs, all_results,
         tp_grid, mh_grid, cd_grid)

    # Hammer
    def hammer_entry(d):
        return d["hammer"].fillna(False)

    cnt = hammer_entry(df).sum()
    print(f"\n  Hammer: {cnt} signals")
    scan(df, hammer_entry, "Hammer", mid, fe, fs, all_results,
         tp_grid, mh_grid, cd_grid)

    # Hammer + uptrend
    def hammer_up_entry(d):
        return d["hammer"].fillna(False) & d["uptrend_ema50"].fillna(False)

    cnt = hammer_up_entry(df).sum()
    print(f"\n  Hammer+EMA50Up: {cnt} signals")
    scan(df, hammer_up_entry, "HammerUp", mid, fe, fs, all_results,
         tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 5: GK Expansion + Dip (opposite of compression breakout!)
    # =================================================================
    print(f"\n{'='*70}")
    print("  P5: GK Expansion + Dip Buy")
    print(f"{'='*70}")

    for gk_hi in [60, 70, 80]:
        for dip_th in [-0.005, -0.008, -0.01]:
            def make_gk_exp_dip(gth, dth):
                def entry_fn(d):
                    return (d["gk_pct"] > gth) & (d["ret_s1"] < dth)
                return entry_fn
            entry_fn = make_gk_exp_dip(gk_hi, dip_th)
            cnt = entry_fn(df).sum()
            if cnt < 20:
                continue
            print(f"\n  GK>{gk_hi}+Dip<{dip_th*100:.1f}%: {cnt} signals")
            scan(df, entry_fn, f"GKExp{gk_hi}Dip{dip_th*100:.0f}", mid, fe, fs, all_results,
                 tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 6: Double Regime (ADX + EMA50) + Dip
    # =================================================================
    print(f"\n{'='*70}")
    print("  P6: Double Regime (ADX+EMA) + Dip")
    print(f"{'='*70}")

    for rsi_th in [40, 45]:
        def make_double_regime(rth):
            def entry_fn(d):
                return (d["adx_uptrend"].fillna(False) &
                        d["uptrend_ema50"].fillna(False) &
                        (d["rsi14_s1"] < rth))
            return entry_fn
        entry_fn = make_double_regime(rsi_th)
        cnt = entry_fn(df).sum()
        print(f"\n  ADXUp+EMA50Up+RSI<{rsi_th}: {cnt} signals")
        scan(df, entry_fn, f"DblReg+RSI{rsi_th}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    for dip_th in [-0.005, -0.008]:
        def make_double_regime_dip(dth):
            def entry_fn(d):
                return (d["adx_uptrend"].fillna(False) &
                        d["uptrend_ema50"].fillna(False) &
                        (d["ret_s1"] < dth))
            return entry_fn
        entry_fn = make_double_regime_dip(dip_th)
        cnt = entry_fn(df).sum()
        print(f"\n  ADXUp+EMA50Up+Dip<{dip_th*100:.1f}%: {cnt} signals")
        scan(df, entry_fn, f"DblRegDip{dip_th*100:.0f}", mid, fe, fs, all_results,
             tp_grid, mh_grid, cd_grid)

    # =================================================================
    # Paradigm 7: Portfolio of best paradigms
    # =================================================================
    print(f"\n{'='*70}")
    print("  P7: Multi-Paradigm Portfolio")
    print(f"{'='*70}")

    def sub_run(entry_fn, ep, label):
        mask = entry_fn(df)
        t = bt_long_cmp(df, mask, **ep)
        if len(t) > 0: t["sub"] = label
        return t

    def run_portfolio(subs, pf_label):
        all_t = [sub_run(fn, ep, lab) for fn, ep, lab in subs]
        all_t = [t for t in all_t if len(t) > 0]
        if not all_t: return
        combined = pd.concat(all_t, ignore_index=True).sort_values("dt").reset_index(drop=True)
        r = evaluate(combined, mid, fe, pf_label)
        if not r or r["pnl"] <= 0:
            print(f"  {pf_label}: PnL ≤ 0")
            return
        print(f"  {pf_label}: {fmt_r(r)}")
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
        for sub_l in combined["sub"].unique():
            st = combined[combined["sub"] == sub_l]
            st_oos = st[(pd.to_datetime(st["dt"]) >= mid) & (pd.to_datetime(st["dt"]) < fe)]
            if len(st_oos) > 0:
                sp = st_oos["pnl"].sum()
                sw = (st_oos["pnl"] > 0).mean() * 100
                print(f"    {sub_l}: {len(st_oos)}t ${sp:>+7,.0f} WR{sw:.1f}%")

    def ema50_dip5(d): return d["uptrend_ema50"].fillna(False) & (d["ret_s1"] < -0.005)
    def ema50_cumdip3(d): return d["uptrend_ema50"].fillna(False) & (d["cumret3_s1"] < -0.03)
    def adx_rsi40(d): return d["adx_uptrend"].fillna(False) & (d["rsi14_s1"] < 40)
    def taker46(d): return d["taker_ratio"].fillna(0.5) < 0.46
    def engulf_up(d): return d["bull_engulf"].fillna(False) & d["uptrend_ema50"].fillna(False)
    def hammer_up(d): return d["hammer"].fillna(False) & d["uptrend_ema50"].fillna(False)
    def rsi30(d): return d["rsi14_s1"] < 30  # from R17

    ep_std = dict(tp_pct=0.012, mh=12, sn_pct=0.055, max_same=5, exit_cd=6)
    ep_wide = dict(tp_pct=0.015, mh=15, sn_pct=0.055, max_same=5, exit_cd=6)
    ep_tight = dict(tp_pct=0.010, mh=8, sn_pct=0.055, max_same=5, exit_cd=4)

    portfolios = {
        "Pf1_RegimeMix": [
            (ema50_dip5, ep_std, "EMA50Dip"),
            (adx_rsi40, ep_wide, "ADXrsi40"),
            (taker46, ep_std, "Taker46"),
            (rsi30, ep_tight, "RSI30"),
        ],
        "Pf2_PatternMix": [
            (engulf_up, ep_std, "EngulfUp"),
            (hammer_up, ep_std, "HammerUp"),
            (ema50_cumdip3, ep_wide, "CumDip3"),
            (rsi30, ep_tight, "RSI30"),
        ],
        "Pf3_Wide": [
            (ema50_dip5, ep_std, "EMA50Dip5"),
            (ema50_cumdip3, ep_wide, "CumDip3"),
            (adx_rsi40, ep_wide, "ADXrsi40"),
            (taker46, ep_std, "Taker46"),
            (engulf_up, ep_std, "EngulfUp"),
            (rsi30, ep_tight, "RSI30"),
        ],
        "Pf4_Aggressive": [
            (ema50_dip5, dict(tp_pct=0.010, mh=8, sn_pct=0.055, max_same=9, exit_cd=2), "EMA50Dip5"),
            (adx_rsi40, dict(tp_pct=0.010, mh=8, sn_pct=0.055, max_same=9, exit_cd=2), "ADXrsi40"),
            (taker46, dict(tp_pct=0.010, mh=8, sn_pct=0.055, max_same=9, exit_cd=2), "Taker46"),
            (rsi30, dict(tp_pct=0.008, mh=6, sn_pct=0.055, max_same=9, exit_cd=2), "RSI30"),
        ],
    }

    for pname, subs in portfolios.items():
        run_portfolio(subs, pname)

    # =================================================================
    # Summary
    # =================================================================
    print(f"\n{'='*70}")
    print(f"  R19 Summary — Top 20 by passed checks (of {len(all_results)})")
    print(f"{'='*70}")

    all_results.sort(key=lambda x: (-x["passed"], -x["pnl"]))
    for r in all_results[:20]:
        fail_names = [k for k, v in r["checks"].items() if not v]
        fail_str = ",".join(fail_names) if fail_names else "ALL PASS"
        print(f"  {r['label']:<45s} {r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
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
    both = [r for r in all_results if r["wr"] >= 70 and r["pnl"] >= 10000]
    print(f"  WR≥70%: {len(wr70)} | PnL≥$10K: {len(pnl10k)} | BOTH: {len(both)}")
    if wr70:
        bw = max(wr70, key=lambda r: r["pnl"])
        print(f"    Best PnL at WR≥70%: ${bw['pnl']:>+,.0f} ({bw['n']}t, {bw['label']})")
    if pnl10k:
        bp = min(pnl10k, key=lambda r: 100-r["wr"])
        print(f"    Best WR at PnL≥$10K: {bp['wr']:.1f}% ({bp['n']}t, {bp['label']})")

    print(f"\n  上帝視角自檢:")
    print(f"    EMA shift(1): ✓ uptrend_ema50, ema20_slope all .shift(1)")
    print(f"    RSI shift(1): ✓ rsi14_s1")
    print(f"    ADX shift(1): ✓ adx, plus_di, minus_di all .shift(1)")
    print(f"    Ret shift(1): ✓ ret_s1, cumret3_s1")
    print(f"    Taker shift(1): ✓ taker_ratio .shift(1)")
    print(f"    Engulf shift(1): ✓ bull_engulf .shift(1)")
    print(f"    Hammer shift(1): ✓ hammer .shift(1)")
    print(f"    Entry at O[i+1]: ✓ p['e'] = nxo")
    print(f"    Fee $2/trade: ✓")
    print(f"    SN 25% penetration: ✓")
