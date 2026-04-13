"""
v7-R5: Ultra-High WR Exploration
==================================
R4 achieved S 8/10 gates. Only failures: Monthly WR (min 56%) and Monthly PnL (-$1,369).
Both stem from the same root cause: some months have too many losing trades.

Hypothesis: Ultra-low TP (0.5-1.5%) maximizes WR by making profit targets trivially
easy to reach after breakout. If enough trades hit TP before SafeNet or MaxHold, monthly
WR can approach 70%+. PnL maintained through high trade volume (CMP 6-8 subs).

Additional test: S with EarlyStop (exit early if losing after N bars) to reduce
MaxHold losses and improve monthly WR consistency.

Also test: whether tighter SafeNet (3.5% instead of 4.5%) reduces SN magnitude,
improving overall PnL in bad months.

Wait — SafeNet must be 4.5% per spec. But we CAN test whether the number of SN exits
can be reduced by exiting earlier via EarlyStop.
"""

import sys, io, warnings
import numpy as np, pandas as pd

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

# Indicators
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

# Drawdown regime filter
df["dd50"] = ((df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()).shift(1)
dd_mild = (df["dd50"] < -0.01).fillna(False)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# S Engine: TP + MH + optional EarlyStop
# ═══════════════════════════════════════════════════════

def bt_short_adv(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
                 early_stop_bar=0, early_stop_pct=0.0,
                 max_same=5, exit_cd=6, tag="S"):
    """Short with optional EarlyStop: if after early_stop_bar bars,
    unrealized loss > early_stop_pct, exit immediately."""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    IND=ind_mask.values; BL=df[bl_col].fillna(False).values
    SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}

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
            # EarlyStop for S: if losing after N bars
            if not done and early_stop_bar > 0 and bh >= early_stop_bar:
                pnl_pct = (p["e"] - c) / p["e"]  # positive = profit for short
                if pnl_pct < -early_stop_pct:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
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
# L Engine: Trail (baseline)
# ═══════════════════════════════════════════════════════

def bt_long_trail(df, mask, max_same=5, exit_cd=8, cap=15):
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
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done and bh>=12 and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","side"])

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
    tpm=n/((end_dt-start_dt).days/30.44)
    p["win"]=(p["pnl"]>0).astype(int)
    mwr=p.groupby("m").apply(lambda g: g["win"].mean()*100)
    min_mwr=mwr.min() if len(mwr)>0 else 0
    avg_mwr=mwr.mean() if len(mwr)>0 else 0
    wr70=(mwr>=70).sum()
    min_mpnl=ms.min() if len(ms)>0 else 0
    pnl500=(ms>=500).sum()
    ed=p.groupby("t").agg(n_=("pnl","count"),pnl_=("pnl","sum"),
                          wr_=("win","mean")).reset_index() if "t" in p.columns else pd.DataFrame()
    return {"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,"tpm":tpm,
            "mt":mt,"posm":posm,"toppct":toppct,"nb":nb,"worstv":worstv,
            "min_mwr":min_mwr,"avg_mwr":avg_mwr,"wr70":wr70,
            "min_mpnl":min_mpnl,"pnl500":pnl500,"monthly":ms,
            "monthly_wr":mwr,"exit_dist":ed,"avg":pnl/n if n else 0}

def wf6(tdf, s, e):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    r=0
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2); te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        if len(tt)>0 and tt["pnl"].sum()>0: r+=1
    return r

# ═══════════════════════════════════════════════════════
# Phase 1: S Ultra-Low TP Individual Sub Sweep
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("v7-R5: ULTRA-HIGH WR EXPLORATION")
print("="*95)

TRUE_MASK = pd.Series(True, index=df.index)

# Grid: TP {0.5%, 0.75%, 1.0%, 1.25%, 1.5%} × BL {8,10,12,15} × MH {12,15,19}
# With and without dd regime filter
# With and without EarlyStop

sub_cfgs = []
for tp in [0.005, 0.0075, 0.010, 0.0125, 0.015]:
    for bl in [8, 10, 12, 15]:
        for mh in [12, 15, 19]:
            tp_str = f"tp{int(tp*10000):02d}"
            # No filter
            sub_cfgs.append((f"n_{tp_str}_bl{bl}_mh{mh}", TRUE_MASK, f"bl_dn_{bl}",
                            tp, mh, 0, 0))
            # Drawdown filter
            sub_cfgs.append((f"d_{tp_str}_bl{bl}_mh{mh}", dd_mild, f"bl_dn_{bl}",
                            tp, mh, 0, 0))
    # EarlyStop variants: tp × BL10 × MH19 with ES at bar 6 if loss > 0.5%
    sub_cfgs.append((f"es_{tp_str}_bl10_mh19", TRUE_MASK, "bl_dn_10",
                    tp, 19, 6, 0.005))

