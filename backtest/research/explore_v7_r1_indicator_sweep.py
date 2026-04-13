"""
v7-R1: Novel Indicator Discovery Sweep
========================================
Hypothesis:
  L: Sample Entropy (info complexity) and Taker Buy Ratio (order flow)
     can identify compression+direction signals independent from v6's
     GK/Ami/Skew/RetSign indicators.
  S: Realized Bipower Variation ratio (jump-robust vol) and Taker Sell
     dominance can identify short opportunities different from v6 GK.

Test: 6 novel indicators individually, each combined with standard
breakout + session filter. Compare vs baseline (pure breakout).

Novel indicators:
  1. SampEn  — Sample Entropy (information complexity of returns)
  2. PE      — Permutation Entropy (ordinal pattern regularity)
  3. TBR     — Taker Buy Ratio = tbv/volume (order flow direction)
  4. RBV     — Realized Bipower Variation / RV (jump detection)
  5. VE      — Volume Entropy (volume distribution complexity)
  6. Kurt    — Excess Kurtosis (tail thickness of returns)

Anti-Lookahead: All indicators .shift(1), breakout .shift(1), entry O[i+1]
SafeNet: 4.5% (200U x 20x constraint)
"""

import sys, io, warnings
import numpy as np, pandas as pd
from itertools import permutations as iterperms

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ─── Constants (DO NOT CHANGE) ───
NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25          # v7: 4.5% SafeNet
EMA_SPAN = 20; MIN_TRAIL = 7
EARLY_STOP_PCT = 0.01; EARLY_STOP_END = 12
BRK_LOOK = 10
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
PCTILE_WIN = 100; WARMUP = 200; MAX_PER_BAR = 2
S_TP = 0.02; S_MAX_HOLD = 15

# ─── Data ───
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
print(f"Loaded {N} bars: {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")

mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)
print(f"IS/OOS split: {mid.date()}")

# ─── Standard Indicators ───
df["ema20"] = df["close"].ewm(span=EMA_SPAN, adjust=False).mean()

cs1 = df["close"].shift(1)
brk_max = df["close"].shift(2).rolling(BRK_LOOK - 1).max()
brk_min = df["close"].shift(2).rolling(BRK_LOOK - 1).min()
df["bl_up"] = cs1 > brk_max
df["bl_dn"] = cs1 < brk_min

df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.weekday
df["sok"]  = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"]   = df["datetime"].dt.to_period("M")

# ─── Novel Indicator 1: Sample Entropy ───
def sample_entropy(x, m=2, r_factor=0.2):
    n = len(x); r = r_factor * np.std(x)
    if r == 0 or n < m + 2: return np.nan
    tm = np.array([x[j:j+m] for j in range(n - m)])
    d0 = tm[:, None, :] - tm[None, :, :]
    B = np.sum(np.max(np.abs(d0), axis=2) <= r) - len(tm)
    tm1 = np.array([x[j:j+m+1] for j in range(n - m - 1)])
    if len(tm1) < 2: return np.nan
    d1 = tm1[:, None, :] - tm1[None, :, :]
    A = np.sum(np.max(np.abs(d1), axis=2) <= r) - len(tm1)
    if B == 0 or A == 0: return np.nan
    return -np.log(A / B)

print("Computing SampEn...", end=" ", flush=True)
se = np.full(N, np.nan)
for i in range(20, N):
    x = df["ret"].iloc[i-20:i].values
    if not np.any(np.isnan(x)): se[i] = sample_entropy(x)
df["sampen"] = se
print("done")

# ─── Novel Indicator 2: Permutation Entropy ───
def perm_entropy(x, m=3):
    n = len(x)
    if n < m: return np.nan
    pl = list(iterperms(range(m)))
    ct = {p: 0 for p in pl}
    for j in range(n - m + 1):
        p = tuple(np.argsort(x[j:j+m]).tolist())
        if p in ct: ct[p] += 1
    total = sum(ct.values())
    if total == 0: return np.nan
    pr = np.array([c/total for c in ct.values() if c > 0])
    return -np.sum(pr * np.log2(pr)) / np.log2(len(pl))

print("Computing PE...", end=" ", flush=True)
pe = np.full(N, np.nan)
for i in range(20, N):
    x = df["ret"].iloc[i-20:i].values
    if not np.any(np.isnan(x)): pe[i] = perm_entropy(x)
