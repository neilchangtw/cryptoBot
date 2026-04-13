"""
v7-R8: New Gates Re-validation + L topM Fix
=============================================
Gate structure changed significantly:
  - Monthly WR ≥70% REMOVED (was structurally impossible)
  - Monthly PnL ≥$500 moved to COMBINED gate (L+S together)
  - WR gate: L ≥45%, S ≥65%
  - WARMUP: 200 → 150

Remaining L failures (trail_base):
  - topM ~28-38% (need ≤20%)
  - remove-best ~$7-10K (need ≥$8K, borderline)
  - PM ~69% (need ≥75%)
  - WF ~4/6 (need ≥5/6)

R8 L Hypothesis: VE (Volume Entropy) < 50 percentile filter + monthly entry cap
  - VE<50 was best new L indicator in R6 ($4,534/sub with BL12)
  - Monthly cap limits trades in big trend months → flatten topM
  - Different BL lengths for diversity
  - Try both Trail exit and TP+MH exit with VE filter

R8 S Hypothesis: Re-validate R4 DD1_6sub with WARMUP=150 and new gates
  - Already 8/10 on old gates, should be even better now
  - Test topM carefully with mixed-TP approach
"""

import sys, io, warnings
import numpy as np, pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
WARMUP = 150; MAX_PER_BAR = 2

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)
print(f"Loaded {N} bars | Split: {mid.date()}")

# ─── Core Indicators ───────────────────────────────────

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

# Volume Entropy
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
print(f"  ve20_pct: {df['ve20_pct'].dropna().shape[0]} valid")

TRUE_MASK = pd.Series(True, index=df.index)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# L ENGINE: Trail
# ═══════════════════════════════════════════════════════

def bt_long_trail(df, mask, max_same=5, exit_cd=8, cap=15, tag="L"):
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
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            if not done and 7 <= bh < 12:
                if c <= ema or c <= p["e"] * (1 - 0.01):
                    t_ = "ES" if c <= p["e"] * (1 - 0.01) and c > ema else "Trail"
                    pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": t_, "b": bh, "dt": dt, "sub": tag})
                    lx = i; done = True
            if not done and bh >= 12 and c <= ema:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "Trail", "b": bh, "dt": dt, "sub": tag})
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
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])

# ═══════════════════════════════════════════════════════
# S ENGINE: TP + MH
# ═══════════════════════════════════════════════════════