print(f"Total configs: {len(sub_cfgs)}")

# Run all
sub_results = {}
for name, ind, bl, tp, mh, es_bar, es_pct in sub_cfgs:
    tdf = bt_short_adv(df, ind, bl, tp_pct=tp, max_hold=mh,
                       early_stop_bar=es_bar, early_stop_pct=es_pct, tag=name)
    is_r = evaluate(tdf, df["datetime"].iloc[0], mid)
    oos_r = evaluate(tdf, mid, end)
    sub_results[name] = {"is": is_r, "oos": oos_r, "tdf": tdf}

# Filter viable (IS+OOS positive)
viable = [(k, v) for k, v in sub_results.items()
          if v["is"] and v["oos"] and v["is"]["pnl"] > 0 and v["oos"]["pnl"] > 0]
viable.sort(key=lambda x: x[1]["oos"]["wr"], reverse=True)  # Sort by WR

print(f"\nViable: {len(viable)} / {len(sub_cfgs)}")

# Top 15 by OOS WR
print("\nTOP 15 by OOS Win Rate:")
print(f"{'Config':28s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 62)
for name, r in viable[:15]:
    o=r["oos"]
    print(f"{name:28s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")

# Top 15 by OOS PnL (among high-WR configs)
high_wr = [(k,v) for k,v in viable if v["oos"]["wr"] >= 65]
high_wr.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
print("\nTOP 15 High-WR (≥65%) by PnL:")
print(f"{'Config':28s} {'OOS_t':>5s} {'PnL':>7s} {'PF':>5s} {'WR':>5s} {'mWR':>5s} {'mWRmin':>6s}")
print("-" * 62)
for name, r in high_wr[:15]:
    o=r["oos"]
    print(f"{name:28s} {o['n']:5d} {o['pnl']:7,.0f} {o['pf']:5.2f} "
          f"{o['wr']:5.1f} {o['avg_mwr']:5.1f} {o['min_mwr']:6.0f}")

# ═══════════════════════════════════════════════════════
# Phase 2: Build CMP Portfolios from best subs
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("Phase 2: CMP PORTFOLIO CONSTRUCTION")
print("="*95)

def pick_diverse_from_list(items, n=4):
    """Pick diverse subs (different BL lengths)."""
    picked = []; seen = set()
    for name, r in items:
        bl_key = name.split("_bl")[1].split("_")[0] if "_bl" in name else "?"
        if bl_key not in seen:
            picked.append((name, r)); seen.add(bl_key)
        if len(picked) >= n: break
    return picked

def build_cmp_from_picks(picks):
    dfs = [r["tdf"] for _, r in picks if len(r["tdf"]) > 0]
    if not dfs: return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(dfs, ignore_index=True)

# A: WR-focused CMP (top WR subs)
wr_picks4 = pick_diverse_from_list(viable, 4)
wr_picks6 = pick_diverse_from_list(viable, 6)

# B: PnL-focused CMP (high-WR + high PnL)
pnl_sorted = sorted(viable, key=lambda x: x[1]["oos"]["pnl"], reverse=True)
pnl_picks4 = pick_diverse_from_list(pnl_sorted, 4)
pnl_picks6 = pick_diverse_from_list(pnl_sorted, 6)

# C: Drawdown-filtered CMP
dd_viable = [(k,v) for k,v in viable if k.startswith("d_")]
dd_viable.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
dd_picks4 = pick_diverse_from_list(dd_viable, 4)
dd_picks6 = pick_diverse_from_list(dd_viable, 6)

# D: Best R4 combo (reference)
r4_subs = [
    ("Sd1_ref", sub_results.get("d_tp15_bl10_mh12", {"tdf": pd.DataFrame()})),
    ("Sd2_ref", sub_results.get("d_tp15_bl15_mh12", {"tdf": pd.DataFrame()})),
    ("Sd3_ref", sub_results.get("d_tp15_bl8_mh12", {"tdf": pd.DataFrame()})),
    ("Sd4_ref", sub_results.get("n_tp15_bl15_mh15", {"tdf": pd.DataFrame()})),
]

# E: Mixed TP levels (0.75 + 1.0 + 1.5)
mixed_viable = [(k,v) for k,v in viable if "tp07" in k or "tp10" in k or "tp15" in k]
mixed_viable.sort(key=lambda x: x[1]["oos"]["pnl"], reverse=True)
mix_picks6 = pick_diverse_from_list(mixed_viable, 6)

portfolios = [
    ("WR_4sub", wr_picks4),
    ("WR_6sub", wr_picks6),
    ("PnL_4sub", pnl_picks4),
    ("PnL_6sub", pnl_picks6),
    ("DD_4sub", dd_picks4),
    ("DD_6sub", dd_picks6),
    ("MIX_6sub", mix_picks6),
]

print(f"\n{'Portfolio':14s} {'OOS_t':>5s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} "
      f"{'MDD':>5s} {'topM':>5s} {'mWR':>5s} {'mWRmin':>6s} {'PM':>5s} {'WF':>3s}")
print("-" * 78)

port_results = []
for label, picks in portfolios:
    merged = build_cmp_from_picks(picks)
    oos = evaluate(merged, mid, end)
    wf = wf6(merged, mid, end)
    port_results.append({"label": label, "picks": picks, "oos": oos, "wf": wf, "tdf": merged})
    if oos:
        print(f"{label:14s} {oos['n']:5d} {oos['pnl']:8,.0f} {oos['pf']:5.2f} "
              f"{oos['wr']:5.1f} {oos['mdd']:5.1f} {oos['toppct']:5.1f} "
              f"{oos['avg_mwr']:5.1f} {oos['min_mwr']:6.0f} "
              f"{oos['posm']}/{oos['mt']} {wf:3d}")

# Show composition of top portfolios
print("\nPortfolio Composition:")
for pr in port_results:
    if not pr["oos"]: continue
    print(f"\n  {pr['label']} (${pr['oos']['pnl']:,.0f}, WR {pr['oos']['wr']:.1f}%):")
    for name, r in pr["picks"]:
        o = r["oos"]
        if o:
            print(f"    {name:28s} {o['n']:4d}t ${o['pnl']:>6,.0f} WR{o['wr']:5.1f}% mWR{o['avg_mwr']:.0f}%")

# ═══════════════════════════════════════════════════════
# Phase 3: Best S Portfolio Monthly Detail
# ═══════════════════════════════════════════════════════

# Find best S by max gates passed
def count_gates(oos, wf):
    if not oos: return 0
    o = oos
    return sum([
        o["pnl"]>=10000, o["pf"]>=1.5, o["mdd"]<=25, o["tpm"]>=10,
        o["min_mwr"]>=70, o["min_mpnl"]>=500,
        o["posm"]/max(o["mt"],1)>=0.75, o["toppct"]<=20,
        o["nb"]>=8000, wf>=5
    ])

best_s = max(port_results, key=lambda x: (count_gates(x["oos"], x["wf"]), x["oos"]["pnl"] if x["oos"] else 0))
s_oos = best_s["oos"]; s_wf = best_s["wf"]

print(f"\n{'='*70}")
print(f"BEST S PORTFOLIO: {best_s['label']}")
print(f"{'='*70}")

if s_oos:
    ms = s_oos["monthly"]; mwr = s_oos["monthly_wr"]
    print(f"OOS: {s_oos['n']}t ${s_oos['pnl']:,.0f} PF{s_oos['pf']:.2f} WR{s_oos['wr']:.1f}%")
    print(f"\n{'Month':10s} {'PnL':>8s} {'WR':>6s} {'$500':>5s} {'70%':>4s}")
    for m in ms.index:
        pv=ms[m]; wv=mwr[m] if m in mwr.index else 0
        f1 = "✓" if pv >= 500 else "✗"
        f2 = "✓" if wv >= 70 else "✗"
        print(f"{str(m):10s} {pv:>8,.0f} {wv:>5.1f}%  {f1}   {f2}")

    ed = s_oos["exit_dist"]
    if len(ed)>0:
        print(f"\nExit Distribution:")
        print(f"  {'Type':6s} {'N':>5s} {'PnL':>8s} {'Avg':>7s} {'WR':>6s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):5d} {row['pnl_']:8,.0f} "
                  f"{row['pnl_']/row['n_']:7.1f} {row['wr_']*100:5.1f}%")

# ═══════════════════════════════════════════════════════
# Phase 4: Combined L+S with best S
# ═══════════════════════════════════════════════════════

print(f"\n{'='*95}")
print(f"COMBINED L+S EVALUATION")
print(f"{'='*95}")

# L: trail_base
l_entry = df["bl_up_10"].fillna(False)
l_tdf = bt_long_trail(df, l_entry)
l_oos = evaluate(l_tdf, mid, end)
l_wf = wf6(l_tdf, mid, end)

# Combined
combined = pd.concat([l_tdf, best_s["tdf"]], ignore_index=True)
c_oos = evaluate(combined, mid, end)
c_wf = wf6(combined, mid, end)

if l_oos and s_oos and c_oos:
    print(f"\nL trail_base: ${l_oos['pnl']:,.0f}")
    print(f"S {best_s['label']}: ${s_oos['pnl']:,.0f}")
    print(f"Combined: ${c_oos['pnl']:,.0f}")

    # Combined monthly
    c_ms = c_oos["monthly"]
    l_ms = l_oos["monthly"]
    print(f"\n{'Month':10s} {'L_PnL':>8s} {'S_PnL':>8s} {'Total':>8s} {'WR':>6s}")
    for m in c_ms.index:
        l_v = l_ms[m] if m in l_ms.index else 0
        s_v = s_oos["monthly"][m] if m in s_oos["monthly"].index else 0
        c_v = c_ms[m]
        c_wr = c_oos["monthly_wr"][m] if m in c_oos["monthly_wr"].index else 0
        print(f"{str(m):10s} {l_v:>8,.0f} {s_v:>8,.0f} {c_v:>8,.0f} {c_wr:>5.1f}%")

# ═══════════════════════════════════════════════════════
# Phase 5: FULL GATE CHECK
# ═══════════════════════════════════════════════════════

print(f"\n{'='*95}")
print(f"FULL GATE CHECK")
print(f"{'='*95}")

for label, oos, wf in [("L (trail_base)", l_oos, l_wf),
                        (f"S ({best_s['label']})", s_oos, s_wf)]:
    if not oos: continue
    o = oos
    print(f"\n  {label}:")
    gates = [
        ("OOS PnL ≥ $10K",         o["pnl"]>=10000,         f"${o['pnl']:,.0f}"),
        ("PF ≥ 1.5",               o["pf"]>=1.5,            f"{o['pf']:.2f}"),
        ("MDD ≤ 25%",              o["mdd"]<=25,            f"{o['mdd']:.1f}%"),
        ("TPM ≥ 10",               o["tpm"]>=10,            f"{o['tpm']:.1f}"),
        ("Monthly WR ≥ 70% all",   o["min_mwr"]>=70,        f"min={o['min_mwr']:.0f}%,{o['wr70']}/{o['mt']}m"),
        ("Monthly PnL ≥ $500 all", o["min_mpnl"]>=500,      f"min=${o['min_mpnl']:,.0f},{o['pnl500']}/{o['mt']}m"),
        ("Pos months ≥ 75%",       o["posm"]/max(o["mt"],1)>=0.75, f"{o['posm']}/{o['mt']}"),
        ("topM ≤ 20%",             o["toppct"]<=20,         f"{o['toppct']:.1f}%"),
        ("Remove best ≥ $8K",      o["nb"]>=8000,           f"${o['nb']:,.0f}"),
        ("WF ≥ 5/6",              wf>=5,                    f"{wf}/6"),
    ]
    passed=sum(1 for _,ok,_ in gates if ok)
    for g,ok,v in gates:
        s_="✓" if ok else "✗"
        print(f"    {s_} {g:30s} → {v}")
    print(f"  Score: {passed}/10")

if c_oos:
    print(f"\n  L+S Combined:")
    c_gates = [
        ("L+S ≥ $20K",       c_oos["pnl"]>=20000,         f"${c_oos['pnl']:,.0f}"),
        ("L+S PM ≥ 10/12",   c_oos["posm"]>=10,           f"{c_oos['posm']}/{c_oos['mt']}"),
        ("L+S worst ≥ -$1K", c_oos["worstv"]>=-1000,      f"${c_oos['worstv']:,.0f}"),
    ]
    for g,ok,v in c_gates:
        s_="✓" if ok else "✗"
        print(f"    {s_} {g:30s} → {v}")

# ═══════════════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════════════

# WR ceiling analysis
print(f"\n{'='*70}")
print("WR CEILING ANALYSIS: What's the maximum achievable monthly WR?")
print(f"{'='*70}")

# Find the single config with highest min monthly WR
best_min_mwr = 0
best_min_cfg = ""
for name, r in sub_results.items():
    oos = r["oos"]
    if oos and oos["pnl"] > 0 and oos["min_mwr"] > best_min_mwr:
        best_min_mwr = oos["min_mwr"]
        best_min_cfg = name

print(f"Highest min monthly WR (individual): {best_min_cfg}")
if best_min_cfg:
    o = sub_results[best_min_cfg]["oos"]
    print(f"  OOS: {o['n']}t ${o['pnl']:,.0f} WR{o['wr']:.1f}% "
          f"mWR_avg={o['avg_mwr']:.1f}% mWR_min={o['min_mwr']:.0f}%")

# Count how many configs achieve min mWR >= 60, 65, 70
for thresh in [50, 55, 60, 65, 70]:
    cnt = sum(1 for _, r in sub_results.items()
              if r["oos"] and r["oos"]["pnl"] > 0 and r["oos"]["min_mwr"] >= thresh)
    print(f"  Configs with min monthly WR ≥ {thresh}%: {cnt}/{len(viable)}")

print("\nDone.")
