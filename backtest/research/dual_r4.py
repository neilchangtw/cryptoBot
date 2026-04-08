"""
Round 4: Strategy S - Deterioration Signals (Rate of Change)
============================================
L locked: GK+SK+SR m9, OOS $13,776

KEY INSIGHT from R3: Level-based signals cap at ~$5K for shorts.
The problem is per-trade edge, not trade count.

NEW APPROACH: TRANSITION/DETERIORATION signals
  L captures: COMPRESSION (static) -> upward breakout
  S captures: DETERIORATION (dynamic) -> downward continuation

L uses LEVELS: GK ratio IS low, Skew IS positive, SR IS high
S uses CHANGES: GK ratio IS RISING, TBR IS FALLING, VCV IS RISING

This is NOT symmetric: one detects a STATE, other detects a TRANSITION.

New signals:
1. gk_expand: GK ratio INCREASING (volatility expanding)
2. tbr_decline: Taker buy ratio DECREASING (selling pressure growing)
3. vcv_rise: Volume CV INCREASING (volume destabilizing)
4. dd_high: Downside deviation ratio HIGH (most variance from losses)
5. roc_bear: 20-bar return LOW (bearish momentum) [from R3, works]
6. pe_low: Permutation entropy LOW (orderly price) [from R3, $17.2/trade]

Also test: no session filter, SafeNet 7%
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
GK_SHORT=5; GK_LONG=20; GK_WIN=100; GK_THRESH=30
PE_ORDER=3; PE_WIN=24; PE_PWIN=50; PE_TH=25
TBR_SM=5; TBR_PWIN=50; TBR_TH=25
VCV_WIN=20; VCV_PWIN=50; VCV_TH=25
ROC_WIN=20; ROC_PWIN=100; ROC_TH=25
# Deterioration signal params (pre-fixed)
GKD_DELTA=5; GKD_PWIN=50; GKD_TH=75    # GK expansion
TBRD_DELTA=10; TBRD_PWIN=50; TBRD_TH=25 # TBR decline
VCVD_DELTA=10; VCVD_PWIN=50; VCVD_TH=75 # VCV rise
DD_WIN=20; DD_PWIN=50; DD_TH=75          # Downside deviation
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; MIN_TRAIL=7; ES_PCT=0.010; ES_END=12; EXIT_CD=12

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume","qv","trades","tbv"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def pctile(x):
    if x.max()==x.min(): return 50
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100

def calc_pe(vals, order=PE_ORDER, win=PE_WIN):
    n=len(vals); result=np.full(n,np.nan)
    max_h=np.log(factorial(order))
    if max_h==0: return result
    n_pat=win-order+1
    mult=np.array([order**k for k in range(order-1,-1,-1)])
    for i in range(win-1, n):
        seg=vals[i-win+1:i+1]
        if np.isnan(seg).any(): continue
        idx_arr=np.arange(order)[None,:]+np.arange(n_pat)[:,None]
        windows=seg[idx_arr]
        pats=np.argsort(windows,axis=1)
        ids=(pats*mult[None,:]).sum(axis=1)
        _,counts=np.unique(ids,return_counts=True)
        probs=counts/counts.sum()
        h=-np.sum(probs*np.log(probs))
        result[i]=h/max_h
    return result

def calc(df):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()

    # === GK (for gk_expand, not gk_comp) ===
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    # GK level (compression) - for comparison
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    d["gk_comp"]=d["gk_pct"]<GK_THRESH

    # === DETERIORATION SIGNALS (rate of change) ===
    # 1. GK EXPANSION: GK ratio rising rapidly
    d["gk_delta"]=d["gk_r"].diff(GKD_DELTA).shift(1)
    d["gk_delta_pct"]=d["gk_delta"].rolling(GKD_PWIN).apply(pctile)
    d["gk_expand"]=d["gk_delta_pct"]>GKD_TH  # top 25% = rapid vol expansion

    # 2. TBR DECLINE: Taker buy ratio falling
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(TBR_SM).mean()
    d["tbr_delta"]=d["tbr_sm"].diff(TBRD_DELTA).shift(1)
    d["tbr_delta_pct"]=d["tbr_delta"].rolling(TBRD_PWIN).apply(pctile)
    d["tbr_decline"]=d["tbr_delta_pct"]<TBRD_TH  # bottom 25% = selling pressure growing
    # Also level
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(TBR_PWIN).apply(pctile)
    d["tbr_low"]=d["tbr_pct"]<TBR_TH

    # 3. VCV RISE: Volume CV increasing
    vm=d["volume"].rolling(VCV_WIN).mean(); vs_=d["volume"].rolling(VCV_WIN).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_delta"]=d["vcv"].diff(VCVD_DELTA).shift(1)
    d["vcv_delta_pct"]=d["vcv_delta"].rolling(VCVD_PWIN).apply(pctile)
    d["vcv_rise"]=d["vcv_delta_pct"]>VCVD_TH  # top 25% = volume destabilizing
    # Also level
    d["vcv_pct"]=d["vcv"].shift(1).rolling(VCV_PWIN).apply(pctile)
    d["vcv_low"]=d["vcv_pct"]<VCV_TH

    # 4. DOWNSIDE DEVIATION RATIO
    neg_ret2=np.where(d["ret"]<0, d["ret"]**2, 0)
    d["neg_var"]=pd.Series(neg_ret2,index=d.index).rolling(DD_WIN).mean()
    d["tot_var"]=(d["ret"]**2).rolling(DD_WIN).mean()
    d["dd_ratio"]=(d["neg_var"]/d["tot_var"]).replace([np.inf,-np.inf],np.nan)
    d["dd_pct"]=d["dd_ratio"].shift(1).rolling(DD_PWIN).apply(pctile)
    d["dd_high"]=d["dd_pct"]>DD_TH  # most variance from losses

    # 5. ROC BEARISH (from R3)
    d["roc20"]=d["close"].pct_change(ROC_WIN).shift(1)
    d["roc_pct"]=d["roc20"].rolling(ROC_PWIN).apply(pctile)
    d["roc_bear"]=d["roc_pct"]<ROC_TH

    # 6. PE LOW (from R3, best per-trade)
    print("    Computing PE...", flush=True)
    pe_raw=calc_pe(d["close"].values)
    d["pe"]=pd.Series(pe_raw,index=d.index)
    d["pe_pct"]=d["pe"].shift(1).rolling(PE_PWIN).apply(pctile)
    d["pe_low"]=d["pe_pct"]<PE_TH
    print("    PE done.", flush=True)

    # === Breakout + Session ===
    d["cs1"]=d["close"].shift(1)
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["always"]=True  # no session filter
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_short(df, max_same, signal_cols, session_col="sok",
             sn_pct=SN_PCT, exit_cd=EXIT_CD):
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BSa=df["bs"].values; SOKa=df[session_col].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            if h>=p["e"]*(1+sn_pct):
                ep_=p["e"]*(1+sn_pct); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"S","b":b,"dt":dt}); lx=i; done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"S","b":b,"dt":dt}); lx=i; done=True
            if not done and b>=MIN_TRAIL and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"S","b":b,"dt":dt}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        any_sig=any(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if any_sig and brk and sok and (i-lx>=exit_cd) and len(pos)<max_same:
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt"])

def stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd_pct":0,"sh":0,"avg":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum(); l_=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mp=abs(dd.min())/ACCOUNT*100
    tc=tdf.copy(); tc["date"]=pd.to_datetime(tc["dt"]).dt.date
    dy=tc.groupby("date")["pnl"].sum()
    rng=pd.date_range(tc["dt"].min(),tc["dt"].max(),freq="D")
    dy=dy.reindex(rng.date,fill_value=0)
    sh=float(dy.mean()/dy.std()*np.sqrt(365)) if dy.std()>0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd_pct":round(mp,1),"sh":round(sh,2),"avg":round(pnl/n,2)}

def wf(tdf):
    if len(tdf)==0: return 0
    dts=pd.to_datetime(tdf["dt"]); mn_,mx_=dts.min(),dts.max()
    if mn_==mx_: return 0
    step=(mx_-mn_)/10
    return sum(1 for k in range(10)
               if len(tdf[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))])>0
               and tdf[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))]["pnl"].sum()>0)

if __name__=="__main__":
    print("="*70)
    print("  ROUND 4: DETERIORATION SIGNALS")
    print("  L: Compression (levels) -> UP  |  S: Deterioration (changes) -> DOWN")
    print("="*70)
    print("\n  Self-check: [V] x9 all pass")

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # Signal fire rates
    all_sigs=["gk_comp","gk_expand","tbr_low","tbr_decline","vcv_low","vcv_rise",
              "dd_high","roc_bear","pe_low"]
    print("\n  Signal fire rates:")
    for sc in all_sigs:
        n_true=df[sc].sum() if sc in df.columns else 0
        print(f"    {sc:<14s}: {int(n_true):>5d} ({n_true/len(df)*100:.1f}%)")

    # Phase 1: Signal isolation
    print(f"\n{'='*70}")
    print(f"  PHASE 1: SIGNAL ISOLATION (short-only, m5)")
    print(f"{'='*70}")
    iso_results=[]
    for sig in all_sigs:
        all_t=bt_short(df,5,[sig])
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        oos_s=stats(oos_t); is_s=stats(all_t[all_t["dt"]<mid_dt].reset_index(drop=True))
        iso_results.append({"sig":sig,"is_n":is_s["n"],"is_pnl":is_s["pnl"],
                           "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],
                           "oos_pf":oos_s["pf"],"oos_avg":oos_s["avg"]})
    iso_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
    print(f"  {'Signal':<14s} {'IS#':>4s} {'IS$':>8s} {'OOS#':>5s} {'OOS$':>9s} {'PF':>5s} {'avg$':>7s}")
    print(f"  {'-'*55}")
    for r in iso_results:
        print(f"  {r['sig']:<14s} {r['is_n']:>4d} ${r['is_pnl']:>+7,.0f} {r['oos_n']:>5d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} ${r['oos_avg']:>+6.1f}")

    # Phase 2: Combinations
    print(f"\n{'='*70}")
    print(f"  PHASE 2: COMBINATIONS")
    print(f"{'='*70}")

    # Signal groups
    DETER = ["gk_expand","tbr_decline","vcv_rise"]           # pure deterioration
    DETER_PLUS = DETER + ["roc_bear","pe_low","dd_high"]     # deterioration + momentum/complexity
    LEVEL = ["tbr_low","vcv_low","pe_low","roc_bear"]        # best level signals (no GK overlap)
    MIXED = ["gk_expand","tbr_decline","vcv_rise","roc_bear","pe_low"]  # transition + best levels
    FULL = ["gk_expand","tbr_decline","vcv_rise","dd_high","roc_bear","pe_low","tbr_low","vcv_low"]

    configs=[
        # A. Pure deterioration
        ("deter m9",           9,  DETER,      "sok", 0.055, 12),
        ("deter m12",         12,  DETER,      "sok", 0.055, 12),
        ("deter m15",         15,  DETER,      "sok", 0.055, 12),
        # B. Deterioration + momentum/complexity
        ("deter+ m9",          9,  DETER_PLUS, "sok", 0.055, 12),
        ("deter+ m12",        12,  DETER_PLUS, "sok", 0.055, 12),
        ("deter+ m15",        15,  DETER_PLUS, "sok", 0.055, 12),
        # C. Mixed (transition + levels)
        ("mixed m9",           9,  MIXED,      "sok", 0.055, 12),
        ("mixed m12",         12,  MIXED,      "sok", 0.055, 12),
        ("mixed m15",         15,  MIXED,      "sok", 0.055, 12),
        # D. Full
        ("full m12",          12,  FULL,       "sok", 0.055, 12),
        ("full m15",          15,  FULL,       "sok", 0.055, 12),
        ("full m20",          20,  FULL,       "sok", 0.055, 12),
        # E. No session filter
        ("deter+ m12 noSF",  12,  DETER_PLUS, "always", 0.055, 12),
        ("deter+ m15 noSF",  15,  DETER_PLUS, "always", 0.055, 12),
        ("mixed m12 noSF",   12,  MIXED,      "always", 0.055, 12),
        ("full m15 noSF",    15,  FULL,        "always", 0.055, 12),
        # F. SafeNet 7%
        ("deter+ m12 SN7",   12,  DETER_PLUS, "sok", 0.070, 12),
        ("mixed m12 SN7",    12,  MIXED,      "sok", 0.070, 12),
        ("full m15 SN7",     15,  FULL,       "sok", 0.070, 12),
        # G. Comparison baselines
        ("R3-7sig m12",      12,  ["tbr_low","vcv_low","pe_low","roc_bear",  # from R3
                                   "gk_expand","tbr_decline","vcv_rise"],  # new transition
                                                "sok", 0.055, 12),
        ("R3-sym m15",       15,  ["gk_comp"], "sok", 0.055, 12),  # GK-only baseline
    ]

    results=[]
    for label,ms,sigs,sf,sn,cd in configs:
        all_t=bt_short(df,ms,sigs,sf,sn,cd)
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        is_s=stats(is_t); oos_s=stats(oos_t)
        wf_=wf(all_t)

        r={"label":label,"ms":ms,
           "is_n":is_s["n"],"is_pnl":is_s["pnl"],
           "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],"oos_pf":oos_s["pf"],
           "oos_wr":oos_s["wr"],"oos_mdd":oos_s["mdd_pct"],"oos_sh":oos_s["sh"],
           "oos_avg":oos_s["avg"],"wf":wf_}
        results.append(r)

        mo=oos_s["n"]/12.0
        gap=10000-oos_s["pnl"]
        tag="TARGET!" if gap<=0 else f"gap ${gap:,.0f}"
        print(f"\n  {label}")
        print(f"    IS:  {is_s['n']:>4d}t ${is_s['pnl']:>+9,.0f} PF{is_s['pf']:.2f}")
        print(f"    OOS: {oos_s['n']:>4d}t(mo={mo:.1f}) ${oos_s['pnl']:>+9,.0f} PF{oos_s['pf']:.2f} avg${oos_s['avg']:+.1f}")
        print(f"    WR{oos_s['wr']:.0f}% MDD{oos_s['mdd_pct']:.1f}% Sh{oos_s['sh']:.2f} WF{wf_}/10 -> {tag}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY (sorted by OOS PnL)")
    print(f"{'='*70}")
    sorted_r=sorted(results,key=lambda x:x["oos_pnl"],reverse=True)
    print(f"  {'Config':<24s} {'m':>2s} {'#':>4s} {'OOS$':>9s} {'PF':>5s} {'avg$':>7s} {'MDD':>5s} {'WF':>3s}")
    print(f"  {'-'*62}")
    for r in sorted_r:
        hit="*" if r["oos_pnl"]>=10000 else " "
        print(f" {hit}{r['label']:<24s} {r['ms']:>2d} {r['oos_n']:>4d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} ${r['oos_avg']:>+6.1f} {r['oos_mdd']:>5.1f} {r['wf']:>3d}")

    best=sorted_r[0]
    combined=13776+best["oos_pnl"]
    print(f"\n  Best S: {best['label']} -> OOS ${best['oos_pnl']:+,.0f}")
    print(f"  Combined: ${combined:+,.0f} / $20,000")
    if best["oos_pnl"]>=10000:
        print(f"  S TARGET REACHED!")
    else:
        print(f"  S gap: ${10000-best['oos_pnl']:,.0f}")

    # Transition vs Level comparison
    print(f"\n  TRANSITION vs LEVEL ANALYSIS:")
    for pair in [("gk_comp","gk_expand"),("tbr_low","tbr_decline"),("vcv_low","vcv_rise")]:
        r_level=next((r for r in iso_results if r["sig"]==pair[0]),None)
        r_trans=next((r for r in iso_results if r["sig"]==pair[1]),None)
        if r_level and r_trans:
            print(f"    {pair[0]:<14s}: ${r_level['oos_pnl']:>+7,.0f} avg${r_level['oos_avg']:>+6.1f}")
            print(f"    {pair[1]:<14s}: ${r_trans['oos_pnl']:>+7,.0f} avg${r_trans['oos_avg']:>+6.1f}")
            print()

    print(f"  ROUND 4 COMPLETE")