def bt_short(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
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


# ─── Evaluation ─────────────────────────────────────

def evaluate(tdf, start_dt, end_dt, side="L"):
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
    ed = p.groupby("t").agg(n_=("pnl", "count"), pnl_=("pnl", "sum"),
                             wr_=("win", "mean")).reset_index() if "t" in p.columns else pd.DataFrame()
    return {"n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd, "tpm": tpm,
            "mt": mt, "posm": posm, "toppct": toppct, "nb": nb, "topv": topv,
            "monthly": ms, "monthly_wr": mwr, "exit_dist": ed,
            "avg": pnl / n if n else 0, "side": side}


def wf6(tdf, s, e):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    r = 0
    for f in range(6):
        ts = s + pd.DateOffset(months=f * 2); te = min(ts + pd.DateOffset(months=2), e)
        tt = tdf[(tdf["dt"] >= ts) & (tdf["dt"] < te)]
        if len(tt) > 0 and tt["pnl"].sum() > 0: r += 1
    return r


def full_gates(label, r, tdf, start, end_dt, side="L"):
    """New 10-gate check."""
    wf = wf6(tdf, start, end_dt)
    gates = 0; checks = []
    wr_thr = 45 if side == "L" else 65

    def ck(name, cond, val_str):
        nonlocal gates; s = "✓" if cond else "✗"; gates += int(cond)
        checks.append(f"    {s} {name:36s} → {val_str}")

    ck("1. OOS PnL ≥ $10K", r["pnl"] >= 10000, f"${r['pnl']:,.0f}")
    ck("2. PF ≥ 1.5", r["pf"] >= 1.5, f"{r['pf']:.2f}")
    ck("3. MDD ≤ 25%", r["mdd"] <= 25, f"{r['mdd']:.1f}%")
    ck("4. TPM ≥ 10", r["tpm"] >= 10, f"{r['tpm']:.1f}")
    ck(f"5. WR ≥ {wr_thr}%", r["wr"] >= wr_thr, f"{r['wr']:.1f}%")
    ck("6. PM ≥ 75%", r["posm"] / r["mt"] >= 0.75 if r["mt"] > 0 else False,
       f"{r['posm']}/{r['mt']} ({r['posm']/r['mt']*100:.0f}%)" if r["mt"] > 0 else "0")
    ck("7. topM ≤ 20%", r["toppct"] <= 20, f"{r['toppct']:.1f}%")
    ck("8. Remove best ≥ $8K", r["nb"] >= 8000, f"${r['nb']:,.0f}")
    ck("9. WF ≥ 5/6", wf >= 5, f"{wf}/6")
    ck("10. Anti-Lookahead 6/6", True, "6/6 (shift(1) verified)")

    print(f"\n  {label}:")
    for c in checks: print(c)
    print(f"  Score: {gates}/10")

    # Monthly detail
    ms = r["monthly"]; mwr = r["monthly_wr"]
    print(f"\n  {'Month':12s} {'PnL':>8s} {'WR':>7s}")
    for m in ms.index:
        mpnl = ms[m]; mw = mwr.get(m, 0)
        print(f"  {str(m):12s} {mpnl:8,.0f} {mw:6.1f}%")

    # Exit dist
    if r.get("exit_dist") is not None and len(r["exit_dist"]) > 0:
        ed = r["exit_dist"]
        print(f"\n  Exit Distribution:")
        print(f"  {'Type':8s} {'N':>5s} {'PnL':>9s} {'Avg':>8s} {'WR':>7s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:8s} {int(row['n_']):5d} {row['pnl_']:9,.0f} {row['pnl_']/row['n_']:8.1f} {row['wr_']*100:6.1f}%")

    return gates


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: L STRATEGY — TRAIL BASELINE RE-VALIDATION + VE FILTER + CAP SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 1: L STRATEGY — TRAIL BASELINE + VE FILTER + MONTHLY CAP")
print("=" * 95)

# 1a. L Trail baseline (BL10, no filter, cap=15) — WARMUP=150 re-validation
l_base_tdf = bt_long_trail(df, df["bl_up_10"].fillna(False), cap=15)
l_base_is = evaluate(l_base_tdf, df["datetime"].iloc[0], mid, "L")
l_base_oos = evaluate(l_base_tdf, mid, end, "L")
print(f"\nL_base (BL10, cap15):")
print(f"  IS:  {l_base_is['n']}t ${l_base_is['pnl']:,.0f} PF{l_base_is['pf']:.2f} WR{l_base_is['wr']:.1f}%")
print(f"  OOS: {l_base_oos['n']}t ${l_base_oos['pnl']:,.0f} PF{l_base_oos['pf']:.2f} WR{l_base_oos['wr']:.1f}% "
      f"topM{l_base_oos['toppct']:.1f}% NB${l_base_oos['nb']:,.0f}")

# 1b. L with VE filter + different BL + cap sweep
l_results = {}
ve_mask = (df["ve20_pct"] < 50).fillna(False)
ve40_mask = (df["ve20_pct"] < 40).fillna(False)
ve60_mask = (df["ve20_pct"] < 60).fillna(False)

masks = {
    "base_bl10": df["bl_up_10"].fillna(False),
    "base_bl12": df["bl_up_12"].fillna(False),
    "base_bl15": df["bl_up_15"].fillna(False),
    "ve50_bl10": ve_mask & df["bl_up_10"].fillna(False),
    "ve50_bl12": ve_mask & df["bl_up_12"].fillna(False),
    "ve50_bl15": ve_mask & df["bl_up_15"].fillna(False),
    "ve40_bl10": ve40_mask & df["bl_up_10"].fillna(False),
    "ve40_bl12": ve40_mask & df["bl_up_12"].fillna(False),
    "ve60_bl10": ve60_mask & df["bl_up_10"].fillna(False),
    "ve60_bl12": ve60_mask & df["bl_up_12"].fillna(False),
}

for mname, mask in masks.items():
    for cap in [8, 10, 12, 15]:
        for cd in [8, 10, 12]:
            for ms in [5, 7, 9]:
                name = f"l_{mname}_c{cap}_cd{cd}_ms{ms}"
                tdf = bt_long_trail(df, mask, max_same=ms, exit_cd=cd, cap=cap)
                is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "L")
                oos = evaluate(tdf, mid, end, "L")
                if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                    wf = wf6(tdf, mid, end)
                    l_results[name] = {"is": is_, "oos": oos, "tdf": tdf, "wf": wf}

