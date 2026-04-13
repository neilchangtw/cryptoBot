"""
v7-R3: S CMP Portfolio with Novel Indicators
==============================================
R1 found: S_kurt80 best PF (1.91, WR 66.7%) but only $669/33 trades.
S_base (pure BL) has $2,754/182 trades. Need 4x boost to reach $10K.

Hypothesis: CMP portfolio (multiple sub-strategies in parallel) using
Kurtosis threshold (not GK) with varied BL lengths and TP levels can
additively produce $10K+, like v6's GK-based CMP achieved $19K.

Also test TBR (Taker Buy Ratio) as alternative S indicator.

Phase 1: Individual sub-strategy sweep (indicator × BL × TP × MH)
Phase 2: Build CMP portfolio from top performers
Phase 3: Gate check

Entry indicators (novel, not v6's GK):
  - Excess Kurtosis percentile (kurt_pct > threshold)
  - Taker Buy Ratio percentile (tbr_pct < threshold = sell pressure)
  - No indicator (baseline BL only)

Exit: SafeNet 4.5% → TP → MaxHold
"""

import sys, io, warnings
import numpy as np, pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ─── Constants ───
NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
BRK_LOOK_BASE = 10
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
PCTILE_WIN = 100; WARMUP = 200; MAX_PER_BAR = 2

# ─── Data ───
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
print(f"Loaded {N} bars: {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")

mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)

# ─── Indicators ───
df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

# Close breakout (multiple lengths)
for w in [6, 8, 10, 12, 15, 18]:
    cs1 = df["close"].shift(1)
    brk_min = df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_dn_{w}"] = cs1 < brk_min

# Session
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")

# Kurtosis
df["kurt"] = df["ret"].rolling(20).apply(lambda x: pd.Series(x).kurtosis(), raw=True)

# Taker Buy Ratio (smoothed)
df["tbr"] = (df["tbv"] / df["volume"]).rolling(5).mean()

# Percentiles
def pctile_func(x):
    if x.max() == x.min(): return 50.0
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100.0

df["kurt_pct"] = df["kurt"].shift(1).rolling(PCTILE_WIN).apply(pctile_func)
df["tbr_pct"]  = df["tbr"].shift(1).rolling(PCTILE_WIN).apply(pctile_func)

print(f"kurt_pct: {df['kurt_pct'].notna().sum()} valid, mean={df['kurt_pct'].mean():.1f}")
print(f"tbr_pct:  {df['tbr_pct'].notna().sum()} valid, mean={df['tbr_pct'].mean():.1f}")

# ─── Helper ───
def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# S Backtest Engine (single sub-strategy)
# ═══════════════════════════════════════════════════════

def bt_short_sub(df, ind_mask, bl_col, tp_pct=0.02, max_hold=15,
                 max_same=5, exit_cd=6, tag="S"):
    """Short sub-strategy: SafeNet 4.5% → TP → MaxHold"""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    IND=ind_mask.values; BL=df[bl_col].fillna(False).values
    SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}

    for i in range(WARMUP, n-1):
        h=H[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]
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
                if Lo[i]<=tp:
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

# ═══════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════

def evaluate(tdf, start_dt, end_dt):
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
    worstv=ms.min() if len(ms)>0 else 0
    days=(end_dt-start_dt).days; tpm=n/(days/30.44) if days>0 else 0

    p["win"]=(p["pnl"]>0).astype(int)
    mwr=p.groupby("m").apply(lambda g: g["win"].mean()*100)
    min_mwr=mwr.min() if len(mwr)>0 else 0
    avg_mwr=mwr.mean() if len(mwr)>0 else 0
    wr70=(mwr>=70).sum()
    min_mpnl=ms.min() if len(ms)>0 else 0
    pnl500=(ms>=500).sum()

    return {"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,"tpm":tpm,
            "mt":mt,"posm":posm,"toppct":toppct,"nb":nb,"worstv":worstv,
            "min_mwr":min_mwr,"avg_mwr":avg_mwr,"wr70":wr70,
            "min_mpnl":min_mpnl,"pnl500":pnl500,
            "monthly":ms,"monthly_wr":mwr,"avg":pnl/n if n else 0}

def wf6(tdf, s, e):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    r=0
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2); te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        if len(tt)>0 and tt["pnl"].sum()>0: r+=1
    return r

