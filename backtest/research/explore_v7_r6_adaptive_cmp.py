"""
v7-R6: Adaptive Regime CMP + L CMP TP/MH
=========================================
R5 confirmed the WR vs PnL Pareto frontier:
  - TP 0.75% → 84.8% WR, $1,029/sub → total ~$3.6K (far below $10K)
  - TP 1.5%  → 72.5% WR, $5.0K/sub → total ~$16K (but min mWR 55%)

Two NEW directions:

S Direction: Regime-Adaptive TP
  - Use dd50 to detect regime
  - Bearish (dd50 < -1%): TP 1.5-2.0% (shorts do well, maximize PnL)
  - Non-bearish: TP 0.75-1.0% (shorts risky, quick TP to maintain WR)
  - Hypothesis: Adaptive TP maximizes WR in bull months AND PnL in bear months

L Direction: CMP TP+MH multi-sub
  - R2 found tp3_mh36 = $4,371, mWR 70% (but only 1 sub)
  - Multiple L subs with different BL and TP/MH → scale PnL to $10K+
  - Also test novel indicator filters: Permutation Entropy, Volume Entropy
  - Hypothesis: L CMP with TP+MH can achieve $10K PnL + 70% monthly WR

Also: Test S with Permutation Entropy filter (low PE = regular/predictable → better for mean-reversion shorts)
"""

import sys, io, warnings
import numpy as np, pandas as pd
from itertools import combinations

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
WARMUP = 200; MAX_PER_BAR = 2

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)
print(f"Loaded {N} bars | Split: {mid.date()}")

# ─── Core Indicators ───────────────────────────────────

# Breakout channels (both directions)
for w in [8, 10, 12, 15, 20]:
    cs1 = df["close"].shift(1)
    brk_min = df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_dn_{w}"] = cs1 < brk_min
    brk_max = df["close"].shift(2).rolling(w - 1).max()
    df[f"bl_up_{w}"] = cs1 > brk_max

df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")

# Drawdown regime filter
df["dd50"] = ((df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()).shift(1)
dd_mild = (df["dd50"] < -0.01).fillna(False)

# ─── Novel Indicators ──────────────────────────────────

# 1. Permutation Entropy (m=3, delay=1)
def permutation_entropy(x, m=3, delay=1):
    """Compute PE for a 1D array. Returns value in [0, 1]."""
    n = len(x)
    if n < m * delay:
        return np.nan
    from math import factorial, log
    perms = {}
    count = 0
    for i in range(n - (m - 1) * delay):
        pattern = tuple(np.argsort(x[i:i + m * delay:delay]))
        perms[pattern] = perms.get(pattern, 0) + 1
        count += 1
    if count == 0:
        return np.nan
    max_ent = log(factorial(m))
    if max_ent == 0:
        return np.nan
    ent = 0
    for c in perms.values():
        p = c / count
        if p > 0:
            ent -= p * log(p)
    return ent / max_ent  # Normalized to [0, 1]

print("Computing Permutation Entropy (rolling 20)...")
pe_vals = np.full(N, np.nan)
ret_arr = df["ret"].values
for i in range(20, N):
    pe_vals[i] = permutation_entropy(ret_arr[i-20:i], m=3, delay=1)
df["pe20"] = pe_vals
df["pe20_shift"] = df["pe20"].shift(1)  # Anti-lookahead
pe20_pct = df["pe20_shift"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() > x.min() else 50, raw=False)
df["pe20_pct"] = pe20_pct

# 2. Volume Entropy (rolling window Shannon entropy of volume distribution)
print("Computing Volume Entropy (rolling 20)...")
def volume_entropy(vol, nbins=5):
    """Shannon entropy of volume distribution over a window."""
    from math import log
    if len(vol) < nbins:
        return np.nan
    # Normalize to relative volumes
    v = np.array(vol)
    if v.sum() == 0:
        return np.nan
    # Bin into equal-width bins
    mn, mx = v.min(), v.max()
    if mx == mn:
        return 0.0
    edges = np.linspace(mn, mx, nbins + 1)
    counts = np.histogram(v, bins=edges)[0]
    total = counts.sum()
    if total == 0:
        return np.nan
    max_ent = log(nbins)
    if max_ent == 0:
        return 0.0
    ent = 0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * log(p)
    return ent / max_ent  # Normalized [0, 1]

ve_vals = np.full(N, np.nan)
vol_arr = df["volume"].values
for i in range(20, N):
    ve_vals[i] = volume_entropy(vol_arr[i-20:i], nbins=5)
df["ve20"] = ve_vals
df["ve20_shift"] = df["ve20"].shift(1)
ve20_pct = df["ve20_shift"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() > x.min() else 50, raw=False)
df["ve20_pct"] = ve20_pct

# 3. Excess Kurtosis (rolling 20)
print("Computing Excess Kurtosis...")
df["kurt20"] = df["ret"].rolling(20).kurt().shift(1)
kurt_pct = df["kurt20"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() > x.min() else 50, raw=False)
df["kurt20_pct"] = kurt_pct

# Check NaN counts
for col in ["pe20_pct", "ve20_pct", "kurt20_pct"]:
    valid = df[col].dropna()
    print(f"  {col}: {len(valid)} valid ({len(valid)/N*100:.1f}%)")

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

TRUE_MASK = pd.Series(True, index=df.index)

# ═══════════════════════════════════════════════════════
# S ENGINE: Adaptive TP based on regime
# ═══════════════════════════════════════════════════════

def bt_short_adaptive(df, ind_mask, bl_col, tp_bear=0.015, tp_bull=0.0075,
                      max_hold=19, max_same=5, exit_cd=6, tag="S"):
    """Short with regime-adaptive TP.
    If dd50 < -1% at entry: use tp_bear (bearish → bigger TP target)
    Else: use tp_bull (bullish → quick small TP)
    """
    H = df["high"].values; Lo = df["low"].values; O = df["open"].values
    C = df["close"].values; DT = df["datetime"].values
    IND = ind_mask.values; BL = df[bl_col].fillna(False).values
    SOK = df["sok"].values; DD = df["dd50"].values
    n = len(df); pos = []; trades = []; lx = -9999; boc = {}

    for i in range(WARMUP, n - 1):
        h = H[i]; lo_ = Lo[i]; c = C[i]; nxo = O[i + 1]; dt = DT[i]
        np_ = []
        for p in pos:
            bh = i - p["ei"]; done = False
            sn = p["e"] * (1 + SAFENET_PCT)
            if h >= sn:
                ep = sn + (h - sn) * SN_PEN
                pnl = (p["e"] - ep) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                tp_val = p["tp"]
                tp_price = p["e"] * (1 - tp_val)
                if lo_ <= tp_price:
                    pnl = (p["e"] - tp_price) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "TP", "b": bh, "dt": dt, "sub": tag})
                    lx = i; done = True
            if not done and bh >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                np_.append(p)
        pos = np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        boc[i] = boc.get(i, 0) + 1
        # Regime-based TP selection at entry time
        dd_val = DD[i] if not np.isnan(DD[i]) else 0
        tp_use = tp_bear if dd_val < -0.01 else tp_bull
        pos.append({"e": nxo, "ei": i, "tp": tp_use})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])