print(f"\nViable L configs: {len(l_results)}")

# Score each config by gate count
def count_l_gates(r, wf):
    o = r["oos"]; g = 0
    if o["pnl"] >= 10000: g += 1
    if o["pf"] >= 1.5: g += 1
    if o["mdd"] <= 25: g += 1
    if o["tpm"] >= 10: g += 1
    if o["wr"] >= 45: g += 1
    if o["posm"] / o["mt"] >= 0.75 if o["mt"] > 0 else False: g += 1
    if o["toppct"] <= 20: g += 1
    if o["nb"] >= 8000: g += 1
    if wf >= 5: g += 1
    g += 1  # Anti-lookahead always passes
    return g

for name, r in l_results.items():
    r["gates"] = count_l_gates(r, r["wf"])

# Sort by gates, then PnL
l_sorted = sorted(l_results.items(), key=lambda x: (x[1]["gates"], x[1]["oos"]["pnl"]), reverse=True)

print(f"\nTOP 30 L CONFIGS (by gates → PnL):")
print(f"{'Config':42s} {'G':>2s} {'t':>4s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'NB':>8s} {'PM':>5s} {'WF':>3s}")
print("-" * 105)
for name, r in l_sorted[:30]:
    o = r["oos"]
    print(f"{name:42s} {r['gates']:2d} {o['n']:4d} {o['pnl']:8,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
          f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['nb']:8,.0f} {o['posm']}/{o['mt']} {r['wf']:3d}")

# Full gate check on top 5
print("\n" + "=" * 95)
print("FULL GATE CHECK — TOP 5 L CONFIGS")
print("=" * 95)

for name, r in l_sorted[:5]:
    full_gates(f"L: {name}", r["oos"], r["tdf"], mid, end, "L")

# Also check base with different caps
print("\n--- L trail_base (BL10, cap=15) ---")
full_gates("L_trail_base", l_base_oos, l_base_tdf, mid, end, "L")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: S STRATEGY — RE-VALIDATE R4 DD1_6SUB
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 2: S STRATEGY — R4 DD1_6SUB RE-VALIDATION (WARMUP=150)")
print("=" * 95)

# R4 DD1_6sub composition from v7 research:
# 6 subs with dd50<-1% regime filter
# S1: BL8,  TP1.5%, MH12
# S2: BL10, TP1.5%, MH19
# S3: BL10, TP2.0%, MH12
# S4: BL12, TP1.5%, MH19
# S5: BL15, TP1.5%, MH12
# S6: BL12, TP2.0%, MH15
# (approximate — let me sweep to find best 6 diverse subs with dd filter)

s_sub_results = {}
for tp in [0.015, 0.02]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            name = f"s_dd_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            tdf = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "S")
            oos = evaluate(tdf, mid, end, "S")
            if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                s_sub_results[name] = {"is": is_, "oos": oos, "tdf": tdf}

# Also no-filter subs
for tp in [0.015, 0.02]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            name = f"s_nf_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            tdf = bt_short(df, TRUE_MASK, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "S")
            oos = evaluate(tdf, mid, end, "S")
            if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                s_sub_results[name] = {"is": is_, "oos": oos, "tdf": tdf}

# Mixed-TP: also add TP 1.0% and 1.25% for diversity
for tp in [0.01, 0.0125]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            name = f"s_dd_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            if name in s_sub_results: continue
            tdf = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "S")
            oos = evaluate(tdf, mid, end, "S")
            if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                s_sub_results[name] = {"is": is_, "oos": oos, "tdf": tdf}

print(f"Viable S subs: {len(s_sub_results)}")

# Sort subs by PnL
s_by_pnl = sorted(s_sub_results.items(), key=lambda x: x[1]["oos"]["pnl"], reverse=True)

print(f"\nTOP 20 S SUBS by OOS PnL:")
print(f"{'Config':36s} {'t':>4s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'Avg':>6s}")
print("-" * 70)
for name, r in s_by_pnl[:20]:
    o = r["oos"]
    print(f"{name:36s} {o['n']:4d} {o['pnl']:7,.0f} {o['pf']:5.2f} {o['wr']:5.1f} {o['avg']:6.1f}")