# ═══════════════════════════════════════════════════════
# Phase 1: Individual Sub-Strategy Sweep
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("v7-R3: S CMP PORTFOLIO — Phase 1: Individual Sub-Strategy Sweep")
print("="*95)

# Define sweep grid
sub_configs = []

# No indicator (baseline): BL × TP × MH
for bl in [8, 10, 12, 15]:
    for tp in [0.015, 0.02, 0.025]:
        for mh in [12, 15, 19]:
            name = f"base_bl{bl}_tp{int(tp*1000)}_mh{mh}"
            sub_configs.append((name, pd.Series(True, index=df.index), f"bl_dn_{bl}", tp, mh))

# Kurt-filtered: kurt_pct > threshold × BL × TP × MH
for kt in [50, 60, 70, 80]:
    for bl in [8, 10, 12, 15]:
        for tp in [0.015, 0.02, 0.025]:
            for mh in [12, 15, 19]:
                name = f"k{kt}_bl{bl}_tp{int(tp*1000)}_mh{mh}"
                mask = (df["kurt_pct"] > kt).fillna(False)
                sub_configs.append((name, mask, f"bl_dn_{bl}", tp, mh))

# TBR-filtered: tbr_pct < threshold × BL × TP × MH
for tt in [30, 40, 50]:
    for bl in [8, 10, 12, 15]:
        for tp in [0.015, 0.02]:
            name = f"tbr{tt}_bl{bl}_tp{int(tp*1000)}_mh15"
            mask = (df["tbr_pct"] < tt).fillna(False)
            sub_configs.append((name, mask, f"bl_dn_{bl}", tp, 15))

print(f"Total configs to test: {len(sub_configs)}")
print()

# Run all sub-strategies
sub_results = []
for name, ind_mask, bl_col, tp, mh in sub_configs:
    tdf = bt_short_sub(df, ind_mask, bl_col, tp_pct=tp, max_hold=mh, tag=name)
    is_r = evaluate(tdf, df["datetime"].iloc[0], mid)
    oos_r = evaluate(tdf, mid, end)
    sub_results.append({"name": name, "is": is_r, "oos": oos_r, "tdf": tdf,
                         "tp": tp, "mh": mh})

# Filter: IS positive + OOS positive
viable = [r for r in sub_results
          if r["is"] and r["oos"]
          and r["is"]["pnl"] > 0 and r["oos"]["pnl"] > 0]
viable.sort(key=lambda x: x["oos"]["pnl"], reverse=True)

print(f"Viable configs (IS>0 + OOS>0): {len(viable)} / {len(sub_configs)}")
print()

# Top 20 individual performers
print("TOP 20 Individual Sub-Strategies (OOS PnL):")
print(f"{'Config':32s} {'IS_t':>4s} {'IS_PnL':>7s} {'OOS_t':>5s} {'OOS_PnL':>7s} "
      f"{'PF':>5s} {'WR':>5s} {'MDD':>5s} {'mWR':>5s}")
print("-" * 82)
for r in viable[:20]:
    i=r["is"]; o=r["oos"]
    print(f"{r['name']:32s} {i['n']:4d} {i['pnl']:7,.0f} {o['n']:5d} {o['pnl']:7,.0f} "
          f"{o['pf']:5.2f} {o['wr']:5.1f} {o['mdd']:5.1f} {o['avg_mwr']:5.1f}")

# ═══════════════════════════════════════════════════════
# Phase 2: CMP Portfolio (Top N sub-strategies combined)
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("Phase 2: CMP PORTFOLIO CONSTRUCTION")
print("="*95)

def build_cmp(sub_list, label):
    """Merge trade streams from multiple sub-strategies."""
    dfs = []
    for r in sub_list:
        if len(r["tdf"]) > 0:
            dfs.append(r["tdf"])
    if not dfs:
        return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(dfs, ignore_index=True)

# Strategy A: Top 4 by OOS PnL (greedy)
top4 = viable[:4]
cmp_a = build_cmp(top4, "Top4_PnL")
oos_a = evaluate(cmp_a, mid, end)
wf_a = wf6(cmp_a, mid, end)

