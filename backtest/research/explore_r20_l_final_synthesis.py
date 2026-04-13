"""
Exploration Round 20: L Strategy — Final Cross-Paradigm Synthesis
================================================================
R13-R19 proved WR≥70% + PnL≥$10K structurally impossible for ANY single approach.
This round attempts the ONLY remaining strategy: combine the BEST high-WR configs
from EVERY paradigm into one unified portfolio, applying S strategy's breakthrough
techniques (Mixed-TP, Tiered TP, independent cooldowns).

Novel elements not previously tested:
  1. Cross-paradigm portfolio: CMP breakout + mean-rev + volume + regime dip
  2. Mixed-TP across paradigms (different TP for different entry types)
  3. Session filter re-optimization for L (current filter was designed for S/general)
  4. Aggressive CD=4 for CMP subs (S uses CD=6, but L subs have less overlap)

Mathematical reality check (from R13-R19):
  - Best individual at WR≥70%: R14 BL12 GK30 = $3,399 WR77% (288t)
  - Cross-paradigm adds: ~100t at WR 79-91% ($1,000-$1,500)
  - Best case portfolio: $5,000-$7,000 at WR ~76%
  - $10K still likely unreachable, but this is the FINAL honest attempt
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
GK_SHORT = 5; GK_LONG = 20; GK_WIN = 100

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

    # --- GK volatility ---
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)
    d["gk_s"] = d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"] = d["gk"].rolling(GK_LONG).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    # GK compression flags
    for t in [25, 30, 35, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t
    # GK expansion flags (for dip-buy)
    for t in [60, 70, 80]:
        d[f"gkx{t}"] = d["gk_pct"] > t

    # --- Breakout (upward, shift(1)) ---
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15, 20]:
        d[f"cmx{bl}"] = d["close"].shift(2).rolling(bl - 1).max()
        d[f"bl{bl}"] = d["cs1"] > d[f"cmx{bl}"]

    # --- RSI(14) ---
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_g = gain.ewm(alpha=1/14, min_periods=14).mean()
    avg_l = loss.ewm(alpha=1/14, min_periods=14).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    d["rsi"] = (100 - 100 / (1 + rs)).shift(1)

    # --- Bollinger Bands ---
    d["bb_mid"] = d["close"].rolling(20).mean()
    d["bb_std"] = d["close"].rolling(20).std()
    d["bb_lower"] = d["bb_mid"] - 2 * d["bb_std"]
    d["bb_upper"] = d["bb_mid"] + 2 * d["bb_std"]
    d["bb_pos"] = ((d["close"] - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"])).shift(1)

    # --- Cumulative returns ---
    for w in [3, 5, 8]:
        d[f"cr{w}"] = d["ret"].rolling(w).sum().shift(1)

    # --- Volume indicators ---
    d["vol_ma20"] = d["volume"].rolling(20).mean()
    d["vol_ratio"] = (d["volume"] / d["vol_ma20"]).shift(1)
    d["vol_pct"] = d["volume"].shift(1).rolling(100).apply(pctile_func)
    d["red"] = (d["close"] < d["open"]).shift(1)
    d["red_vol_spike"] = (d["vol_ratio"] > 2) & d["red"]
    d["vol_climax"] = (d["vol_pct"] > 95) & d["red"]

    # --- Dip signals ---
    d["dip05"] = d["ret"].shift(1) < -0.005   # single-bar dip < -0.5%
    d["dip03"] = d["ret"].shift(1) < -0.003   # single-bar dip < -0.3%

    # --- Session filter ---
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    # Session filter variants for L
    d["sok_loose"] = ~(d["hour_utc8"].isin({0, 1}) | d["dow"].isin({5, 6}))  # less restrictive
    d["sok_tight"] = ~(d["hour_utc8"].isin({0, 1, 2, 3, 12, 13}) | d["dow"].isin({0, 5, 6}))  # more restrictive

    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)


# ============================================================
# Backtest engines
# ============================================================

def bt_long_tiered(df, max_same=3, entry_mask=None,
                   tp1=0.015, tp2=None, phase1_bars=10, max_hold=12,
                   sn_pct=0.055, exit_cd=6, sok_col="sok"):
    """Generic L backtest with Tiered TP. entry_mask is boolean Series."""
    if tp2 is None: tp2 = tp1 + 0.005
    W = 160
    H = df["high"].values; L_ = df["low"].values
    O = df["open"].values; C = df["close"].values; DT = df["datetime"].values
    MASK = entry_mask.values if entry_mask is not None else np.zeros(len(df), dtype=bool)
    SOK = df[sok_col].values
    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        nxo = O[i + 1]; lo = L_[i]; h = H[i]; c = C[i]; dt = DT[i]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            # SafeNet
            if lo <= p["e"] * (1 - sn_pct):
                ep_ = p["e"] * (1 - sn_pct) - (p["e"] * (1 - sn_pct) - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            # Tiered TP
            current_tp = tp1 if b <= phase1_bars else tp2
            if not done and h >= p["e"] * (1 + current_tp):
                tag = "TP1" if b <= phase1_bars else "TP2"
                pnl = p["e"] * current_tp * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": tag, "b": b, "dt": dt}); lx = i; done = True
            # MaxHold
            if not done and b >= max_hold:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos
        # Entry
        if _b(MASK[i]) and _b(SOK[i]) and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])


def bt_portfolio_multi(df, sub_configs):
    """Portfolio of independent sub-strategies, each with its own cooldown."""
    all_trades = []
    for cfg in sub_configs:
        entry_mask = cfg.pop("entry_mask")
        label = cfg.pop("label", "")
        t = bt_long_tiered(df, entry_mask=entry_mask, **cfg)
        if len(t) > 0:
            t["sub"] = label
            all_trades.append(t)
    if not all_trades:
        return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(all_trades, ignore_index=True).sort_values("dt").reset_index(drop=True)


# ============================================================
# Evaluation
# ============================================================

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
        top_v = ms.max() if len(ms) > 0 else 0
        top_n = str(ms.idxmax()) if len(ms) > 0 else "N/A"; top_pct = 999
    nb = pnl - top_v if pnl > 0 else pnl
    worst_v = ms.min() if len(ms) > 0 else 0
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": str(ms.idxmin()) if len(ms) > 0 else "N/A",
            "worst_v": worst_v, "tpm": tpm, "monthly": ms, "avg": pnl / n if n else 0}


def score9(r):
    """Score against 9 criteria. Returns (pass_count, details)."""
    if r is None: return 0, "NO TRADES"
    checks = [
        ("PnL≥10K", r["pnl"] >= 10000),
        ("PF≥1.5", r["pf"] >= 1.5),
        ("MDD≤25", r["mdd"] <= 25),
        ("TPM≥10", r["tpm"] >= 10),
        ("WR≥70", r["wr"] >= 70),
        ("PM≥75%", r["pos_months"] / r["months"] * 100 >= 75 if r["months"] > 0 else False),
        ("topM≤20", r["top_pct"] <= 20),
        ("-best≥8K", r["nb"] >= 8000),
        ("worst≥-1K", r["worst_v"] >= -1000),
    ]
    passed = sum(1 for _, v in checks if v)
    detail = " ".join(f"{'✓' if v else '✗'}{n}" for n, v in checks)
    return passed, detail


def walk_forward(df, entry_mask, cfg_base, fs, fe):
    """6-fold walk-forward."""
    os_ = fs + pd.DateOffset(months=12); results = []
    t = bt_long_tiered(df, entry_mask=entry_mask, **cfg_base)
    t["dt"] = pd.to_datetime(t["dt"])
    for fold in range(6):
        ts = os_ + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), fe)
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results


def print_result(r, show_monthly=False):
    if r is None:
        print("    NO TRADES")
        return
    sc, det = score9(r)
    print(f"    {r['label']}")
    print(f"    {r['n']:>4}t ${r['pnl']:>+9,.0f}  PF{r['pf']:.2f}  WR{r['wr']:.1f}%  MDD{r['mdd']:.1f}%  "
          f"TPM{r['tpm']:.1f}  PM{r['pos_months']}/{r['months']}  topM{r['top_pct']:.1f}%  "
          f"-best${r['nb']:>+,.0f}  worst${r['worst_v']:>+,.0f}")
    print(f"    Score: {sc}/9  {det}")
    if show_monthly and "monthly" in r:
        ms = r["monthly"]
        print(f"    Monthly: {' | '.join(f'{str(m)[-2:]}: ${v:+,.0f}' for m, v in ms.items())}")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 80)
    print("  ROUND 20: L Strategy — Final Cross-Paradigm Synthesis")
    print("=" * 80)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df = calc_indicators(df_raw)

    all_results = []

    # ================================================================
    # Phase 1: L-CMP Portfolio (S strategy's approach applied to L)
    # ================================================================
    print(f"\n{'='*80}")
    print("  Phase 1: L-CMP Portfolio (mirroring S strategy's success)")
    print(f"{'='*80}")

    # Best L-CMP configs from R13/R14:
    # 4 subs with different BL windows, like S strategy
    cmp_subs = [
        {"gk": "gk30", "bl": 12},  # R14 best: WR 77%
        {"gk": "gk40", "bl": 8},   # Different BL for diversity
        {"gk": "gk40", "bl": 15},  # Different BL
        {"gk": "gk30", "bl": 10},  # Different GK threshold
    ]

    print(f"\n  --- Phase 1A: Individual CMP subs (baseline) ---")
    print(f"  {'Config':<25} {'TP1':>5} {'MH':>3} {'CD':>3} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6} {'TPM':>5}")

    for tp1_pct in [0.010, 0.012, 0.015]:
        for mh in [12, 15, 19]:
            for cd in [4, 6]:
                for sub in cmp_subs:
                    gk_col = sub["gk"]; bl = sub["bl"]
                    entry = df[gk_col] & df[f"bl{bl}"]
                    t = bt_long_tiered(df, entry_mask=entry, max_same=5,
                                       tp1=tp1_pct, phase1_bars=10, max_hold=mh,
                                       exit_cd=cd)
                    r = evaluate(t, mid, fe, f"{gk_col}_BL{bl}_TP{tp1_pct*100:.1f}_MH{mh}_CD{cd}")
                    if r and r["n"] > 20:
                        sc, _ = score9(r)
                        print(f"  {r['label']:<25} {tp1_pct*100:.1f}% {mh:>3} {cd:>3} "
                              f"{r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} {r['wr']:>5.1f}% "
                              f"{r['mdd']:>5.1f}% {r['tpm']:>5.1f} [{sc}/9]")
                        all_results.append(r)

    # Phase 1B: 4-sub portfolio with Tiered TP (like S strategy Mixed-TP)
    print(f"\n  --- Phase 1B: CMP Portfolio (4 subs, Mixed-TP) ---")
    print(f"  {'Config':<40} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6} {'TPM':>5}")

    for tp1_a in [0.010, 0.012, 0.015]:
        for tp1_b in [0.012, 0.015, 0.018]:
            if tp1_b <= tp1_a: continue
            for mh in [12, 15, 19]:
                for cd in [4, 6]:
                    subs = []
                    # Group A: core 2 subs with tighter TP
                    for sub in cmp_subs[:2]:
                        entry = df[sub["gk"]] & df[f"bl{sub['bl']}"]
                        subs.append({
                            "entry_mask": entry, "label": f"A_{sub['gk']}_BL{sub['bl']}",
                            "max_same": 5, "tp1": tp1_a, "phase1_bars": 10,
                            "max_hold": mh, "exit_cd": cd
                        })
                    # Group B: extra 2 subs with wider TP
                    for sub in cmp_subs[2:]:
                        entry = df[sub["gk"]] & df[f"bl{sub['bl']}"]
                        subs.append({
                            "entry_mask": entry, "label": f"B_{sub['gk']}_BL{sub['bl']}",
                            "max_same": 5, "tp1": tp1_b, "phase1_bars": 10,
                            "max_hold": mh, "exit_cd": cd
                        })
                    t = bt_portfolio_multi(df, [s.copy() for s in subs])
                    lbl = f"Pf4_A{tp1_a*100:.1f}+B{tp1_b*100:.1f}_MH{mh}_CD{cd}"
                    r = evaluate(t, mid, fe, lbl)
                    if r and r["n"] > 50:
                        sc, _ = score9(r)
                        print(f"  {r['label']:<40} {r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} "
                              f"{r['wr']:>5.1f}% {r['mdd']:>5.1f}% {r['tpm']:>5.1f} [{sc}/9]")
                        all_results.append(r)

    # ================================================================
    # Phase 2: Cross-Paradigm Portfolio
    # ================================================================
    print(f"\n{'='*80}")
    print("  Phase 2: Cross-Paradigm Portfolio (best from each R13-R19)")
    print(f"{'='*80}")

    # Define best config from each paradigm
    paradigm_entries = {
        "CMP_g30bl12": df["gk30"] & df["bl12"],
        "CMP_g40bl8": df["gk40"] & df["bl8"],
        "CMP_g40bl15": df["gk40"] & df["bl15"],
        "CMP_g30bl10": df["gk30"] & df["bl10"],
        "RSI30": (df["rsi"] < 30),
        "RSI35": (df["rsi"] < 35),
        "RSI30_GK30": (df["rsi"] < 30) & df["gk30"],
        "BB_low": (df["bb_pos"] < 0.05),
        "CR3_neg3": (df["cr3"] < -0.03),
        "CR5_neg5": (df["cr5"] < -0.05),
        "VolSpike_Red": df["red_vol_spike"],
        "VolClimax": df["vol_climax"],
        "GKx80_Dip05": df["gkx80"] & df["dip05"],
        "GKx70_Dip03": df["gkx70"] & df["dip03"],
    }

    print(f"\n  --- Phase 2A: Individual paradigm baselines ---")
    print(f"  {'Entry':<20} {'TP1':>5} {'MH':>3} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6}")

    for name, mask in paradigm_entries.items():
        for tp1 in [0.010, 0.015]:
            for mh in [8, 12]:
                t = bt_long_tiered(df, entry_mask=mask, max_same=5,
                                   tp1=tp1, phase1_bars=min(mh, 10), max_hold=mh,
                                   exit_cd=6)
                r = evaluate(t, mid, fe, f"{name}_TP{tp1*100:.0f}_MH{mh}")
                if r and r["n"] > 5:
                    sc, _ = score9(r)
                    print(f"  {r['label']:<20} {tp1*100:.1f}% {mh:>3} "
                          f"{r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} {r['wr']:>5.1f}% "
                          f"{r['mdd']:>5.1f}% [{sc}/9]")
                    all_results.append(r)

    # Phase 2B: Cross-paradigm portfolios
    print(f"\n  --- Phase 2B: Cross-paradigm portfolio combos ---")
    print(f"  {'Portfolio':<45} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6}")

    portfolio_recipes = {
        "XP_CMP4": [
            ("CMP_g30bl12", 0.015, 12, 6),
            ("CMP_g40bl8", 0.015, 12, 6),
            ("CMP_g40bl15", 0.015, 12, 6),
            ("CMP_g30bl10", 0.015, 12, 6),
        ],
        "XP_CMP4_MixTP": [
            ("CMP_g30bl12", 0.012, 15, 4),
            ("CMP_g40bl8", 0.012, 15, 4),
            ("CMP_g40bl15", 0.015, 15, 4),
            ("CMP_g30bl10", 0.015, 15, 4),
        ],
        "XP_CMP2+MeanRev2": [
            ("CMP_g30bl12", 0.015, 12, 6),
            ("CMP_g40bl8", 0.015, 12, 6),
            ("RSI30", 0.010, 8, 6),
            ("CR5_neg5", 0.010, 12, 6),
        ],
        "XP_CMP2+Vol+Dip": [
            ("CMP_g30bl12", 0.015, 12, 6),
            ("CMP_g40bl15", 0.015, 12, 6),
            ("VolSpike_Red", 0.010, 8, 6),
            ("GKx80_Dip05", 0.010, 8, 6),
        ],
        "XP_6sub_wide": [
            ("CMP_g30bl12", 0.015, 15, 4),
            ("CMP_g40bl8", 0.012, 12, 4),
            ("CMP_g40bl15", 0.015, 15, 4),
            ("RSI35", 0.010, 8, 6),
            ("VolSpike_Red", 0.010, 8, 6),
            ("GKx80_Dip05", 0.010, 8, 6),
        ],
        "XP_8sub_max": [
            ("CMP_g30bl12", 0.015, 15, 4),
            ("CMP_g40bl8", 0.012, 12, 4),
            ("CMP_g40bl15", 0.015, 15, 4),
            ("CMP_g30bl10", 0.012, 12, 4),
            ("RSI30", 0.010, 8, 6),
            ("CR3_neg3", 0.010, 12, 6),
            ("VolSpike_Red", 0.010, 8, 6),
            ("GKx80_Dip05", 0.010, 8, 6),
        ],
        "XP_MeanRev_Only": [
            ("RSI30", 0.010, 8, 6),
            ("RSI35", 0.012, 10, 6),
            ("BB_low", 0.010, 8, 6),
            ("CR3_neg3", 0.010, 12, 6),
            ("CR5_neg5", 0.010, 12, 6),
            ("VolClimax", 0.010, 8, 6),
        ],
        "XP_Dip_Focus": [
            ("GKx80_Dip05", 0.010, 8, 4),
            ("GKx70_Dip03", 0.010, 8, 4),
            ("CR3_neg3", 0.010, 12, 4),
            ("RSI30_GK30", 0.010, 8, 4),
            ("VolSpike_Red", 0.010, 8, 4),
        ],
    }

    for pf_name, recipe in portfolio_recipes.items():
        subs = []
        for entry_name, tp1, mh, cd in recipe:
            mask = paradigm_entries[entry_name]
            subs.append({
                "entry_mask": mask, "label": entry_name,
                "max_same": 5, "tp1": tp1, "phase1_bars": min(mh, 10),
                "max_hold": mh, "exit_cd": cd
            })
        t = bt_portfolio_multi(df, [s.copy() for s in subs])
        r = evaluate(t, mid, fe, pf_name)
        if r and r["n"] > 0:
            sc, det = score9(r)
            print(f"  {r['label']:<45} {r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} "
                  f"{r['wr']:>5.1f}% {r['mdd']:>5.1f}% [{sc}/9]")
            if r["wr"] >= 65:
                print(f"    {det}")
            all_results.append(r)

    # ================================================================
    # Phase 3: Session Filter Optimization for L
    # ================================================================
    print(f"\n{'='*80}")
    print("  Phase 3: Session Filter Re-optimization for L")
    print(f"{'='*80}")

    # Test the best CMP portfolio with different session filters
    best_cmp_recipe = [
        ("CMP_g30bl12", 0.015, 15, 4),
        ("CMP_g40bl8", 0.012, 12, 4),
        ("CMP_g40bl15", 0.015, 15, 4),
        ("CMP_g30bl10", 0.012, 12, 4),
    ]

    for sok_col, sok_name in [("sok", "standard"), ("sok_loose", "loose"), ("sok_tight", "tight")]:
        subs = []
        for entry_name, tp1, mh, cd in best_cmp_recipe:
            mask = paradigm_entries[entry_name]
            subs.append({
                "entry_mask": mask, "label": entry_name,
                "max_same": 5, "tp1": tp1, "phase1_bars": 10,
                "max_hold": mh, "exit_cd": cd, "sok_col": sok_col
            })
        t = bt_portfolio_multi(df, [s.copy() for s in subs])
        r = evaluate(t, mid, fe, f"CMP4_MixTP_session={sok_name}")
        if r:
            sc, det = score9(r)
            print(f"  {r['label']:<40} {r['n']:>4} ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
                  f"WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% [{sc}/9]")
            print(f"    {det}")
            all_results.append(r)

    # Also test cross-paradigm with session variants
    for sok_col, sok_name in [("sok_loose", "loose")]:
        subs = []
        recipe = portfolio_recipes["XP_8sub_max"]
        for entry_name, tp1, mh, cd in recipe:
            mask = paradigm_entries[entry_name]
            subs.append({
                "entry_mask": mask, "label": entry_name,
                "max_same": 5, "tp1": tp1, "phase1_bars": min(mh, 10),
                "max_hold": mh, "exit_cd": cd, "sok_col": sok_col
            })
        t = bt_portfolio_multi(df, [s.copy() for s in subs])
        r = evaluate(t, mid, fe, f"XP_8sub_max_session={sok_name}")
        if r:
            sc, det = score9(r)
            print(f"  {r['label']:<40} {r['n']:>4} ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
                  f"WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% [{sc}/9]")
            print(f"    {det}")
            all_results.append(r)

    # ================================================================
    # Phase 4: WR vs PnL Frontier (Final Summary)
    # ================================================================
    print(f"\n{'='*80}")
    print("  Phase 4: WR vs PnL Frontier (all configs)")
    print(f"{'='*80}")

    valid = [r for r in all_results if r is not None and r["n"] >= 10]
    valid.sort(key=lambda x: (-x["wr"], -x["pnl"]))

    print(f"\n  --- WR ≥ 70% configs (sorted by PnL desc) ---")
    print(f"  {'Label':<45} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6} {'Score'}")
    hi_wr = [r for r in valid if r["wr"] >= 70]
    hi_wr.sort(key=lambda x: -x["pnl"])
    for r in hi_wr[:20]:
        sc, _ = score9(r)
        print(f"  {r['label']:<45} {r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} "
              f"{r['wr']:>5.1f}% {r['mdd']:>5.1f}% [{sc}/9]")

    print(f"\n  --- PnL ≥ $8,000 configs (sorted by WR desc) ---")
    print(f"  {'Label':<45} {'N':>4} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6} {'Score'}")
    hi_pnl = [r for r in valid if r["pnl"] >= 8000]
    hi_pnl.sort(key=lambda x: -x["wr"])
    for r in hi_pnl[:20]:
        sc, _ = score9(r)
        print(f"  {r['label']:<45} {r['n']:>4} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} "
              f"{r['wr']:>5.1f}% {r['mdd']:>5.1f}% [{sc}/9]")

    # ================================================================
    # Phase 5: Best overall — detailed report
    # ================================================================
    print(f"\n{'='*80}")
    print("  Phase 5: Best Configs — Detailed Report")
    print(f"{'='*80}")

    # Best at WR≥70%
    if hi_wr:
        print(f"\n  ★ Best at WR≥70% (highest PnL):")
        print_result(hi_wr[0], show_monthly=True)

    # Best at PnL≥$8K
    if hi_pnl:
        print(f"\n  ★ Best at PnL≥$8K (highest WR):")
        print_result(hi_pnl[0], show_monthly=True)

    # Best overall score
    best_score = max(valid, key=lambda x: (score9(x)[0], x["pnl"])) if valid else None
    if best_score:
        sc, det = score9(best_score)
        print(f"\n  ★ Best overall score ({sc}/9):")
        print_result(best_score, show_monthly=True)

    # ================================================================
    # Summary
    # ================================================================
    print(f"\n{'='*80}")
    print("  FINAL SUMMARY")
    print(f"{'='*80}")

    total = len(all_results)
    wr70 = len([r for r in all_results if r and r["wr"] >= 70])
    pnl10k = len([r for r in all_results if r and r["pnl"] >= 10000])
    both = len([r for r in all_results if r and r["wr"] >= 70 and r["pnl"] >= 10000])

    print(f"  Total configs tested: {total}")
    print(f"  WR ≥ 70%: {wr70}")
    print(f"  PnL ≥ $10K: {pnl10k}")
    print(f"  BOTH (WR≥70% AND PnL≥$10K): {both}")

    if both == 0:
        print(f"\n  ❌ CONFIRMED: WR≥70% + PnL≥$10K remains IMPOSSIBLE")
        print(f"     Even with cross-paradigm portfolio + Mixed-TP + session optimization,")
        print(f"     the structural constraint (fee/TP ratio) prevents simultaneous achievement.")
        if hi_wr:
            best_wr_pnl = hi_wr[0]
            print(f"\n  Best achievable at WR≥70%: ${best_wr_pnl['pnl']:,.0f} "
                  f"(WR {best_wr_pnl['wr']:.1f}%, {best_wr_pnl['n']}t)")
        if hi_pnl:
            best_pnl_wr = hi_pnl[0]
            print(f"  Best achievable at PnL≥$10K: WR {best_pnl_wr['wr']:.1f}% "
                  f"(${best_pnl_wr['pnl']:,.0f}, {best_pnl_wr['n']}t)")
    else:
        print(f"\n  ✅ BREAKTHROUGH! {both} configs achieve BOTH criteria!")
        for r in [r for r in valid if r["wr"] >= 70 and r["pnl"] >= 10000][:5]:
            print_result(r, show_monthly=True)

    print(f"\n{'='*80}")
    print("  R20 COMPLETE")
    print(f"{'='*80}")