df["pe"] = pe
print("done")

# ─── Novel Indicator 3: Taker Buy Ratio ───
df["tbr"] = (df["tbv"] / df["volume"]).rolling(5).mean()

# ─── Novel Indicator 4: Realized Bipower Variation / RV ───
ar = df["ret"].abs()
rbv = (np.pi / 2) * (ar * ar.shift(1)).rolling(20).sum()
rv  = (df["ret"] ** 2).rolling(20).sum()
df["rbv_ratio"] = (rbv / rv).replace([np.inf, -np.inf], np.nan)

# ─── Novel Indicator 5: Volume Entropy ───
def vol_entropy(x):
    x = x[x > 0]
    if len(x) < 2: return np.nan
    p = x / x.sum()
    return -np.sum(p * np.log(p + 1e-15)) / np.log(len(x))
df["ve"] = df["volume"].rolling(20).apply(vol_entropy, raw=True)

# ─── Novel Indicator 6: Excess Kurtosis ───
df["kurt"] = df["ret"].rolling(20).apply(
    lambda x: pd.Series(x).kurtosis(), raw=True)

# ─── Percentile (shift(1) + rolling(100)) ───
def pctile_func(x):
    if x.max() == x.min(): return 50.0
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100.0

IND_COLS = ["sampen", "pe", "tbr", "rbv_ratio", "ve", "kurt"]
print("Percentiling indicators:")
for col in IND_COLS:
    df[f"{col}_pct"] = df[col].shift(1).rolling(PCTILE_WIN).apply(pctile_func)
    v = df[f"{col}_pct"].notna().sum()
    mn = df[f"{col}_pct"].mean()
    print(f"  {col:12s}: {v:5d} valid, mean={mn:.1f}")

# ─── Helper ───
def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════════
# Backtest Engines
# ═══════════════════════════════════════════════════════════

def bt_long(df, entry_mask, max_same=5, exit_cd=8, cap=15):
    """Long: SafeNet 4.5% → EarlyStop(7-12,>1%) → EMA20 Trail(min7)"""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values; YM=df["ym"].values
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
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt}); lx=i; done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_short(df, entry_mask, max_same=5, exit_cd=8, cap=15):
    """Short: SafeNet 4.5% → TP 2% → MaxHold 15"""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}

    for i in range(WARMUP, n-1):
        h=H[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1+SAFENET_PCT)
            if h>=sn:
                ep=sn+(h-sn)*SN_PEN
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            if not done:
                tp=p["e"]*(1-S_TP)
                if Lo[i]<=tp:
                    pnl=(p["e"]-tp)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt}); lx=i; done=True
            if not done and bh>=S_MAX_HOLD:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt}); lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

# ═══════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════

def evaluate(tdf, start_dt, end_dt, label=""):
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

    # Monthly WR
    p["win"]=(p["pnl"]>0).astype(int)
    mwr=p.groupby("m").apply(lambda g: g["win"].mean()*100)
    min_mwr=mwr.min() if len(mwr)>0 else 0
    avg_mwr=mwr.mean() if len(mwr)>0 else 0
    wr70=(mwr>=70).sum()

    # Monthly PnL >= $500 check
    min_mpnl=ms.min() if len(ms)>0 else 0
    pnl500=(ms>=500).sum()

    # Exit type breakdown
    if "t" in p.columns:
        exit_dist=p.groupby("t").agg(n_=("pnl","count"),pnl_=("pnl","sum"),
                                      wr_=("win","mean")).reset_index()
    else:
        exit_dist=pd.DataFrame()

    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "tpm":tpm,"months":mt,"posm":posm,"toppct":toppct,"topv":topv,
            "nb":nb,"worstv":worstv,"min_mwr":min_mwr,"avg_mwr":avg_mwr,
            "wr70":wr70,"min_mpnl":min_mpnl,"pnl500":pnl500,
            "monthly":ms,"monthly_wr":mwr,"exit_dist":exit_dist,
            "avg":pnl/n if n else 0}

def walk_forward_6(tdf, s, e):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    r=[]
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2); te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        r.append(fp>0)
    return sum(r)

