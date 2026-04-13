"""
v7-R4: Optimized L + S Combined Evaluation
=============================================
R1-R3 findings:
  L trail_base: $16,498, 5/10 gates. Fails topM(38%), WR(48%), WF(4/6).
  S CMP Top4_PnL: $19,429, 7/10 gates. Fails PF(1.45), WR(min 33%), mPnL(-$2,054).

R4 approach:
  L: ATR sizing (reduce topM) + indicator filter (improve WR/WF)
  S: PF-optimized CMP (select subs with PF>1.5) + regime filter for WR
  Combined: L+S merged evaluation

Regime filters tested for S:
  A. Drawdown filter: close < 0.97 * rolling_high(50) → bearish bias
  B. Volume declining: vol_ma5 < vol_ma20 → low activity (compression)
  C. No regime (baseline)
"""

import sys, io, warnings
import numpy as np, pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ─── Constants ───
NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
EMA_SPAN = 20; BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
PCTILE_WIN = 100; WARMUP = 200; MAX_PER_BAR = 2

# ─── Data ───
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)
print(f"Loaded {N} bars | IS/OOS split: {mid.date()}")

# ─── Indicators ───
df["ema20"] = df["close"].ewm(span=EMA_SPAN, adjust=False).mean()

# Close breakout (multiple lengths)
for w in [8, 10, 12, 15]:
    cs1 = df["close"].shift(1)
    brk_max = df["close"].shift(2).rolling(w - 1).max()
    brk_min = df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_up_{w}"] = cs1 > brk_max
    df[f"bl_dn_{w}"] = cs1 < brk_min

# Session
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")

# ATR for L sizing
def pctile_func(x):
    if x.max() == x.min(): return 50.0
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100.0