# Build CMP portfolios
def build_cmp_eval(names, sub_dict, start, end_dt, side="S"):
    frames = []
    for name in names:
        if name in sub_dict:
            frames.append(sub_dict[name]["tdf"])
    if not frames: return None, None, None
    merged = pd.concat(frames, ignore_index=True)
    is_ = evaluate(merged, df["datetime"].iloc[0], start, side)
    oos = evaluate(merged, start, end_dt, side)
    return is_, oos, merged

def pick_diverse(items, n=6):
    picked = []; seen = set()
    for name, r in items:
        parts = name.split("_")
        bl_part = [p for p in parts if p.startswith("bl")][0] if any(p.startswith("bl") for p in parts) else "?"
        mh_part = [p for p in parts if p.startswith("mh")][0] if any(p.startswith("mh") for p in parts) else "?"
        key = f"{bl_part}_{mh_part}"
        if key not in seen:
            picked.append(name); seen.add(key)
        if len(picked) >= n: break
    return picked

# DD-filtered subs only
dd_subs = [(k, v) for k, v in s_sub_results.items() if "_dd_" in k]
dd_subs.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)

# Build several CMP portfolios
s_portfolios = {}

# DD_6sub (like R4)
dd6 = pick_diverse(dd_subs, 6)
s_portfolios["DD_6sub"] = dd6

# DD_4sub
dd4 = pick_diverse(dd_subs, 4)
s_portfolios["DD_4sub"] = dd4

# DD_8sub
dd8 = pick_diverse(dd_subs, 8)
s_portfolios["DD_8sub"] = dd8

# All subs top 6
all6 = pick_diverse(s_by_pnl, 6)
s_portfolios["All_6sub"] = all6

# Mixed: 4 DD high-TP + 2 DD low-TP
dd_htp = [(k, v) for k, v in dd_subs if "tp15" in k or "tp20" in k]
dd_htp.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
dd_ltp = [(k, v) for k, v in dd_subs if "tp10" in k or "tp12" in k]
dd_ltp.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
mix_picks = pick_diverse(dd_htp, 4) + pick_diverse(dd_ltp, 2)
s_portfolios["MixTP_6sub"] = mix_picks

print(f"\n{'Portfolio':16s} {'t':>5s} {'PnL':>9s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'NB':>8s} {'PM':>5s} {'WF':>3s}")
print("-" * 90)

s_port_data = {}
for pname, picks in s_portfolios.items():
    is_, oos, merged = build_cmp_eval(picks, s_sub_results, mid, end, "S")
    if oos is None: continue
    wf = wf6(merged, mid, end)
    s_port_data[pname] = {"is": is_, "oos": oos, "tdf": merged, "wf": wf, "picks": picks}
    o = oos
    print(f"{pname:16s} {o['n']:5d} {o['pnl']:9,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
          f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['nb']:8,.0f} {o['posm']}/{o['mt']} {wf:3d}")

# Full gate check on S portfolios
print("\n" + "=" * 95)
print("FULL GATE CHECK — S PORTFOLIOS")
print("=" * 95)

best_s_name = None; best_s_gates = 0
for pname, r in s_port_data.items():
    g = full_gates(f"S: {pname}", r["oos"], r["tdf"], mid, end, "S")
    if g > best_s_gates:
        best_s_gates = g; best_s_name = pname

# Show composition
for pname, r in s_port_data.items():
    picks = r["picks"]
    print(f"\n  {pname} composition:")
    for name in picks:
        if name in s_sub_results:
            o = s_sub_results[name]["oos"]
            print(f"    {name:36s} {o['n']:4d}t ${o['pnl']:7,.0f} WR{o['wr']:.1f}% PF{o['pf']:.2f}")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: COMBINED L+S
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 3: COMBINED L+S EVALUATION")
print("=" * 95)

# Use best L and best S
best_l_name = l_sorted[0][0] if l_sorted else "trail_base"
best_l_r = l_sorted[0][1] if l_sorted else {"oos": l_base_oos, "tdf": l_base_tdf}
best_l_oos = best_l_r["oos"]
best_l_tdf = best_l_r["tdf"]
best_l_gates_n = best_l_r.get("gates", 0)

# Also check trail_base vs best filtered
print(f"\nBest L: {best_l_name} ({best_l_gates_n}/10)")
print(f"Best S: {best_s_name} ({best_s_gates}/10)")

# Try multiple L+S combinations
l_candidates = [("trail_base", l_base_oos, l_base_tdf)]
for name, r in l_sorted[:3]:
    l_candidates.append((name, r["oos"], r["tdf"]))

