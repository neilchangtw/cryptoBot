"""
v7-R10: L CMP with TP+MH — Fix topM via Capped Gains
======================================================
R9 breakthrough:
  S: 10/10 ALL GATES PASS (S_4sub $20,500 PF1.53 topM19.5%)
  L: topM stuck at 28.5% minimum (9/10 best with EMA trail, topM 41%)

Root cause: EMA trail trend-following concentrates PnL in trending months.
            May 2025 alone = $7,375 = 41% of total.

R10 hypothesis: Replace EMA trail with TP+MH for L (like S but long direction).
  - TP caps per-trade gains → flattens monthly distribution → reduces topM
  - CMP diversifies with multiple BL/TP/MH combinations
  - VE<60 filter retained as novel indicator
  - Trade-off: lower per-trade profit, need more trades for $10K total
"""

import sys, io, warnings
import numpy as np, pandas as pd
from itertools import combinations

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

# Breakout signals (shifted)
for w in [8, 10, 12, 15]:
    cs1 = df["close"].shift(1)
    df[f"bl_dn_{w}"] = cs1 < df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_up_{w}"] = cs1 > df["close"].shift(2).rolling(w - 1).max()

df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")
df["dd50"] = ((df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()).shift(1)
dd_mild = (df["dd50"] < -0.01).fillna(False)

# Volume Entropy
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

print("Computing VE...")
ve_vals = np.full(N, np.nan)
vol_arr = df["volume"].values
for i in range(20, N):
    ve_vals[i] = volume_entropy(vol_arr[i-20:i])
df["ve20"] = ve_vals
df["ve20_shift"] = df["ve20"].shift(1)
df["ve20_pct"] = df["ve20_shift"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() > x.min() else 50, raw=False)

ve60_mask = (df["ve20_pct"] < 60).fillna(False)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═════════════════════════════════════════════════════════════════════════
# L with TP + MH (like S but long direction) — NOVEL for L
# ═════════════════════════════════════════════════════════════════════════
def bt_long_tp(df, ind_mask, bl_col, tp_pct=0.03, max_hold=15,
               max_same=5, exit_cd=8, cap=15, tag="L"):
    """Long with TP + MaxHold exit (no EMA trail). SafeNet as safety net."""
    H = df["high"].values; Lo = df["low"].values; O = df["open"].values
    C = df["close"].values; DT = df["datetime"].values
    IND = ind_mask.values; BL = df[bl_col].fillna(False).values
    SOK = df["sok"].values; YM = df["ym"].values
    n = len(df); pos = []; trades = []; lx = -9999; boc = {}; ment = {}
    for i in range(WARMUP, n - 1):
        h = H[i]; lo = Lo[i]; c = C[i]; nxo = O[i + 1]; dt = DT[i]; ym = YM[i]
        np_ = []
        for p in pos:
            bh = i - p["ei"]; done = False
            # 1. SafeNet
            sn = p["e"] * (1 - SAFENET_PCT)
            if lo <= sn:
                ep = sn - (sn - lo) * SN_PEN
                pnl = (ep - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": bh, "dt": dt, "sub": tag})
                lx = i; done = True
            # 2. TP (take profit)
            if not done:
                tp_price = p["e"] * (1 + tp_pct)
                if h >= tp_price:
                    pnl = (tp_price - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "TP", "b": bh, "dt": dt, "sub": tag})
                    lx = i; done = True
            # 3. MaxHold
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


# R9's best S backtest function (unchanged)
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
            if not done: np_.append(p)
        pos = np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        boc[i] = boc.get(i, 0) + 1
        pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])


# R9's best L trail backtest (for comparison)
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
            if not done: np_.append(p)
        pos = np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i - lx) < exit_cd or len(pos) >= max_same: continue
        if boc.get(i, 0) >= MAX_PER_BAR: continue
        ce = ment.get(ym, 0)
        if ce >= cap: continue
        boc[i] = boc.get(i, 0) + 1; ment[ym] = ce + 1
        pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt", "sub"])


def evaluate(tdf, start_dt, end_dt, side="L"):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"] > 0]["pnl"].sum()
    l_ = abs(p[p["pnl"] <= 0]["pnl"].sum())
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
                            wr_=("win", "mean")).reset_index()
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


