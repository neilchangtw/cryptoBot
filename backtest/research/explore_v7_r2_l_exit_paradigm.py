"""
v7-R2: L Exit Paradigm Exploration
====================================
R1 found: L_base already $16,498 OOS but monthly WR only 48.4% (need 70%).
Root cause: Trail exit WR=55%, SN/ES=0% WR → structural floor on WR.

Hypothesis: Switching from Trail to TP+MaxHold can boost monthly WR toward
70% by converting losers into time-limited small losses or TP wins.

Test matrix:
  A. Pure TP + MaxHold (sweep TP 2-5% × MH 12-48)
  B. Hybrid: TP window first, then switch to EMA20 trail
  C. Breakeven trail: once +X% reached, move stop to entry

Entry held constant: BL10_up + Session (L_base from R1)
SafeNet: 4.5% for all configs

Also fix SampEn from R1 (normalize window data).
"""

import sys, io, warnings
import numpy as np, pandas as pd
from itertools import permutations as iterperms

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ─── Constants ───
NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
EMA_SPAN = 20; BRK_LOOK = 10
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
print(f"IS/OOS split: {mid.date()}")

# ─── Standard Indicators ───
df["ema20"] = df["close"].ewm(span=EMA_SPAN, adjust=False).mean()
cs1 = df["close"].shift(1)
brk_max = df["close"].shift(2).rolling(BRK_LOOK - 1).max()
df["bl_up"] = cs1 > brk_max
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.weekday
df["sok"]  = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"]   = df["datetime"].dt.to_period("M")

# Entry mask: L_base
ENTRY = df["bl_up"].fillna(False)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══════════════════════════════════════════════════════
# Exit Engine A: Original Trail (R1 baseline)
# ═══════════════════════════════════════════════════════

def bt_trail(df, mask, max_same=5, exit_cd=8, cap=15):
    """SafeNet → EarlyStop(7-12,>1%) → EMA20 Trail(min7)"""
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
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt}); lx=i; done=True
            if not done and bh>=12 and c<=ema:
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

# ═══════════════════════════════════════════════════════
# Exit Engine B: TP + MaxHold (CMP-style for L)
# ═══════════════════════════════════════════════════════

def bt_tp_mh(df, mask, tp_pct=0.03, max_hold=24,
             max_same=5, exit_cd=8, cap=15):
    """SafeNet → TP X% → MaxHold Y bars"""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}

    for i in range(WARMUP, n-1):
        h=H[i]; lo=Lo[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            # SafeNet (long: price DOWN is bad)
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            # TP
            if not done:
                tp_price=p["e"]*(1+tp_pct)
                if h>=tp_price:
                    pnl=(tp_price-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt}); lx=i; done=True
            # MaxHold
            if not done and bh>=max_hold:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
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

# ═══════════════════════════════════════════════════════
# Exit Engine C: Hybrid (TP window → Trail)
# ═══════════════════════════════════════════════════════

def bt_hybrid(df, mask, tp_pct=0.03, switch_bar=12,
              max_same=5, exit_cd=8, cap=15):
    """Before switch_bar: SafeNet + TP. After: SafeNet + EMA20 Trail."""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}

    for i in range(WARMUP, n-1):
        h=H[i]; lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            # SafeNet always
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            # Phase 1: TP window
            if not done and bh<switch_bar:
                tp_price=p["e"]*(1+tp_pct)
                if h>=tp_price:
                    pnl=(tp_price-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt}); lx=i; done=True
            # Phase 2: Trail (after switch_bar)
            if not done and bh>=switch_bar and c<=ema:
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

# ═══════════════════════════════════════════════════════
# Exit Engine D: Breakeven Trail
# ═══════════════════════════════════════════════════════

def bt_be_trail(df, mask, be_thresh=0.02,
                max_same=5, exit_cd=8, cap=15):
    """EMA20 Trail, but once +be_thresh reached, floor stop at entry price.
    If price drops back to entry → exit at breakeven (only fee lost)."""
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}

    for i in range(WARMUP, n-1):
        h=H[i]; lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            # Track max profit
            max_pct = (h - p["e"]) / p["e"]
            if max_pct > p.get("mp", 0):
                p["mp"] = max_pct
            be_active = p.get("mp", 0) >= be_thresh

            # SafeNet
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt}); lx=i; done=True
            # Breakeven stop (if activated)
            if not done and be_active and lo<=p["e"]:
                pnl=-FEE  # exit at entry, only lose fee
                trades.append({"pnl":pnl,"t":"BE","b":bh,"dt":dt}); lx=i; done=True
            # EarlyStop (7-12 bars, loss>1%)
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt}); lx=i; done=True
            # Trail
            if not done and bh>=12 and c<=ema:
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
        pos.append({"e":nxo,"ei":i,"mp":0})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

