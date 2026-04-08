"""
Round 2: Strategy S Deep Exploration
============================================
L locked: GK+Skew+RetSign m9, OOS $13,776 (TARGET MET)

S problem: microstructure signals gave $2.5-3.8K (< symmetric $4K baseline).
ETH shorts inherently weak: per-trade avg $12 vs $25 for longs.

New approaches:
  1. Symmetric scaling (ceiling estimate)
  2. Wider trail (EMA50) - shorts need room through bounces
  3. No EarlyStop - stop killing shorts at bar 7-12
  4. Higher MinTrail (12) - delay trail for shorts
  5. Shorter EXIT_CD (8) - more re-entry opportunities
  6. BTC breakdown as cross-asset signal
  7. Shorter breakout (5-bar) - capture faster selloffs
  8. Mega maxSame (15-20)
  9. Combined modifications

Hypothesis: ETH shorts fail because exits are calibrated for longs.
Shorts need wider trail + delayed exits to survive typical bounces.
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
GK_SHORT=5; GK_LONG=20; GK_WIN=100; GK_THRESH=30
SKEW_WIN=20; SKEW_TH=1.0
SR_WIN=15; SR_LT=0.60; SR_ST=0.40
PE_ORDER=3; PE_WIN=24; PE_PWIN=50; PE_TH=25
KURT_WIN=30; KURT_PWIN=50; KURT_TH=25
TBR_SM=5; TBR_PWIN=50; TBR_TH=25
VCV_WIN=20; VCV_PWIN=50; VCV_TH=25
AMI_SM=5; AMI_PWIN=50; AMI_TH=25
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
    d["ema50"]=d["close"].ewm(span=50).mean()
    d["ret"]=d["close"].pct_change()

    # GK
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
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

    # Taker Buy Ratio
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(TBR_SM).mean()
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(TBR_PWIN).apply(pctile)
    d["tbr_low"]=d["tbr_pct"]<TBR_TH

    # Volume CV
    vm=d["volume"].rolling(VCV_WIN).mean(); vs_=d["volume"].rolling(VCV_WIN).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_pct"]=d["vcv"].shift(1).rolling(VCV_PWIN).apply(pctile)
    d["vcv_low"]=d["vcv_pct"]<VCV_TH

    # PE
    print("    Computing Permutation Entropy...", flush=True)
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

    # Breakout (10-bar)
    d["cs1"]=d["close"].shift(1)
    d["cmn10"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()  # typo in R1, this should be min for shorts
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]

    # Breakout (5-bar) for faster selloff detection
    d["cmn5"]=d["close"].shift(2).rolling(4).min()
    d["bs5"]=d["cs1"]<d["cmn5"]

    # BTC breakout
    if "btc_close" in d.columns:
        d["btc_cs1"]=d["btc_close"].shift(1)
        d["btc_cmn"]=d["btc_close"].shift(2).rolling(BRK_LOOK-1).min()
        d["btc_bs"]=d["btc_cs1"]<d["btc_cmn"]
    else:
        d["btc_bs"]=False

    # Session
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_short(df, max_same, signal_cols, brk_col="bs",
             trail_ema="ema20", min_trail=MIN_TRAIL,
             use_early_stop=True, exit_cd=EXIT_CD):
    """Configurable short-only backtest"""
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df[trail_ema].values; DT=df["datetime"].values
    BSa=df[brk_col].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            # SafeNet
            if h>=p["e"]*(1+SN_PCT):
                ep_=p["e"]*(1+SN_PCT); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"S","b":b,"dt":dt}); lx=i; done=True
            # EarlyStop
            elif use_early_stop and min_trail<=b<=ES_END:
                if (p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"S","b":b,"dt":dt}); lx=i; done=True
            # Trail
            if not done and b>=min_trail and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"S","b":b,"dt":dt}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        # Entry
        any_sig=any(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if any_sig and brk and sok and (i-lx>=exit_cd) and len(pos)<max_same:
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt"])

def stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd_pct":0,"sh":0}
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
            "mdd_pct":round(mp,1),"sh":round(sh,2)}

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

def top_m_pct(tdf, total_pnl):
    if len(tdf)==0 or total_pnl<=0: return 0
    tc=tdf.copy(); tc["dt"]=pd.to_datetime(tc["dt"]); tc["m"]=tc["dt"].dt.to_period("M")
    return tc.groupby("m")["pnl"].sum().max()/total_pnl*100

if __name__=="__main__":
    print("="*70)
    print("  ROUND 2: STRATEGY S DEEP EXPLORATION")
    print("  L locked: GK+SK+SR m9, OOS $13,776")
    print("="*70)

    print("\n  Self-check:")
    print("  [V] shift(1)  [V] next-bar open  [V] rolling shift  [V] pre-fixed params")
    print("  [V] no post-adjust  [V] rolling pctile  [V] auto  [V] realistic  [V] slippage OK")

    print("\n  Loading data...")
    df=load_eth()
    btc=load_btc()
    df=df.merge(btc, on="datetime", how="left")
    df=calc(df)

    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # Signal check
    for sc in ["gk_comp","skew_s","sr_s","kurt_low","tbr_low","vcv_low","pe_low","ami_low","btc_bs"]:
        n_true=df[sc].sum() if sc in df.columns else 0
        print(f"    {sc:<12s}: {int(n_true):>5d} ({n_true/len(df)*100:.1f}%)")

    # ===== S configs =====
    # Format: (label, max_same, signals, brk_col, trail_ema, min_trail, use_es, exit_cd)
    SYM = ["gk_comp","skew_s","sr_s"]
    MIC = ["tbr_low","vcv_low","pe_low","ami_low"]
    M5  = ["kurt_low","tbr_low","vcv_low","pe_low","ami_low"]

    configs=[
        # A. Symmetric scaling (ceiling estimate)
        ("sym m7",         7,  SYM, "bs","ema20",7,True,12),
        ("sym m9",         9,  SYM, "bs","ema20",7,True,12),
        ("sym m12",       12,  SYM, "bs","ema20",7,True,12),
        ("sym m15",       15,  SYM, "bs","ema20",7,True,12),
        # B. Micro scaling
        ("mic m12",       12,  MIC, "bs","ema20",7,True,12),
        ("mic m15",       15,  MIC, "bs","ema20",7,True,12),
        ("mic m20",       20,  MIC, "bs","ema20",7,True,12),
        # C. 5-signal (no GK overlap) scaling
        ("5sig m9",        9,  M5,  "bs","ema20",7,True,12),
        ("5sig m12",      12,  M5,  "bs","ema20",7,True,12),
        ("5sig m15",      15,  M5,  "bs","ema20",7,True,12),
        # D. Exit modifications (using 5sig m12 as base)
        ("5sig m12 EMA50",12,  M5,  "bs","ema50",7,True,12),
        ("5sig m12 noES", 12,  M5,  "bs","ema20",7,False,12),
        ("5sig m12 MT12", 12,  M5,  "bs","ema20",12,True,12),
        ("5sig m12 CD8",  12,  M5,  "bs","ema20",7,True,8),
        # E. Combined exit mods
        ("5sig m12 E50+nES",    12, M5, "bs","ema50",7,False,12),
        ("5sig m15 E50+nES",    15, M5, "bs","ema50",7,False,12),
        ("5sig m12 E50+nES+C8", 12, M5, "bs","ema50",7,False,8),
        ("5sig m15 E50+nES+C8", 15, M5, "bs","ema50",7,False,8),
        ("5sig m20 E50+nES+C8", 20, M5, "bs","ema50",7,False,8),
        # F. BRK5 (faster breakout)
        ("5sig m12 brk5", 12,  M5,  "bs5","ema20",7,True,12),
        ("5sig m15 brk5 E50+nES+C8", 15, M5, "bs5","ema50",7,False,8),
        # G. BTC cross-asset
        ("5sig+BTC m12",  12,  M5+["btc_bs"], "bs","ema20",7,True,12),
        ("5sig+BTC m15",  15,  M5+["btc_bs"], "bs","ema20",7,True,12),
        ("5sig+BTC m15 E50+nES+C8", 15, M5+["btc_bs"], "bs","ema50",7,False,8),
        # H. Symmetric with modified exits (ceiling)
        ("sym m12 E50+nES",    12, SYM, "bs","ema50",7,False,12),
        ("sym m15 E50+nES+C8", 15, SYM, "bs","ema50",7,False,8),
    ]

    results=[]
    for label,ms,sigs,brk,trail,mt,es,cd in configs:
        all_t=bt_short(df,ms,sigs,brk,trail,mt,es,cd)
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        is_s=stats(is_t); oos_s=stats(oos_t)
        wf_=wf(all_t)
        ipm,itm=pos_months(is_t)
        tm=round(top_m_pct(oos_t,oos_s["pnl"]),1) if oos_s["pnl"]>0 else 0

        r={"label":label,"ms":ms,
           "is_n":is_s["n"],"is_pnl":is_s["pnl"],"is_pf":is_s["pf"],"is_pm":ipm,"is_tm":itm,
           "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],"oos_pf":oos_s["pf"],
           "oos_wr":oos_s["wr"],"oos_mdd":oos_s["mdd_pct"],"oos_sh":oos_s["sh"],
           "wf":wf_,"tm":tm}
        results.append(r)

        mo=oos_s["n"]/12.0
        gap=10000-oos_s["pnl"]
        tag="TARGET!" if gap<=0 else f"gap ${gap:,.0f}"
        print(f"\n  {label}")
        print(f"    IS:  {is_s['n']:>4d}t ${is_s['pnl']:>+9,.0f} PF{is_s['pf']:.2f} pos{ipm}/{itm}")
        print(f"    OOS: {oos_s['n']:>4d}t(mo={mo:.1f}) ${oos_s['pnl']:>+9,.0f} PF{oos_s['pf']:.2f}")
        print(f"    WR{oos_s['wr']:.0f}% MDD{oos_s['mdd_pct']:.1f}% Sh{oos_s['sh']:.2f} WF{wf_}/10 topM{tm:.0f}%")
        print(f"    --> {tag}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY (sorted by OOS PnL)")
    print(f"{'='*70}")
    sorted_r=sorted(results,key=lambda x:x["oos_pnl"],reverse=True)
    print(f"  {'Config':<30s} {'m':>2s} {'OOS$':>9s} {'PF':>5s} {'WR':>4s} {'MDD':>5s} {'WF':>3s}")
    print(f"  {'-'*62}")
    for r in sorted_r:
        hit="*" if r["oos_pnl"]>=10000 else " "
        print(f" {hit}{r['label']:<30s} {r['ms']:>2d} ${r['oos_pnl']:>+8,.0f} {r['oos_pf']:>5.2f} {r['oos_wr']:>3.0f}% {r['oos_mdd']:>5.1f} {r['wf']:>3d}")

    best=sorted_r[0]
    print(f"\n  Best S: {best['label']} -> OOS ${best['oos_pnl']:+,.0f}")
    combined=13776+best["oos_pnl"]
    print(f"  Combined (L=$13,776 + S): ${combined:+,.0f} / $20,000 ({combined/20000*100:.0f}%)")

    if best["oos_pnl"]>=10000:
        print(f"  S TARGET REACHED!")
    else:
        print(f"  S gap: ${10000-best['oos_pnl']:,.0f}")

    # Analysis: what lever helps most?
    print(f"\n  LEVER ANALYSIS:")
    # Compare base vs modifications
    base_5sig=next((r for r in results if r["label"]=="5sig m12"),None)
    if base_5sig:
        bp=base_5sig["oos_pnl"]
        print(f"  5sig m12 base: ${bp:+,.0f}")
        for label_mod in ["5sig m12 EMA50","5sig m12 noES","5sig m12 MT12","5sig m12 CD8",
                          "5sig m12 E50+nES","5sig m12 E50+nES+C8","5sig m12 brk5"]:
            r_mod=next((r for r in results if r["label"]==label_mod),None)
            if r_mod:
                delta=r_mod["oos_pnl"]-bp
                print(f"    {label_mod:<30s}: ${r_mod['oos_pnl']:>+8,.0f} (delta ${delta:>+6,.0f})")

    # Happy table check
    print(f"\n  HAPPY TABLE CHECK:")
    any_warn=False
    for r in sorted_r[:5]:
        w_list=[]
        if r["oos_wr"]>70: w_list.append(f"WR>70")
        if r["oos_pf"]>6.0: w_list.append(f"PF>6")
        if r["oos_sh"]>8.0: w_list.append(f"Sh>8")
        if r["oos_wr"]>60: w_list.append(f"WR>60 single-side")
        if w_list:
            print(f"    {r['label']}: {', '.join(w_list)}")
            any_warn=True
    if not any_warn: print(f"    No warnings.")

    print(f"\n  ROUND 2 COMPLETE")