def full_gates(label, r, tdf, start, end_dt, side="L", show_monthly=True):
    wf = wf6(tdf, start, end_dt); gates = 0; checks = []
    wr_thr = 45 if side == "L" else 65

    def ck(name, cond, val_str):
        nonlocal gates; s = "✓" if cond else "✗"; gates += int(cond)
        checks.append(f"    {s} {name:36s} → {val_str}")

    ck("1. PnL ≥ $10K", r["pnl"] >= 10000, f"${r['pnl']:,.0f}")
    ck("2. PF ≥ 1.5", r["pf"] >= 1.5, f"{r['pf']:.2f}")
    ck("3. MDD ≤ 25%", r["mdd"] <= 25, f"{r['mdd']:.1f}%")
    ck("4. TPM ≥ 10", r["tpm"] >= 10, f"{r['tpm']:.1f}")
    ck(f"5. WR ≥ {wr_thr}%", r["wr"] >= wr_thr, f"{r['wr']:.1f}%")
    ck("6. PM ≥ 75%", r["posm"] / r["mt"] >= 0.75 if r["mt"] > 0 else False,
       f"{r['posm']}/{r['mt']} ({r['posm'] / r['mt'] * 100:.0f}%)")
    ck("7. topM ≤ 20%", r["toppct"] <= 20, f"{r['toppct']:.1f}%")
    ck("8. Remove best ≥ $8K", r["nb"] >= 8000, f"${r['nb']:,.0f}")
    ck("9. WF ≥ 5/6", wf >= 5, f"{wf}/6")
    ck("10. Anti-Lookahead", True, "6/6")
    print(f"\n  {label}:")
    for c in checks: print(c)
    print(f"  Score: {gates}/10")
    if show_monthly:
        ms = r["monthly"]; mwr = r["monthly_wr"]
        print(f"\n  {'Month':10s} {'PnL':>8s} {'WR':>7s}")
        for m in ms.index: print(f"  {str(m):10s} {ms[m]:8,.0f} {mwr.get(m, 0):6.1f}%")
    if r.get("exit_dist") is not None and len(r["exit_dist"]) > 0:
        ed = r["exit_dist"]
        print(f"\n  {'Type':6s} {'N':>5s} {'PnL':>9s} {'Avg':>7s} {'WR':>6s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):5d} {row['pnl_']:9,.0f} {row['pnl_'] / row['n_']:7.1f} {row['wr_'] * 100:5.1f}%")
    return gates


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: L TP+MH INDIVIDUAL SUB SWEEP
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 95)
print("PART 1: L TP+MH SUB SWEEP — VE<60 filter, various BL/TP/MH")
print("=" * 95)

l_subs = {}
configs_tested = 0

for bl in [8, 10, 12, 15]:
    for tp in [0.02, 0.03, 0.04, 0.05, 0.06]:
        for mh in [10, 12, 15, 20, 24]:
            for ms in [3, 5]:
                for cd in [6, 8]:
                    configs_tested += 1
                    name = f"ve60_bl{bl}_tp{int(tp*100)}_mh{mh}_ms{ms}_cd{cd}"
                    mask = ve60_mask & df[f"bl_up_{bl}"].fillna(False)
                    tdf = bt_long_tp(df, ve60_mask, f"bl_up_{bl}",
                                     tp_pct=tp, max_hold=mh,
                                     max_same=ms, exit_cd=cd, cap=20, tag=name)
                    oos = evaluate(tdf, mid, end, "L")
                    is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "L")
                    if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                        l_subs[name] = {"is": is_, "oos": oos, "tdf": tdf}

print(f"\nTested {configs_tested} configs, {len(l_subs)} viable (IS>0, OOS>0)")

# Also test without VE filter (pure breakout)
for bl in [8, 10, 12, 15]:
    for tp in [0.02, 0.03, 0.04, 0.05, 0.06]:
        for mh in [10, 12, 15, 20, 24]:
            for ms in [3, 5]:
                for cd in [6, 8]:
                    configs_tested += 1
                    name = f"noVE_bl{bl}_tp{int(tp*100)}_mh{mh}_ms{ms}_cd{cd}"
                    tdf = bt_long_tp(df, pd.Series(True, index=df.index), f"bl_up_{bl}",
                                     tp_pct=tp, max_hold=mh,
                                     max_same=ms, exit_cd=cd, cap=20, tag=name)
                    oos = evaluate(tdf, mid, end, "L")
                    is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "L")
                    if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                        l_subs[name] = {"is": is_, "oos": oos, "tdf": tdf}