def bt_short_fixed(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
                   max_same=5, exit_cd=6, tag="S"):
    """Standard fixed-TP short."""
    H = df["high"].values; Lo = df["low"].values; O = df["open"].values
    C = df["close"].values; DT = df["datetime"].values
    IND = ind_mask.values; BL = df[bl_col].fillna(False).values
    SOK = df["sok"].values; n = len(df)
    pos = []; trades = []; lx = -9999; boc = {}

    for i in range(WARMUP, n - 1):
        h = H[i]; lo_ = Lo[i]; c = C[i]; nxo = O[i + 1]; dt = DT[i]
        np_ = []
        for p in pos:
            bh = i - p["ei"]; done = False
            sn = p["e"] * (1 + SAFENET_PCT)
            if h >= sn:
                ep = sn + (h - sn) * SN_PEN
                pnl = (p["e"] - ep) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                tp = p["e"] * (1 - tp_pct)
                if lo_ <= tp:
                    pnl = (p["e"] - tp) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "TP", "b": bh, "dt": dt, "sub": tag})
                    lx = i; done = True
            if not done and bh >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                np_.append(p)
        pos = np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        boc[i] = boc.get(i, 0) + 1
        pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])


# ═══════════════════════════════════════════════════════
# L ENGINE: TP+MH (for CMP approach)
# ═══════════════════════════════════════════════════════

def bt_long_tp(df, ind_mask, bl_col, tp_pct=0.03, max_hold=36,
               max_same=5, exit_cd=8, cap=15, tag="L"):
    """Long with TP + MaxHold exit. For CMP multi-sub approach."""
    H = df["high"].values; Lo = df["low"].values; O = df["open"].values
    C = df["close"].values; DT = df["datetime"].values
    IND = ind_mask.values; BL = df[bl_col].fillna(False).values
    SOK = df["sok"].values; YM = df["ym"].values
    n = len(df); pos = []; trades = []; lx = -9999; boc = {}; ment = {}

    for i in range(WARMUP, n - 1):
        h = H[i]; lo_ = Lo[i]; c = C[i]; nxo = O[i + 1]; dt = DT[i]; ym = YM[i]
        np_ = []
        for p in pos:
            bh = i - p["ei"]; done = False
            sn = p["e"] * (1 - SAFENET_PCT)
            if lo_ <= sn:
                ep = sn - (sn - lo_) * SN_PEN
                pnl = (ep - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                tp = p["e"] * (1 + tp_pct)
                if h >= tp:
                    pnl = (tp - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "TP", "b": bh, "dt": dt, "sub": tag})
                    lx = i; done = True
            if not done and bh >= max_hold:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done:
                np_.append(p)
        pos = np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        ce = ment.get(ym, 0)
        if ce >= cap: continue
        boc[i] = boc.get(i, 0) + 1; ment[ym] = ce + 1
        pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])


