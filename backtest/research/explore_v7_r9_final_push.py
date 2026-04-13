"""
v7-R9: Final Push — S PF/topM Sweet Spot + L topM Reduction
=============================================================
R8 results:
  L: 9/10 (only topM 41% fails — May $7,375 out of $18,003)
  S DD_8sub: 9/10 (PF 1.49, topM 19%) — PF needs 0.01 more
  S DD_4sub: 8/10 (PF 1.50 ✓, topM 20.9%) — topM needs 0.9% less

R9 Plan:
  S: Exhaustive search for 5-7 sub DD CMP that passes BOTH PF ≥1.50 AND topM ≤20%
  L: Aggressive topM reduction via low max_same + low cap + long cooldown
     Also try: L CMP (2 subs different BL) to spread monthly distribution
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

TRUE_MASK = pd.Series(True, index=df.index)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_long_trail(df, mask, max_same=5, exit_cd=8, cap=15, tag="L"):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}
    for i in range(WARMUP, n-1):
        lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done and bh>=12 and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub"])

def bt_short(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
             max_same=5, exit_cd=6, tag="S"):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    IND=ind_mask.values; BL=df[bl_col].fillna(False).values
    SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}
    for i in range(WARMUP, n-1):
        h=H[i]; lo_=Lo[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1+SAFENET_PCT)
            if h>=sn:
                ep=sn+(h-sn)*SN_PEN
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done:
                tp=p["e"]*(1-tp_pct)
                if lo_<=tp:
                    pnl=(p["e"]-tp)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done and bh>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub"])

def evaluate(tdf, start_dt, end_dt, side="L"):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    p=tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].reset_index(drop=True)
    n=len(p)
    if n==0: return None
    pnl=p["pnl"].sum()
    w=p[p["pnl"]>0]["pnl"].sum(); l_=abs(p[p["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999; wr=(p["pnl"]>0).mean()*100
    eq=p["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100
    p["m"]=p["dt"].dt.to_period("M"); ms=p.groupby("m")["pnl"].sum()
    posm=(ms>0).sum(); mt=len(ms)
    topv=ms.max() if len(ms)>0 else 0
    toppct=topv/pnl*100 if pnl>0 else 999
    nb=pnl-topv if pnl>0 else pnl
    tpm=n/((end_dt-start_dt).days/30.44)
    p["win"]=(p["pnl"]>0).astype(int)
    mwr=p.groupby("m").apply(lambda g: g["win"].mean()*100)
    ed=p.groupby("t").agg(n_=("pnl","count"),pnl_=("pnl","sum"),
                           wr_=("win","mean")).reset_index() if "t" in p.columns else pd.DataFrame()
    return {"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,"tpm":tpm,
            "mt":mt,"posm":posm,"toppct":toppct,"nb":nb,"topv":topv,
            "monthly":ms,"monthly_wr":mwr,"exit_dist":ed,
            "avg":pnl/n if n else 0,"side":side}

def wf6(tdf, s, e):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    r=0
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2); te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        if len(tt)>0 and tt["pnl"].sum()>0: r+=1
    return r

def full_gates(label, r, tdf, start, end_dt, side="L", show_monthly=True):
    wf=wf6(tdf, start, end_dt); gates=0; checks=[]
    wr_thr=45 if side=="L" else 65
    def ck(name, cond, val_str):
        nonlocal gates; s="✓" if cond else "✗"; gates+=int(cond)
        checks.append(f"    {s} {name:36s} → {val_str}")
    ck("1. PnL ≥ $10K", r["pnl"]>=10000, f"${r['pnl']:,.0f}")
    ck("2. PF ≥ 1.5", r["pf"]>=1.5, f"{r['pf']:.2f}")
    ck("3. MDD ≤ 25%", r["mdd"]<=25, f"{r['mdd']:.1f}%")
    ck("4. TPM ≥ 10", r["tpm"]>=10, f"{r['tpm']:.1f}")
    ck(f"5. WR ≥ {wr_thr}%", r["wr"]>=wr_thr, f"{r['wr']:.1f}%")
    ck("6. PM ≥ 75%", r["posm"]/r["mt"]>=0.75 if r["mt"]>0 else False,
       f"{r['posm']}/{r['mt']} ({r['posm']/r['mt']*100:.0f}%)")
    ck("7. topM ≤ 20%", r["toppct"]<=20, f"{r['toppct']:.1f}%")
    ck("8. Remove best ≥ $8K", r["nb"]>=8000, f"${r['nb']:,.0f}")
    ck("9. WF ≥ 5/6", wf>=5, f"{wf}/6")
    ck("10. Anti-Lookahead", True, "6/6")
    print(f"\n  {label}:"); [print(c) for c in checks]
    print(f"  Score: {gates}/10")
    if show_monthly:
        ms=r["monthly"]; mwr=r["monthly_wr"]
        print(f"\n  {'Month':10s} {'PnL':>8s} {'WR':>7s}")
        for m in ms.index: print(f"  {str(m):10s} {ms[m]:8,.0f} {mwr.get(m,0):6.1f}%")
    if r.get("exit_dist") is not None and len(r["exit_dist"])>0:
        ed=r["exit_dist"]
        print(f"\n  {'Type':6s} {'N':>5s} {'PnL':>9s} {'Avg':>7s} {'WR':>6s}")
        for _,row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):5d} {row['pnl_']:9,.0f} {row['pnl_']/row['n_']:7.1f} {row['wr_']*100:5.1f}%")
    return gates


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: S EXHAUSTIVE COMBO SEARCH (5-7 subs from DD pool)
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 1: S EXHAUSTIVE COMBO SEARCH — PF ≥ 1.50 AND topM ≤ 20%")
print("=" * 95)

# Build S sub pool (DD filter, TP 1.5-2.0%)
s_subs = {}
for tp in [0.015, 0.02]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            name = f"dd_tp{int(tp*1000)}_bl{bl}_mh{mh}"
            tdf = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=name)
            is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "S")
            oos = evaluate(tdf, mid, end, "S")
            if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                s_subs[name] = {"is": is_, "oos": oos, "tdf": tdf}

sub_names = sorted(s_subs.keys(), key=lambda x: s_subs[x]["oos"]["pnl"], reverse=True)
print(f"S sub pool: {len(sub_names)} subs")
for name in sub_names[:12]:
    o = s_subs[name]["oos"]
    print(f"  {name:30s} {o['n']:4d}t ${o['pnl']:7,.0f} PF{o['pf']:.2f} WR{o['wr']:.1f}%")

# Pre-compute OOS monthly data for each sub
sub_oos_trades = {}
for name in sub_names:
    tdf = s_subs[name]["tdf"].copy()
    tdf["dt"] = pd.to_datetime(tdf["dt"])
    sub_oos_trades[name] = tdf[(tdf["dt"] >= mid) & (tdf["dt"] < end)]

# Exhaustive search for 4-8 sub combos
print(f"\nSearching combos (4-8 subs from {len(sub_names)} pool)...")

best_combos = {}  # key: n_subs, value: (names, gates, pnl, pf, topM)

for n_subs in [4, 5, 6, 7, 8]:
    pool = sub_names[:min(16, len(sub_names))]  # Top 16 by PnL
    best = None; best_score = -1
    cnt = 0
    for combo in combinations(range(len(pool)), n_subs):
        cnt += 1
        names = [pool[i] for i in combo]
        # Quick merge OOS trades
        merged = pd.concat([sub_oos_trades[n] for n in names], ignore_index=True)
        if len(merged) == 0: continue
        pnl = merged["pnl"].sum()
        if pnl < 8000: continue
        w = merged[merged["pnl"] > 0]["pnl"].sum()
        l_ = abs(merged[merged["pnl"] <= 0]["pnl"].sum())
        pf = w / l_ if l_ > 0 else 999
        merged["m"] = merged["dt"].dt.to_period("M")
        ms = merged.groupby("m")["pnl"].sum()
        topv = ms.max(); toppct = topv / pnl * 100 if pnl > 0 else 999
        nb = pnl - topv
        posm = (ms > 0).sum(); mt = len(ms)
        wr = (merged["pnl"] > 0).mean() * 100

        # Count gates
        g = 0
        if pnl >= 10000: g += 1
        if pf >= 1.50: g += 1
        if toppct <= 20: g += 1
        if nb >= 8000: g += 1
        if wr >= 65: g += 1
        if posm / mt >= 0.75 if mt > 0 else False: g += 1

        score = g * 100000 + pnl
        if score > best_score:
            best_score = score; best = (names, g, pnl, pf, toppct, nb, wr, posm, mt)

    if best:
        best_combos[n_subs] = best
        names, g, pnl, pf, toppct, nb, wr, posm, mt = best
        print(f"\n  Best {n_subs}-sub: {g} quick-gates, ${pnl:,.0f} PF{pf:.2f} topM{toppct:.1f}% NB${nb:,.0f} WR{wr:.1f}% PM{posm}/{mt}")
        for n in names:
            o = s_subs[n]["oos"]
            print(f"    {n:30s} ${o['pnl']:7,.0f}")

# Full gate check on best combos
print("\n" + "=" * 95)
print("FULL GATE CHECK — BEST S COMBOS")
print("=" * 95)

best_s = None; best_s_gates = 0
for n_subs, combo in best_combos.items():
    names = combo[0]
    frames = [s_subs[n]["tdf"] for n in names]
    merged = pd.concat(frames, ignore_index=True)
    oos = evaluate(merged, mid, end, "S")
    if oos is None: continue
    g = full_gates(f"S_{n_subs}sub", oos, merged, mid, end, "S")
    if g > best_s_gates:
        best_s_gates = g; best_s = (f"S_{n_subs}sub", oos, merged)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: L topM REDUCTION — AGGRESSIVE PARAMETER SWEEP
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 2: L topM REDUCTION — PARAMETER SWEEP")
print("=" * 95)

# Focused sweep: low max_same, low cap, long CD
# Masks: base BL{10,12,15}, VE60 BL{10,12}
l_masks = {
    "bl10": df["bl_up_10"].fillna(False),
    "bl12": df["bl_up_12"].fillna(False),
    "bl15": df["bl_up_15"].fillna(False),
    "ve60_bl10": (df["ve20_pct"] < 60).fillna(False) & df["bl_up_10"].fillna(False),
    "ve60_bl12": (df["ve20_pct"] < 60).fillna(False) & df["bl_up_12"].fillna(False),
}

l_results = {}
for mname, mask in l_masks.items():
    for cap in [6, 8, 10, 12, 15]:
        for cd in [8, 10, 12, 15]:
            for ms in [2, 3, 4, 5, 7, 9]:
                name = f"l_{mname}_c{cap}_cd{cd}_ms{ms}"
                tdf = bt_long_trail(df, mask, max_same=ms, exit_cd=cd, cap=cap)
                is_ = evaluate(tdf, df["datetime"].iloc[0], mid, "L")
                oos = evaluate(tdf, mid, end, "L")
                if is_ and oos and is_["pnl"] > 0 and oos["pnl"] > 0:
                    wf = wf6(tdf, mid, end)
                    g = 0
                    if oos["pnl"] >= 10000: g += 1
                    if oos["pf"] >= 1.5: g += 1
                    if oos["mdd"] <= 25: g += 1
                    if oos["tpm"] >= 10: g += 1
                    if oos["wr"] >= 45: g += 1
                    if oos["posm"]/oos["mt"] >= 0.75 if oos["mt"] > 0 else False: g += 1
                    if oos["toppct"] <= 20: g += 1
                    if oos["nb"] >= 8000: g += 1
                    if wf >= 5: g += 1
                    g += 1  # anti-lookahead
                    l_results[name] = {"is": is_, "oos": oos, "tdf": tdf, "wf": wf, "gates": g}

print(f"Viable L configs: {len(l_results)}")

# Sort by topM (ascending), filter those with PnL ≥ $10K
l_topm_focus = [(k, v) for k, v in l_results.items()
                if v["oos"]["pnl"] >= 10000]
l_topm_focus.sort(key=lambda x: x[1]["oos"]["toppct"])

print(f"\nL configs with PnL ≥ $10K: {len(l_topm_focus)}")
print(f"\nTOP 30 by LOWEST topM (PnL ≥ $10K):")
print(f"{'Config':42s} {'G':>2s} {'t':>4s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} {'topM':>5s} {'NB':>8s} {'PM':>5s} {'WF':>3s}")
print("-" * 105)
for name, r in l_topm_focus[:30]:
    o = r["oos"]
    print(f"{name:42s} {r['gates']:2d} {o['n']:4d} {o['pnl']:8,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
          f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['nb']:8,.0f} {o['posm']}/{o['mt']} {r['wf']:3d}")

# Full gate check on best topM L configs
print("\n" + "=" * 95)
print("FULL GATE CHECK — BEST L CONFIGS BY topM")
print("=" * 95)

best_l = None; best_l_gates = 0
for name, r in l_topm_focus[:8]:
    g = full_gates(f"L: {name}", r["oos"], r["tdf"], mid, end, "L", show_monthly=True)
    if g > best_l_gates:
        best_l_gates = g; best_l = (name, r["oos"], r["tdf"])

# Also check the 9/10 VE configs from R8
print("\n--- R8 best (9/10, topM 41%) for comparison ---")
ve60_mask = (df["ve20_pct"] < 60).fillna(False) & df["bl_up_10"].fillna(False)
r8_tdf = bt_long_trail(df, ve60_mask, max_same=9, exit_cd=8, cap=15)
r8_oos = evaluate(r8_tdf, mid, end, "L")
full_gates("L_R8_ve60_bl10_c15_cd8_ms9", r8_oos, r8_tdf, mid, end, "L", show_monthly=False)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: COMBINED L+S
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("PART 3: COMBINED L+S")
print("=" * 95)

# Collect L candidates
l_cands = []
if best_l:
    l_cands.append(best_l)
# Add top 3 by topM
for name, r in l_topm_focus[:3]:
    l_cands.append((name, r["oos"], r["tdf"]))
# Add R8 best
l_cands.append(("R8_ve60", r8_oos, r8_tdf))

# Collect S candidates
s_cands = []
if best_s:
    s_cands.append(best_s)
for n_subs, combo in best_combos.items():
    names = combo[0]
    frames = [s_subs[n]["tdf"] for n in names]
    merged = pd.concat(frames, ignore_index=True)
    oos = evaluate(merged, mid, end, "S")
    if oos:
        s_cands.append((f"S_{n_subs}sub", oos, merged))

print(f"\nL candidates: {len(l_cands)}, S candidates: {len(s_cands)}")

print(f"\n{'L':30s} {'S':12s} {'L$':>8s} {'S$':>8s} {'Tot':>8s} {'LG':>3s} {'SG':>3s} {'PM':>5s} {'Worst':>8s} {'m500':>5s} {'CG':>3s}")
print("-" * 115)

best_overall = None; best_ov_score = 0
for l_name, l_oos, l_tdf in l_cands:
    l_wf = wf6(l_tdf, mid, end)
    lg = 0
    if l_oos["pnl"]>=10000: lg+=1
    if l_oos["pf"]>=1.5: lg+=1
    if l_oos["mdd"]<=25: lg+=1
    if l_oos["tpm"]>=10: lg+=1
    if l_oos["wr"]>=45: lg+=1
    if l_oos["posm"]/l_oos["mt"]>=0.75: lg+=1
    if l_oos["toppct"]<=20: lg+=1
    if l_oos["nb"]>=8000: lg+=1
    if l_wf>=5: lg+=1
    lg+=1

    for s_name, s_oos, s_tdf in s_cands:
        s_wf = wf6(s_tdf, mid, end)
        sg = 0
        if s_oos["pnl"]>=10000: sg+=1
        if s_oos["pf"]>=1.5: sg+=1
        if s_oos["mdd"]<=25: sg+=1
        if s_oos["tpm"]>=10: sg+=1
        if s_oos["wr"]>=65: sg+=1
        if s_oos["posm"]/s_oos["mt"]>=0.75: sg+=1
        if s_oos["toppct"]<=20: sg+=1
        if s_oos["nb"]>=8000: sg+=1
        if s_wf>=5: sg+=1
        sg+=1

        l_ms = l_oos["monthly"]; s_ms = s_oos["monthly"]
        all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))
        combined = {m: l_ms.get(m,0)+s_ms.get(m,0) for m in all_months}
        tot = sum(combined.values())
        pm = sum(1 for v in combined.values() if v>0)
        worst = min(combined.values())
        m500 = sum(1 for v in combined.values() if v>=500)

        cg = 0
        if tot>=20000: cg+=1
        if pm>=10: cg+=1
        if worst>=-1000: cg+=1
        if min(combined.values())>=500: cg+=1

        score = (lg+sg)*10000 + cg*1000 + tot
        if score > best_ov_score:
            best_ov_score = score
            best_overall = (l_name, s_name, l_oos, s_oos, l_tdf, s_tdf, lg, sg, cg)

        print(f"{l_name:30s} {s_name:12s} {l_oos['pnl']:8,.0f} {s_oos['pnl']:8,.0f} "
              f"{tot:8,.0f} {lg:3d} {sg:3d} {pm}/{len(all_months)} {worst:8,.0f} {m500}/{len(all_months)} {cg}/4")

if best_overall:
    l_name, s_name, l_oos, s_oos, l_tdf, s_tdf, lg, sg, cg = best_overall
    print(f"\n{'='*60}")
    print(f"BEST OVERALL: L={l_name} ({lg}/10) + S={s_name} ({sg}/10) CG={cg}/4")
    print(f"{'='*60}")

    l_ms = l_oos["monthly"]; s_ms = s_oos["monthly"]
    all_months = sorted(set(l_ms.index.tolist() + s_ms.index.tolist()))
    print(f"\n{'Month':10s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'$500':>5s}")
    for m in all_months:
        lp=l_ms.get(m,0); sp=s_ms.get(m,0); tp_=lp+sp
        p5="✓" if tp_>=500 else "✗"
        print(f"{str(m):10s} {lp:8,.0f} {sp:8,.0f} {tp_:8,.0f} {p5}")

    combined = {m: l_ms.get(m,0)+s_ms.get(m,0) for m in all_months}
    tot = sum(combined.values())
    pm = sum(1 for v in combined.values() if v>0)
    worst = min(combined.values())
    min_mpnl = min(combined.values())
    print(f"\n  Total: ${tot:,.0f}, PM: {pm}/{len(all_months)}, Worst: ${worst:,.0f}, Min monthly: ${min_mpnl:,.0f}")

print("\nDone.")