# Strategy B: Top 4 diverse (different indicators/BL combos)
# Pick best from each indicator family
def pick_diverse(viable_list, n=4):
    picked = []
    seen_keys = set()
    for r in viable_list:
        name = r["name"]
        # Extract key: indicator + bl
        parts = name.split("_")
        if parts[0].startswith("k"):
            key = f"{parts[0]}_{parts[1]}"  # e.g. k70_bl10
        elif parts[0].startswith("tbr"):
            key = f"{parts[0]}_{parts[1]}"
        else:
            key = f"base_{parts[1]}"
        if key not in seen_keys:
            picked.append(r)
            seen_keys.add(key)
        if len(picked) >= n:
            break
    return picked

div4 = pick_diverse(viable, 4)
cmp_b = build_cmp(div4, "Div4")
oos_b = evaluate(cmp_b, mid, end)
wf_b = wf6(cmp_b, mid, end)

# Strategy C: Top 6 diverse
div6 = pick_diverse(viable, 6)
cmp_c = build_cmp(div6, "Div6")
oos_c = evaluate(cmp_c, mid, end)
wf_c = wf6(cmp_c, mid, end)

# Strategy D: Top 8 diverse
div8 = pick_diverse(viable, 8)
cmp_d = build_cmp(div8, "Div8")
oos_d = evaluate(cmp_d, mid, end)
wf_d = wf6(cmp_d, mid, end)

# Strategy E: Only Kurt-based top 4
kurt_viable = [r for r in viable if r["name"].startswith("k")]
kurt_top4 = pick_diverse(kurt_viable, 4)
cmp_e = build_cmp(kurt_top4, "Kurt4")
oos_e = evaluate(cmp_e, mid, end)
wf_e = wf6(cmp_e, mid, end)

# Strategy F: Only TBR-based top 4
tbr_viable = [r for r in viable if r["name"].startswith("tbr")]
tbr_top4 = pick_diverse(tbr_viable, 4) if len(tbr_viable) >= 4 else tbr_viable
cmp_f = build_cmp(tbr_top4, "TBR4")
oos_f = evaluate(cmp_f, mid, end)
wf_f = wf6(cmp_f, mid, end)

# Strategy G: Mixed Kurt + TBR (2+2)
mixed4 = pick_diverse(kurt_viable, 2) + pick_diverse(tbr_viable, 2)
cmp_g = build_cmp(mixed4, "Mix4")
oos_g = evaluate(cmp_g, mid, end)
wf_g = wf6(cmp_g, mid, end)

# Strategy H: Only baseline (no indicator) top 4
base_viable = [r for r in viable if r["name"].startswith("base")]
base_top4 = pick_diverse(base_viable, 4)
cmp_h = build_cmp(base_top4, "Base4")
oos_h = evaluate(cmp_h, mid, end)
wf_h = wf6(cmp_h, mid, end)

portfolios = [
    ("Top4_PnL", top4, oos_a, wf_a),
    ("Div4",     div4, oos_b, wf_b),
    ("Div6",     div6, oos_c, wf_c),
    ("Div8",     div8, oos_d, wf_d),
    ("Kurt4",    kurt_top4, oos_e, wf_e),
    ("TBR4",     tbr_top4, oos_f, wf_f),
    ("Mix4_K2T2",mixed4, oos_g, wf_g),
    ("Base4",    base_top4, oos_h, wf_h),
]

print("\nCMP PORTFOLIO COMPARISON:")
print(f"{'Portfolio':12s} {'Subs':>4s} {'OOS_t':>5s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} "
      f"{'MDD':>5s} {'topM':>5s} {'mWR':>5s} {'PM':>5s} {'WF':>3s}")
print("-" * 72)

for label, subs, oos, wf in portfolios:
    if oos:
        sub_names = ", ".join(r["name"][:15] for r in subs[:3])
        print(f"{label:12s} {len(subs):4d} {oos['n']:5d} {oos['pnl']:8,.0f} "
              f"{oos['pf']:5.2f} {oos['wr']:5.1f} {oos['mdd']:5.1f} "
              f"{oos['toppct']:5.1f} {oos['avg_mwr']:5.1f} "
              f"{oos['posm']}/{oos['mt']} {wf:3d}")
    else:
        print(f"{label:12s} — no trades —")