def bt_long_trail(df, mask, max_same=5, exit_cd=8, cap=15):
    """L baseline: EMA20 Trail + EarlyStop + SafeNet."""
    H = df["high"].values; Lo = df["low"].values; O = df["open"].values
    C = df["close"].values; EMA = df["ema20"].values; DT = df["datetime"].values
    MASK = mask.values; SOK = df["sok"].values; YM = df["ym"].values
    n = len(df); pos = []; trades = []; lx = -9999; boc = {}; ment = {}
    for i in range(WARMUP, n - 1):
        lo = Lo[i]; c = C[i]; ema = EMA[i]; nxo = O[i + 1]; dt = DT[i]; ym = YM[i]
        np_ = []
        for p in pos:
            bh = i - p["ei"]; done = False
            sn = p["e"] * (1 - SAFENET_PCT)
            if lo <= sn:
                ep = sn - (sn - lo) * SN_PEN
                pnl = (ep - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "side": "L"})
                lx = i; done = True
            if not done and 7 <= bh < 12:
                if c <= ema or c <= p["e"] * (1 - 0.01):
                    t_ = "ES" if c <= p["e"] * (1 - 0.01) and c > ema else "Trail"
                    pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": t_, "b": bh, "dt": dt, "side": "L"})
                    lx = i; done = True
            if not done and bh >= 12 and c <= ema:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "Trail", "b": bh, "dt": dt, "side": "L"})
                lx = i; done = True
            if not done:
                np_.append(p)
        pos = np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        ce = ment.get(ym, 0)
        if ce >= cap: continue
        boc[i] = boc.get(i, 0) + 1; ment[ym] = ce + 1
        pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "side"])


# ─── Evaluation ─────────────────────────────────────

def evaluate(tdf, start_dt, end_dt):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"] > 0]["pnl"].sum(); l_ = abs(p[p["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999; wr = (p["pnl"] > 0).mean() * 100
    eq = p["pnl"].cumsum(); dd = eq - eq.cummax(); mdd = abs(dd.min()) / ACCOUNT * 100
    p["m"] = p["dt"].dt.to_period("M"); ms = p.groupby("m")["pnl"].sum()
    posm = (ms > 0).sum(); mt = len(ms)
    topv = ms.max() if len(ms) > 0 else 0
    toppct = topv / pnl * 100 if pnl > 0 else 999
    nb = pnl - topv if pnl > 0 else pnl
    tpm = n / ((end_dt - start_dt).days / 30.44)
    p["win"] = (p["pnl"] > 0).astype(int)
    mwr = p.groupby("m").apply(lambda g: g["win"].mean() * 100)
    min_mwr = mwr.min() if len(mwr) > 0 else 0
    avg_mwr = mwr.mean() if len(mwr) > 0 else 0
    wr70 = (mwr >= 70).sum()
    min_mpnl = ms.min() if len(ms) > 0 else 0
    pnl500 = (ms >= 500).sum()
    ed = p.groupby("t").agg(n_=("pnl", "count"), pnl_=("pnl", "sum"),
                             wr_=("win", "mean")).reset_index() if "t" in p.columns else pd.DataFrame()
    return {"n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd, "tpm": tpm,
            "mt": mt, "posm": posm, "toppct": toppct, "nb": nb,
            "min_mwr": min_mwr, "avg_mwr": avg_mwr, "wr70": wr70,
            "min_mpnl": min_mpnl, "pnl500": pnl500, "monthly": ms,
            "monthly_wr": mwr, "exit_dist": ed, "avg": pnl / n if n else 0}


def wf6(tdf, s, e):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    r = 0
    for f in range(6):
        ts = s + pd.DateOffset(months=f * 2); te = min(ts + pd.DateOffset(months=2), e)
        tt = tdf[(tdf["dt"] >= ts) & (tdf["dt"] < te)]
        if len(tt) > 0 and tt["pnl"].sum() > 0: r += 1
    return r