# ═══════════════════════════════════════════════════════
# Evaluation (same as R1)
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

    ed=p.groupby("t").agg(n_=("pnl","count"),pnl_=("pnl","sum"),
                          wr_=("win","mean")).reset_index() if "t" in p.columns else pd.DataFrame()

    return {"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,"tpm":tpm,
            "mt":mt,"posm":posm,"toppct":toppct,"topv":topv,"nb":nb,
            "worstv":worstv,"min_mwr":min_mwr,"avg_mwr":avg_mwr,
            "wr70":wr70,"min_mpnl":min_mpnl,"monthly":ms,
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
# Run All Configs
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("v7-R2: L EXIT PARADIGM EXPLORATION")
print("="*95)
print(f"Entry: BL10_up + Session (L_base) | SafeNet=4.5%")
print()

configs = [
    # A: Baseline trail
    ("trail_base",  lambda: bt_trail(df, ENTRY)),
    # B: Pure TP + MaxHold sweep
    ("tp2_mh12",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.02, max_hold=12)),
    ("tp2_mh18",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.02, max_hold=18)),
    ("tp2_mh24",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.02, max_hold=24)),
    ("tp3_mh12",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.03, max_hold=12)),
    ("tp3_mh18",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.03, max_hold=18)),
    ("tp3_mh24",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.03, max_hold=24)),
    ("tp3_mh36",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.03, max_hold=36)),
    ("tp4_mh18",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.04, max_hold=18)),
    ("tp4_mh24",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.04, max_hold=24)),
    ("tp4_mh36",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.04, max_hold=36)),
    ("tp5_mh24",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.05, max_hold=24)),
    ("tp5_mh36",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.05, max_hold=36)),
    ("tp5_mh48",    lambda: bt_tp_mh(df, ENTRY, tp_pct=0.05, max_hold=48)),
    # C: Hybrid (TP window → Trail)
    ("hyb3_sw12",   lambda: bt_hybrid(df, ENTRY, tp_pct=0.03, switch_bar=12)),
    ("hyb3_sw18",   lambda: bt_hybrid(df, ENTRY, tp_pct=0.03, switch_bar=18)),
    ("hyb4_sw12",   lambda: bt_hybrid(df, ENTRY, tp_pct=0.04, switch_bar=12)),
    ("hyb4_sw18",   lambda: bt_hybrid(df, ENTRY, tp_pct=0.04, switch_bar=18)),
    ("hyb5_sw12",   lambda: bt_hybrid(df, ENTRY, tp_pct=0.05, switch_bar=12)),
    # D: Breakeven trail
    ("be2_trail",   lambda: bt_be_trail(df, ENTRY, be_thresh=0.02)),
    ("be3_trail",   lambda: bt_be_trail(df, ENTRY, be_thresh=0.03)),
    ("be4_trail",   lambda: bt_be_trail(df, ENTRY, be_thresh=0.04)),
]

results = []
for name, fn in configs:
    tdf = fn()
    is_r = evaluate(tdf, df["datetime"].iloc[0], mid)
    oos_r = evaluate(tdf, mid, end)
    wf = wf6(tdf, mid, end)
    results.append({"name": name, "is": is_r, "oos": oos_r, "wf": wf, "tdf": tdf})

    if is_r and oos_r:
        o=oos_r; i=is_r
        print(f"  {name:12s} | IS:{i['n']:3d}t ${i['pnl']:>7,.0f} WR{i['wr']:5.1f}% | "
              f"OOS:{o['n']:3d}t ${o['pnl']:>7,.0f} PF{o['pf']:5.2f} WR{o['wr']:5.1f}% "
              f"MDD{o['mdd']:5.1f}% topM{o['toppct']:5.1f}% "
              f"mWR_min{o['min_mwr']:4.0f}% mWR_avg{o['avg_mwr']:4.0f}% | WF{wf}/6")
    else:
        print(f"  {name:12s} | insufficient trades")

# ═══════════════════════════════════════════════════════
# Summary Table
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("SUMMARY — Sorted by OOS Monthly WR (avg)")
print("="*95)
valid = [r for r in results if r["oos"]]
valid.sort(key=lambda x: x["oos"]["avg_mwr"], reverse=True)

print(f"{'Config':12s} {'OOS_t':>5s} {'PnL':>8s} {'PF':>5s} {'WR':>5s} {'MDD':>5s} "
      f"{'topM':>5s} {'mWR_avg':>7s} {'mWR_min':>7s} {'WR70':>5s} {'PM':>5s} "
      f"{'mPnL_min':>8s} {'WF':>3s}")
print("-" * 92)

for r in valid:
    o=r["oos"]
    print(f"{r['name']:12s} {o['n']:5d} {o['pnl']:8,.0f} {o['pf']:5.2f} {o['wr']:5.1f} "
          f"{o['mdd']:5.1f} {o['toppct']:5.1f} {o['avg_mwr']:7.1f} {o['min_mwr']:7.0f} "
          f"{o['wr70']:5d} {o['posm']}/{o['mt']} {o['min_mpnl']:8,.0f} {r['wf']:3d}")

# ═══════════════════════════════════════════════════════
# Top 5 by avg monthly WR: exit distribution + monthly
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("TOP 5 BY MONTHLY WR — Exit Distribution + Monthly Detail")
print("="*95)