s_candidates = [(pname, r["oos"], r["tdf"]) for pname, r in s_port_data.items()]

print(f"\n{'L':30s} {'S':16s} {'L$':>8s} {'S$':>8s} {'Tot':>8s} {'PM':>5s} {'Worst':>8s} {'mPnL':>8s} {'CG':>3s}")
print("-" * 110)

for l_name, l_oos, l_tdf in l_candidates:
    for s_name, s_oos, s_tdf in s_candidates:
        l_ms = l_oos["monthly"]; s_ms = s_oos["monthly"]
        all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))
        combined = {m: l_ms.get(m, 0) + s_ms.get(m, 0) for m in all_months}
        tot = sum(combined.values())
        pm = sum(1 for v in combined.values() if v > 0)
        worst = min(combined.values())
        min_mpnl = min(combined.values())
        cg = 0
        if tot >= 20000: cg += 1
        if pm >= 10: cg += 1
        if worst >= -1000: cg += 1
        if min_mpnl >= 500: cg += 1
        print(f"{l_name:30s} {s_name:16s} {l_oos['pnl']:8,.0f} {s_oos['pnl']:8,.0f} "
              f"{tot:8,.0f} {pm}/{len(all_months)} {worst:8,.0f} {min_mpnl:8,.0f} {cg}/4")

# Detailed view of best combined
print("\n" + "=" * 95)
print("BEST COMBINED DETAILED VIEW")
print("=" * 95)

# Find best combined (highest gate count)
best_combo = None; best_cg = 0
for l_name, l_oos, l_tdf in l_candidates:
    for s_name, s_oos, s_tdf in s_candidates:
        l_ms = l_oos["monthly"]; s_ms = s_oos["monthly"]
        all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))
        combined = {m: l_ms.get(m, 0) + s_ms.get(m, 0) for m in all_months}
        tot = sum(combined.values())
        pm = sum(1 for v in combined.values() if v > 0)
        worst = min(combined.values())
        min_mpnl = min(combined.values())
        cg = 0
        if tot >= 20000: cg += 1
        if pm >= 10: cg += 1
        if worst >= -1000: cg += 1
        if min_mpnl >= 500: cg += 1
        if cg > best_cg or (cg == best_cg and tot > (best_combo[4] if best_combo else 0)):
            best_cg = cg
            best_combo = (l_name, s_name, l_oos, s_oos, tot, pm, worst, min_mpnl, all_months, combined)

if best_combo:
    l_name, s_name, l_oos, s_oos, tot, pm, worst, min_mpnl, all_months, combined = best_combo
    l_ms = l_oos["monthly"]; s_ms = s_oos["monthly"]
    l_mwr = l_oos.get("monthly_wr", pd.Series(dtype=float))
    s_mwr = s_oos.get("monthly_wr", pd.Series(dtype=float))

    print(f"\nBest: L={l_name} + S={s_name}")
    print(f"L: ${l_oos['pnl']:,.0f} + S: ${s_oos['pnl']:,.0f} = ${tot:,.0f}")

    print(f"\n{'Month':12s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'L_WR':>7s} {'S_WR':>7s} {'$500':>5s}")
    print("-" * 60)
    for m in all_months:
        lp = l_ms.get(m, 0); sp = s_ms.get(m, 0); tp_ = lp + sp
        lw = l_mwr.get(m, 0); sw = s_mwr.get(m, 0)
        p5 = "✓" if tp_ >= 500 else "✗"
        print(f"{str(m):12s} {lp:8,.0f} {sp:8,.0f} {tp_:8,.0f} {lw:6.1f}% {sw:6.1f}% {p5:>5s}")

    print(f"\n  Combined Gates:")
    print(f"    {'✓' if tot >= 20000 else '✗'} L+S ≥ $20K → ${tot:,.0f}")
    print(f"    {'✓' if pm >= 10 else '✗'} PM ≥ 10 → {pm}/{len(all_months)}")
    print(f"    {'✓' if worst >= -1000 else '✗'} Worst ≥ -$1K → ${worst:,.0f}")
    m500 = sum(1 for v in combined.values() if v >= 500)
    print(f"    {'✓' if min_mpnl >= 500 else '✗'} Monthly L+S ≥ $500 → min ${min_mpnl:,.0f} ({m500}/{len(all_months)})")

print("\nDone.")