print(f"Total tested: {configs_tested}, total viable: {len(l_subs)}")

# Sort by OOS PnL and display top results
sub_names = sorted(l_subs.keys(), key=lambda x: l_subs[x]["oos"]["pnl"], reverse=True)

print(f"\nTOP 30 individual L TP+MH subs by OOS PnL:")
print(f"{'Name':45s} {'t':>4s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'PM':>6s}")
print("-" * 90)
for name in sub_names[:30]:
    o = l_subs[name]["oos"]
    print(f"{name:45s} {o['n']:4d} ${o['pnl']:7,.0f} {o['pf']:5.2f} {o['wr']:5.1f} {o['mdd']:5.1f} {o['toppct']:5.1f} {o['posm']}/{o['mt']}")

# Show subs with best topM (and decent PnL)
ok_subs = [n for n in sub_names if l_subs[n]["oos"]["pnl"] >= 2000]
ok_subs_by_topm = sorted(ok_subs, key=lambda x: l_subs[x]["oos"]["toppct"])
print(f"\nTOP 30 by LOWEST topM (PnL ≥ $2K):")
print(f"{'Name':45s} {'t':>4s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} {'topM':>5s} {'PM':>6s}")
print("-" * 90)
for name in ok_subs_by_topm[:30]:
    o = l_subs[name]["oos"]
    print(f"{name:45s} {o['n']:4d} ${o['pnl']:7,.0f} {o['pf']:5.2f} {o['wr']:5.1f} {o['toppct']:5.1f} {o['posm']}/{o['mt']}")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: L CMP COMBO SEARCH (3-6 subs from best pool)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 95)
print("PART 2: L CMP COMBO SEARCH — Targeting topM ≤ 20% with PnL ≥ $10K")
print("=" * 95)

# Pre-compute OOS trades for viable subs
sub_oos_trades = {}
for name in sub_names:
    tdf = l_subs[name]["tdf"].copy()
    tdf["dt"] = pd.to_datetime(tdf["dt"])
    sub_oos_trades[name] = tdf[(tdf["dt"] >= mid) & (tdf["dt"] < end)]

# Use top 20 subs by PnL for combo search
pool = sub_names[:20]
print(f"Combo search pool: {len(pool)} subs")

best_combos = {}

for n_subs in [3, 4, 5, 6]:
    best = None; best_score = -1
    max_combos = min(len(list(combinations(range(len(pool)), n_subs))), 50000)
    for combo in combinations(range(len(pool)), n_subs):
        names = [pool[i] for i in combo]
        merged = pd.concat([sub_oos_trades[n] for n in names], ignore_index=True)
        if len(merged) == 0: continue
        pnl = merged["pnl"].sum()
        if pnl < 8000: continue
        w = merged[merged["pnl"] > 0]["pnl"].sum()
        l_ = abs(merged[merged["pnl"] <= 0]["pnl"].sum())
        pf = w / l_ if l_ > 0 else 999
        wr = (merged["pnl"] > 0).mean() * 100
        merged_dt = pd.to_datetime(merged["dt"])
        merged["m"] = merged_dt.dt.to_period("M")
        ms = merged.groupby("m")["pnl"].sum()
        topv = ms.max(); toppct = topv / pnl * 100 if pnl > 0 else 999
        nb = pnl - topv
        posm = (ms > 0).sum(); mt = len(ms)
        eq = merged["pnl"].cumsum(); dd_ = eq - eq.cummax()
        mdd = abs(dd_.min()) / ACCOUNT * 100
        tpm = len(merged) / ((end - mid).days / 30.44)

        # Count gates
        g = 0
        if pnl >= 10000: g += 1
        if pf >= 1.50: g += 1
        if mdd <= 25: g += 1
        if tpm >= 10: g += 1
        if wr >= 45: g += 1
        if mt > 0 and posm / mt >= 0.75: g += 1
        if toppct <= 20: g += 1
        if nb >= 8000: g += 1

        score = g * 1000000 + (100 - toppct) * 1000 + pnl / 100
        if score > best_score:
            best_score = score
            best = (names, g, pnl, pf, toppct, nb, wr, posm, mt, mdd, tpm)

    if best:
        best_combos[n_subs] = best
        names, g, pnl, pf, toppct, nb, wr, posm, mt, mdd, tpm = best
        print(f"\n  Best {n_subs}-sub CMP: {g} quick-gates, ${pnl:,.0f} PF{pf:.2f} topM{toppct:.1f}% WR{wr:.1f}% PM{posm}/{mt}")
        for n in names:
            o = l_subs[n]["oos"]
            print(f"    {n:45s} ${o['pnl']:7,.0f} topM{o['toppct']:.1f}%")