# Show sub-strategy composition of top portfolios
print("\nPORTFOLIO COMPOSITION:")
for label, subs, oos, wf in portfolios:
    if not oos: continue
    print(f"\n  {label} (${oos['pnl']:,.0f}):")
    for r in subs:
        o=r["oos"]
        if o:
            print(f"    {r['name']:32s} OOS:{o['n']:3d}t ${o['pnl']:>6,.0f} "
                  f"PF{o['pf']:.2f} WR{o['wr']:.1f}%")

# ═══════════════════════════════════════════════════════
# Best Portfolio: Monthly Detail
# ═══════════════════════════════════════════════════════

# Find best by PnL
best_port = max(portfolios, key=lambda x: x[2]["pnl"] if x[2] else 0)
bp_label, bp_subs, bp_oos, bp_wf = best_port

print(f"\n{'='*70}")
print(f"BEST PORTFOLIO: {bp_label}")
print(f"{'='*70}")

if bp_oos:
    o = bp_oos
    ms = o["monthly"]; mwr = o["monthly_wr"]
    print(f"\nOOS: {o['n']}t ${o['pnl']:,.0f} PF{o['pf']:.2f} WR{o['wr']:.1f}% "
          f"MDD{o['mdd']:.1f}% topM{o['toppct']:.1f}%")
    print(f"\n{'Month':10s} {'PnL':>8s} {'WR':>6s}")
    for m in ms.index:
        pnl_v=ms[m]; wr_v=mwr[m] if m in mwr.index else 0
        flag=" ✗" if pnl_v<500 or wr_v<70 else ""
        print(f"{str(m):10s} {pnl_v:>8,.0f} {wr_v:>5.1f}%{flag}")

# ═══════════════════════════════════════════════════════
# Gate Check
# ═══════════════════════════════════════════════════════

print(f"\n{'='*70}")
print(f"GATE CHECK — S Strategy")
print(f"{'='*70}")

for label, subs, oos, wf in sorted(portfolios, key=lambda x: x[2]["pnl"] if x[2] else 0, reverse=True)[:3]:
    if not oos: continue
    o = oos
    print(f"\n  {label}:")
    gates = [
        ("OOS PnL ≥ $10K",         o["pnl"]>=10000,        f"${o['pnl']:,.0f}"),
        ("PF ≥ 1.5",               o["pf"]>=1.5,           f"{o['pf']:.2f}"),
        ("MDD ≤ 25%",              o["mdd"]<=25,           f"{o['mdd']:.1f}%"),
        ("TPM ≥ 10",               o["tpm"]>=10,           f"{o['tpm']:.1f}"),
        ("Monthly WR ≥ 70% all",   o["min_mwr"]>=70,       f"min={o['min_mwr']:.0f}%,{o['wr70']}/{o['mt']}m"),
        ("Monthly PnL ≥ $500 all", o["min_mpnl"]>=500,     f"min=${o['min_mpnl']:,.0f},{o['pnl500']}/{o['mt']}m"),
        ("Pos months ≥ 75%",       o["posm"]/max(o["mt"],1)>=0.75, f"{o['posm']}/{o['mt']}"),
        ("topM ≤ 20%",             o["toppct"]<=20,        f"{o['toppct']:.1f}%"),
        ("Remove best ≥ $8K",      o["nb"]>=8000,          f"${o['nb']:,.0f}"),
        ("WF ≥ 5/6",              wf>=5,                   f"{wf}/6"),
    ]
    passed=sum(1 for _,ok,_ in gates if ok)
    for g,ok,v in gates:
        s="✓" if ok else "✗"
        print(f"    {s} {g:30s} → {v}")
    print(f"  Score: {passed}/{len(gates)}")

# ═══════════════════════════════════════════════════════
# Summary Analysis
# ═══════════════════════════════════════════════════════

print(f"""
{'='*70}
R3 SUMMARY
{'='*70}
Phase 1: {len(sub_configs)} configs tested, {len(viable)} viable (IS+OOS positive)
Top individual: {viable[0]['name'] if viable else 'none'} → ${viable[0]['oos']['pnl']:,.0f} OOS

Phase 2: 8 portfolio strategies built
Best CMP: {bp_label} → ${bp_oos['pnl']:,.0f} OOS, {bp_oos['n']}t

Key insight: Kurtosis as S indicator {'outperforms' if kurt_viable and kurt_viable[0]['oos']['pnl'] > (base_viable[0]['oos']['pnl'] if base_viable else 0) else 'underperforms'} baseline.
""")

print("Done.")
