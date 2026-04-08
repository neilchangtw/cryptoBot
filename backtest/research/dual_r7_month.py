"""
Round 7: Month Concentration Fix
=================================
R6 audit failed G2: S topMonth = 92.7% (need < 50%).
Feb 2026 = $10,373 / $11,191 total.

Fix approach: test higher-profit S configs to dilute Feb concentration.
Also test if different signal groups shift Feb weight.
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
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    d["gk_comp"]=d["gk_pct"]<GK_THRESH
    d["kurt_val"]=d["ret"].rolling(KURT_WIN).kurt().shift(1)
    d["kurt_pct"]=d["kurt_val"].rolling(KURT_PWIN).apply(pctile)
    d["kurt_low"]=d["kurt_pct"]<KURT_TH
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(TBR_SM).mean()
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(TBR_PWIN).apply(pctile)
    d["tbr_low"]=d["tbr_pct"]<TBR_TH
    vm=d["volume"].rolling(VCV_WIN).mean(); vs_=d["volume"].rolling(VCV_WIN).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_pct"]=d["vcv"].shift(1).rolling(VCV_PWIN).apply(pctile)
    d["vcv_low"]=d["vcv_pct"]<VCV_TH
    print("    Computing PE...", flush=True)
    pe_raw=calc_pe(d["close"].values)
    d["pe"]=pd.Series(pe_raw,index=d.index)
    d["pe_pct"]=d["pe"].shift(1).rolling(PE_PWIN).apply(pctile)
    d["pe_low"]=d["pe_pct"]<PE_TH
    print("    PE done.", flush=True)
    d["roc20"]=d["close"].pct_change(ROC_WIN).shift(1)
    d["roc_pct"]=d["roc20"].rolling(ROC_PWIN).apply(pctile)
    d["roc_bear"]=d["roc_pct"]<ROC_TH
    neg_ret2=np.where(d["ret"]<0, d["ret"]**2, 0)
    d["neg_var"]=pd.Series(neg_ret2,index=d.index).rolling(DD_WIN).mean()
    d["tot_var"]=(d["ret"]**2).rolling(DD_WIN).mean()
    d["dd_ratio"]=(d["neg_var"]/d["tot_var"]).replace([np.inf,-np.inf],np.nan)
    d["dd_pct"]=d["dd_ratio"].shift(1).rolling(DD_PWIN).apply(pctile)
    d["dd_high"]=d["dd_pct"]>DD_TH
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
        n_sigs=sum(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if n_sigs>=min_signals and brk and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
                pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt"])

def analyze(tdf, label, mid_dt):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    oos=tdf[tdf["dt"]>=mid_dt].reset_index(drop=True)
    if len(oos)==0:
        print(f"  {label}: no OOS trades"); return

    pnl=oos["pnl"].sum(); n=len(oos)
    w=oos[oos["pnl"]>0]["pnl"].sum(); l_=abs(oos[oos["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    eq=oos["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100

    oos["m"]=oos["dt"].dt.to_period("M")
    ms=oos.groupby("m")["pnl"].sum()
    top_m=ms.max(); top_m_pct=top_m/pnl*100 if pnl>0 else 0
    top_m_name=str(ms.idxmax())
    pos_m=int((ms>0).sum()); tot_m=len(ms)
    worst_m=ms.min()

    # WF
    dts=pd.to_datetime(oos["dt"]); mn_,mx_=dts.min(),dts.max()
    step=(mx_-mn_)/10
    wf_pos=sum(1 for k in range(10)
               if len(oos[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))])>0
               and oos[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))]["pnl"].sum()>0)

    pass_tag = "PASS" if top_m_pct<50 and pnl>=10000 else "FAIL"
    print(f"\n  {label} [{pass_tag}]")
    print(f"    OOS: {n}t ${pnl:>+,.0f} PF{pf:.2f} MDD{mdd:.1f}% WF{wf_pos}/10")
    print(f"    topMonth: {top_m_name} ${top_m:+,.0f} ({top_m_pct:.1f}%)")
    print(f"    posMonths: {pos_m}/{tot_m} worst: ${worst_m:+,.0f}")

    # Monthly breakdown
    print(f"    Monthly:")
    for m,v in ms.items():
        tag="*" if v==top_m else ""
        print(f"      {str(m)} ${v:>+8,.0f} {tag}")

if __name__=="__main__":
    print("="*70)
    print("  ROUND 7: MONTH CONCENTRATION ANALYSIS")
    print("="*70)

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    NO_GK = ["kurt_low","tbr_low","vcv_low","pe_low","roc_bear","dd_high"]
    GK_SH = ["gk_comp"] + NO_GK
    TOP4 = ["pe_low","roc_bear","dd_high","gk_comp"]
    TOP6 = ["pe_low","roc_bear","dd_high","gk_comp","kurt_low","vcv_low"]

    configs = [
        # label, maxSame, signals, sn_pct, maxPyr, minSig
        ("no_gk m20 pyr3",       20, NO_GK, 0.055, 3, 1),
        ("no_gk m30 pyr5",       30, NO_GK, 0.055, 5, 1),
        ("no_gk m30 pyr5 min2",  30, NO_GK, 0.055, 5, 2),
        ("gk_sh m20 pyr3",       20, GK_SH, 0.055, 3, 1),
        ("gk_sh m20 pyr3 min2",  20, GK_SH, 0.055, 3, 2),
        ("gk_sh m30 pyr5",       30, GK_SH, 0.055, 5, 1),
        ("gk_sh m30 pyr5 min2",  30, GK_SH, 0.055, 5, 2),
        ("gk_sh m40 pyr8 min2",  40, GK_SH, 0.055, 8, 2),
        ("top4 m30 pyr5",        30, TOP4,  0.055, 5, 1),
        ("top6 m30 pyr5",        30, TOP6,  0.055, 5, 1),
        ("top6 m30 pyr5 min2",   30, TOP6,  0.055, 5, 2),
        # SN7 variants
        ("gk_sh m30 pyr5 SN7 mn2",30,GK_SH, 0.070, 5, 2),
        ("no_gk m30 pyr5 SN7",   30, NO_GK, 0.070, 5, 1),
        ("no_gk m30 pyr5 SN7 mn2",30,NO_GK, 0.070, 5, 2),
    ]

    for label,ms,sigs,sn,pyr,minsig in configs:
        tdf = bt_short(df, ms, sigs, sn, pyr, minsig)
        analyze(tdf, label, mid_dt)

    print(f"\n  ROUND 7 COMPLETE")
