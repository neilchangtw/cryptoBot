"""Robustness check for Round 1: Skewness Momentum. SKEW_WIN and SKEW_TH +/-20%."""
import os, sys, numpy as np, pandas as pd, warnings
from datetime import timedelta
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; MIN_TRAIL=7; ES_PCT=0.010; ES_END=12; EXIT_CD=12; MAX_SAME=4

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def run_one(df, skew_win, skew_th):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()
    d["skew"]=d["ret"].rolling(skew_win).skew().shift(1)
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"]; d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["skl_p"]=d["skew"].shift(1)>skew_th; d["sks_p"]=d["skew"].shift(1)<-skew_th
    d["bl_p"]=d["bl"].shift(1); d["bs_p"]=d["bs"].shift(1); d["sok_p"]=d["sok"].shift(1)

    W=skew_win+BRK_LOOK+20
    H=d["high"].values;L=d["low"].values;C=d["close"].values;O=d["open"].values
    E=d["ema20"].values;DT=d["datetime"].values;SK=d["skew"].values
    BL=d["bl"].values;BS=d["bs"].values;SOK=d["sok"].values
    SLP=d["skl_p"].values;SSP=d["sks_p"].values
    BLP=d["bl_p"].values;BSP=d["bs_p"].values;SOKP=d["sok_p"].values

    def _b(v):
        try:
            if pd.isna(v): return False
        except: pass
        return bool(v)

    lp=[];sp=[];trades=[];lx=-999;sx=-999
    for i in range(W,len(d)-1):
        h,lo,c,ema,dt,nxo=H[i],L[i],C[i],E[i],DT[i],O[i+1]
        nl=[]
        for p in lp:
            b=i-p["ei"];done=False
            if lo<=p["e"]*(1-SN_PCT):
                ep=p["e"]*(1-SN_PCT);ep-=(ep-lo)*0.25
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (c-p["e"])/p["e"]<-ES_PCT:
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            if not done and b>=MIN_TRAIL and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"dt":dt});lx=i;done=True
            if not done: nl.append(p)
        lp=nl
        ns=[]
        for p in sp:
            b=i-p["ei"];done=False
            if h>=p["e"]*(1+SN_PCT):
                ep=p["e"]*(1+SN_PCT);ep+=(h-ep)*0.25
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            if not done and b>=MIN_TRAIL and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"dt":dt});sx=i;done=True
            if not done: ns.append(p)
        sp=ns

        sk=SK[i]
        if np.isnan(sk): continue
        bl=BL[i];bs=BS[i];sok=SOK[i]
        if np.isnan(bl):bl=False
        if np.isnan(bs):bs=False
        skl=sk>skew_th;sks=sk<-skew_th
        bl=bool(bl);bs=bool(bs);sok=bool(sok)
        fl=not(_b(SLP[i]) and _b(BLP[i]) and _b(SOKP[i]))
        fs=not(_b(SSP[i]) and _b(BSP[i]) and _b(SOKP[i]))
        if skl and bl and sok and fl and (i-lx>=EXIT_CD) and len(lp)<MAX_SAME:
            lp.append({"e":nxo,"ei":i})
        if sks and bs and sok and fs and (i-sx>=EXIT_CD) and len(sp)<MAX_SAME:
            sp.append({"e":nxo,"ei":i})

    if not trades: return 0,0,0,0,0,0
    tdf=pd.DataFrame(trades); tdf["dt"]=pd.to_datetime(tdf["dt"])
    last_dt=d["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)
    is_t=tdf[tdf["dt"]<mid_dt]; oos_t=tdf[tdf["dt"]>=mid_dt]
    def _pf(t):
        if len(t)==0: return 0,0,0
        w=t[t["pnl"]>0]["pnl"].sum(); l_=abs(t[t["pnl"]<=0]["pnl"].sum())
        return len(t),round(t["pnl"].sum(),0),round(w/l_,2) if l_>0 else 999
    isn,isp,ispf=_pf(is_t); on,op,opf=_pf(oos_t)
    return isn,isp,ispf,on,op,opf

if __name__=="__main__":
    print("Robustness: SKEW_WIN x SKEW_TH (+/-20%)")
    print("="*70)
    df=load()
    grid=[]
    for sw in [16,20,24]:
        for st in [0.8,1.0,1.2]:
            isn,isp,ispf,on,op,opf=run_one(df,sw,st)
            grid.append((sw,st,isn,isp,ispf,on,op,opf))
            tag=" <-- BASE" if sw==20 and st==1.0 else ""
            print(f"  WIN={sw:>2d} TH={st:.1f} | IS: {isn:>3d}t ${isp:>+7,.0f} PF{ispf:>5.2f} | OOS: {on:>3d}t ${op:>+7,.0f} PF{opf:>5.2f}{tag}")
    print("="*70)
    oos_vals=[g[6] for g in grid]
    print(f"  OOS PnL range: ${min(oos_vals):+,.0f} ~ ${max(oos_vals):+,.0f}")
    print(f"  Base OOS: ${grid[4][6]:+,.0f}")
    print(f"  All positive? {'YES' if all(v>0 for v in oos_vals) else 'NO'}")

    # Hand verification: print first 3 entry signals with details
    print("\n" + "="*70)
    print("  HAND VERIFICATION: First 3 entries")
    print("="*70)
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()
    d["skew"]=d["ret"].rolling(20).skew().shift(1)
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(9).max()
    d["cmn"]=d["close"].shift(2).rolling(9).min()
    d["bl"]=d["cs1"]>d["cmx"]; d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))

    count=0
    for i in range(50,len(d)-1):
        sk=d["skew"].iloc[i]
        if pd.isna(sk): continue
        bl=bool(d["bl"].iloc[i]); bs=bool(d["bs"].iloc[i]); sok=bool(d["sok"].iloc[i])
        skl=sk>1.0; sks=sk<-1.0
        if (skl and bl and sok) or (sks and bs and sok):
            side="LONG" if skl else "SHORT"
            print(f"\n  Entry #{count+1}: {side} at bar {i}")
            print(f"    datetime:  {d['datetime'].iloc[i]}")
            print(f"    skew[i] (=skew of bars up to i-1): {sk:.4f}")
            print(f"    close[i-1]: {d['close'].iloc[i-1]:.2f}")
            if skl:
                print(f"    max(close[i-2..i-10]): {d['cmx'].iloc[i]:.2f}")
                print(f"    breakout: {d['close'].iloc[i-1]:.2f} > {d['cmx'].iloc[i]:.2f} = {bl}")
            else:
                print(f"    min(close[i-2..i-10]): {d['cmn'].iloc[i]:.2f}")
                print(f"    breakout: {d['close'].iloc[i-1]:.2f} < {d['cmn'].iloc[i]:.2f} = {bs}")
            print(f"    session_ok: hour={d['datetime'].iloc[i].hour} weekday={d['datetime'].iloc[i].weekday()} -> {sok}")
            print(f"    entry_price (next bar open): {d['open'].iloc[i+1]:.2f}")
            count+=1
            if count>=3: break