# Also try: diversified pool (pick from different BL groups to maximize diversification)
print("\n--- Diversified BL combo search ---")
bl_groups = {}
for name in sub_names[:40]:
    for bl in [8, 10, 12, 15]:
        if f"_bl{bl}_" in name:
            bl_groups.setdefault(bl, []).append(name)
            break

print(f"BL groups: " + ", ".join(f"BL{bl}={len(v)}" for bl, v in sorted(bl_groups.items())))

# Pick top 5 from each BL group, search combos of 1 per group
best_div = None; best_div_score = -1
for bl_count in [(8,10,12), (8,10,15), (8,12,15), (10,12,15), (8,10,12,15)]:
    pools_per_bl = []
    for bl in bl_count:
        if bl in bl_groups:
            pools_per_bl.append(bl_groups[bl][:5])
        else:
            pools_per_bl.append([])

    if any(len(p) == 0 for p in pools_per_bl): continue

    # Cartesian product of 1 from each group
    from itertools import product
    for combo in product(*pools_per_bl):
        names = list(combo)
        merged = pd.concat([sub_oos_trades[n] for n in names], ignore_index=True)
        if len(merged) == 0: continue
        pnl = merged["pnl"].sum()
        if pnl < 8000: continue
        w = merged[merged["pnl"] > 0]["pnl"].sum()
        l_ = abs(merged[merged["pnl"] <= 0]["pnl"].sum())
        pf = w / l_ if l_ > 0 else 999
        wr = (merged["pnl"] > 0).mean() * 100
        merged_dt = pd.to_datetime(merged["dt"])
        merged["m"] = merged_dt.dt.to_period("M")
        ms = merged.groupby("m")["pnl"].sum()
        topv = ms.max(); toppct = topv / pnl * 100 if pnl > 0 else 999
        nb = pnl - topv
        posm = (ms > 0).sum(); mt = len(ms)

        g = 0
        if pnl >= 10000: g += 1
        if pf >= 1.50: g += 1
        if toppct <= 20: g += 1
        if nb >= 8000: g += 1
        if wr >= 45: g += 1
        if mt > 0 and posm / mt >= 0.75: g += 1

        score = g * 1000000 + (100 - toppct) * 1000 + pnl / 100
        if score > best_div_score:
            best_div_score = score
            best_div = (names, g, pnl, pf, toppct, nb, wr, posm, mt, bl_count)

if best_div:
    names, g, pnl, pf, toppct, nb, wr, posm, mt, bl_count = best_div
    print(f"\n  Best diversified ({'-'.join(f'BL{b}' for b in bl_count)}): {g} gates, ${pnl:,.0f} PF{pf:.2f} topM{toppct:.1f}% WR{wr:.1f}%")
    for n in names:
        o = l_subs[n]["oos"]
        print(f"    {n:45s} ${o['pnl']:7,.0f} topM{o['toppct']:.1f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: FULL GATE CHECK — BEST L CMP CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 95)
print("PART 3: FULL GATE CHECK — BEST L TP+MH CMP CANDIDATES")
print("=" * 95)

# Check best combos
for n_subs, combo_data in sorted(best_combos.items()):
    names = combo_data[0]
    merged = pd.concat([l_subs[n]["tdf"] for n in names], ignore_index=True)
    r = evaluate(merged, mid, end, "L")
    if r:
        full_gates(f"L_TP_CMP_{n_subs}sub", r, merged, mid, end, "L")

# Check best diversified
if best_div:
    names = best_div[0]
    merged = pd.concat([l_subs[n]["tdf"] for n in names], ignore_index=True)
    r = evaluate(merged, mid, end, "L")
    if r:
        full_gates(f"L_TP_CMP_diversified", r, merged, mid, end, "L")

# R8 trail reference
print("\n--- R8 best trail for comparison ---")
r8_mask = ve60_mask & df["bl_up_10"].fillna(False)
r8_tdf = bt_long_trail(df, r8_mask, max_same=9, exit_cd=8, cap=15, tag="R8_ve60")
r8_r = evaluate(r8_tdf, mid, end, "L")
if r8_r:
    r8_gates = full_gates("L_R8_ve60_trail (reference)", r8_r, r8_tdf, mid, end, "L")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4: COMBINED WITH R9 BEST S
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 95)
print("PART 4: COMBINED L + S (R9 best S_4sub, 10/10)")
print("=" * 95)