# ═══════════════════════════════════════════════════════════
# Configuration Sweep
# ═══════════════════════════════════════════════════════════

print("\n" + "="*90)
print("v7-R1: NOVEL INDICATOR DISCOVERY SWEEP")
print("="*90)
print(f"SafeNet={SAFENET_PCT*100:.1f}% | NOTIONAL=${NOTIONAL:,} | FEE=${FEE} | BRK={BRK_LOOK}")
print(f"L exit: SN→EarlyStop→EMA20Trail | S exit: SN→TP{S_TP*100:.0f}%→MH{S_MAX_HOLD}")
print()

configs = [
    # ── L configs ──
    ("L_base",     "L", df["bl_up"].fillna(False)),
    ("L_sampen20", "L", df["bl_up"].fillna(False) & (df["sampen_pct"] < 20)),
    ("L_pe20",     "L", df["bl_up"].fillna(False) & (df["pe_pct"] < 20)),
    ("L_tbr70",    "L", df["bl_up"].fillna(False) & (df["tbr_pct"] > 70)),
    ("L_rbv20",    "L", df["bl_up"].fillna(False) & (df["rbv_ratio_pct"] < 20)),
    ("L_ve20",     "L", df["bl_up"].fillna(False) & (df["ve_pct"] < 20)),
    ("L_kurt80",   "L", df["bl_up"].fillna(False) & (df["kurt_pct"] > 80)),
    # ── S configs ──
    ("S_base",     "S", df["bl_dn"].fillna(False)),
    ("S_sampen20", "S", df["bl_dn"].fillna(False) & (df["sampen_pct"] < 20)),
    ("S_pe20",     "S", df["bl_dn"].fillna(False) & (df["pe_pct"] < 20)),
    ("S_tbr30",    "S", df["bl_dn"].fillna(False) & (df["tbr_pct"] < 30)),
    ("S_rbv20",    "S", df["bl_dn"].fillna(False) & (df["rbv_ratio_pct"] < 20)),
    ("S_ve20",     "S", df["bl_dn"].fillna(False) & (df["ve_pct"] < 20)),
    ("S_kurt80",   "S", df["bl_dn"].fillna(False) & (df["kurt_pct"] > 80)),
]

results = []
for name, side, mask in configs:
    mask = mask.fillna(False)
    sig_rate = mask.iloc[WARMUP:].mean() * 100
    if side == "L":
        tdf = bt_long(df, mask)
    else:
        tdf = bt_short(df, mask)

    is_r = evaluate(tdf, df["datetime"].iloc[0], mid, f"{name}_IS")
    oos_r = evaluate(tdf, mid, end, f"{name}_OOS")
    wf = walk_forward_6(tdf, mid, end)

    results.append({"name": name, "side": side, "is": is_r, "oos": oos_r,
                     "wf": wf, "tdf": tdf, "sig": sig_rate})

    # One-line summary
    if is_r and oos_r:
        print(f"  {name:12s} sig{sig_rate:4.1f}% | "
              f"IS:{is_r['n']:3d}t ${is_r['pnl']:>7,.0f} PF{is_r['pf']:5.2f} WR{is_r['wr']:5.1f}% | "
              f"OOS:{oos_r['n']:3d}t ${oos_r['pnl']:>7,.0f} PF{oos_r['pf']:5.2f} "
              f"WR{oos_r['wr']:5.1f}% MDD{oos_r['mdd']:5.1f}% | WF{wf}/6")
    elif is_r:
        print(f"  {name:12s} sig{sig_rate:4.1f}% | "
              f"IS:{is_r['n']:3d}t ${is_r['pnl']:>7,.0f} | OOS: 0 trades")
    else:
        print(f"  {name:12s} sig{sig_rate:4.1f}% | IS: 0 trades | OOS: 0 trades")

# ═══════════════════════════════════════════════════════════
# Summary Comparison Table
# ═══════════════════════════════════════════════════════════

print("\n" + "="*90)
print("SUMMARY COMPARISON TABLE")
print("="*90)
hdr = (f"{'Config':12s} {'Side':4s} {'Sig%':>5s} {'IS_t':>4s} {'IS_PnL':>8s} "
       f"{'OOS_t':>5s} {'OOS_PnL':>8s} {'PF':>5s} {'WR%':>5s} {'MDD%':>5s} "
       f"{'topM%':>5s} {'mWR':>5s} {'PM':>5s} {'WF':>3s}")