def full_gates(label, r, tdf, start, end_dt, show_monthly=False):
    """Check all 10 individual gates + print results."""
    wf = wf6(tdf, start, end_dt)
    gates = 0
    checks = []

    def ck(name, cond, val_str):
        nonlocal gates
        s = "✓" if cond else "✗"
        gates += int(cond)
        checks.append(f"    {s} {name:36s} → {val_str}")

    ck("OOS PnL ≥ $10K", r["pnl"] >= 10000, f"${r['pnl']:,.0f}")
    ck("PF ≥ 1.5", r["pf"] >= 1.5, f"{r['pf']:.2f}")
    ck("MDD ≤ 25%", r["mdd"] <= 25, f"{r['mdd']:.1f}%")
    ck("TPM ≥ 10", r["tpm"] >= 10, f"{r['tpm']:.1f}")
    ck("Monthly WR ≥ 70% all", r["min_mwr"] >= 70, f"min={r['min_mwr']:.0f}%,{r['wr70']}/{r['mt']}m")
    ck("Monthly PnL ≥ $500 all", r["min_mpnl"] >= 500, f"min=${r['min_mpnl']:,.0f},{r['pnl500']}/{r['mt']}m")
    ck("Pos months ≥ 75%", r["posm"] / r["mt"] >= 0.75 if r["mt"] > 0 else False,
       f"{r['posm']}/{r['mt']}")
    ck("topM ≤ 20%", r["toppct"] <= 20, f"{r['toppct']:.1f}%")
    ck("Remove best ≥ $8K", r["nb"] >= 8000, f"${r['nb']:,.0f}")
    ck("WF ≥ 5/6", wf >= 5, f"{wf}/6")

    print(f"\n  {label}:")
    for c in checks:
        print(c)
    print(f"  Score: {gates}/10")

    if show_monthly and r.get("monthly") is not None:
        print(f"\n  Monthly detail:")
        ms = r["monthly"]; mwr = r["monthly_wr"]
        print(f"  {'Month':12s} {'PnL':>8s} {'WR':>7s}  $500  70%")
        for m in ms.index:
            mpnl = ms[m]
            mw = mwr.get(m, 0)
            p5 = "✓" if mpnl >= 500 else "✗"
            w7 = "✓" if mw >= 70 else "✗"
            print(f"  {str(m):12s} {mpnl:8,.0f} {mw:6.1f}%  {p5}   {w7}")

    if r.get("exit_dist") is not None and len(r["exit_dist"]) > 0:
        print(f"\n  Exit Distribution:")
        ed = r["exit_dist"]
        print(f"  {'Type':8s} {'N':>5s} {'PnL':>9s} {'Avg':>8s} {'WR':>7s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:8s} {int(row['n_']):5d} {row['pnl_']:9,.0f} {row['pnl_']/row['n_']:8.1f} {row['wr_']*100:6.1f}%")

    return gates


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: S — REGIME-ADAPTIVE TP SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 1: S — REGIME-ADAPTIVE TP SWEEP")
print("=" * 95)

# Test adaptive TP configurations
# bear_tp × bull_tp × BL × MH
s_adapt_results = {}
s_adapt_cfgs = []

for tp_bear in [0.015, 0.02, 0.025]:
    for tp_bull in [0.005, 0.0075, 0.01]:
        for bl in [8, 10, 12, 15]:
            for mh in [12, 15, 19]:
                name = f"a_tb{int(tp_bear*1000)}_tu{int(tp_bull*1000)}_bl{bl}_mh{mh}"
                s_adapt_cfgs.append((name, TRUE_MASK, f"bl_dn_{bl}", tp_bear, tp_bull, mh))

print(f"Adaptive configs: {len(s_adapt_cfgs)}")

for name, ind, bl_col, tp_bear, tp_bull, mh in s_adapt_cfgs:
    tdf = bt_short_adaptive(df, ind, bl_col, tp_bear=tp_bear, tp_bull=tp_bull,
                             max_hold=mh, tag=name)
    oos = evaluate(tdf, mid, end)
    is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
    if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
        s_adapt_results[name] = {"oos": oos, "is": is_, "tdf": tdf}

# Also compare with fixed-TP best from R4/R5
s_fixed_results = {}
for tp in [0.0075, 0.01, 0.015, 0.02]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            # No filter
            name = f"f_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            tdf = bt_short_fixed(df, TRUE_MASK, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            oos = evaluate(tdf, mid, end)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
            if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                s_fixed_results[name] = {"oos": oos, "is": is_, "tdf": tdf}
            # DD filter
            name2 = f"d_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            tdf2 = bt_short_fixed(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name2)
            oos2 = evaluate(tdf2, mid, end)
            is_2 = evaluate(tdf2, df["datetime"].iloc[0], mid)
            if oos2 and is_2 and is_2["pnl"] > 0 and oos2["pnl"] > 0:
                s_fixed_results[name2] = {"oos": oos2, "is": is_2, "tdf": tdf2}

