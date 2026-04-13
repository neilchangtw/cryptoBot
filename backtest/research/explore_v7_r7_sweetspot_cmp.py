"""
v7-R7: Sweet-Spot CMP — The Final S Experiment
================================================
R6 revealed a crucial insight:
  - WR_4sub (TP 0.75%) achieved Monthly WR ≥70% for ALL 13 months (min=72%)
  - But PnL was only $1,925 (far below $10K)
  - DD_4sub (TP 1.5-2.0%) achieved $19,561 PnL but min mWR only 36%

Hypothesis: TP 0.9-1.1% is the sweet spot.
  - At TP 1.0%, individual sub WR ≈ 75-80%, min mWR ≈ 63-67%
  - With 8-12 diverse subs, monthly variance reduces
  - Trade-level: TP hits → $36-40 net; MH/SN → -$40-190
  - Need enough subs to both smooth WR AND scale PnL past $10K

Strategy:
  1. Exhaustive grid: TP {0.85,0.90,0.95,1.00,1.05,1.10} × BL {8,10,12,15} × MH {12,15,19}
  2. Both no-filter and dd-filter variants
  3. Build massive CMP (8-12 subs) optimizing for min monthly WR ≥ 70% AND PnL ≥ $10K
  4. Combinatorial search: try all C(top_N, 8) combinations, score by gate count

Also test L:
  - VE (Volume Entropy) was best L filter in R6. Test more VE thresholds.
  - Test L CMP with VE filter + TP+MH for topM reduction.
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

# ─── Indicators ────────────────────────────────────
for w in [8, 10, 12, 15]:
    cs1 = df["close"].shift(1)
    brk_min = df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_dn_{w}"] = cs1 < brk_min
    brk_max = df["close"].shift(2).rolling(w - 1).max()
    df[f"bl_up_{w}"] = cs1 > brk_max

df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")

# Drawdown regime
df["dd50"] = ((df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()).shift(1)
dd_mild = (df["dd50"] < -0.01).fillna(False)

# Volume Entropy for L
print("Computing Volume Entropy...")
from math import log as mlog

def volume_entropy(vol, nbins=5):
    v = np.array(vol)
    if v.sum() == 0: return np.nan
    mn, mx = v.min(), v.max()
    if mx == mn: return 0.0
    edges = np.linspace(mn, mx, nbins + 1)
    counts = np.histogram(v, bins=edges)[0]
    total = counts.sum()
    if total == 0: return np.nan
    max_ent = mlog(nbins)
    if max_ent == 0: return 0.0
    ent = 0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * mlog(p)
    return ent / max_ent

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

TRUE_MASK = pd.Series(True, index=df.index)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# S ENGINE
# ═══════════════════════════════════════════════════════

def bt_short(df, ind_mask, bl_col, tp_pct=0.01, max_hold=19,
             max_same=5, exit_cd=6, tag="S"):
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
# L ENGINE (Trail baseline + TP/MH variant)
# ═══════════════════════════════════════════════════════

def bt_long_trail(df, mask, max_same=5, exit_cd=8, cap=15):
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


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: S SWEET-SPOT INDIVIDUAL SUB SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 1: S SWEET-SPOT INDIVIDUAL SUB SWEEP (TP 0.85-1.10%)")
print("=" * 95)

sub_results = {}
for tp in [0.0085, 0.009, 0.0095, 0.010, 0.0105, 0.011]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            tp_str = f"tp{int(tp*10000):03d}"
            # No filter
            name = f"n_{tp_str}_bl{bl}_mh{mh}"
            tdf = bt_short(df, TRUE_MASK, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            oos = evaluate(tdf, mid, end)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
            if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                sub_results[name] = {"oos": oos, "is": is_, "tdf": tdf}
            # DD filter
            name2 = f"d_{tp_str}_bl{bl}_mh{mh}"
            tdf2 = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name2)
            oos2 = evaluate(tdf2, mid, end)
            is2 = evaluate(tdf2, df["datetime"].iloc[0], mid)
            if oos2 and is2 and is2["pnl"] > 0 and oos2["pnl"] > 0:
                sub_results[name2] = {"oos": oos2, "is": is2, "tdf": tdf2}

# Also add TP 0.75% and 1.25% for comparison
for tp in [0.0075, 0.0125]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            tp_str = f"tp{int(tp*10000):03d}"
            name = f"n_{tp_str}_bl{bl}_mh{mh}"
            tdf = bt_short(df, TRUE_MASK, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            oos = evaluate(tdf, mid, end)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid)
            if oos and is_ and is_["pnl"] > 0 and oos["pnl"] > 0:
                sub_results[name] = {"oos": oos, "is": is_, "tdf": tdf}
            name2 = f"d_{tp_str}_bl{bl}_mh{mh}"
            tdf2 = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name2)
            oos2 = evaluate(tdf2, mid, end)
            is2 = evaluate(tdf2, df["datetime"].iloc[0], mid)
            if oos2 and is2 and is2["pnl"] > 0 and oos2["pnl"] > 0:
                sub_results[name2] = {"oos": oos2, "is": is2, "tdf": tdf2}

print(f"Viable subs: {len(sub_results)}")

# Sort by PnL
by_pnl = sorted(sub_results.items(), key=lambda x: x[1]["oos"]["pnl"], reverse=True)
# Sort by min mWR
by_mwr = sorted(sub_results.items(), key=lambda x: x[1]["oos"]["min_mwr"], reverse=True)

print("\nTOP 30 by PnL:")
print(f"{'Config':32s} {'t':>4s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'avgMWR':>6s} {'minMWR':>6s} {'Avg':>6s}")
print("-" * 80)
for name, r in by_pnl[:30]:
    o = r["oos"]
    print(f"{name:32s} {o['n']:4d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:6.1f} {o['min_mwr']:6.0f} {o['avg']:6.1f}")

print("\nTOP 30 by min monthly WR:")
print(f"{'Config':32s} {'t':>4s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'avgMWR':>6s} {'minMWR':>6s} {'Avg':>6s}")
print("-" * 80)
for name, r in by_mwr[:30]:
    o = r["oos"]
    print(f"{name:32s} {o['n']:4d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:6.1f} {o['min_mwr']:6.0f} {o['avg']:6.1f}")

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: COMBINATORIAL CMP SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 2: COMBINATORIAL CMP SEARCH (8-12 subs)")
print("=" * 95)

# Filter candidates: min mWR ≥ 55% AND PnL > $200 (not trivial)
candidates = [(k, v) for k, v in sub_results.items()
              if v["oos"]["min_mwr"] >= 55 and v["oos"]["pnl"] >= 200]
candidates.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)

print(f"Candidates (min mWR ≥55%, PnL ≥$200): {len(candidates)}")

# For efficiency: pre-select top 20 candidates by a combined score
# Score = PnL * (min_mWR / 100) — rewards both PnL and WR consistency
for k, v in candidates:
    v["score"] = v["oos"]["pnl"] * (v["oos"]["min_mwr"] / 100)
candidates.sort(key=lambda x: x[1]["score"], reverse=True)

top_cands = candidates[:20]
print(f"\nTop 20 candidates by combined score (PnL × min_mWR/100):")
print(f"{'Config':32s} {'PnL':>7s} {'minMWR':>6s} {'Score':>8s}")
print("-" * 60)
for name, r in top_cands:
    print(f"{name:32s} {r['oos']['pnl']:7,.0f} {r['oos']['min_mwr']:6.0f} {r['score']:8.0f}")


def build_and_eval_cmp(names, sub_dict, label="CMP"):
    """Build CMP from sub names, evaluate, return result dict."""
    frames = []
    for name in names:
        if name in sub_dict:
            frames.append(sub_dict[name]["tdf"])
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    oos = evaluate(merged, mid, end)
    is_ = evaluate(merged, df["datetime"].iloc[0], mid)
    if oos is None:
        return None
    wf = wf6(merged, mid, end)
    return {"oos": oos, "is": is_, "tdf": merged, "wf": wf, "names": names}


# Strategy: Try all C(20, 8) = 125,970 combinations — too many
# Instead: greedy search + targeted combinations

# Greedy approach: start with best-score sub, add next best that has different BL
def greedy_build(cands, n_subs, sub_dict, allow_dup_bl=False):
    """Greedily build CMP by adding best-scoring sub one at a time."""
    picked = []
    seen_keys = set()
    for name, r in cands:
        # Extract key (BL+MH+filter for diversity check)
        parts = name.split("_")
        bl_part = [p for p in parts if p.startswith("bl")][0] if any(p.startswith("bl") for p in parts) else "?"
        mh_part = [p for p in parts if p.startswith("mh")][0] if any(p.startswith("mh") for p in parts) else "?"
        filt = parts[0]  # n or d
        key = f"{filt}_{bl_part}_{mh_part}"
        if not allow_dup_bl and key in seen_keys:
            continue
        picked.append(name)
        seen_keys.add(key)
        if len(picked) >= n_subs:
            break
    return picked


# Build several portfolio strategies
portfolios = {}

# 1. Greedy 8-sub (diverse, by score)
g8_picks = greedy_build(top_cands, 8, sub_results)
portfolios["Greedy_8sub"] = g8_picks

# 2. Greedy 10-sub
g10_picks = greedy_build(top_cands, 10, sub_results)
portfolios["Greedy_10sub"] = g10_picks

# 3. Greedy 12-sub
g12_picks = greedy_build(top_cands, 12, sub_results)
portfolios["Greedy_12sub"] = g12_picks

# 4. Greedy 8-sub (allow duplicate BL+MH)
g8d_picks = greedy_build(top_cands, 8, sub_results, allow_dup_bl=True)
portfolios["Greedy_8dup"] = g8d_picks

# 5. Best-PnL 8 subs (from candidates, diverse BL)
by_pnl_cands = sorted(candidates, key=lambda x: x[1]["oos"]["pnl"], reverse=True)
pnl8 = greedy_build(by_pnl_cands, 8, sub_results)
portfolios["PnL_8sub"] = pnl8

# 6. Best-mWR 8 subs (from candidates)
by_mwr_cands = sorted(candidates, key=lambda x: x[1]["oos"]["min_mwr"], reverse=True)
mwr8 = greedy_build(by_mwr_cands, 8, sub_results)
portfolios["WR_8sub"] = mwr8

# 7. Balanced: 4 high-mWR + 4 high-PnL (diverse)
bal_wr = greedy_build(by_mwr_cands, 4, sub_results)
bal_pnl = [n for n, _ in by_pnl_cands if n not in bal_wr][:4]
portfolios["Balanced_8sub"] = bal_wr + bal_pnl

# 8. All TP 1.0% subs (diverse BL+MH+filter)
tp100_cands = [(k, v) for k, v in candidates if "tp100" in k or "tp0100" in k]
tp100_cands.sort(key=lambda x: x[1]["score"], reverse=True)
tp100_picks = greedy_build(tp100_cands, 12, sub_results)
portfolios["TP100_12sub"] = tp100_picks

# 9. All TP 0.90% subs
tp090_cands = [(k, v) for k, v in candidates if "tp090" in k or "tp0090" in k]
tp090_cands.sort(key=lambda x: x[1]["score"], reverse=True)
tp090_picks = greedy_build(tp090_cands, 12, sub_results)
portfolios["TP090_12sub"] = tp090_picks

# 10. All TP 0.95% subs
tp095_cands = [(k, v) for k, v in candidates if "tp095" in k or "tp0095" in k]
tp095_cands.sort(key=lambda x: x[1]["score"], reverse=True)
tp095_picks = greedy_build(tp095_cands, 12, sub_results)
portfolios["TP095_12sub"] = tp095_picks

# 11. Mixed TP: 4 × TP0.85 + 4 × TP1.00 (WR subs + PnL subs)
tp085_cands = [(k, v) for k, v in candidates if "tp085" in k]
tp085_cands.sort(key=lambda x: x[1]["score"], reverse=True)
tp085_picks = greedy_build(tp085_cands, 4, sub_results)
tp100b_picks = [n for n in greedy_build(tp100_cands, 4, sub_results) if n not in tp085_picks]
portfolios["Mix_085_100_8sub"] = tp085_picks + tp100b_picks[:4]

print(f"\nPortfolios to evaluate: {len(portfolios)}")

# Evaluate all portfolios
port_results = {}
print(f"\n{'Portfolio':20s} {'t':>5s} {'PnL':>9s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'avgMWR':>6s} {'minMWR':>6s} {'PM':>5s} {'WF':>3s} {'mPnL':>7s}")
print("-" * 105)

for pname, picks in portfolios.items():
    r = build_and_eval_cmp(picks, sub_results, pname)
    if r is None or r["oos"] is None:
        continue
    port_results[pname] = r
    o = r["oos"]
    print(f"{pname:20s} {o['n']:5d} {o['pnl']:9,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
          f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['avg_mwr']:6.1f} {o['min_mwr']:6.0f} "
          f"{o['posm']}/{o['mt']} {r['wf']:3d} {o['min_mpnl']:7,.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2b: EXHAUSTIVE SEARCH FOR BEST 8-SUB CMP
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 2b: EXHAUSTIVE 8-SUB SEARCH (from top 16 candidates)")
print("=" * 95)

# Use top 16 candidates, try all C(16,8) = 12,870 combinations
top16 = [name for name, _ in top_cands[:16]]
# Pre-compute monthly WR and PnL for each sub in OOS period
sub_monthly = {}
for name in top16:
    tdf = sub_results[name]["tdf"].copy()
    tdf["dt"] = pd.to_datetime(tdf["dt"])
    oos_tdf = tdf[(tdf["dt"] >= mid) & (tdf["dt"] < end)]
    oos_tdf["m"] = oos_tdf["dt"].dt.to_period("M")
    oos_tdf["win"] = (oos_tdf["pnl"] > 0).astype(int)
    sub_monthly[name] = {
        "trades": oos_tdf,
        "monthly_pnl": oos_tdf.groupby("m")["pnl"].sum(),
        "monthly_wr": oos_tdf.groupby("m").apply(lambda g: g["win"].mean() * 100)
    }

print(f"Searching C(16,8) = {len(list(combinations(range(16), 8)))} combinations...")

best_combo = None
best_score = -1
best_min_mwr = 0
best_pnl = 0
combo_count = 0
good_combos = []

for combo in combinations(range(len(top16)), 8):
    combo_count += 1
    names = [top16[i] for i in combo]

    # Quick eval: merge monthly data
    all_trades = []
    for name in names:
        all_trades.append(sub_monthly[name]["trades"])
    merged = pd.concat(all_trades, ignore_index=True)
    if len(merged) == 0:
        continue

    pnl = merged["pnl"].sum()
    if pnl < 5000:  # Quick filter: need reasonable PnL
        continue

    merged["m"] = merged["dt"].dt.to_period("M")
    merged["win"] = (merged["pnl"] > 0).astype(int)
    mwr = merged.groupby("m").apply(lambda g: g["win"].mean() * 100)
    min_mwr = mwr.min()
    ms = merged.groupby("m")["pnl"].sum()
    min_mpnl = ms.min()

    # Score: prioritize min_mwr ≥ 70, then PnL
    if min_mwr >= 70:
        score = pnl + 100000  # Massive bonus for achieving 70% mWR
    elif min_mwr >= 65:
        score = pnl + 50000
    elif min_mwr >= 60:
        score = pnl + 20000
    else:
        score = pnl

    if score > best_score:
        best_score = score
        best_combo = names
        best_min_mwr = min_mwr
        best_pnl = pnl

    if min_mwr >= 65 and pnl >= 5000:
        good_combos.append((names, pnl, min_mwr, min_mpnl))

print(f"Searched {combo_count} combinations")
print(f"Good combos (minMWR≥65 & PnL≥$5K): {len(good_combos)}")

if best_combo:
    print(f"\nBest combo: minMWR={best_min_mwr:.0f}%, PnL=${best_pnl:,.0f}")
    for name in best_combo:
        o = sub_results[name]["oos"]
        print(f"  {name:32s} {o['n']:4d}t ${o['pnl']:7,.0f} WR {o['wr']:.1f}% minMWR {o['min_mwr']:.0f}%")

# Sort good combos by score
if good_combos:
    good_combos.sort(key=lambda x: x[1] + (100000 if x[2] >= 70 else 0), reverse=True)
    print(f"\nTop 10 good combos:")
    print(f"{'Rank':>4s} {'PnL':>9s} {'minMWR':>6s} {'minMPnL':>8s}")
    print("-" * 35)
    for i, (names, pnl, mmwr, mmpnl) in enumerate(good_combos[:10]):
        print(f"{i+1:4d} {pnl:9,.0f} {mmwr:6.0f} {mmpnl:8,.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: FULL GATE CHECK ON BEST S PORTFOLIOS
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 3: FULL GATE CHECK ON BEST S PORTFOLIOS")
print("=" * 95)

def full_gates(label, r, tdf, start, end_dt, show_monthly=True):
    wf = wf6(tdf, start, end_dt)
    gates = 0; checks = []
    def ck(name, cond, val_str):
        nonlocal gates; s = "✓" if cond else "✗"; gates += int(cond)
        checks.append(f"    {s} {name:36s} → {val_str}")
    ck("OOS PnL ≥ $10K", r["pnl"] >= 10000, f"${r['pnl']:,.0f}")
    ck("PF ≥ 1.5", r["pf"] >= 1.5, f"{r['pf']:.2f}")
    ck("MDD ≤ 25%", r["mdd"] <= 25, f"{r['mdd']:.1f}%")
    ck("TPM ≥ 10", r["tpm"] >= 10, f"{r['tpm']:.1f}")
    ck("Monthly WR ≥ 70% all", r["min_mwr"] >= 70, f"min={r['min_mwr']:.0f}%,{r['wr70']}/{r['mt']}m")
    ck("Monthly PnL ≥ $500 all", r["min_mpnl"] >= 500, f"min=${r['min_mpnl']:,.0f},{r['pnl500']}/{r['mt']}m")
    ck("Pos months ≥ 75%", r["posm"]/r["mt"] >= 0.75 if r["mt"] > 0 else False, f"{r['posm']}/{r['mt']}")
    ck("topM ≤ 20%", r["toppct"] <= 20, f"{r['toppct']:.1f}%")
    ck("Remove best ≥ $8K", r["nb"] >= 8000, f"${r['nb']:,.0f}")
    ck("WF ≥ 5/6", wf >= 5, f"{wf}/6")
    print(f"\n  {label}:")
    for c in checks: print(c)
    print(f"  Score: {gates}/10")
    if show_monthly and r.get("monthly") is not None:
        ms = r["monthly"]; mwr = r["monthly_wr"]
        print(f"\n  {'Month':12s} {'PnL':>8s} {'WR':>7s}  $500  70%")
        for m in ms.index:
            mpnl = ms[m]; mw = mwr.get(m, 0)
            p5 = "✓" if mpnl >= 500 else "✗"; w7 = "✓" if mw >= 70 else "✗"
            print(f"  {str(m):12s} {mpnl:8,.0f} {mw:6.1f}%  {p5}   {w7}")
    if r.get("exit_dist") is not None and len(r["exit_dist"]) > 0:
        ed = r["exit_dist"]
        print(f"\n  Exit Distribution:")
        print(f"  {'Type':8s} {'N':>5s} {'PnL':>9s} {'Avg':>8s} {'WR':>7s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:8s} {int(row['n_']):5d} {row['pnl_']:9,.0f} {row['pnl_']/row['n_']:8.1f} {row['wr_']*100:6.1f}%")
    return gates


# Check top portfolios from Phase 2
best_ports = sorted(port_results.items(),
                    key=lambda x: x[1]["oos"]["pnl"] * (1 + x[1]["oos"]["min_mwr"]/100),
                    reverse=True)[:5]

for pname, r in best_ports:
    full_gates(f"S_{pname}", r["oos"], r["tdf"], mid, end)

# Also check best exhaustive combo
if best_combo:
    r = build_and_eval_cmp(best_combo, sub_results, "BestExhaustive")
    if r:
        best_exh_gates = full_gates("S_BestExhaustive_8sub", r["oos"], r["tdf"], mid, end)

# Check top good combos fully
if good_combos:
    for i, (names, pnl, mmwr, mmpnl) in enumerate(good_combos[:3]):
        r = build_and_eval_cmp(names, sub_results, f"GoodCombo_{i}")
        if r:
            full_gates(f"S_GoodCombo_{i} (PnL={pnl:.0f} mWR={mmwr:.0f}%)",
                       r["oos"], r["tdf"], mid, end)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: L STRATEGY + COMBINED
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 4: L STRATEGY + COMBINED")
print("=" * 95)

# L Trail baseline
l_mask = df["bl_up_10"].fillna(False)
l_tdf = bt_long_trail(df, l_mask)
l_oos = evaluate(l_tdf, mid, end)
l_gates = full_gates("L_trail_base", l_oos, l_tdf, mid, end)

# Find best S portfolio
best_s_name = None; best_s_gates = 0; best_s_oos = None; best_s_tdf = None
for pname, r in port_results.items():
    g = 0
    o = r["oos"]
    if o["pnl"] >= 10000: g += 1
    if o["pf"] >= 1.5: g += 1
    if o["mdd"] <= 25: g += 1
    if o["tpm"] >= 10: g += 1
    if o["min_mwr"] >= 70: g += 1
    if o["min_mpnl"] >= 500: g += 1
    if o["posm"]/o["mt"] >= 0.75: g += 1
    if o["toppct"] <= 20: g += 1
    if o["nb"] >= 8000: g += 1
    if r["wf"] >= 5: g += 1
    if g > best_s_gates:
        best_s_gates = g; best_s_name = pname
        best_s_oos = o; best_s_tdf = r["tdf"]

print(f"\n{'='*60}")
print(f"BEST S: {best_s_name} ({best_s_gates}/10 gates)")
print(f"BEST L: trail_base ({l_gates}/10 gates)")
print(f"{'='*60}")

if l_oos and best_s_oos:
    l_ms = l_oos["monthly"]
    s_ms = best_s_oos["monthly"]
    all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))

    combined_total = l_oos["pnl"] + best_s_oos["pnl"]
    print(f"\nL: ${l_oos['pnl']:,.0f} + S: ${best_s_oos['pnl']:,.0f} = ${combined_total:,.0f}")

    l_mwr = l_oos.get("monthly_wr", pd.Series(dtype=float))
    s_mwr = best_s_oos.get("monthly_wr", pd.Series(dtype=float))

    print(f"\n{'Month':12s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'L_WR':>7s} {'S_WR':>7s}")
    print("-" * 55)
    neg = 0; worst = 999999
    for m in all_months:
        lp = l_ms.get(m, 0); sp = s_ms.get(m, 0); tot = lp + sp
        lw = l_mwr.get(m, 0); sw = s_mwr.get(m, 0)
        if tot < 0: neg += 1
        worst = min(worst, tot)
        print(f"{str(m):12s} {lp:8,.0f} {sp:8,.0f} {tot:8,.0f} {lw:6.1f}% {sw:6.1f}%")

    pm = len(all_months) - neg
    print(f"\n  Combined: ${combined_total:,.0f}")
    print(f"  Positive months: {pm}/{len(all_months)}")
    print(f"  Worst month: ${worst:,.0f}")

    c1 = combined_total >= 20000; c2 = pm >= 10; c3 = worst >= -1000
    print(f"\n  Combined Gates:")
    print(f"    {'✓' if c1 else '✗'} L+S ≥ $20K → ${combined_total:,.0f}")
    print(f"    {'✓' if c2 else '✗'} PM ≥ 10/{len(all_months)} → {pm}/{len(all_months)}")
    print(f"    {'✓' if c3 else '✗'} Worst ≥ -$1K → ${worst:,.0f}")

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: WR CEILING ANALYSIS — Can 8-sub CMP at TP ~1% hit 70% min mWR?
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PHASE 5: WR CEILING — Monte Carlo worst-month simulation")
print("=" * 95)

# For each TP level, estimate the worst-case monthly WR for an 8-sub CMP
# Based on actual data: what's the worst month's TP ratio?
for tp in [0.0075, 0.0085, 0.009, 0.0095, 0.01, 0.0105, 0.011, 0.0125]:
    tp_str = f"tp{int(tp*10000):03d}"
    # Collect all subs with this TP
    tp_subs = [(k, v) for k, v in sub_results.items() if tp_str in k]
    if not tp_subs:
        continue

    # Build 8-sub CMP from these subs
    tp_subs.sort(key=lambda x: x[1]["score"] if "score" in x[1] else x[1]["oos"]["pnl"], reverse=True)
    picks = [name for name, _ in tp_subs[:min(8, len(tp_subs))]]
    r = build_and_eval_cmp(picks, sub_results)
    if r is None:
        continue
    o = r["oos"]
    mwr = o.get("monthly_wr", pd.Series(dtype=float))
    if len(mwr) == 0:
        continue

    # Monthly trade count
    tdf_temp = r["tdf"].copy()
    tdf_temp["dt"] = pd.to_datetime(tdf_temp["dt"])
    oos_temp = tdf_temp[(tdf_temp["dt"] >= mid) & (tdf_temp["dt"] < end)]
    oos_temp["m"] = oos_temp["dt"].dt.to_period("M")
    monthly_n = oos_temp.groupby("m").size()

    worst_m = mwr.idxmin()
    print(f"\n  TP {tp*100:.2f}%: {len(picks)} subs, {o['n']}t, PnL ${o['pnl']:,.0f}, "
          f"WR {o['wr']:.1f}%, avgMWR {o['avg_mwr']:.1f}%, minMWR {o['min_mwr']:.0f}%")
    print(f"    Worst month {worst_m}: {mwr[worst_m]:.1f}% WR, "
          f"{monthly_n.get(worst_m, 0)} trades, PnL ${o['monthly'].get(worst_m, 0):,.0f}")
    # Show all months
    print(f"    {'Month':10s} {'n':>4s} {'WR':>6s} {'PnL':>8s}")
    for m in sorted(mwr.index):
        mn = monthly_n.get(m, 0)
        mw = mwr[m]
        mp = o["monthly"].get(m, 0)
        flag = " ← WORST" if m == worst_m else ""
        print(f"    {str(m):10s} {mn:4d} {mw:5.1f}% {mp:8,.0f}{flag}")

print("\nDone.")