print(hdr)
print("-" * len(hdr))

for r in results:
    o = r["oos"]; i = r["is"]
    if o and i:
        print(f"{r['name']:12s} {r['side']:4s} {r['sig']:5.1f} "
              f"{i['n']:4d} {i['pnl']:8,.0f} "
              f"{o['n']:5d} {o['pnl']:8,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
              f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['avg_mwr']:5.1f} "
              f"{o['posm']}/{o['months']} {r['wf']:3d}")
    else:
        print(f"{r['name']:12s} — insufficient trades —")

# ═══════════════════════════════════════════════════════════
# Indicator Edge vs Baseline
# ═══════════════════════════════════════════════════════════

print("\n" + "="*90)
print("INDICATOR EDGE vs BASELINE")
print("="*90)

lb = next((r for r in results if r["name"]=="L_base"), None)
sb = next((r for r in results if r["name"]=="S_base"), None)
lb_pnl = lb["oos"]["pnl"] if lb and lb["oos"] else 0
sb_pnl = sb["oos"]["pnl"] if sb and sb["oos"] else 0
lb_wr  = lb["oos"]["wr"]  if lb and lb["oos"] else 0
sb_wr  = sb["oos"]["wr"]  if sb and sb["oos"] else 0

print(f"\nL baseline: OOS ${lb_pnl:,.0f}, WR {lb_wr:.1f}%")
for r in results:
    if r["side"]=="L" and r["name"]!="L_base" and r["oos"]:
        o=r["oos"]
        dp=o["pnl"]-lb_pnl; dw=o["wr"]-lb_wr
        avg=o["avg"]
        print(f"  {r['name']:12s}: ${o['pnl']:>7,.0f} ({dp:>+7,.0f}) "
              f"WR{o['wr']:5.1f}% ({dw:>+5.1f}) avg=${avg:>6.1f} "
              f"mWR_min={o['min_mwr']:.0f}% mPnL_min=${o['min_mpnl']:,.0f}")

print(f"\nS baseline: OOS ${sb_pnl:,.0f}, WR {sb_wr:.1f}%")
for r in results:
    if r["side"]=="S" and r["name"]!="S_base" and r["oos"]:
        o=r["oos"]
        dp=o["pnl"]-sb_pnl; dw=o["wr"]-sb_wr
        avg=o["avg"]
        print(f"  {r['name']:12s}: ${o['pnl']:>7,.0f} ({dp:>+7,.0f}) "
              f"WR{o['wr']:5.1f}% ({dw:>+5.1f}) avg=${avg:>6.1f} "
              f"mWR_min={o['min_mwr']:.0f}% mPnL_min=${o['min_mpnl']:,.0f}")

# ═══════════════════════════════════════════════════════════
# Top configs: Monthly + Exit breakdown
# ═══════════════════════════════════════════════════════════

for side_label in ["L", "S"]:
    side_r = [r for r in results if r["side"]==side_label
              and r["oos"] and r["oos"]["pnl"]>0]
    if not side_r: continue
    side_r.sort(key=lambda x: x["oos"]["pnl"], reverse=True)

    print(f"\n{'='*70}")
    print(f"TOP {side_label} CONFIGS — Monthly + Exit Detail")
    print(f"{'='*70}")

    for r in side_r[:3]:
        o = r["oos"]
        ms = o["monthly"]; mwr = o["monthly_wr"]
        print(f"\n  {r['name']} (OOS ${o['pnl']:,.0f}, PF{o['pf']:.2f}, WR{o['wr']:.1f}%)")
        print(f"  {'Month':10s} {'PnL':>8s} {'WR':>6s}")
        for m in ms.index:
            pnl=ms[m]; wr=mwr[m] if m in mwr.index else 0
            flag=" ✗" if pnl<0 or wr<70 else ""
            print(f"  {str(m):10s} {pnl:>8,.0f} {wr:>5.1f}%{flag}")

        # Exit distribution
        ed = o["exit_dist"]
        if len(ed)>0:
            print(f"\n  Exit Distribution:")
            print(f"  {'Type':6s} {'N':>4s} {'PnL':>8s} {'Avg':>7s} {'WR':>6s}")
            for _, row in ed.iterrows():
                print(f"  {row['t']:6s} {int(row['n_']):4d} "
                      f"{row['pnl_']:8,.0f} {row['pnl_']/row['n_']:7.1f} "
                      f"{row['wr_']*100:5.1f}%")

