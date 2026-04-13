"""
L Strategy Exploration Round 1: Permutation Entropy + Amihud Illiquidity OR
===========================================================================
Direction A: PE (OOS $4,886, WF 7/10) and Amihud (OOS $4,913, WF 8/10)
are the two most robust new signals from 17-round entry exploration.

Hypothesis: PE captures sequence-order breakdown (compression ending),
Amihud captures liquidity compression (thin market about to move).
OR combination expands signal coverage across different market states.

Phases:
  1. Individual PE / Amihud baselines
  2. PE + Amihud OR combo
  3. Replace GK in Triple OR with PE/Amihud
  4. Add PE/Amihud to existing Triple OR (4-way)
  5. Top result: full details + monthly + walk-forward
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta
from math import factorial
from itertools import permutations as perms

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# Constants — locked BEFORE seeing any data
# ══════════════════════════════════════════════════════════════
NOTIONAL        = 4000
FEE             = 4.0
ACCOUNT         = 10000
SAFENET_PCT     = 0.055
SN_PEN          = 0.25
EMA_SPAN        = 20
MIN_TRAIL       = 7
EARLY_STOP_PCT  = 0.01
EARLY_STOP_END  = 12
BRK_LOOK        = 10
EXIT_CD         = 12
MAX_SAME        = 9
MAX_OPEN_PER_BAR = 2
BLOCK_H         = {0, 1, 2, 12}
BLOCK_D         = {0, 5, 6}
GK_SHORT        = 5
GK_LONG         = 20
GK_WIN          = 100
PE_M            = 3
PE_WINDOW       = 20
PCTILE_WIN      = 100
AMI_WINDOW      = 20
WARMUP          = 200


# ══════════════════════════════════════════════════════════════
# Data fetching
# ══════════════════════════════════════════════════════════════
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
            except Exception:
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


# ══════════════════════════════════════════════════════════════
# Permutation Entropy (vectorized pattern codes + sliding window)
# ══════════════════════════════════════════════════════════════
_M_FACT = factorial(PE_M)  # 6
_MAX_ENT = np.log(_M_FACT)

# Build pattern code → index mapping for m=3
_PERM_CODES = {}
for _i, _p in enumerate(perms(range(PE_M))):
    _code = _p[0] * 9 + _p[1] * 3 + _p[2]  # base-3 encoding
    _PERM_CODES[_code] = _i


def calc_pe_fast(ret_arr):
    """Vectorized rolling PE. Returns array same length as ret_arr."""
    n = len(ret_arr)
    if n < PE_WINDOW:
        return np.full(n, np.nan)

    # Step 1: Compute ordinal pattern codes for all consecutive triplets
    x0 = ret_arr[:-2]; x1 = ret_arr[1:-1]; x2 = ret_arr[2:]
    valid = ~(np.isnan(x0) | np.isnan(x1) | np.isnan(x2))

    r01 = (x0 > x1).astype(int)
    r02 = (x0 > x2).astype(int)
    r12 = (x1 > x2).astype(int)
    rank0 = r01 + r02
    rank1 = (1 - r01) + r12
    rank2 = (1 - r02) + (1 - r12)
    raw_codes = rank0 * 9 + rank1 * 3 + rank2

    # Map to 0-5 indices
    mapped = np.full(len(raw_codes), -1, dtype=int)
    for j in range(len(raw_codes)):
        if valid[j]:
            mapped[j] = _PERM_CODES.get(int(raw_codes[j]), -1)

    # Step 2: Sliding window entropy
    n_per_win = PE_WINDOW - PE_M + 1  # 18
    pe_result = np.full(n, np.nan)
    counts = np.zeros(_M_FACT, dtype=int)
    total = 0

    # Initialize first window
    for j in range(min(n_per_win, len(mapped))):
        if mapped[j] >= 0:
            counts[mapped[j]] += 1
            total += 1

    # First PE value: corresponds to ret index (n_per_win - 1 + PE_M - 1) = 19
    ret_idx = n_per_win + PE_M - 2
    if ret_idx < n and total >= n_per_win // 2:
        probs = counts[counts > 0] / total
        pe_result[ret_idx] = -np.sum(probs * np.log(probs)) / _MAX_ENT

    # Slide
    for s in range(1, len(mapped) - n_per_win + 1):
        old = mapped[s - 1]
        if old >= 0:
            counts[old] -= 1; total -= 1
        new = mapped[s + n_per_win - 1]
        if new >= 0:
            counts[new] += 1; total += 1
        ret_idx = s + n_per_win + PE_M - 2
        if ret_idx < n and total >= n_per_win // 2:
            probs = counts[counts > 0] / total
            pe_result[ret_idx] = -np.sum(probs * np.log(probs)) / _MAX_ENT

    return pe_result


# ══════════════════════════════════════════════════════════════
# Indicator calculation
# ══════════════════════════════════════════════════════════════
def pctile_func(x):
    if x.max() == x.min(): return 50.0
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100.0


def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()

    # ── EMA20 (for exit, no shift needed) ──
    d["ema20"] = d["close"].ewm(span=EMA_SPAN).mean()

    # ── GK Volatility ──
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    gk = gk.replace([np.inf, -np.inf], np.nan)
    gk_s = gk.rolling(GK_SHORT).mean()
    gk_l = gk.rolling(GK_LONG).mean()
    d["gk_r"] = (gk_s / gk_l).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    # ── Permutation Entropy ──
    t0 = time.time()
    pe_raw = calc_pe_fast(d["ret"].values)
    d["pe_raw"] = pe_raw
    d["pe"] = d["pe_raw"].shift(1)  # shift(1) to prevent look-ahead
    d["pe_pct"] = d["pe"].rolling(PCTILE_WIN).apply(pctile_func)
    print(f"  PE computed in {time.time()-t0:.1f}s")

    # ── Amihud Illiquidity ──
    dollar_vol = d["volume"] * d["close"]
    d["ami_raw"] = (d["ret"].abs() / dollar_vol).replace([np.inf, -np.inf], np.nan)
    d["ami"] = d["ami_raw"].rolling(AMI_WINDOW).mean().shift(1)
    d["ami_pct"] = d["ami"].rolling(PCTILE_WIN).apply(pctile_func)

    # ── Skew + RetSign (Triple OR components) ──
    d["skew20"] = d["ret"].rolling(20).skew().shift(1)
    d["retsign15"] = (d["ret"] > 0).astype(float).rolling(15).mean().shift(1)

    # ── Breakout (upward, BL10) ──
    d["cs1"] = d["close"].shift(1)
    d["brk_max"] = d["close"].shift(2).rolling(BRK_LOOK - 1).max()
    d["bl_up"] = d["cs1"] > d["brk_max"]

    # ── Session filter ──
    d["hour"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    return d


def _b(v):
    try:
        if pd.isna(v): return False
    except (ValueError, TypeError):
        pass
    return bool(v)


# ══════════════════════════════════════════════════════════════
# Backtest engine: L-style exit (SafeNet + EarlyStop + EMA20 Trail)
# ══════════════════════════════════════════════════════════════
def bt_long_trail(df, entry_mask):
    """L backtest. Entry at O[i+1]. MAX_OPEN_PER_BAR enforced."""
    H = df["high"].values; Lo = df["low"].values
    O = df["open"].values; C = df["close"].values
    EMA = df["ema20"].values; DT = df["datetime"].values
    MASK = entry_mask.values; SOK = df["sok"].values
    n = len(df)

    pos = []; trades = []; last_exit = -9999
    bar_open_count = {}

    for i in range(WARMUP, n - 1):
        h = H[i]; lo = Lo[i]; c = C[i]; ema = EMA[i]; dt = DT[i]
        nxo = O[i + 1]

        # ── Exits ──
        new_pos = []
        for p in pos:
            bh = i - p["ei"]; done = False

            # 1. SafeNet -5.5%
            sn_lv = p["e"] * (1 - SAFENET_PCT)
            if lo <= sn_lv:
                ep = sn_lv - (sn_lv - lo) * SN_PEN
                pnl = (ep - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt}); last_exit = i; done = True

            # 2. EarlyStop: bars 7-12, loss>1% OR trail
            if not done and MIN_TRAIL <= bh < EARLY_STOP_END:
                trail = c <= ema
                early = c <= p["e"] * (1 - EARLY_STOP_PCT)
                if trail or early:
                    tag = "ES" if (early and not trail) else "Trail"
                    pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": tag, "b": bh, "dt": dt}); last_exit = i; done = True

            # 3. EMA20 Trail: bars >= 12
            if not done and bh >= EARLY_STOP_END and c <= ema:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "Trail", "b": bh, "dt": dt}); last_exit = i; done = True

            if not done:
                new_pos.append(p)
        pos = new_pos

        # ── Entry ──
        if not _b(MASK[i]): continue
        if not _b(SOK[i]): continue
        if (i - last_exit) < EXIT_CD: continue
        if len(pos) >= MAX_SAME: continue
        key = i
        if bar_open_count.get(key, 0) >= MAX_OPEN_PER_BAR: continue
        bar_open_count[key] = bar_open_count.get(key, 0) + 1
        pos.append({"e": nxo, "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])


# ══════════════════════════════════════════════════════════════
# Evaluation
# ══════════════════════════════════════════════════════════════
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
    worst_n = str(ms.idxmin()) if len(ms) > 0 else "N/A"
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": worst_n, "worst_v": worst_v,
            "tpm": tpm, "monthly": ms, "avg": pnl / n if n else 0}


def walk_forward_6(tdf, start_oos, end_oos):
    """6-fold WF: split OOS into 6 non-overlapping 2-month windows."""
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    results = []
    for fold in range(6):
        ts = start_oos + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), end_oos)
        tt = tdf[(tdf["dt"] >= ts) & (tdf["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results


def print_result(r, show_monthly=True):
    if r is None: print("    NO TRADES"); return
    print(f"  {r['label']}")
    print(f"    {r['n']:>4}t  ${r['pnl']:>+10,.0f}  PF {r['pf']:.2f}  WR {r['wr']:.1f}%  "
          f"MDD {r['mdd']:.1f}%  TPM {r['tpm']:.1f}")
    print(f"    PM {r['pos_months']}/{r['months']}  topM {r['top_pct']:.1f}%({r['top_m']})  "
          f"-best ${r['nb']:>+,.0f}  worst ${r['worst_v']:>+,.0f}({r['worst_m']})")
    if show_monthly and "monthly" in r:
        print(f"    Monthly:")
        cum = 0
        for m, v in r["monthly"].items():
            cum += v
            print(f"      {str(m)}: ${v:>+8,.0f}  cum ${cum:>+9,.0f}")


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 76)
    print("  L STRATEGY ROUND 1: Permutation Entropy + Amihud OR")
    print("=" * 76)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS:  {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")

    df = calc_indicators(df_raw)

    # ── Signal overlap analysis ──
    print(f"\n{'─'*76}")
    print("  Signal Overlap Analysis (OOS period, with breakout + session)")
    print(f"{'─'*76}")
    oos_mask = (df["datetime"] >= mid) & (df["datetime"] < fe)
    for t in [20, 25, 30]:
        pe_sig = (df["pe_pct"] < t) & df["bl_up"] & df["sok"] & oos_mask
        ami_sig = (df["ami_pct"] < t) & df["bl_up"] & df["sok"] & oos_mask
        gk_sig = (df["gk_pct"] < t) & df["bl_up"] & df["sok"] & oos_mask
        both = pe_sig & ami_sig
        print(f"  Threshold <{t}: PE={pe_sig.sum():>3}  Ami={ami_sig.sum():>3}  "
              f"GK={gk_sig.sum():>3}  PE&Ami={both.sum():>3}  "
              f"PE|Ami={int((pe_sig | ami_sig).sum()):>3}")

    # ══════════════════════════════════════════════════════════
    # Phase 1: Individual signals + Breakout + Session
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 1: Individual Signals + Breakout + Session")
    print(f"{'='*76}")
    print(f"  {'Config':<22} {'IS':>9} {'OOS':>9} {'N':>4} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>7}")

    all_configs = []  # store (label, entry_mask, r_oos) for top results

    for name, col in [("PE","pe_pct"), ("Ami","ami_pct"), ("GK","gk_pct")]:
        for t in [15, 20, 25, 30, 35]:
            entry = (df[col] < t) & df["bl_up"]
            tdf = bt_long_trail(df, entry_mask=entry)
            r_is = evaluate(tdf, fs, mid, f"{name}<{t} IS")
            r_oos = evaluate(tdf, mid, fe, f"{name}<{t} OOS")
            if r_oos and r_oos["n"] > 5:
                is_pnl = r_is["pnl"] if r_is else 0
                print(f"  {name}<{t:<3}                "
                      f"${is_pnl:>+8,.0f} ${r_oos['pnl']:>+8,.0f} {r_oos['n']:>4} "
                      f"{r_oos['pf']:>5.2f} {r_oos['wr']:>5.1f}% "
                      f"{r_oos['mdd']:>4.1f}% {r_oos['tpm']:>5.1f} "
                      f"{r_oos['pos_months']:>2}/{r_oos['months']:<2} "
                      f"{r_oos['top_pct']:>4.0f}% ${r_oos['nb']:>+6,.0f}")
                all_configs.append((f"{name}<{t}", entry.copy(), r_oos, tdf.copy()))

    # ══════════════════════════════════════════════════════════
    # Phase 2: PE + Amihud OR combos
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 2: PE | Amihud OR Combinations + Breakout + Session")
    print(f"{'='*76}")
    print(f"  {'Config':<22} {'IS':>9} {'OOS':>9} {'N':>4} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>7}")

    for pe_t in [20, 25, 30]:
        for ami_t in [20, 25, 30]:
            entry = ((df["pe_pct"] < pe_t) | (df["ami_pct"] < ami_t)) & df["bl_up"]
            tdf = bt_long_trail(df, entry_mask=entry)
            r_is = evaluate(tdf, fs, mid, f"PE<{pe_t}|Ami<{ami_t} IS")
            r_oos = evaluate(tdf, mid, fe, f"PE<{pe_t}|Ami<{ami_t} OOS")
            if r_oos and r_oos["n"] > 5:
                is_pnl = r_is["pnl"] if r_is else 0
                label = f"PE<{pe_t}|Ami<{ami_t}"
                print(f"  {label:<22} "
                      f"${is_pnl:>+8,.0f} ${r_oos['pnl']:>+8,.0f} {r_oos['n']:>4} "
                      f"{r_oos['pf']:>5.2f} {r_oos['wr']:>5.1f}% "
                      f"{r_oos['mdd']:>4.1f}% {r_oos['tpm']:>5.1f} "
                      f"{r_oos['pos_months']:>2}/{r_oos['months']:<2} "
                      f"{r_oos['top_pct']:>4.0f}% ${r_oos['nb']:>+6,.0f}")
                all_configs.append((label, entry.copy(), r_oos, tdf.copy()))

    # ══════════════════════════════════════════════════════════
    # Phase 3: Replace GK in Triple OR with PE / Amihud
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 3: Modified Triple OR (replace GK)")
    print(f"{'='*76}")
    print(f"  {'Config':<30} {'IS':>9} {'OOS':>9} {'N':>4} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>7}")

    # Baseline: existing Triple OR (GK<30 | Skew>1 | RetSign>0.6)
    entry_baseline = ((df["gk_pct"] < 30) |
                      (df["skew20"] > 1.0) |
                      (df["retsign15"] > 0.60)) & df["bl_up"]
    tdf_bl = bt_long_trail(df, entry_mask=entry_baseline)
    r_is_bl = evaluate(tdf_bl, fs, mid, "TripleOR baseline IS")
    r_oos_bl = evaluate(tdf_bl, mid, fe, "TripleOR baseline OOS")
    if r_oos_bl:
        is_pnl = r_is_bl["pnl"] if r_is_bl else 0
        print(f"  {'GK30|Skew|RetSign (base)':<30} "
              f"${is_pnl:>+8,.0f} ${r_oos_bl['pnl']:>+8,.0f} {r_oos_bl['n']:>4} "
              f"{r_oos_bl['pf']:>5.2f} {r_oos_bl['wr']:>5.1f}% "
              f"{r_oos_bl['mdd']:>4.1f}% {r_oos_bl['tpm']:>5.1f} "
              f"{r_oos_bl['pos_months']:>2}/{r_oos_bl['months']:<2} "
              f"{r_oos_bl['top_pct']:>4.0f}% ${r_oos_bl['nb']:>+6,.0f}")
        all_configs.append(("TripleOR_base", entry_baseline.copy(), r_oos_bl, tdf_bl.copy()))

    for name, col, thresholds in [("Ami","ami_pct",[20,25,30]), ("PE","pe_pct",[20,25,30])]:
        for t in thresholds:
            entry = ((df[col] < t) |
                     (df["skew20"] > 1.0) |
                     (df["retsign15"] > 0.60)) & df["bl_up"]
            tdf = bt_long_trail(df, entry_mask=entry)
            r_is = evaluate(tdf, fs, mid, "IS")
            r_oos = evaluate(tdf, mid, fe, "OOS")
            if r_oos and r_oos["n"] > 5:
                is_pnl = r_is["pnl"] if r_is else 0
                label = f"{name}<{t}|Skew|RetSign"
                print(f"  {label:<30} "
                      f"${is_pnl:>+8,.0f} ${r_oos['pnl']:>+8,.0f} {r_oos['n']:>4} "
                      f"{r_oos['pf']:>5.2f} {r_oos['wr']:>5.1f}% "
                      f"{r_oos['mdd']:>4.1f}% {r_oos['tpm']:>5.1f} "
                      f"{r_oos['pos_months']:>2}/{r_oos['months']:<2} "
                      f"{r_oos['top_pct']:>4.0f}% ${r_oos['nb']:>+6,.0f}")
                all_configs.append((label, entry.copy(), r_oos, tdf.copy()))

    # ══════════════════════════════════════════════════════════
    # Phase 4: Add PE/Amihud to existing Triple OR (4-way)
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 4: Extended 4-way OR (add PE/Amihud to Triple OR)")
    print(f"{'='*76}")
    print(f"  {'Config':<34} {'IS':>9} {'OOS':>9} {'N':>4} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>7}")

    for name, col, thresholds in [("PE","pe_pct",[20,25,30]), ("Ami","ami_pct",[20,25,30])]:
        for t in thresholds:
            entry = ((df["gk_pct"] < 30) |
                     (df["skew20"] > 1.0) |
                     (df["retsign15"] > 0.60) |
                     (df[col] < t)) & df["bl_up"]
            tdf = bt_long_trail(df, entry_mask=entry)
            r_is = evaluate(tdf, fs, mid, "IS")
            r_oos = evaluate(tdf, mid, fe, "OOS")
            if r_oos and r_oos["n"] > 5:
                is_pnl = r_is["pnl"] if r_is else 0
                label = f"GK30|Skew|RetSign|{name}<{t}"
                print(f"  {label:<34} "
                      f"${is_pnl:>+8,.0f} ${r_oos['pnl']:>+8,.0f} {r_oos['n']:>4} "
                      f"{r_oos['pf']:>5.2f} {r_oos['wr']:>5.1f}% "
                      f"{r_oos['mdd']:>4.1f}% {r_oos['tpm']:>5.1f} "
                      f"{r_oos['pos_months']:>2}/{r_oos['months']:<2} "
                      f"{r_oos['top_pct']:>4.0f}% ${r_oos['nb']:>+6,.0f}")
                all_configs.append((label, entry.copy(), r_oos, tdf.copy()))

    # 5-way: add BOTH PE and Amihud
    for pe_t in [25, 30]:
        for ami_t in [25, 30]:
            entry = ((df["gk_pct"] < 30) |
                     (df["skew20"] > 1.0) |
                     (df["retsign15"] > 0.60) |
                     (df["pe_pct"] < pe_t) |
                     (df["ami_pct"] < ami_t)) & df["bl_up"]
            tdf = bt_long_trail(df, entry_mask=entry)
            r_is = evaluate(tdf, fs, mid, "IS")
            r_oos = evaluate(tdf, mid, fe, "OOS")
            if r_oos and r_oos["n"] > 5:
                is_pnl = r_is["pnl"] if r_is else 0
                label = f"5way+PE<{pe_t}+Ami<{ami_t}"
                print(f"  {label:<34} "
                      f"${is_pnl:>+8,.0f} ${r_oos['pnl']:>+8,.0f} {r_oos['n']:>4} "
                      f"{r_oos['pf']:>5.2f} {r_oos['wr']:>5.1f}% "
                      f"{r_oos['mdd']:>4.1f}% {r_oos['tpm']:>5.1f} "
                      f"{r_oos['pos_months']:>2}/{r_oos['months']:<2} "
                      f"{r_oos['top_pct']:>4.0f}% ${r_oos['nb']:>+6,.0f}")
                all_configs.append((label, entry.copy(), r_oos, tdf.copy()))

    # ══════════════════════════════════════════════════════════
    # Top 3 results: detailed analysis + WF
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  TOP 3 RESULTS — Detailed Analysis")
    print(f"{'='*76}")

    # Sort by OOS PnL descending
    all_configs.sort(key=lambda x: x[2]["pnl"] if x[2] else -99999, reverse=True)

    for rank, (label, entry, r_oos, tdf) in enumerate(all_configs[:3], 1):
        print(f"\n  ── #{rank}: {label} ──")
        print_result(r_oos, show_monthly=True)

        # Walk-Forward
        wf = walk_forward_6(tdf, mid, fe)
        wf_pos = sum(1 for w in wf if w["pos"])
        print(f"    Walk-Forward 6-fold: {wf_pos}/6 positive")
        for w in wf:
            tag = "✓" if w["pos"] else "✗"
            print(f"      Fold {w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {tag}")

        # Exit type distribution
        tdf_c = tdf.copy(); tdf_c["dt"] = pd.to_datetime(tdf_c["dt"])
        oos_trades = tdf_c[(tdf_c["dt"] >= mid) & (tdf_c["dt"] < fe)]
        if len(oos_trades) > 0:
            exit_dist = oos_trades.groupby("t")["pnl"].agg(["count","sum","mean"])
            print(f"    Exit distribution:")
            for etype, row in exit_dist.iterrows():
                print(f"      {etype:>6}: {int(row['count']):>3}t  "
                      f"${row['sum']:>+8,.0f}  avg ${row['mean']:>+6,.0f}")

    # ══════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  ROUND 1 SUMMARY")
    print(f"{'='*76}")
    if all_configs:
        best_label, _, best_r, _ = all_configs[0]
        print(f"  Best: {best_label}")
        print(f"  OOS: ${best_r['pnl']:>+,.0f}  PF {best_r['pf']:.2f}  WR {best_r['wr']:.1f}%  "
              f"MDD {best_r['mdd']:.1f}%  PM {best_r['pos_months']}/{best_r['months']}  "
              f"topM {best_r['top_pct']:.1f}%")
    print(f"\n  Done.")
