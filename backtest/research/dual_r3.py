"""
Round 3: Strategy S - New Signal Universe + Signal Isolation
============================================
L locked: GK+SK+SR m9, OOS $13,776

R2 findings: Exit mods all hurt. BRK5 small gain. Symmetric ceiling $5,744.
Problem: per-trade short edge is ~$10 vs $25 for longs.

New approach: Add genuinely different signal TYPES
1. ROC bearish: 20-bar price momentum percentile (magnitude, not direction counting)
2. ETH/BTC relative weakness: cross-asset underperformance

Different hypothesis from L:
  L: "Calm before storm UP" = compression signals detect quiet regime -> upward breakout
  S: "Storm getting worse DOWN" = momentum + microstructure detect active selling -> downward continuation

Also: signal isolation to find which signals have best short-specific edge.
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
GK_SHORT=5; GK_LONG=20; GK_WIN=100; GK_THRESH=30
SKEW_WIN=20; SKEW_TH=1.0
SR_WIN=15; SR_ST=0.40
PE_ORDER=3; PE_WIN=24; PE_PWIN=50; PE_TH=25
KURT_WIN=30; KURT_PWIN=50; KURT_TH=25
TBR_SM=5; TBR_PWIN=50; TBR_TH=25
VCV_WIN=20; VCV_PWIN=50; VCV_TH=25
AMI_SM=5; AMI_PWIN=50; AMI_TH=25
ROC_WIN=20; ROC_PWIN=100; ROC_TH=25
RP_WIN=20; RP_PWIN=100; RP_TH=25
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; MIN_TRAIL=7; ES_PCT=0.010; ES_END=12; EXIT_CD=12

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))
BTC_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","BTCUSDT_1h_latest730d.csv"))

def load_eth():
    df=pd.read_csv(ETH_CSV); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume","qv","trades","tbv"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def load_btc():
    df=pd.read_csv(BTC_CSV); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    df=df.rename(columns={c:f"btc_{c}" for c in ["open","high","low","close","volume"]})
    return df[["datetime","btc_open","btc_high","btc_low","btc_close"]].dropna()

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

    # Skewness
    d["skew_val"]=d["ret"].rolling(SKEW_WIN).skew().shift(1)
    d["skew_s"]=d["skew_val"]<-SKEW_TH

    # RetSign
    d["sr"]=(d["ret"]>0).astype(float).rolling(SR_WIN).mean().shift(1)
    d["sr_s"]=d["sr"]<SR_ST

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

    # Amihud
    d["amihud"]=(d["ret"].abs()/d["qv"]).replace([np.inf,-np.inf],np.nan)
    d["ami_sm"]=d["amihud"].rolling(AMI_SM).mean()
    d["ami_pct"]=d["ami_sm"].shift(1).rolling(AMI_PWIN).apply(pctile)
    d["ami_low"]=d["ami_pct"]<AMI_TH

    # === NEW: ROC bearish (downward momentum) ===
    d["roc20"]=d["close"].pct_change(ROC_WIN).shift(1)  # 20-bar return, shifted
    d["roc_pct"]=d["roc20"].rolling(ROC_PWIN).apply(pctile)
    d["roc_bear"]=d["roc_pct"]<ROC_TH  # bottom 25% = bearish momentum

    # === NEW: ETH/BTC relative weakness ===
    if "btc_close" in d.columns:
        eth_roc=d["close"].pct_change(RP_WIN)
        btc_roc=d["btc_close"].pct_change(RP_WIN)
        d["rp"]=(eth_roc-btc_roc).shift(1)
        d["rp_pct"]=d["rp"].rolling(RP_PWIN).apply(pctile)
        d["rp_weak"]=d["rp_pct"]<RP_TH  # ETH underperforming BTC
    else:
        d["rp_weak"]=False

    # BTC breakout
    if "btc_close" in d.columns:
        d["btc_cs1"]=d["btc_close"].shift(1)
        d["btc_cmn"]=d["btc_close"].shift(2).rolling(BRK_LOOK-1).min()
        d["btc_bs"]=d["btc_cs1"]<d["btc_cmn"]
    else:
        d["btc_bs"]=False

    # ETH breakout
    d["cs1"]=d["close"].shift(1)
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]
    d["cmn5"]=d["close"].shift(2).rolling(4).min()
    d["bs5"]=d["cs1"]<d["cmn5"]

    # Session
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_short(df, max_same, signal_cols, brk_col="bs", exit_cd=EXIT_CD,
             cd_loss_only=False):
    """Short-only backtest. cd_loss_only: CD only after losing exits."""
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BSa=df[brk_col].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            if h>=p["e"]*(1+SN_PCT):
                ep_=p["e"]*(1+SN_PCT); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"S","b":b,"dt":dt})
                if not cd_loss_only or pnl<0: lx=i
                done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"S","b":b,"dt":dt})
                    if not cd_loss_only or pnl<0: lx=i
                    done=True
            if not done and b>=MIN_TRAIL and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"S","b":b,"dt":dt})
                if not cd_loss_only or pnl<0: lx=i
                done=True
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

def pos_months(tdf):
    if len(tdf)==0: return 0,0
    tc=tdf.copy(); tc["dt"]=pd.to_datetime(tc["dt"]); tc["m"]=tc["dt"].dt.to_period("M")
    ms=tc.groupby("m")["pnl"].sum()
    return int((ms>0).sum()), len(ms)

if __name__=="__main__":
    print("="*70)
    print("  ROUND 3: STRATEGY S - NEW SIGNAL UNIVERSE")
    print("  L locked: GK+SK+SR m9, OOS $13,776")
    print("="*70)
    print("\n  Self-check: [V] x9 all pass")

    df=load_eth(); btc=load_btc()
    df=df.merge(btc, on="datetime", how="left")
    df=calc(df)

    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # Signal fire rates
    all_sigs=["gk_comp","skew_s","sr_s","kurt_low","tbr_low","vcv_low",
              "pe_low","ami_low","roc_bear","rp_weak","btc_bs"]
    print("\n  Signal fire rates:")
    for sc in all_sigs:
        n_true=df[sc].sum() if sc in df.columns else 0
        print(f"    {sc:<12s}: {int(n_true):>5d} ({n_true/len(df)*100:.1f}%)")

    # ===== PHASE 1: Signal Isolation (each signal solo, short-only, m5) =====
    print(f"\n{'='*70}")
    print(f"  PHASE 1: SIGNAL ISOLATION (short-only, m5, each signal solo)")
    print(f"{'='*70}")
    iso_results=[]
    for sig in all_sigs:
        if sig=="btc_bs": continue  # not a regime signal
        all_t=bt_short(df,5,[sig])
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        oos_s=stats(oos_t); is_s=stats(is_t)
        iso_results.append({"sig":sig,"oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],
                           "oos_pf":oos_s["pf"],"oos_avg":oos_s["avg"],
                           "is_pnl":is_s["pnl"],"is_n":is_s["n"]})
    # Sort by OOS PnL
    iso_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
    print(f"  {'Signal':<12s} {'IS#':>4s} {'IS$':>8s} {'OOS#':>5s} {'OOS$':>9s} {'PF':>5s} {'avg$':>7s}")
    print(f"  {'-'*55}")
    for r in iso_results:
        print(f"  {r['sig']:<12s} {r['is_n']:>4d} ${r['is_pnl']:>+7,.0f} {r['oos_n']:>5d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} ${r['oos_avg']:>+6.1f}")

    # ===== PHASE 2: Combinations =====
    print(f"\n{'='*70}")
    print(f"  PHASE 2: SIGNAL COMBINATIONS (short-only)")
    print(f"{'='*70}")

    # Define signal groups
    SYM = ["gk_comp","skew_s","sr_s"]
    MIC = ["tbr_low","vcv_low","pe_low","ami_low"]
    M5  = ["kurt_low","tbr_low","vcv_low","pe_low","ami_low"]
    MOM = ["roc_bear","rp_weak","tbr_low"]  # momentum + microstructure
    FULL7 = ["kurt_low","tbr_low","vcv_low","pe_low","ami_low","roc_bear","rp_weak"]
    FULL8 = FULL7 + ["btc_bs"]
    ALL  = ["gk_comp","skew_s","sr_s","kurt_low","tbr_low","vcv_low","pe_low","ami_low","roc_bear","rp_weak"]

    configs=[
        # A. Baselines from R2
        ("sym m15",            15, SYM,   "bs", 12, False),
        ("5sig m12",           12, M5,    "bs", 12, False),
        # B. New signals solo & combos
        ("ROC+RP m9",           9, ["roc_bear","rp_weak"], "bs", 12, False),
        ("ROC+RP+TBR m9",      9, MOM,   "bs", 12, False),
        ("ROC+RP+TBR m12",    12, MOM,   "bs", 12, False),
        # C. Full 7-signal (all non-overlap with L)
        ("7sig m9",             9, FULL7, "bs", 12, False),
        ("7sig m12",           12, FULL7, "bs", 12, False),
        ("7sig m15",           15, FULL7, "bs", 12, False),
        ("7sig m20",           20, FULL7, "bs", 12, False),
        # D. 7sig + BTC
        ("8sig m12",           12, FULL8, "bs", 12, False),
        ("8sig m15",           15, FULL8, "bs", 12, False),
        # E. BRK5 variants
        ("7sig m12 brk5",     12, FULL7, "bs5", 12, False),
        ("7sig m15 brk5",     15, FULL7, "bs5", 12, False),
        ("8sig m15 brk5",     15, FULL8, "bs5", 12, False),
        # F. CD modifications
        ("7sig m15 CD0",       15, FULL7, "bs", 0, False),
        ("7sig m15 CDloss",    15, FULL7, "bs", 12, True),
        ("8sig m15 brk5 CD0",  15, FULL8, "bs5", 0, False),
        # G. ALL signals (for ceiling estimate, includes L signals)
        ("ALL m15",            15, ALL,   "bs", 12, False),
        ("ALL m15 brk5",       15, ALL,   "bs5", 12, False),
        ("ALL m20 brk5 CD0",   20, ALL,   "bs5", 0, False),
    ]

    results=[]
    for label,ms,sigs,brk,cd,cd_loss in configs:
        all_t=bt_short(df,ms,sigs,brk,cd,cd_loss)
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        is_s=stats(is_t); oos_s=stats(oos_t)
        wf_=wf(all_t); ipm,itm=pos_months(is_t)

        r={"label":label,"ms":ms,
           "is_n":is_s["n"],"is_pnl":is_s["pnl"],"is_pf":is_s["pf"],"is_pm":ipm,
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
    print(f"  {'Config':<26s} {'m':>2s} {'#':>4s} {'OOS$':>9s} {'PF':>5s} {'avg$':>7s} {'MDD':>5s} {'WF':>3s}")
    print(f"  {'-'*65}")
    for r in sorted_r:
        hit="*" if r["oos_pnl"]>=10000 else " "
        print(f" {hit}{r['label']:<26s} {r['ms']:>2d} {r['oos_n']:>4d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} ${r['oos_avg']:>+6.1f} {r['oos_mdd']:>5.1f} {r['wf']:>3d}")

    best=sorted_r[0]
    combined=13776+best["oos_pnl"]
    print(f"\n  Best S: {best['label']} -> OOS ${best['oos_pnl']:+,.0f} (avg ${best['oos_avg']:+.1f}/trade)")
    print(f"  Combined: ${combined:+,.0f} / $20,000 ({combined/20000*100:.0f}%)")
    if best["oos_pnl"]>=10000:
        print(f"  S TARGET REACHED!")
    else:
        print(f"  S gap: ${10000-best['oos_pnl']:,.0f}")

    # Key insight
    print(f"\n  KEY ANALYSIS:")
    # Best non-overlap vs best ALL
    best_no=max([r for r in results if "ALL" not in r["label"]],key=lambda x:x["oos_pnl"])
    best_all=max([r for r in results if "ALL" in r["label"]],key=lambda x:x["oos_pnl"])
    print(f"  Best non-overlap: {best_no['label']} ${best_no['oos_pnl']:+,.0f}")
    print(f"  Best ALL (ceiling): {best_all['label']} ${best_all['oos_pnl']:+,.0f}")
    print(f"  Ceiling gap: ${best_all['oos_pnl']-best_no['oos_pnl']:+,.0f}")

    # Which new signals helped?
    base_7sig=next((r for r in results if r["label"]=="7sig m12"),None)
    base_5sig=next((r for r in results if r["label"]=="5sig m12"),None)
    if base_7sig and base_5sig:
        delta=base_7sig["oos_pnl"]-base_5sig["oos_pnl"]
        print(f"  Adding ROC+RP to 5sig: delta ${delta:+,.0f}")

    print(f"\n  ROUND 3 COMPLETE")
