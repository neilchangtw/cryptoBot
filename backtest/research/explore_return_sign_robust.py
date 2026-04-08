"""
R16 Robustness Audit: Return Sign Consistency
Test 3x3 grid: SR_WIN=[12,15,18] x SR_TH=[0.55,0.60,0.65]
"""
import os, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")

NOTIONAL=2000;FEE=2.0;ACCOUNT=10000
BRK_LOOK=10;BLOCK_H={0,1,2,12};BLOCK_D={0,5,6}
SN_PCT=0.055;MIN_TRAIL=7;ES_PCT=0.010;ES_END=12;EXIT_CD=12;MAX_SAME=4

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV);df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def calc(df, sr_win, sr_lt):
    sr_st = 1.0 - sr_lt
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()
    d["pos"]=(d["ret"]>0).astype(float)
    d["sr"]=d["pos"].rolling(sr_win).mean().shift(1)
    d["sr_long"]=d["sr"]>sr_lt
    d["sr_short"]=d["sr"]<sr_st
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"];d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["sl_p"]=d["sr"].shift(1)>sr_lt;d["ss_p"]=d["sr"].shift(1)<sr_st
    d["bl_p"]=d["bl"].shift(1);d["bs_p"]=d["bs"].shift(1);d["sok_p"]=d["sok"].shift(1)
    return d

def _b(v):
    try:
        if pd.isna(v):return False
    except:pass
    return bool(v)

def bt(df, sr_win):
    W=sr_win+BRK_LOOK+20
    H=df["high"].values;L=df["low"].values;C=df["close"].values;O=df["open"].values
    E=df["ema20"].values;DT=df["datetime"].values
    SL=df["sr_long"].values;SS=df["sr_short"].values
    BL=df["bl"].values;BS=df["bs"].values;SOK=df["sok"].values
    SLP=df["sl_p"].values;SSP=df["ss_p"].values
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
        sl_=SL[i];ss_=SS[i]
        bl=BL[i];bs=BS[i];sok=SOK[i]
        if np.isnan(bl):bl=False
        if np.isnan(bs):bs=False
        try:
            if pd.isna(sl_):sl_=False
            if pd.isna(ss_):ss_=False
        except:pass
        sl_=bool(sl_);ss_=bool(ss_);bl=bool(bl);bs=bool(bs);sok=bool(sok)
        fl=not(_b(SLP[i]) and _b(BLP[i]) and _b(SOKP[i]))
        fs=not(_b(SSP[i]) and _b(BSP[i]) and _b(SOKP[i]))
        if sl_ and bl and sok and fl and(i-lx>=EXIT_CD) and len(lp)<MAX_SAME:
            lp.append({"e":nxo,"ei":i})
        if ss_ and bs and sok and fs and(i-sx>=EXIT_CD) and len(sp)<MAX_SAME:
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
    print("  R16 Robustness: Return Sign Consistency")
    print("  3x3 grid: SR_WIN=[12,15,18] x SR_TH=[0.55,0.60,0.65]")
    print("="*60)
    df_raw=load()
    results=[]
    for w in [12,15,18]:
        for th in [0.55,0.60,0.65]:
            d=calc(df_raw, w, th)
            all_t=bt(d, w);all_t["dt"]=pd.to_datetime(all_t["dt"])
            last_dt=d["datetime"].iloc[-1];mid_dt=last_dt-pd.Timedelta(days=365)
            is_t=all_t[all_t["dt"]<mid_dt]
            oos_t=all_t[all_t["dt"]>=mid_dt]
            is_s=stats(is_t);oos_s=stats(oos_t)
            results.append({"w":w,"th":th,"is_n":is_s["n"],"is_pnl":is_s["pnl"],"is_pf":is_s["pf"],
                           "oos_n":oos_s["n"],"oos_pnl":oos_s["pnl"],"oos_pf":oos_s["pf"],
                           "oos_wr":oos_s["wr"],"oos_sh":oos_s["sh"]})
            print(f"  W={w} TH={th:.2f}: IS {is_s['n']:>3d}t ${is_s['pnl']:>+8,.0f} PF{is_s['pf']:.2f} | OOS {oos_s['n']:>3d}t ${oos_s['pnl']:>+8,.0f} PF{oos_s['pf']:.2f} WR{oos_s['wr']:.0f}%")

    rdf=pd.DataFrame(results)
    pos_oos=sum(1 for _,r in rdf.iterrows() if r["oos_pnl"]>0)
    pos_is=sum(1 for _,r in rdf.iterrows() if r["is_pnl"]>0)
    print(f"\n  OOS positive: {pos_oos}/{len(rdf)}")
    print(f"  IS positive: {pos_is}/{len(rdf)}")
    print(f"  OOS range: ${rdf['oos_pnl'].min():+,.0f} ~ ${rdf['oos_pnl'].max():+,.0f}")
    print(f"  IS range: ${rdf['is_pnl'].min():+,.0f} ~ ${rdf['is_pnl'].max():+,.0f}")
    best=rdf.loc[rdf["oos_pnl"].idxmax()]
    print(f"  Best OOS: W={int(best['w'])} TH={best['th']:.2f} ${best['oos_pnl']:+,.0f}")
