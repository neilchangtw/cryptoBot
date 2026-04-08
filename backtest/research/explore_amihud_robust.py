"""Robustness for Round 2: Amihud. AMI_WIN and AMI_TH +/-20%."""
import os, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
NOTIONAL=2000;FEE=2.0;ACCOUNT=10000
AMI_AVG=10;BRK_LOOK=10;BLOCK_H={0,1,2,12};BLOCK_D={0,5,6}
SN_PCT=0.055;MIN_TRAIL=7;ES_PCT=0.010;ES_END=12;EXIT_CD=12;MAX_SAME=4
BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV);df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)

def run_one(df,ami_win,ami_th):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change().abs()
    d["ami_raw"]=d["ret"]/d["volume"];d["ami_raw"]=d["ami_raw"].replace([np.inf,-np.inf],np.nan)
    d["ami"]=d["ami_raw"].rolling(AMI_AVG).mean()
    d["ami_pct"]=d["ami"].shift(1).rolling(ami_win).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    d["cs1"]=d["close"].shift(1);d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"];d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["ami_p"]=d["ami_pct"].shift(1)<ami_th
    d["bl_p"]=d["bl"].shift(1);d["bs_p"]=d["bs"].shift(1);d["sok_p"]=d["sok"].shift(1)
    def _b(v):
        try:
            if pd.isna(v):return False
        except:pass
        return bool(v)
    W=AMI_AVG+ami_win+BRK_LOOK+20
    H=d["high"].values;L=d["low"].values;C=d["close"].values;O=d["open"].values
    E=d["ema20"].values;DT=d["datetime"].values;AP=d["ami_pct"].values
    BL=d["bl"].values;BS=d["bs"].values;SOK=d["sok"].values
    AMP=d["ami_p"].values;BLP=d["bl_p"].values;BSP=d["bs_p"].values;SOKP=d["sok_p"].values
    lp=[];sp=[];trades=[];lx=-999;sx=-999
    for i in range(W,len(d)-1):
        h,lo,c,ema,dt,nxo=H[i],L[i],C[i],E[i],DT[i],O[i+1]
        nl=[]
        for p in lp:
            b=i-p["ei"];done=False
            if lo<=p["e"]*(1-SN_PCT):ep=p["e"]*(1-SN_PCT);ep-=(ep-lo)*0.25;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if(c-p["e"])/p["e"]<-ES_PCT:pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            if not done and b>=MIN_TRAIL and c<=ema:pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            if not done:nl.append(p)
        lp=nl
        ns=[]
        for p in sp:
            b=i-p["ei"];done=False
            if h>=p["e"]*(1+SN_PCT):ep=p["e"]*(1+SN_PCT);ep+=(h-ep)*0.25;pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if(p["e"]-c)/p["e"]<-ES_PCT:pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            if not done and b>=MIN_TRAIL and c>=ema:pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE;trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            if not done:ns.append(p)
        sp=ns
        ap=AP[i]
        if np.isnan(ap):continue
        bl=BL[i];bs=BS[i];sok=SOK[i]
        if np.isnan(bl):bl=False
        if np.isnan(bs):bs=False
        comp=ap<ami_th;bl=bool(bl);bs=bool(bs);sok=bool(sok)
        fl=not(_b(AMP[i]) and _b(BLP[i]) and _b(SOKP[i]))
        fs=not(_b(AMP[i]) and _b(BSP[i]) and _b(SOKP[i]))
        if comp and bl and sok and fl and(i-lx>=EXIT_CD) and len(lp)<MAX_SAME:lp.append({"e":nxo,"ei":i})
        if comp and bs and sok and fs and(i-sx>=EXIT_CD) and len(sp)<MAX_SAME:sp.append({"e":nxo,"ei":i})
    if not trades:return 0,0,0,0,0,0
    tdf=pd.DataFrame(trades);tdf["dt"]=pd.to_datetime(tdf["dt"])
    last_dt=d["datetime"].iloc[-1];mid_dt=last_dt-pd.Timedelta(days=365)
    is_t=tdf[tdf["dt"]<mid_dt];oos_t=tdf[tdf["dt"]>=mid_dt]
    def _pf(t):
        if len(t)==0:return 0,0,0
        w=t[t["pnl"]>0]["pnl"].sum();l_=abs(t[t["pnl"]<=0]["pnl"].sum())
        return len(t),round(t["pnl"].sum(),0),round(w/l_ if l_>0 else 999,2)
    isn,isp,ispf=_pf(is_t);on,op,opf=_pf(oos_t)
    return isn,isp,ispf,on,op,opf

if __name__=="__main__":
    print("Robustness: AMI_WIN x AMI_TH (+/-20%)")
    print("="*70)
    df=load()
    for aw in [40,50,60]:
        for at in [20,25,30]:
            isn,isp,ispf,on,op,opf=run_one(df,aw,at)
            tag=" <-- BASE" if aw==50 and at==25 else ""
            print(f"  WIN={aw:>2d} TH={at:>2d} | IS: {isn:>3d}t ${isp:>+7,.0f} PF{ispf:>5.2f} | OOS: {on:>3d}t ${op:>+7,.0f} PF{opf:>5.2f}{tag}")
    oos_vals=[]
    for aw in [40,50,60]:
        for at in [20,25,30]:
            _,_,_,_,op,_=run_one(df,aw,at);oos_vals.append(op)
    print(f"  OOS range: ${min(oos_vals):+,.0f} ~ ${max(oos_vals):+,.0f}")
    print(f"  All positive? {'YES' if all(v>0 for v in oos_vals) else 'NO'}")