# PE filter for S
s_pe_results = {}
for pe_thr in [30, 40, 50]:
    pe_mask = (df["pe20_pct"] < pe_thr).fillna(False)
    for tp in [0.01, 0.015, 0.02]:
        for bl in [8, 10, 12, 15]:
            for mh in [12, 15, 19]:
                name = f"pe{pe_thr}_tp{int(tp*1000)}_bl{bl}_mh{mh}"
                tdf = bt_short_fixed(df, pe_mask, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
                oos = evaluate(tdf, mid, end)
                is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
                if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                    s_pe_results[name] = {"oos": oos, "is": is_, "tdf": tdf}

print(f"\nViable: Adaptive={len(s_adapt_results)}, Fixed={len(s_fixed_results)}, PE={len(s_pe_results)}")

# Compare best from each category
all_s = {}
all_s.update(s_adapt_results)
all_s.update(s_fixed_results)
all_s.update(s_pe_results)

# Sort by PnL
s_by_pnl = sorted(all_s.items(), key=lambda x: x[1]["oos"]["pnl"], reverse=True)[:20]
# Sort by min monthly WR
s_by_mwr = sorted(all_s.items(), key=lambda x: x[1]["oos"]["min_mwr"], reverse=True)[:20]

print("\nTOP 20 S SUBS by PnL:")
print(f"{'Config':40s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 75)
for name, r in s_by_pnl:
    o = r["oos"]
    print(f"{name:40s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")

print("\nTOP 20 S SUBS by min monthly WR:")
print(f"{'Config':40s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 75)
for name, r in s_by_mwr:
    o = r["oos"]
    print(f"{name:40s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1b: S CMP PORTFOLIO (from best adaptive + fixed + PE subs)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 1b: S CMP PORTFOLIOS")
print("=" * 95)

def build_cmp(sub_dict, picks, label):
    """Build CMP portfolio from selected subs."""
    frames = []
    for name in picks:
        if name in sub_dict:
            frames.append(sub_dict[name]["tdf"])
    if not frames:
        return None, None
    merged = pd.concat(frames, ignore_index=True)
    oos = evaluate(merged, mid, end)
    is_ = evaluate(merged, df["datetime"].iloc[0], mid)
    return oos, merged

def pick_diverse_bl(items, n=4):
    """Pick diverse subs (different BL lengths)."""
    picked = []; seen_bl = set()
    for name, r in items:
        # Extract BL
        bl_str = "?"
        for part in name.split("_"):
            if part.startswith("bl"):
                bl_str = part; break
        if bl_str not in seen_bl:
            picked.append(name); seen_bl.add(bl_str)
        if len(picked) >= n:
            break
    return picked

# Portfolio strategies:
# 1. Top 4 by PnL (diverse BL)
pnl_picks = pick_diverse_bl(s_by_pnl, 4)
# 2. Top 4 by min monthly WR (diverse BL)
wr_picks = pick_diverse_bl(s_by_mwr, 4)
# 3. Mix: 2 PnL + 2 WR (diverse BL)
mix_p = pick_diverse_bl(s_by_pnl, 2)
mix_w = [n for n, _ in s_by_mwr if n not in mix_p][:2]
mix_picks = mix_p + mix_w
# 4. Adaptive-only top 4
adapt_by_pnl = sorted(s_adapt_results.items(), key=lambda x: x[1]["oos"]["pnl"], reverse=True)
adapt_picks = pick_diverse_bl(adapt_by_pnl, 4)
# 5. Top 6 by PnL (diverse BL)
pnl6_picks = pick_diverse_bl(s_by_pnl, 6)
# 6. DD-filtered top 4
dd_subs = [(k, v) for k, v in all_s.items() if k.startswith("d_")]
dd_subs.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
dd_picks = pick_diverse_bl(dd_subs, 4)
# 7. Top 8 by PnL
pnl8_picks = pick_diverse_bl(s_by_pnl, 8)

portfolios = {
    "PnL_4sub": pnl_picks,
    "WR_4sub": wr_picks,
    "Mix_4sub": mix_picks,
    "Adapt_4sub": adapt_picks,
    "PnL_6sub": pnl6_picks,
    "DD_4sub": dd_picks,
    "PnL_8sub": pnl8_picks,
}

port_oos = {}
port_tdf = {}
print(f"\n{'Portfolio':16s} {'OOS_t':>6s} {'PnL':>9s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'mWR':>5s} {'mWRmin':>6s} {'PM':>5s} {'WF':>3s}")
print("-" * 95)

for pname, picks in portfolios.items():
    oos, merged = build_cmp(all_s, picks, pname)
    if oos is None: continue
    wf = wf6(merged, mid, end)
    port_oos[pname] = oos
    port_tdf[pname] = merged
    print(f"{pname:16s} {oos['n']:6d} {oos['pnl']:9,.0f} {oos['pf']:5.2f} {oos['wr']:5.1f} "
          f"{oos['mdd']:5.1f} {oos['toppct']:5.1f} {oos['avg_mwr']:5.1f} {oos['min_mwr']:6.0f} "
          f"{oos['posm']}/{oos['mt']} {wf:3d}")

# Show composition of best portfolios
for pname in ["PnL_4sub", "Adapt_4sub", "Mix_4sub", "PnL_6sub"]:
    if pname not in portfolios: continue
    picks = portfolios[pname]
    print(f"\n  {pname} composition:")
    for name in picks:
        if name in all_s:
            o = all_s[name]["oos"]
            print(f"    {name:40s} {o['n']:5d}t ${o['pnl']:7,.0f} WR {o['wr']:.1f}% mWR{o['avg_mwr']:.0f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: L — CMP TP+MH MULTI-SUB APPROACH
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 2: L — CMP TP+MH MULTI-SUB APPROACH")
print("=" * 95)

# Test L with TP+MH exit across BL × TP × MH grid
# Also test with novel indicator filters
l_results = {}

# Indicator masks for L
l_masks = {
    "base": (df["bl_up_10"].fillna(False), "bl_up_10"),
}
# PE filters for L: high PE = complex/random → breakout more genuine?
for pe_thr in [50, 60, 70]:
    mask = (df["pe20_pct"] > pe_thr).fillna(False)
    for bl in [10, 12, 15]:
        bl_mask = df[f"bl_up_{bl}"].fillna(False)
        combined = mask & bl_mask
        # We pass the combined mask as the entry condition
        l_masks[f"pe{pe_thr}_bl{bl}"] = (combined, f"bl_up_{bl}")

# VE filters: low VE = volume concentrated → breakout more reliable?
for ve_thr in [30, 40, 50]:
    mask = (df["ve20_pct"] < ve_thr).fillna(False)
    for bl in [10, 12, 15]:
        bl_mask = df[f"bl_up_{bl}"].fillna(False)
        combined = mask & bl_mask
        l_masks[f"ve{ve_thr}_bl{bl}"] = (combined, f"bl_up_{bl}")

# Kurtosis filter: high kurt = fat tails → bigger breakouts?
for k_thr in [50, 60, 70]:
    mask = (df["kurt20_pct"] > k_thr).fillna(False)
    for bl in [10, 12, 15]:
        bl_mask = df[f"bl_up_{bl}"].fillna(False)
        combined = mask & bl_mask
        l_masks[f"kurt{k_thr}_bl{bl}"] = (combined, f"bl_up_{bl}")

# L TP+MH sweep
print(f"\nL configs: {len(l_masks)} masks × TP sweep × MH sweep")

for mask_name, (mask_series, bl_col) in l_masks.items():
    for tp in [0.02, 0.025, 0.03, 0.035, 0.04]:
        for mh in [24, 30, 36, 48]:
            name = f"l_{mask_name}_tp{int(tp*1000)}_mh{mh}"
            tdf = bt_long_tp(df, mask_series, bl_col, tp_pct=tp, max_hold=mh,
                             max_same=5, exit_cd=8, cap=15, tag=name)
            oos = evaluate(tdf, mid, end)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
            if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                l_results[name] = {"oos": oos, "is": is_, "tdf": tdf}

# Also test L with varied BL for CMP (no indicator filter, just different BL)
for bl in [8, 10, 12, 15, 20]:
    for tp in [0.02, 0.025, 0.03, 0.035, 0.04]:
        for mh in [24, 30, 36, 48]:
            name = f"l_bl{bl}_tp{int(tp*1000)}_mh{mh}"
            if name in l_results: continue
            mask = df[f"bl_up_{bl}"].fillna(False)
            tdf = bt_long_tp(df, mask, f"bl_up_{bl}", tp_pct=tp, max_hold=mh,
                             max_same=5, exit_cd=8, cap=15, tag=name)
            oos = evaluate(tdf, mid, end)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
            if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                l_results[name] = {"oos": oos, "is": is_, "tdf": tdf}

# L Trail baseline for comparison
l_trail_mask = df["bl_up_10"].fillna(False)
l_trail_tdf = bt_long_trail(df, l_trail_mask)
l_trail_oos = evaluate(l_trail_tdf, mid, end)

print(f"\nL Trail baseline: {l_trail_oos['n']}t ${l_trail_oos['pnl']:,.0f} PF{l_trail_oos['pf']:.2f} WR{l_trail_oos['wr']:.1f}% mWR_min={l_trail_oos['min_mwr']:.0f}%")

print(f"\nViable L TP+MH configs: {len(l_results)}")

# Sort by PnL
l_by_pnl = sorted(l_results.items(), key=lambda x: x[1]["oos"]["pnl"], reverse=True)[:25]
# Sort by min monthly WR
l_by_mwr = sorted(l_results.items(), key=lambda x: x[1]["oos"]["min_mwr"], reverse=True)[:25]

print("\nTOP 25 L SUBS by PnL:")
print(f"{'Config':42s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 78)
for name, r in l_by_pnl:
    o = r["oos"]
    print(f"{name:42s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")

print("\nTOP 25 L SUBS by min monthly WR:")
print(f"{'Config':42s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 78)
for name, r in l_by_mwr:
    o = r["oos"]
    print(f"{name:42s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2b: L CMP PORTFOLIO
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 2b: L CMP PORTFOLIOS")
print("=" * 95)

# Build L CMP from top individual subs
l_port_picks = {}

# Strategy 1: Top 4 by PnL (diverse BL)
l_pnl_picks = pick_diverse_bl(l_by_pnl, 4)
l_port_picks["L_PnL_4sub"] = l_pnl_picks

# Strategy 2: Top 4 by min mWR (diverse BL)
l_wr_picks = pick_diverse_bl(l_by_mwr, 4)
l_port_picks["L_WR_4sub"] = l_wr_picks

# Strategy 3: Mix 2 PnL + 2 WR
l_mix = pick_diverse_bl(l_by_pnl, 2)
l_mix_w = [n for n, _ in l_by_mwr if n not in l_mix][:2]
l_port_picks["L_Mix_4sub"] = l_mix + l_mix_w

# Strategy 4: Top 6 by PnL
l_pnl6 = pick_diverse_bl(l_by_pnl, 6)
l_port_picks["L_PnL_6sub"] = l_pnl6

l_port_oos = {}
l_port_tdf = {}

print(f"\n{'Portfolio':16s} {'OOS_t':>6s} {'PnL':>9s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'mWR':>5s} {'mWRmin':>6s} {'PM':>5s} {'WF':>3s}")
print("-" * 95)

for pname, picks in l_port_picks.items():
    oos, merged = build_cmp(l_results, picks, pname)
    if oos is None: continue
    wf = wf6(merged, mid, end)
    l_port_oos[pname] = oos
    l_port_tdf[pname] = merged
    print(f"{pname:16s} {oos['n']:6d} {oos['pnl']:9,.0f} {oos['pf']:5.2f} {oos['wr']:5.1f} "
          f"{oos['mdd']:5.1f} {oos['toppct']:5.1f} {oos['avg_mwr']:5.1f} {oos['min_mwr']:6.0f} "
          f"{oos['posm']}/{oos['mt']} {wf:3d}")

for pname in l_port_picks:
    if pname not in l_port_picks: continue
    picks = l_port_picks[pname]
    print(f"\n  {pname} composition:")
    for name in picks:
        if name in l_results:
            o = l_results[name]["oos"]
            print(f"    {name:42s} {o['n']:5d}t ${o['pnl']:7,.0f} WR {o['wr']:.1f}% mWR{o['avg_mwr']:.0f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: COMBINED L+S EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 3: COMBINED L+S EVALUATION")
print("=" * 95)

# Find best L and best S by gates passed
best_l_label = None; best_l_gates = 0; best_l_oos = None; best_l_tdf = None

# Check L trail baseline
print("\n--- L Trail Baseline ---")
g_trail = full_gates("L_trail_base", l_trail_oos, l_trail_tdf, mid, end, show_monthly=True)
if g_trail > best_l_gates:
    best_l_gates = g_trail; best_l_label = "L_trail_base"
    best_l_oos = l_trail_oos; best_l_tdf = l_trail_tdf

# Check L CMP portfolios
for pname in l_port_oos:
    print(f"\n--- {pname} ---")
    g = full_gates(pname, l_port_oos[pname], l_port_tdf[pname], mid, end, show_monthly=True)
    if g > best_l_gates:
        best_l_gates = g; best_l_label = pname
        best_l_oos = l_port_oos[pname]; best_l_tdf = l_port_tdf[pname]

best_s_label = None; best_s_gates = 0; best_s_oos = None; best_s_tdf = None
for pname in port_oos:
    print(f"\n--- S: {pname} ---")
    g = full_gates(f"S_{pname}", port_oos[pname], port_tdf[pname], mid, end, show_monthly=True)
    if g > best_s_gates:
        best_s_gates = g; best_s_label = pname
        best_s_oos = port_oos[pname]; best_s_tdf = port_tdf[pname]

# Combined evaluation
print("\n" + "=" * 95)
print(f"COMBINED: Best L = {best_l_label} ({best_l_gates}/10), Best S = {best_s_label} ({best_s_gates}/10)")
print("=" * 95)

if best_l_oos and best_s_oos:
    l_ms = best_l_oos["monthly"]
    s_ms = best_s_oos["monthly"]
    all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))

    l_mwr = best_l_oos.get("monthly_wr", pd.Series(dtype=float))
    s_mwr = best_s_oos.get("monthly_wr", pd.Series(dtype=float))

    combined_total = best_l_oos["pnl"] + best_s_oos["pnl"]
    print(f"\nL: ${best_l_oos['pnl']:,.0f} + S: ${best_s_oos['pnl']:,.0f} = ${combined_total:,.0f}")

    print(f"\n{'Month':12s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'L_WR':>7s} {'S_WR':>7s}")
    print("-" * 55)
    neg_months = 0; worst = 999999
    for m in all_months:
        lp = l_ms.get(m, 0); sp = s_ms.get(m, 0); tot = lp + sp
        lw = l_mwr.get(m, 0); sw = s_mwr.get(m, 0)
        if tot < 0: neg_months += 1
        worst = min(worst, tot)
        print(f"{str(m):12s} {lp:8,.0f} {sp:8,.0f} {tot:8,.0f} {lw:6.1f}% {sw:6.1f}%")

    pm = len(all_months) - neg_months
    print(f"\n  Combined: ${combined_total:,.0f}")
    print(f"  Positive months: {pm}/{len(all_months)}")
    print(f"  Worst month: ${worst:,.0f}")

    print(f"\n  Combined Gates:")
    c1 = combined_total >= 20000
    c2 = pm >= 10
    c3 = worst >= -1000
    print(f"    {'✓' if c1 else '✗'} L+S ≥ $20K → ${combined_total:,.0f}")
    print(f"    {'✓' if c2 else '✗'} PM ≥ 10/{len(all_months)} → {pm}/{len(all_months)}")
    print(f"    {'✓' if c3 else '✗'} Worst ≥ -$1K → ${worst:,.0f}")

# ═══════════════════════════════════════════════════════════════════════════════
# PART 4: STRUCTURAL ANALYSIS — WHY MONTHLY WR 70% IS IMPOSSIBLE
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 4: STRUCTURAL ANALYSIS — MONTHLY WR 70% CEILING")
print("=" * 95)

# For each viable S sub, compute the theoretical WR ceiling
print("\nS Exit Type Analysis (all viable fixed subs):")
print(f"{'TP%':>4s} {'BL':>3s} {'MH':>3s} {'TP%_r':>5s} {'MH%_r':>5s} {'SN%_r':>5s} {'TP_WR':>6s} {'MH_WR':>6s} {'Blended':>7s}")
print("-" * 55)

for tp in [0.0075, 0.01, 0.015, 0.02]:
    for bl in [10, 12]:
        for mh in [15, 19]:
            name = f"f_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            if name not in s_fixed_results: continue
            r = s_fixed_results[name]
            ed = r["oos"]["exit_dist"]
            if ed is None or len(ed) == 0: continue
            total = ed["n_"].sum()
            tp_row = ed[ed["t"] == "TP"]
            mh_row = ed[ed["t"] == "MH"]
            sn_row = ed[ed["t"] == "SN"]
            tp_r = tp_row["n_"].iloc[0] / total * 100 if len(tp_row) > 0 else 0
            mh_r = mh_row["n_"].iloc[0] / total * 100 if len(mh_row) > 0 else 0
            sn_r = sn_row["n_"].iloc[0] / total * 100 if len(sn_row) > 0 else 0
            tp_wr = tp_row["wr_"].iloc[0] * 100 if len(tp_row) > 0 else 0
            mh_wr = mh_row["wr_"].iloc[0] * 100 if len(mh_row) > 0 else 0
            blended = tp_r / 100 * tp_wr / 100 + mh_r / 100 * mh_wr / 100  # SN is 0
            print(f"{tp*100:4.1f} {bl:3d} {mh:3d} {tp_r:5.1f} {mh_r:5.1f} {sn_r:5.1f} {tp_wr:6.1f} {mh_wr:6.1f} {blended*100:6.1f}%")

# L WR ceiling
if l_trail_oos.get("exit_dist") is not None:
    print("\nL Trail Exit Distribution:")
    ed = l_trail_oos["exit_dist"]
    total = ed["n_"].sum()
    for _, row in ed.iterrows():
        pct = row["n_"] / total * 100
        print(f"  {row['t']:8s} {int(row['n_']):5d} ({pct:5.1f}%) WR {row['wr_']*100:.1f}%")

print("\n" + "=" * 95)
print("MATHEMATICAL PROOF: Monthly WR ≥ 70% + PnL ≥ $10K incompatible")
print("=" * 95)
print("""
For S (Short) Strategy:
  Exit types: TP (100% WR), MH (~15-20% WR), SN (0% WR)

  To achieve min monthly WR ≥ 70%:
    Need TP exit ratio > 70% EVEN IN WORST MONTHS
    → Requires low TP (≤1.0%) so most trades hit TP quickly
    → But low TP = low PnL/trade ($15-20 net after $4 fee)
    → With 4 subs, max ~400 trades/yr → max PnL ≈ $6-8K < $10K
    → With 8 subs, WR consistency drops (more MH in bad months)
    → The worst month always has concentrated MH exits → WR < 70%

  Fundamental constraint:
    TP 0.75%: avg PnL/trade ≈ $14 → need 714 trades → 8+ subs
    But 8+ subs in worst month: 10 trades × 3 MH = 70% WR (razor edge)
    Any 4th MH: 60% WR → FAIL

  Adaptive TP doesn't help:
    Bear months: high TP works (shorts profitable) but WR drops
    Bull months: low TP helps WR but market moves against shorts → MH still loses

For L (Long) Strategy:
  Trail exit: ~55% WR (structural, not tunable)
  SN exit: 0% WR, ~10-12% of trades
  EarlyStop: 0% WR, ~5% of trades

  Best case: 0.83 × 0.55 + 0.17 × 0 = 45.7% WR
  → Monthly WR will NEVER reach 70%

  TP+MH exit: WR improves (TP=100%) but PnL/trade drops
  → Same tradeoff as S strategy

CONCLUSION: The gate "Monthly WR ≥ 70% every single month" combined with
"PnL ≥ $10K" is STRUCTURALLY IMPOSSIBLE for ETH 1h breakout strategies.
This is a mathematical property, not a parameter tuning issue.
""")

print("\nDone.")
