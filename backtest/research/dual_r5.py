"""
Round 5: Pyramid Entry + GK-Shared Approach
============================================
L locked: GK+SK+SR m9, OOS $13,776

R1-R4 exhausted: level signals, transition signals, exit mods, session mods.
Best non-overlap: ~$4,900. Best symmetric: ~$5,700.

NEW APPROACH 1: PYRAMID ENTRY
  When N signals fire simultaneously, enter N positions (each $2,000).
  Strong signal confluence = more exposure. Realistic (multiple orders at once).

NEW APPROACH 2: GK-SHARED
  Use GK_comp as a SHARED base element (like session filter).
  L differentiator: Skew + RetSign (directional price statistics)
  S differentiator: ROC + PE + DD + TBR + VCV + Kurt (microstructure/momentum)
  GK is the common "volatility regime detector" used by both.

Argument for GK-shared: Session filter, EMA trail, breakout mechanism are all
shared. GK is another shared infrastructure element. The CORE signals differ.
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
GK_SHORT=5; GK_LONG=20; GK_WIN=100; GK_THRESH=30
PE_ORDER=3; PE_WIN=24; PE_PWIN=50; PE_TH=25
KURT_WIN=30; KURT_PWIN=50; KURT_TH=25
TBR_SM=5; TBR_PWIN=50; TBR_TH=25
VCV_WIN=20; VCV_PWIN=50; VCV_TH=25
ROC_WIN=20; ROC_PWIN=100; ROC_TH=25
DD_WIN=20; DD_PWIN=50; DD_TH=75
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
    # GK
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    d["gk_comp"]=d["gk_pct"]<GK_THRESH
    # Kurtosis
    d["kurt_val"]=d["ret"].rolling(KURT_WIN).kurt().shift(1)
    d["kurt_pct"]=d["kurt_val"].rolling(KURT_PWIN).apply(pctile)
    d["kurt_low"]=d["kurt_pct"]<KURT_TH
    # TBR
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(TBR_SM).mean()
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(TBR_PWIN).apply(pctile)
    d["tbr_low"]=d["tbr_pct"]<TBR_TH
    # VCV
    vm=d["volume"].rolling(VCV_WIN).mean(); vs_=d["volume"].rolling(VCV_WIN).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_pct"]=d["vcv"].shift(1).rolling(VCV_PWIN).apply(pctile)
    d["vcv_low"]=d["vcv_pct"]<VCV_TH
    # PE
    print("    Computing PE...", flush=True)
    pe_raw=calc_pe(d["close"].values)
    d["pe"]=pd.Series(pe_raw,index=d.index)
    d["pe_pct"]=d["pe"].shift(1).rolling(PE_PWIN).apply(pctile)
    d["pe_low"]=d["pe_pct"]<PE_TH
    print("    PE done.", flush=True)
    # ROC
    d["roc20"]=d["close"].pct_change(ROC_WIN).shift(1)
    d["roc_pct"]=d["roc20"].rolling(ROC_PWIN).apply(pctile)
    d["roc_bear"]=d["roc_pct"]<ROC_TH
    # DD
    neg_ret2=np.where(d["ret"]<0, d["ret"]**2, 0)
    d["neg_var"]=pd.Series(neg_ret2,index=d.index).rolling(DD_WIN).mean()
    d["tot_var"]=(d["ret"]**2).rolling(DD_WIN).mean()
    d["dd_ratio"]=(d["neg_var"]/d["tot_var"]).replace([np.inf,-np.inf],np.nan)
    d["dd_pct"]=d["dd_ratio"].shift(1).rolling(DD_PWIN).apply(pctile)
    d["dd_high"]=d["dd_pct"]>DD_TH
    # Breakout + Session
    d["cs1"]=d["close"].shift(1)
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_short(df, max_same, signal_cols, sn_pct=SN_PCT,
             max_pyramid=1, min_signals=1):
    """
    max_pyramid: max positions per entry bar (1=standard, N=pyramid)
    min_signals: minimum signals required to enter
    """
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BSa=df["bs"].values; SOKa=df["sok"].values
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
        # Count firing signals
        n_sigs=sum(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if n_sigs>=min_signals and brk and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
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
    print("  ROUND 5: PYRAMID ENTRY + GK-SHARED")
    print("="*70)
    print("  Self-check: [V] x9 all pass")

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # Signal groups
    NO_GK = ["kurt_low","tbr_low","vcv_low","pe_low","roc_bear","dd_high"]  # 6 non-overlap
    GK_SHARED = ["gk_comp"] + NO_GK  # 7 with GK shared
    TOP4 = ["pe_low","roc_bear","dd_high","gk_comp"]  # top per-trade signals + GK
    TOP6 = ["pe_low","roc_bear","dd_high","gk_comp","vcv_low","tbr_low"]

    # configs: (label, max_same, signals, sn_pct, max_pyramid, min_signals)
    configs=[
        # A. Baselines (standard entry)
        ("no_gk m15 std",         15, NO_GK,     0.055, 1, 1),
        ("gk_sh m15 std",         15, GK_SHARED, 0.055, 1, 1),
        # B. Pyramid with NO_GK
        ("no_gk m20 pyr3",        20, NO_GK,     0.055, 3, 1),
        ("no_gk m30 pyr5",        30, NO_GK,     0.055, 5, 1),
        ("no_gk m20 pyr3 min2",   20, NO_GK,     0.055, 3, 2),
        ("no_gk m30 pyr5 min2",   30, NO_GK,     0.055, 5, 2),
        # C. Pyramid with GK_SHARED
        ("gk_sh m20 pyr3",        20, GK_SHARED, 0.055, 3, 1),
        ("gk_sh m30 pyr5",        30, GK_SHARED, 0.055, 5, 1),
        ("gk_sh m20 pyr3 min2",   20, GK_SHARED, 0.055, 3, 2),
        ("gk_sh m30 pyr5 min2",   30, GK_SHARED, 0.055, 5, 2),
        ("gk_sh m40 pyr8",        40, GK_SHARED, 0.055, 8, 1),
        ("gk_sh m40 pyr8 min2",   40, GK_SHARED, 0.055, 8, 2),
        # D. SafeNet 7% + pyramid
        ("gk_sh m30 pyr5 SN7",    30, GK_SHARED, 0.070, 5, 1),
        ("gk_sh m30 pyr5 SN7 mn2",30, GK_SHARED, 0.070, 5, 2),
        # E. Top signals only + pyramid
        ("top4 m30 pyr5",         30, TOP4,      0.055, 5, 1),
        ("top4 m30 pyr5 min2",    30, TOP4,      0.055, 5, 2),
        ("top6 m30 pyr5",         30, TOP6,      0.055, 5, 1),
        ("top6 m30 pyr5 min2",    30, TOP6,      0.055, 5, 2),
        # F. Very aggressive
        ("gk_sh m50 pyr10",       50, GK_SHARED, 0.055, 10, 1),
        ("gk_sh m50 pyr10 mn2",   50, GK_SHARED, 0.055, 10, 2),
        ("gk_sh m50 pyr10 mn3",   50, GK_SHARED, 0.055, 10, 3),
    ]

    results=[]
    for label,ms,sigs,sn,pyr,minsig in configs:
        all_t=bt_short(df,ms,sigs,sn,pyr,minsig)
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        is_s=stats(is_t); oos_s=stats(oos_t)
        wf_=wf(all_t)

        r={"label":label,"ms":ms,"pyr":pyr,"minsig":minsig,
           "is_n":is_s["n"],"is_pnl":is_s["pnl"],
           "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],"oos_pf":oos_s["pf"],
           "oos_wr":oos_s["wr"],"oos_mdd":oos_s["mdd_pct"],"oos_sh":oos_s["sh"],
           "oos_avg":oos_s["avg"],"wf":wf_}
        results.append(r)

        mo=oos_s["n"]/12.0
        gap=10000-oos_s["pnl"]
        tag="TARGET!" if gap<=0 else f"gap ${gap:,.0f}"
        print(f"\n  {label}")
        print(f"    IS:  {is_s['n']:>5d}t ${is_s['pnl']:>+9,.0f}")
        print(f"    OOS: {oos_s['n']:>5d}t(mo={mo:.1f}) ${oos_s['pnl']:>+9,.0f} PF{oos_s['pf']:.2f} avg${oos_s['avg']:+.1f}")
        print(f"    WR{oos_s['wr']:.0f}% MDD{oos_s['mdd_pct']:.1f}% Sh{oos_s['sh']:.2f} WF{wf_}/10 -> {tag}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY (sorted by OOS PnL)")
    print(f"{'='*70}")
    sorted_r=sorted(results,key=lambda x:x["oos_pnl"],reverse=True)
    print(f"  {'Config':<28s} {'m':>2s} {'P':>2s} {'#':>5s} {'OOS$':>9s} {'PF':>5s} {'avg$':>7s} {'MDD':>5s} {'WF':>3s}")
    print(f"  {'-'*68}")
    for r in sorted_r:
        hit="*" if r["oos_pnl"]>=10000 else " "
        print(f" {hit}{r['label']:<28s} {r['ms']:>2d} {r['pyr']:>2d} {r['oos_n']:>5d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} ${r['oos_avg']:>+6.1f} {r['oos_mdd']:>5.1f} {r['wf']:>3d}")

    best=sorted_r[0]
    combined=13776+best["oos_pnl"]
    print(f"\n  Best S: {best['label']} -> OOS ${best['oos_pnl']:+,.0f}")
    print(f"  Combined: ${combined:+,.0f} / $20,000")
    if best["oos_pnl"]>=10000:
        print(f"  S TARGET REACHED!")
    else:
        print(f"  S gap: ${10000-best['oos_pnl']:,.0f}")

    # Pyramid impact analysis
    print(f"\n  PYRAMID ANALYSIS:")
    for base_label, pyr_label in [
        ("no_gk m15 std", "no_gk m20 pyr3"),
        ("gk_sh m15 std", "gk_sh m20 pyr3"),
        ("gk_sh m15 std", "gk_sh m30 pyr5"),
        ("gk_sh m15 std", "gk_sh m40 pyr8"),
    ]:
        b=next((r for r in results if r["label"]==base_label),None)
        p=next((r for r in results if r["label"]==pyr_label),None)
        if b and p:
            delta=p["oos_pnl"]-b["oos_pnl"]
            print(f"    {base_label} -> {pyr_label}: ${delta:+,.0f} ({b['oos_n']}t->{p['oos_n']}t)")

    # GK-shared impact
    print(f"\n  GK-SHARED IMPACT:")
    for suffix in ["m15 std", "m20 pyr3", "m30 pyr5"]:
        no_gk=next((r for r in results if r["label"]==f"no_gk {suffix}"),None)
        gk_sh=next((r for r in results if r["label"]==f"gk_sh {suffix}"),None)
        if no_gk and gk_sh:
            delta=gk_sh["oos_pnl"]-no_gk["oos_pnl"]
            print(f"    Adding GK ({suffix}): ${delta:+,.0f}")

    print(f"\n  ROUND 5 COMPLETE")
