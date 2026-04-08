"""
Round 23: Triple OR Relaxed Entry Controls
============================================
R18 Triple OR = $9,863 (best). R19-R22 all worse (signal variations dilute).
Bottleneck is not signal quality — it's entry quantity controls.

Changes from R18:
  1. Remove freshness filter (allow re-entry on sustained signals)
  2. maxSame 4→5 (MDD 8.1% << 25% limit, headroom exists)

Signal logic IDENTICAL to R18: (GK OR Skew OR RetSign) AND breakout AND session.
Also test variants: A=no fresh only, B=maxSame5 only, C=both.
"""
import os, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")

NOTIONAL=2000;FEE=2.0;ACCOUNT=10000
GK_SHORT=5;GK_LONG=20;GK_WIN=100;GK_THRESH=30
SKEW_WIN=20;SKEW_TH=1.0
SR_WIN=15;SR_LT=0.60;SR_ST=0.40
BRK_LOOK=10;BLOCK_H={0,1,2,12};BLOCK_D={0,5,6}
SN_PCT=0.055;MIN_TRAIL=7;ES_PCT=0.010;ES_END=12;EXIT_CD=12

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV);df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def calc(df):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()
    log_hl=np.log(d["high"]/d["low"])
    log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    d["gk_comp"]=d["gk_pct"]<GK_THRESH
    d["skew"]=d["ret"].rolling(SKEW_WIN).skew().shift(1)
    d["skew_l"]=d["skew"]>SKEW_TH;d["skew_s"]=d["skew"]<-SKEW_TH
    d["sr"]=(d["ret"]>0).astype(float).rolling(SR_WIN).mean().shift(1)
    d["sr_l"]=d["sr"]>SR_LT;d["sr_s"]=d["sr"]<SR_ST
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"];d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["any_l"]=d["gk_comp"]|d["skew_l"]|d["sr_l"]
    d["any_s"]=d["gk_comp"]|d["skew_s"]|d["sr_s"]
    d["al_p"]=d["any_l"].shift(1);d["as_p"]=d["any_s"].shift(1)
    d["bl_p"]=d["bl"].shift(1);d["bs_p"]=d["bs"].shift(1);d["sok_p"]=d["sok"].shift(1)
    return d

def _b(v):
    try:
        if pd.isna(v):return False
    except:pass
    return bool(v)

def bt(df, use_fresh=True, max_same=4):
    W=GK_LONG+GK_WIN+20
    H=df["high"].values;L=df["low"].values;C=df["close"].values;O=df["open"].values
    E=df["ema20"].values;DT=df["datetime"].values
    GKC=df["gk_comp"].values;SKL=df["skew_l"].values;SKS=df["skew_s"].values
    SRL=df["sr_l"].values;SRS=df["sr_s"].values
    BL=df["bl"].values;BS=df["bs"].values;SOK=df["sok"].values
    ALP=df["al_p"].values;ASP=df["as_p"].values
    BLP=df["bl_p"].values;BSP=df["bs_p"].values;SOKP=df["sok_p"].values
    lp=[];sp=[];trades=[];lx=-999;sx=-999
    for i in range(W,len(df)-1):
        h,lo,c,ema,dt,nxo=H[i],L[i],C[i],E[i],DT[i],O[i+1]
        nl=[]
        for p in lp:
            b=i-p["ei"];done=False
            if lo<=p["e"]*(1-SN_PCT):
                ep_=p["e"]*(1-SN_PCT);ep_-=(ep_-lo)*0.25
                pnl=(ep_-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"L","b":b,"dt":dt});lx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if(c-p["e"])/p["e"]<-ES_PCT:
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"L","b":b,"dt":dt});lx=i;done=True
            if not done and b>=MIN_TRAIL and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"L","b":b,"dt":dt});lx=i;done=True
            if not done:nl.append(p)
        lp=nl
        ns=[]
        for p in sp:
            b=i-p["ei"];done=False
            if h>=p["e"]*(1+SN_PCT):
                ep_=p["e"]*(1+SN_PCT);ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"S","b":b,"dt":dt});sx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if(p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"S","b":b,"dt":dt});sx=i;done=True
            if not done and b>=MIN_TRAIL and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"S","b":b,"dt":dt});sx=i;done=True
            if not done:ns.append(p)
        sp=ns
        gkc=_b(GKC[i]);skl=_b(SKL[i]);sks=_b(SKS[i]);srl=_b(SRL[i]);srs=_b(SRS[i])
        bl=BL[i];bs=BS[i];sok=SOK[i]
        if np.isnan(bl) if isinstance(bl,(float,np.floating)) else False:bl=False
        if np.isnan(bs) if isinstance(bs,(float,np.floating)) else False:bs=False
        bl=bool(bl);bs=bool(bs);sok=bool(sok)
        long_sig=(gkc or skl or srl) and bl and sok
        short_sig=(gkc or sks or srs) and bs and sok
        if use_fresh:
            fl=not(_b(ALP[i]) and _b(BLP[i]) and _b(SOKP[i]))
            fs=not(_b(ASP[i]) and _b(BSP[i]) and _b(SOKP[i]))
        else:
            fl=True;fs=True
        if long_sig and fl and(i-lx>=EXIT_CD) and len(lp)<max_same:
            lp.append({"e":nxo,"ei":i})
        if short_sig and fs and(i-sx>=EXIT_CD) and len(sp)<max_same:
            sp.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt"])