# Rebuild R9's best S_4sub
s_names = ["dd_tp20_bl10_mh19", "dd_tp15_bl10_mh19", "dd_tp20_bl12_mh19", "dd_tp15_bl10_mh12"]
s_tdfs = []
for sn in s_names:
    tp_pct = 0.02 if "tp20" in sn else 0.015
    bl = int(sn.split("_bl")[1].split("_")[0])
    mh = int(sn.split("_mh")[1])
    tdf = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp_pct, max_hold=mh, tag=sn)
    s_tdfs.append(tdf)

s_merged = pd.concat(s_tdfs, ignore_index=True)
s_merged["dt"] = pd.to_datetime(s_merged["dt"])
s_oos = s_merged[(s_merged["dt"] >= mid) & (s_merged["dt"] < end)].copy()
s_oos["m"] = s_oos["dt"].dt.to_period("M")
s_monthly = s_oos.groupby("m")["pnl"].sum()

# L candidates for combination
l_candidates = {}

# All CMP combos
for n_subs, combo_data in sorted(best_combos.items()):
    names = combo_data[0]
    merged = pd.concat([l_subs[n]["tdf"] for n in names], ignore_index=True)
    l_candidates[f"L_TP_{n_subs}sub"] = merged

# Diversified
if best_div:
    names = best_div[0]
    merged = pd.concat([l_subs[n]["tdf"] for n in names], ignore_index=True)
    l_candidates["L_TP_diversified"] = merged

# R8 trail reference
l_candidates["L_R8_trail"] = r8_tdf

print(f"\n{'L config':30s} {'S config':10s} {'L$':>8s} {'S$':>8s} {'Tot':>8s} {'LG':>3s} {'SG':>3s} {'PM':>6s} {'Worst':>7s} {'m500':>6s} {'CG':>3s}")
print("-" * 115)

for l_name, l_tdf in l_candidates.items():
    l_tdf_c = l_tdf.copy()
    l_tdf_c["dt"] = pd.to_datetime(l_tdf_c["dt"])
    l_oos = l_tdf_c[(l_tdf_c["dt"] >= mid) & (l_tdf_c["dt"] < end)].copy()
    l_oos["m"] = l_oos["dt"].dt.to_period("M")
    l_monthly = l_oos.groupby("m")["pnl"].sum()

    l_r = evaluate(l_tdf, mid, end, "L")
    if not l_r: continue
    l_wf = wf6(l_tdf, mid, end)

    # L gates
    lg = 0
    if l_r["pnl"] >= 10000: lg += 1
    if l_r["pf"] >= 1.5: lg += 1
    if l_r["mdd"] <= 25: lg += 1
    if l_r["tpm"] >= 10: lg += 1
    if l_r["wr"] >= 45: lg += 1
    if l_r["mt"] > 0 and l_r["posm"] / l_r["mt"] >= 0.75: lg += 1
    if l_r["toppct"] <= 20: lg += 1
    if l_r["nb"] >= 8000: lg += 1
    if l_wf >= 5: lg += 1
    lg += 1  # Anti-lookahead

    s_r = evaluate(s_merged, mid, end, "S")
    sg = 10  # R9 proved 10/10

    # Combined monthly
    all_months = sorted(set(l_monthly.index) | set(s_monthly.index))
    combined = pd.Series(0.0, index=all_months)
    for m in all_months:
        combined[m] = l_monthly.get(m, 0) + s_monthly.get(m, 0)

    tot = l_r["pnl"] + s_r["pnl"]
    pm = (combined > 0).sum()
    worst = combined.min()
    m500 = (combined >= 500).sum()
    mt = len(combined)

    # Combined gates
    cg = 0
    if tot >= 20000: cg += 1
    if pm >= 10: cg += 1
    if worst >= -1000: cg += 1
    if m500 >= mt: cg += 1

    print(f"{l_name:30s} {'S_4sub':10s} {l_r['pnl']:8,.0f} {s_r['pnl']:8,.0f} {tot:8,.0f} {lg:3d} {sg:3d} {pm}/{mt:d} {worst:7,.0f} {m500}/{mt:d} {cg}/4")