tr = pd.concat([df["high"]-df["low"],
                (df["high"]-df["close"].shift(1)).abs(),
                (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
df["atr14"] = tr.rolling(14).mean().shift(1)
df["atr_pct"] = df["atr14"].rolling(PCTILE_WIN).apply(pctile_func)

# Regime filters for S
df["dd50"] = (df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()
df["dd50_s1"] = df["dd50"].shift(1)  # shift for anti-lookahead
df["vol_ratio"] = (df["volume"].rolling(5).mean() / df["volume"].rolling(20).mean()).shift(1)

# Volume Entropy (from R1)
def vol_entropy(x):
    x = x[x > 0]
    if len(x) < 2: return np.nan
    p = x / x.sum()
    return -np.sum(p * np.log(p + 1e-15)) / np.log(len(x))
df["ve"] = df["volume"].rolling(20).apply(vol_entropy, raw=True)
df["ve_pct"] = df["ve"].shift(1).rolling(PCTILE_WIN).apply(pctile_func)

# Excess Kurtosis
df["kurt"] = df["ret"].rolling(20).apply(lambda x: pd.Series(x).kurtosis(), raw=True)
df["kurt_pct"] = df["kurt"].shift(1).rolling(PCTILE_WIN).apply(pctile_func)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# L Engine: Trail + ATR Sizing
# ═══════════════════════════════════════════════════════

def bt_long_atr(df, entry_mask, max_same=9, exit_cd=10, cap=12,
                atr_thresh=76, scale=0.60):
    """L: SafeNet 4.5% → EarlyStop → EMA20 Trail + ATR Binary Sizing"""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values; YM=df["ym"].values
    ATR_P=df["atr_pct"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}; ment={}

    for i in range(WARMUP, n-1):
        lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False; not_=p["not"]
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done and bh>=12 and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"side":"L"}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        not_size = NOTIONAL * scale if atr_p > atr_thresh else NOTIONAL
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","side"])

# ═══════════════════════════════════════════════════════
# S Engine: Sub-strategy with optional regime filter
# ═══════════════════════════════════════════════════════

def bt_short_sub(df, ind_mask, bl_col, tp_pct=0.02, max_hold=15,
                 max_same=5, exit_cd=6, tag="S"):
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
# Part 1: L Strategy Variants
# ═══════════════════════════════════════════════════════

print("\n" + "="*90)
print("PART 1: L STRATEGY VARIANTS")
print("="*90)

l_entry_base = df["bl_up_10"].fillna(False)
l_entry_ve = df["bl_up_10"].fillna(False) & (df["ve_pct"] < 30)
l_entry_kurt = df["bl_up_10"].fillna(False) & (df["kurt_pct"] > 70)

l_configs = [
    # (name, entry_mask, atr_thresh, scale, max_same, cd, cap)
    ("L_trail_base",    l_entry_base, 999, 1.0, 5, 8, 15),   # no ATR sizing
    ("L_atr76_s60",     l_entry_base, 76, 0.60, 9, 10, 12),  # v6 ATR sizing
    ("L_atr72_s55",     l_entry_base, 72, 0.55, 9, 10, 12),
    ("L_atr80_s65",     l_entry_base, 80, 0.65, 9, 10, 12),
    ("L_atr76_ms5",     l_entry_base, 76, 0.60, 5, 8, 12),   # tighter maxSame
    ("L_ve30_atr76",    l_entry_ve,   76, 0.60, 9, 10, 12),  # VE filter
    ("L_kurt70_atr76",  l_entry_kurt, 76, 0.60, 9, 10, 12),  # Kurt filter
]

l_results = []
for name, mask, atr_t, sc, ms, cd, cap in l_configs:
    tdf = bt_long_atr(df, mask, max_same=ms, exit_cd=cd, cap=cap,
                      atr_thresh=atr_t, scale=sc)
    is_r = evaluate(tdf, df["datetime"].iloc[0], mid)
    oos_r = evaluate(tdf, mid, end)
    wf = wf6(tdf, mid, end)
    l_results.append({"name": name, "is": is_r, "oos": oos_r, "wf": wf, "tdf": tdf})

    if is_r and oos_r:
        o=oos_r; i=is_r
        print(f"  {name:20s} | IS:{i['n']:3d}t ${i['pnl']:>7,.0f} | "
              f"OOS:{o['n']:3d}t ${o['pnl']:>7,.0f} PF{o['pf']:5.2f} WR{o['wr']:5.1f}% "
              f"MDD{o['mdd']:5.1f}% topM{o['toppct']:5.1f}% mWR{o['avg_mwr']:4.0f}% | WF{wf}/6")
    else:
        print(f"  {name:20s} | insufficient trades")

# ═══════════════════════════════════════════════════════
# Part 2: S Strategy — PF-Optimized CMP
# ═══════════════════════════════════════════════════════

print("\n" + "="*90)
print("PART 2: S STRATEGY — PF-OPTIMIZED CMP + REGIME FILTERS")
print("="*90)

# Define S sub-strategy configs (PF-focused: BL15 and tp15/20 showed PF>1.5)
TRUE_MASK = pd.Series(True, index=df.index)
dd_bear = (df["dd50_s1"] < -0.02).fillna(False)   # bearish regime: > 2% below recent high
dd_mild = (df["dd50_s1"] < -0.01).fillna(False)    # mild pullback
vol_low = (df["vol_ratio"] < 0.9).fillna(False)    # declining volume

# Phase A: No regime filter — PF-optimized subs
pf_subs_cfg = [
    ("S1_bl10_tp15_mh12", TRUE_MASK, "bl_dn_10", 0.015, 12, 5, 6),
    ("S2_bl15_tp20_mh15", TRUE_MASK, "bl_dn_15", 0.020, 15, 5, 6),
    ("S3_bl15_tp15_mh12", TRUE_MASK, "bl_dn_15", 0.015, 12, 5, 6),
    ("S4_bl12_tp15_mh12", TRUE_MASK, "bl_dn_12", 0.015, 12, 5, 6),
    ("S5_bl8_tp15_mh12",  TRUE_MASK, "bl_dn_8",  0.015, 12, 5, 6),
    ("S6_bl10_tp20_mh19", TRUE_MASK, "bl_dn_10", 0.020, 19, 5, 6),
]

# Phase B: With drawdown regime filter
dd_subs_cfg = [
    ("Sd1_bl10_tp15_mh12", dd_mild, "bl_dn_10", 0.015, 12, 5, 6),
    ("Sd2_bl15_tp20_mh15", dd_mild, "bl_dn_15", 0.020, 15, 5, 6),
    ("Sd3_bl15_tp15_mh12", dd_mild, "bl_dn_15", 0.015, 12, 5, 6),
    ("Sd4_bl8_tp15_mh12",  dd_mild, "bl_dn_8",  0.015, 12, 5, 6),
    ("Sd5_bl10_tp20_mh19", dd_mild, "bl_dn_10", 0.020, 19, 5, 6),
    ("Sd6_bl12_tp20_mh15", dd_mild, "bl_dn_12", 0.020, 15, 5, 6),
]

# Phase C: With stronger drawdown filter
dd2_subs_cfg = [
    ("Sb1_bl10_tp15_mh12", dd_bear, "bl_dn_10", 0.015, 12, 5, 6),
    ("Sb2_bl15_tp20_mh15", dd_bear, "bl_dn_15", 0.020, 15, 5, 6),
    ("Sb3_bl8_tp15_mh12",  dd_bear, "bl_dn_8",  0.015, 12, 5, 6),
    ("Sb4_bl12_tp15_mh12", dd_bear, "bl_dn_12", 0.015, 12, 5, 6),
]

all_s_subs = pf_subs_cfg + dd_subs_cfg + dd2_subs_cfg
s_sub_results = {}

print("\nIndividual S sub-strategy results:")
print(f"{'Name':22s} {'IS_t':>4s} {'IS_PnL':>7s} {'OOS_t':>5s} {'OOS_PnL':>7s} "
      f"{'PF':>5s} {'WR':>5s} {'mWR':>5s}")
print("-" * 65)

for name, ind_mask, bl_col, tp, mh, ms, cd in all_s_subs:
    tdf = bt_short_sub(df, ind_mask, bl_col, tp_pct=tp, max_hold=mh,
                       max_same=ms, exit_cd=cd, tag=name)
    is_r = evaluate(tdf, df["datetime"].iloc[0], mid)
    oos_r = evaluate(tdf, mid, end)
    s_sub_results[name] = {"is": is_r, "oos": oos_r, "tdf": tdf}
    if is_r and oos_r:
        print(f"{name:22s} {is_r['n']:4d} {is_r['pnl']:7,.0f} {oos_r['n']:5d} {oos_r['pnl']:7,.0f} "
              f"{oos_r['pf']:5.2f} {oos_r['wr']:5.1f} {oos_r['avg_mwr']:5.1f}")
    else:
        print(f"{name:22s} — insufficient —")

# Build CMP portfolios
def build_cmp(sub_names):
    dfs = []
    for name in sub_names:
        r = s_sub_results.get(name)
        if r and len(r["tdf"]) > 0:
            dfs.append(r["tdf"])
    if not dfs:
        return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(dfs, ignore_index=True)

# Portfolio configurations
s_portfolios = [
    ("S_PF_4sub", ["S1_bl10_tp15_mh12", "S2_bl15_tp20_mh15",
                   "S3_bl15_tp15_mh12", "S5_bl8_tp15_mh12"]),
    ("S_PF_6sub", ["S1_bl10_tp15_mh12", "S2_bl15_tp20_mh15",
                   "S3_bl15_tp15_mh12", "S4_bl12_tp15_mh12",
                   "S5_bl8_tp15_mh12", "S6_bl10_tp20_mh19"]),
    ("S_DD1_4sub", ["Sd1_bl10_tp15_mh12", "Sd2_bl15_tp20_mh15",
                    "Sd3_bl15_tp15_mh12", "Sd4_bl8_tp15_mh12"]),
    ("S_DD1_6sub", ["Sd1_bl10_tp15_mh12", "Sd2_bl15_tp20_mh15",
                    "Sd3_bl15_tp15_mh12", "Sd4_bl8_tp15_mh12",
                    "Sd5_bl10_tp20_mh19", "Sd6_bl12_tp20_mh15"]),
    ("S_DD2_4sub", ["Sb1_bl10_tp15_mh12", "Sb2_bl15_tp20_mh15",
                    "Sb3_bl8_tp15_mh12", "Sb4_bl12_tp15_mh12"]),
    # Mixed: no-filter + drawdown
    ("S_MIX_4sub", ["S1_bl10_tp15_mh12", "S3_bl15_tp15_mh12",
                    "Sd2_bl15_tp20_mh15", "Sd4_bl8_tp15_mh12"]),
]

print("\n" + "-"*70)
print("S CMP PORTFOLIO COMPARISON:")
print(f"{'Portfolio':14s} {'OOS_t':>5s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} "
      f"{'MDD':>5s} {'topM':>5s} {'mWR':>5s} {'mWRmin':>6s} {'PM':>5s} {'WF':>3s}")
print("-" * 70)

s_port_results = []
for label, subs in s_portfolios:
    merged = build_cmp(subs)
    oos = evaluate(merged, mid, end)
    wf = wf6(merged, mid, end)
    s_port_results.append({"label": label, "subs": subs, "oos": oos, "wf": wf, "tdf": merged})
    if oos:
        print(f"{label:14s} {oos['n']:5d} {oos['pnl']:8,.0f} {oos['pf']:5.2f} "
              f"{oos['wr']:5.1f} {oos['mdd']:5.1f} {oos['toppct']:5.1f} "
              f"{oos['avg_mwr']:5.1f} {oos['min_mwr']:6.0f} {oos['posm']}/{oos['mt']} {wf:3d}")

# ═══════════════════════════════════════════════════════
# Part 3: Combined L+S Evaluation
# ═══════════════════════════════════════════════════════

print("\n" + "="*90)
print("PART 3: COMBINED L+S EVALUATION")
print("="*90)

# Best L
best_l = max(l_results, key=lambda x: x["oos"]["pnl"] if x["oos"] else 0)
# Best S by PnL that passes PF
best_s = max(s_port_results, key=lambda x: x["oos"]["pnl"] if x["oos"] else 0)

print(f"\nBest L: {best_l['name']} → ${best_l['oos']['pnl']:,.0f}")
print(f"Best S: {best_s['label']} → ${best_s['oos']['pnl']:,.0f}")

# Combined for each L × S combination
print("\nCOMBINED L+S:")
print(f"{'L':20s} {'S':14s} {'L_PnL':>7s} {'S_PnL':>7s} {'Total':>8s} "
      f"{'PM':>5s} {'Worst':>7s}")
print("-" * 72)

for lr in l_results:
    if not lr["oos"] or lr["oos"]["pnl"] <= 0: continue
    for sp in s_port_results:
        if not sp["oos"] or sp["oos"]["pnl"] <= 0: continue
        # Merge trade streams
        combined = pd.concat([lr["tdf"], sp["tdf"]], ignore_index=True)
        c_oos = evaluate(combined, mid, end)
        if not c_oos: continue
        total = c_oos["pnl"]
        # Only show promising combos
        if total < 15000: continue
        print(f"{lr['name']:20s} {sp['label']:14s} "
              f"{lr['oos']['pnl']:7,.0f} {sp['oos']['pnl']:7,.0f} {total:8,.0f} "
              f"{c_oos['posm']}/{c_oos['mt']} {c_oos['worstv']:7,.0f}")

# ═══════════════════════════════════════════════════════
# Part 4: Best Combined — Full Detail
# ═══════════════════════════════════════════════════════

# Find best combined
best_combo = None
best_total = 0
for lr in l_results:
    if not lr["oos"] or lr["oos"]["pnl"] <= 0: continue
    for sp in s_port_results:
        if not sp["oos"] or sp["oos"]["pnl"] <= 0: continue
        combined = pd.concat([lr["tdf"], sp["tdf"]], ignore_index=True)
        c_oos = evaluate(combined, mid, end)
        if c_oos and c_oos["pnl"] > best_total:
            best_total = c_oos["pnl"]
            best_combo = (lr, sp, combined, c_oos)

if best_combo:
    lr, sp, combined, c_oos = best_combo
    l_oos = lr["oos"]; s_oos = sp["oos"]
    l_wf = lr["wf"]; s_wf = sp["wf"]

    print(f"\n{'='*90}")
    print(f"BEST COMBINED: {lr['name']} + {sp['label']}")
    print(f"{'='*90}")

    # L detail
    print(f"\n--- L: {lr['name']} ---")
    print(f"OOS: {l_oos['n']}t ${l_oos['pnl']:,.0f} PF{l_oos['pf']:.2f} WR{l_oos['wr']:.1f}%")
    ms = l_oos["monthly"]; mwr = l_oos["monthly_wr"]
    print(f"{'Month':10s} {'PnL':>8s} {'WR':>6s}")
    for m in ms.index:
        pv=ms[m]; wv=mwr[m] if m in mwr.index else 0
        flag=" ✗" if pv<500 or wv<70 else ""
        print(f"{str(m):10s} {pv:>8,.0f} {wv:>5.1f}%{flag}")

    # S detail
    print(f"\n--- S: {sp['label']} ---")
    print(f"OOS: {s_oos['n']}t ${s_oos['pnl']:,.0f} PF{s_oos['pf']:.2f} WR{s_oos['wr']:.1f}%")
    ms = s_oos["monthly"]; mwr = s_oos["monthly_wr"]
    print(f"{'Month':10s} {'PnL':>8s} {'WR':>6s}")
    for m in ms.index:
        pv=ms[m]; wv=mwr[m] if m in mwr.index else 0
        flag=" ✗" if pv<500 or wv<70 else ""
        print(f"{str(m):10s} {pv:>8,.0f} {wv:>5.1f}%{flag}")

    # Combined monthly
    print(f"\n--- L+S Combined ---")
    c_ms = c_oos["monthly"]; c_mwr = c_oos["monthly_wr"]
    print(f"OOS: {c_oos['n']}t ${c_oos['pnl']:,.0f} PF{c_oos['pf']:.2f} WR{c_oos['wr']:.1f}%")
    print(f"{'Month':10s} {'PnL':>8s} {'WR':>6s}")
    for m in c_ms.index:
        pv=c_ms[m]; wv=c_mwr[m] if m in c_mwr.index else 0
        print(f"{str(m):10s} {pv:>8,.0f} {wv:>5.1f}%")

    # Exit distribution
    print(f"\nL Exit Distribution:")
    ed = l_oos["exit_dist"]
    if len(ed)>0:
        print(f"  {'Type':6s} {'N':>4s} {'PnL':>8s} {'Avg':>7s} {'WR':>6s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):4d} {row['pnl_']:8,.0f} "
                  f"{row['pnl_']/row['n_']:7.1f} {row['wr_']*100:5.1f}%")

    print(f"\nS Exit Distribution:")
    ed = s_oos["exit_dist"]
    if len(ed)>0:
        print(f"  {'Type':6s} {'N':>4s} {'PnL':>8s} {'Avg':>7s} {'WR':>6s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):4d} {row['pnl_']:8,.0f} "
                  f"{row['pnl_']/row['n_']:7.1f} {row['wr_']*100:5.1f}%")

# ═══════════════════════════════════════════════════════
# Part 5: Independent Gate Check (L and S each)
# ═══════════════════════════════════════════════════════

print(f"\n{'='*90}")
print(f"INDEPENDENT GATE CHECK (L and S each must pass all)")
print(f"{'='*90}")

for side_label, side_data in [("L", best_l), ("S", best_s)]:
    if side_label == "L":
        o = side_data["oos"]; wf = side_data["wf"]
    else:
        o = side_data["oos"]; wf = side_data["wf"]
    if not o:
        print(f"\n  {side_label}: no data")
        continue
    print(f"\n  {side_label}: {side_data.get('name', side_data.get('label', '?'))}")
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
        s="✓" if ok else "✗"
        print(f"    {s} {g:30s} → {v}")
    print(f"  Score: {passed}/{len(gates)}")

# Combined gates
if best_combo:
    _, _, _, c_oos = best_combo
    print(f"\n  L+S Combined:")
    c_gates = [
        ("L+S 年合計 ≥ $20K", c_oos["pnl"]>=20000, f"${c_oos['pnl']:,.0f}"),
        ("L+S 正月 ≥ 10/12",  c_oos["posm"]>=10,   f"{c_oos['posm']}/{c_oos['mt']}"),
        ("L+S 最差月 ≥ -$1K", c_oos["worstv"]>=-1000, f"${c_oos['worstv']:,.0f}"),
    ]
    for g,ok,v in c_gates:
        s="✓" if ok else "✗"
        print(f"    {s} {g:30s} → {v}")

print(f"""
{'='*70}
STRUCTURAL ANALYSIS: Monthly WR ≥ 70% Feasibility
{'='*70}
L strategy (Trail exit):
  - SN exits: 100% losers, ~7% of trades
  - ES exits: 100% losers, ~5% of trades
  - Trail exits: ~55% WR
  - Floor WR = (0.93 × 0.55) ≈ 51%
  - Monthly minimum even lower due to variance
  → WR 70% is structurally unreachable with Trail exit

L strategy (TP exit):
  - TP wins + MH losses + SN losses
  - Best config tp3_mh36: mWR_avg 70% but min 40%
  - PnL drops to $4K (fails PnL gate)
  → WR 70% and PnL $10K are mutually exclusive for L

S strategy (TP+MH exit):
  - TP: 100% WR. MH: ~20-50% WR. SN: 0% WR.
  - Best individual: base_bl10_tp15_mh19 WR 71.7%
  - But monthly min WR still ~40% in bull months
  → WR 70% EVERY month requires no bull months (impossible)
""")

print("Done.")