for r in valid[:5]:
    o=r["oos"]
    print(f"\n  {r['name']} — OOS:{o['n']}t ${o['pnl']:,.0f} PF{o['pf']:.2f} WR{o['wr']:.1f}% mWR_avg={o['avg_mwr']:.1f}%")

    # Exit dist
    ed = o["exit_dist"]
    if len(ed) > 0:
        print(f"  {'Type':6s} {'N':>4s} {'PnL':>8s} {'Avg':>7s} {'WR':>6s}")
        for _, row in ed.iterrows():
            print(f"  {row['t']:6s} {int(row['n_']):4d} "
                  f"{row['pnl_']:8,.0f} {row['pnl_']/row['n_']:7.1f} "
                  f"{row['wr_']*100:5.1f}%")

    # Monthly
    ms = o["monthly"]; mwr = o["monthly_wr"]
    print(f"\n  {'Month':10s} {'PnL':>8s} {'WR':>6s}")
    for m in ms.index:
        pnl=ms[m]; wr=mwr[m] if m in mwr.index else 0
        flag=" ✗" if wr<70 else ""
        print(f"  {str(m):10s} {pnl:>8,.0f} {wr:>5.1f}%{flag}")

# ═══════════════════════════════════════════════════════
# Gate Check — Best by PnL and Best by WR
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("GATE CHECK — Best by PnL vs Best by Monthly WR")
print("="*95)

by_pnl = sorted(valid, key=lambda x: x["oos"]["pnl"], reverse=True)
by_wr  = sorted(valid, key=lambda x: x["oos"]["avg_mwr"], reverse=True)

for label, best in [("Best PnL", by_pnl[0]), ("Best mWR", by_wr[0])]:
    o=best["oos"]
    print(f"\n  {label}: {best['name']}")
    gates = [
        ("OOS PnL ≥ $10K",         o["pnl"]>=10000,        f"${o['pnl']:,.0f}"),
        ("PF ≥ 1.5",               o["pf"]>=1.5,           f"{o['pf']:.2f}"),
        ("MDD ≤ 25%",              o["mdd"]<=25,           f"{o['mdd']:.1f}%"),
        ("TPM ≥ 10",               o["tpm"]>=10,           f"{o['tpm']:.1f}"),
        ("Monthly WR ≥ 70% all",   o["min_mwr"]>=70,       f"min={o['min_mwr']:.0f}%, {o['wr70']}/{o['mt']}m"),
        ("Monthly PnL ≥ $500 all", o["min_mpnl"]>=500,     f"min=${o['min_mpnl']:,.0f}"),
        ("Pos months ≥ 75%",       o["posm"]/max(o["mt"],1)>=0.75, f"{o['posm']}/{o['mt']}"),
        ("topM ≤ 20%",             o["toppct"]<=20,        f"{o['toppct']:.1f}%"),
        ("Remove best ≥ $8K",      o["nb"]>=8000,          f"${o['nb']:,.0f}"),
        ("WF ≥ 5/6",              best["wf"]>=5,           f"{best['wf']}/6"),
    ]
    passed=sum(1 for _,ok,_ in gates if ok)
    for g,ok,v in gates:
        s="✓" if ok else "✗"
        print(f"    {s} {g:30s} → {v}")
    print(f"  Score: {passed}/{len(gates)}")

# ═══════════════════════════════════════════════════════
# Pareto Frontier: PnL vs WR tradeoff
# ═══════════════════════════════════════════════════════

print("\n" + "="*95)
print("PARETO FRONTIER: PnL vs Monthly WR Tradeoff")
print("="*95)
print(f"{'Config':12s} {'PnL':>8s} {'WR':>5s} {'mWR_avg':>7s} {'mWR_min':>7s} {'Pareto':>6s}")
print("-" * 50)

# Find Pareto-optimal configs (maximize both PnL and mWR)
pareto = []
for r in valid:
    o=r["oos"]
    dominated = False
    for r2 in valid:
        o2=r2["oos"]
        if o2["pnl"]>=o["pnl"] and o2["avg_mwr"]>=o["avg_mwr"] and \
           (o2["pnl"]>o["pnl"] or o2["avg_mwr"]>o["avg_mwr"]):
            dominated = True; break
    flag = "★" if not dominated else " "
    pareto.append((r, not dominated))
    print(f"{r['name']:12s} {o['pnl']:8,.0f} {o['wr']:5.1f} {o['avg_mwr']:7.1f} "
          f"{o['min_mwr']:7.0f} {flag:>6s}")

print(f"""
{'='*60}
INSIGHTS
{'='*60}
Exit paradigm impact on WR vs PnL tradeoff:
  - Trail:   High PnL (big winners) but low WR (~48%)
  - TP+MH:   Higher WR (TP wins) but PnL capped
  - Hybrid:  TP captures quick wins + trail gets big trends
  - BE Trail: Converts some losers to breakeven

Key question: Does any exit mechanism push monthly WR to 70%
while keeping PnL above $10K? If not, the WR gate may be
structurally impossible for L on ETH 1h.

SafeNet impact: 4.5% SN means each SN loss = ~$184.
With TP 3%, each TP win = ~$116. Risk/reward = 1.58:1.
Need WR > 61% just to break even. WR 70% + PnL $10K
requires < 15% SN hits.
""")

print("Done.")