def stats(tdf):
    if len(tdf)==0:return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sh":0}
    n=len(tdf);pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum();l_=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    eq=tdf["pnl"].cumsum();dd=eq-eq.cummax();mdd=dd.min();mp=abs(mdd)/ACCOUNT*100
    tc=tdf.copy();tc["date"]=pd.to_datetime(tc["dt"]).dt.date
    dy=tc.groupby("date")["pnl"].sum()
    rng=pd.date_range(tc["dt"].min(),tc["dt"].max(),freq="D")
    dy=dy.reindex(rng.date,fill_value=0)
    sh=float(dy.mean()/dy.std()*np.sqrt(365)) if dy.std()>0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mp,1),"sh":round(sh,2)}

if __name__=="__main__":
    print("="*60)
    print("  Round 23: Triple OR — Relaxed Entry Controls")
    print("="*60)
    df=load();df=calc(df)

    configs = [
        ("A: R18 baseline (fresh=Y, max=4)", True, 4),
        ("B: No freshness (fresh=N, max=4)", False, 4),
        ("C: maxSame=5 (fresh=Y, max=5)", True, 5),
        ("D: Both relaxed (fresh=N, max=5)", False, 5),
        ("E: maxSame=6 (fresh=Y, max=6)", True, 6),
        ("F: No fresh + max=6 (fresh=N, max=6)", False, 6),
    ]

    last_dt=df["datetime"].iloc[-1];mid_dt=last_dt-pd.Timedelta(days=365)
    oos_m=(last_dt-mid_dt).days/30.44

    results = []
    for label, uf, ms in configs:
        all_t=bt(df, use_fresh=uf, max_same=ms)
        all_t["dt"]=pd.to_datetime(all_t["dt"])
        oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
        is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
        is_s=stats(is_t);oos_s=stats(oos_t)
        # WF
        dts=pd.to_datetime(all_t["dt"]);mn_,mx_=dts.min(),dts.max()
        step=(mx_-mn_)/10;wf=sum(1 for k in range(10) if len(all_t[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))])>0 and all_t[(dts>=mn_+step*k)&(dts<mn_+step*(k+1))]["pnl"].sum()>0)
        results.append({"label":label,"uf":uf,"ms":ms,
                        "is_n":is_s["n"],"is_pnl":is_s["pnl"],"is_pf":is_s["pf"],
                        "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],"oos_pf":oos_s["pf"],
                        "oos_wr":oos_s["wr"],"oos_mdd":oos_s["mdd_pct"],"oos_sh":oos_s["sh"],"wf":wf})
        hit="YES" if oos_s["pnl"]>=10000 else "no"
        print(f"\n  {label}")
        print(f"    IS:  {is_s['n']:>4d}t ${is_s['pnl']:>+8,.0f} PF{is_s['pf']:.2f}")
        print(f"    OOS: {oos_s['n']:>4d}t ${oos_s['pnl']:>+8,.0f} PF{oos_s['pf']:.2f} WR{oos_s['wr']:.0f}% MDD{oos_s['mdd_pct']:.1f}% Sh{oos_s['sh']:.2f}")
        print(f"    WF: {wf}/10  TARGET: {hit}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY TABLE")
    print(f"{'='*60}")
    print(f"  {'Config':<38s} {'OOS$':>8s} {'PF':>5s} {'MDD%':>5s} {'WF':>4s} {'HIT':>4s}")
    for r in results:
        hit="YES" if r["oos_pnl"]>=10000 else "no"
        print(f"  {r['label']:<38s} ${r['oos_pnl']:>+7,.0f} {r['oos_pf']:>5.2f} {r['oos_mdd']:>5.1f} {r['wf']:>3d} {hit:>4s}")

    best=max(results,key=lambda x:x["oos_pnl"])
    print(f"\n  Best: {best['label']} OOS ${best['oos_pnl']:+,.0f}")
    if best["oos_pnl"]>=10000:
        print(f"  TARGET REACHED!")
    else:
        print(f"  Gap: ${10000-best['oos_pnl']:,.0f}")
    print(f"\n  ROUND 23 COMPLETE")