# ═══════════════════════════════════════════════════════════
# Gate Check — Best L + Best S
# ═══════════════════════════════════════════════════════════

print("\n" + "="*90)
print("GATE CHECK")
print("="*90)

for side_label in ["L", "S"]:
    sr = [r for r in results if r["side"]==side_label
          and r["oos"] and r["oos"]["pnl"]>0]
    if not sr:
        print(f"\n  {side_label}: No positive OOS config!")
        continue
    sr.sort(key=lambda x: x["oos"]["pnl"], reverse=True)
    best = sr[0]; o = best["oos"]

    print(f"\n  {side_label} Champion: {best['name']}")
    gates = [
        ("OOS PnL ≥ $10K",         o["pnl"]>=10000,        f"${o['pnl']:,.0f}"),
        ("PF ≥ 1.5",               o["pf"]>=1.5,           f"{o['pf']:.2f}"),
        ("MDD ≤ 25%",              o["mdd"]<=25,           f"{o['mdd']:.1f}%"),
        ("TPM ≥ 10",               o["tpm"]>=10,           f"{o['tpm']:.1f}"),
        ("Monthly WR ≥ 70% all",   o["min_mwr"]>=70,       f"min={o['min_mwr']:.0f}%, {o['wr70']}/{o['months']}m"),
        ("Monthly PnL ≥ $500 all", o["min_mpnl"]>=500,     f"min=${o['min_mpnl']:,.0f}, {o['pnl500']}/{o['months']}m"),
        ("Pos months ≥ 75%",       o["posm"]/max(o["months"],1)>=0.75, f"{o['posm']}/{o['months']}"),
        ("topM ≤ 20%",             o["toppct"]<=20,        f"{o['toppct']:.1f}%"),
        ("Remove best ≥ $8K",      o["nb"]>=8000,          f"${o['nb']:,.0f}"),
        ("WF ≥ 5/6",              best["wf"]>=5,           f"{best['wf']}/6"),
    ]
    passed=sum(1 for _,ok,_ in gates if ok)
    for g,ok,v in gates:
        s="✓" if ok else "✗"
        print(f"    {s} {g:30s} → {v}")
    print(f"  Score: {passed}/{len(gates)}")

# ═══════════════════════════════════════════════════════════
# Anti-Lookahead Checklist
# ═══════════════════════════════════════════════════════════

print(f"""
{'='*50}
Anti-Lookahead 6/6 Checklist
{'='*50}
  ✓ 1. All indicators .shift(1) before percentile
  ✓ 2. Percentiles: .shift(1).rolling({PCTILE_WIN})
  ✓ 3. Entry price = O[i+1]
  ✓ 4. IS/OOS split fixed at day 365
  ✓ 5. SampEn/PE/TBR/RBV/VE/Kurt all .shift(1)
  ✓ 6. Breakout: close.shift(1) > close.shift(2).rolling().max()
  → 6/6 PASS
""")

# ═══════════════════════════════════════════════════════════
# Signal frequency analysis
# ═══════════════════════════════════════════════════════════

print("="*50)
print("SIGNAL FREQUENCY ANALYSIS (OOS period)")
print("="*50)
oos_df = df[df["datetime"] >= mid]
for col in IND_COLS:
    lo20 = (oos_df[f"{col}_pct"] < 20).mean() * 100
    hi80 = (oos_df[f"{col}_pct"] > 80).mean() * 100
    print(f"  {col:12s}: <20pct={lo20:5.1f}%  >80pct={hi80:5.1f}%")

bl_up_rate = (oos_df["bl_up"].fillna(False)).mean() * 100
bl_dn_rate = (oos_df["bl_dn"].fillna(False)).mean() * 100
sok_rate = oos_df["sok"].mean() * 100
print(f"  BL_up: {bl_up_rate:.1f}%  BL_dn: {bl_dn_rate:.1f}%  Session OK: {sok_rate:.1f}%")

print("\nDone.")