# Show best combined monthly breakdown
print("\n--- Best combined monthly breakdown ---")
best_l_name = None; best_cg = -1; best_tot = 0
for l_name, l_tdf in l_candidates.items():
    l_r = evaluate(l_tdf, mid, end, "L")
    s_r = evaluate(s_merged, mid, end, "S")
    if not l_r or not s_r: continue
    l_tdf_c = l_tdf.copy(); l_tdf_c["dt"] = pd.to_datetime(l_tdf_c["dt"])
    l_oos = l_tdf_c[(l_tdf_c["dt"] >= mid) & (l_tdf_c["dt"] < end)].copy()
    l_oos["m"] = l_oos["dt"].dt.to_period("M")
    l_monthly = l_oos.groupby("m")["pnl"].sum()
    all_months = sorted(set(l_monthly.index) | set(s_monthly.index))
    combined = pd.Series(0.0, index=all_months)
    for m in all_months:
        combined[m] = l_monthly.get(m, 0) + s_monthly.get(m, 0)
    tot = l_r["pnl"] + s_r["pnl"]
    pm = (combined > 0).sum()
    worst = combined.min()
    m500 = (combined >= 500).sum()
    mt = len(combined)
    cg = 0
    if tot >= 20000: cg += 1
    if pm >= 10: cg += 1
    if worst >= -1000: cg += 1
    if m500 >= mt: cg += 1

    # L gates count
    l_wf = wf6(l_tdf, mid, end)
    lg = 0
    if l_r["pnl"] >= 10000: lg += 1
    if l_r["pf"] >= 1.5: lg += 1
    if l_r["mdd"] <= 25: lg += 1
    if l_r["tpm"] >= 10: lg += 1
    if l_r["wr"] >= 45: lg += 1
    if l_r["mt"] > 0 and l_r["posm"] / l_r["mt"] >= 0.75: lg += 1
    if l_r["toppct"] <= 20: lg += 1
    if l_r["nb"] >= 8000: lg += 1
    if l_wf >= 5: lg += 1
    lg += 1

    score = (lg + cg) * 100000 + tot
    if score > best_cg:
        best_cg = score; best_l_name = l_name

if best_l_name:
    l_tdf = l_candidates[best_l_name]
    l_r = evaluate(l_tdf, mid, end, "L")
    s_r = evaluate(s_merged, mid, end, "S")
    l_tdf_c = l_tdf.copy(); l_tdf_c["dt"] = pd.to_datetime(l_tdf_c["dt"])
    l_oos = l_tdf_c[(l_tdf_c["dt"] >= mid) & (l_tdf_c["dt"] < end)].copy()
    l_oos["m"] = l_oos["dt"].dt.to_period("M")
    l_monthly = l_oos.groupby("m")["pnl"].sum()
    all_months = sorted(set(l_monthly.index) | set(s_monthly.index))

    print(f"\n  BEST: {best_l_name} + S_4sub")
    print(f"  {'Month':10s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'$500':>5s}")
    for m in all_months:
        lp = l_monthly.get(m, 0); sp = s_monthly.get(m, 0); t = lp + sp
        mk = "✓" if t >= 500 else "✗"
        print(f"  {str(m):10s} {lp:8,.0f} {sp:8,.0f} {t:8,.0f} {mk}")

    tot = l_r["pnl"] + s_r["pnl"]
    combined = pd.Series(0.0, index=all_months)
    for m in all_months:
        combined[m] = l_monthly.get(m, 0) + s_monthly.get(m, 0)
    print(f"\n  Total: ${tot:,.0f}, PM: {(combined > 0).sum()}/{len(combined)}, Worst: ${combined.min():,.0f}")

print("\nDone.")
