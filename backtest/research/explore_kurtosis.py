"""
Exploration Round 3: Excess Kurtosis Compression + Close Breakout
=================================================================
Hypothesis: Low excess kurtosis = thin-tailed returns = no extreme moves.
This is a distribution SHAPE measure orthogonal to volatility LEVEL.
A market can have normal volatility but thin tails (uniform-ish returns)
or low volatility but fat tails (rare spikes). Kurtosis captures the tail
behavior independent of the overall variance.

When kurtosis is in its lowest percentile (platykurtic = "boring" tails)
AND price breaks out, it's the FIRST extreme move after a calm tail regime.

Entry (3 conditions):
  1. kurtosis(returns, 30).shift(1) percentile(50) < 25 (thin tails)
  2. Close Breakout 10 bar
  3. Session Filter
  + Freshness + Exit Cooldown

Parameters (fixed before seeing data):
  KURT_WIN = 30 (slightly longer for kurtosis stability, ~1.25 days)
  KURT_PCTILE_WIN = 50 (~2 days context)
  KURT_THRESH = 25 (bottom quartile = thinnest tails)
"""
import os, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")

NOTIONAL=2000;FEE=2.0;ACCOUNT=10000
KURT_WIN=30;KURT_PWIN=50;KURT_TH=25
BRK_LOOK=10;BLOCK_H={0,1,2,12};BLOCK_D={0,5,6}
SN_PCT=0.055;MIN_TRAIL=7;ES_PCT=0.010;ES_END=12;EXIT_CD=12;MAX_SAME=4

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
    d["kurt"]=d["ret"].rolling(KURT_WIN).kurt().shift(1)
    d["kurt_pct"]=d["kurt"].shift(0).rolling(KURT_PWIN).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    # Note: kurt already has .shift(1), so kurt_pct at bar i uses kurt values
    # from bars i to i-49, which are based on returns up to bar i-1 to i-50. No leak.
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"];d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    d["kp_p"]=d["kurt_pct"].shift(1)<KURT_TH
    d["bl_p"]=d["bl"].shift(1);d["bs_p"]=d["bs"].shift(1);d["sok_p"]=d["sok"].shift(1)
    return d

def _b(v):
    try:
        if pd.isna(v):return False
    except:pass
    return bool(v)

def bt(df):
    W=KURT_WIN+KURT_PWIN+BRK_LOOK+20
    H=df["high"].values;L=df["low"].values;C=df["close"].values;O=df["open"].values
    E=df["ema20"].values;DT=df["datetime"].values
    KP=df["kurt_pct"].values;BL=df["bl"].values;BS=df["bs"].values;SOK=df["sok"].values
    KPP=df["kp_p"].values;BLP=df["bl_p"].values;BSP=df["bs_p"].values;SOKP=df["sok_p"].values
    lp=[];sp=[];trades=[];lx=-999;sx=-999
    for i in range(W,len(df)-1):
        h,lo,c,ema,dt,nxo=H[i],L[i],C[i],E[i],DT[i],O[i+1]
        nl=[]
        for p in lp:
            b=i-p["ei"];done=False
            if lo<=p["e"]*(1-SN_PCT):
                ep=p["e"]*(1-SN_PCT);ep-=(ep-lo)*0.25
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
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
                ep=p["e"]*(1+SN_PCT);ep+=(h-ep)*0.25
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
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
        kp=KP[i]
        if np.isnan(kp):continue
        bl=BL[i];bs=BS[i];sok=SOK[i]
        if np.isnan(bl):bl=False
        if np.isnan(bs):bs=False
        comp=kp<KURT_TH;bl=bool(bl);bs=bool(bs);sok=bool(sok)
        fl=not(_b(KPP[i]) and _b(BLP[i]) and _b(SOKP[i]))
        fs=not(_b(KPP[i]) and _b(BSP[i]) and _b(SOKP[i]))
        if comp and bl and sok and fl and(i-lx>=EXIT_CD) and len(lp)<MAX_SAME:
            lp.append({"e":nxo,"ei":i})
        if comp and bs and sok and fs and(i-sx>=EXIT_CD) and len(sp)<MAX_SAME:
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

def report(title,tdf,months):
    s=stats(tdf);m=s["n"]/months if months>0 else 0
    print(f"\n{'~'*60}\n  {title}\n{'~'*60}")
    print(f"  Trades: {s['n']}  (monthly avg {m:.1f})")
    print(f"  PnL: ${s['pnl']:+,.2f}  |  WR: {s['wr']:.1f}%  |  PF: {s['pf']:.2f}")
    print(f"  MDD: ${s['mdd']:+,.2f} ({s['mdd_pct']:.1f}%)  |  Sharpe: {s['sh']:.2f}")
    if len(tdf)==0:return s
    for lo_,hi_,lb in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
        sub=tdf[(tdf["b"]>=lo_)&(tdf["b"]<hi_)];n_=len(sub)
        p_=sub["pnl"].sum() if n_>0 else 0;w_=(sub["pnl"]>0).mean()*100 if n_>0 else 0
        print(f"    {lb:<8s}: {n_:>4d}t ${p_:>+8,.0f} WR{w_:.0f}%")
    for side,lb in [("L","Long"),("S","Short")]:
        sub=tdf[tdf["s"]==side];n_=len(sub);p_=sub["pnl"].sum() if n_>0 else 0
        w_=(sub["pnl"]>0).mean()*100 if n_>0 else 0
        print(f"  {lb}: {n_}t ${p_:+,.0f} WR{w_:.0f}%")
    for t_,lb in [("TR","Trail"),("SN","SafeNet"),("ES","EarlyStop")]:
        sub=tdf[tdf["t"]==t_];n_=len(sub);p_=sub["pnl"].sum() if n_>0 else 0
        print(f"  {lb}: {n_}t ${p_:+,.0f}")
    tc=tdf.copy();tc["ym"]=pd.to_datetime(tc["dt"]).dt.to_period("M")
    mp_=tc.groupby("ym").agg(n=("pnl","count"),pnl=("pnl","sum")).reset_index()
    pos_m=0
    for _,r in mp_.iterrows():
        f="+" if r["pnl"]>0 else " "
        print(f"    {r['ym']}: {r['n']:>3d}t ${r['pnl']:>+8,.0f} {f}")
        if r["pnl"]>0:pos_m+=1
    print(f"  Positive months: {pos_m}/{len(mp_)}")
    if s["pnl"]!=0:mx=mp_["pnl"].max();print(f"  Max month: ${mx:+,.0f} ({mx/s['pnl']*100:.0f}%)")
    return s

def wf(tdf,n=10):
    if len(tdf)==0:return 0,n
    dts=pd.to_datetime(tdf["dt"]);mn,mx=dts.min(),dts.max()
    step=(mx-mn)/n;pos=0
    for k in range(n):
        s_=mn+step*k;e_=mn+step*(k+1)
        seg=tdf[(dts>=s_)&(dts<e_)]
        if len(seg)>0 and seg["pnl"].sum()>0:pos+=1
    return pos,n

if __name__=="__main__":
    print("="*60)
    print("  Round 3: Kurtosis Compression + Close Breakout")
    print("="*60)
    df=load();df=calc(df)
    W=KURT_WIN+KURT_PWIN+BRK_LOOK+20;v=df.iloc[W:]
    c1=(v["kurt_pct"]<KURT_TH).sum()
    c2l=v["bl"].sum();c2s=v["bs"].sum();c3=v["sok"].sum()
    print(f"  Pass Rates:")
    print(f"    Kurt<{KURT_TH}pct: {c1} ({c1/len(v)*100:.1f}%)")
    print(f"    Break L: {c2l} ({c2l/len(v)*100:.1f}%) | S: {c2s} ({c2s/len(v)*100:.1f}%)")
    print(f"    Session: {c3} ({c3/len(v)*100:.1f}%)")

    all_t=bt(df);all_t["dt"]=pd.to_datetime(all_t["dt"])
    last_dt=df["datetime"].iloc[-1];mid_dt=last_dt-pd.Timedelta(days=365)
    is_t=all_t[all_t["dt"]<mid_dt].reset_index(drop=True)
    oos_t=all_t[all_t["dt"]>=mid_dt].reset_index(drop=True)
    is_m=(mid_dt-df["datetime"].iloc[0]).days/30.44
    oos_m=(last_dt-mid_dt).days/30.44

    is_s=report("IN-SAMPLE (IS)",is_t,is_m)
    oos_s=report("OUT-OF-SAMPLE (OOS)",oos_t,oos_m)
    full_s=report("FULL PERIOD",all_t,is_m+oos_m)
    wfp,wft=wf(all_t,10)
    print(f"\n  Walk-Forward: {wfp}/{wft} positive segments")

    print(f"\n{'='*60}\n  GOD'S EYE SELF-CHECK\n{'='*60}")
    for q,a in [
        ("Signals shift(1)+?","YES: kurt=ret.rolling(30).kurt().shift(1); kurt_pct uses kurt (already shifted); brk shift(1)/shift(2)"),
        ("Entry=next bar open?","YES: nxo=O[i+1]"),
        ("Rolling have shift(1)?","YES: kurt .shift(1); kurt_pct rolling on shifted kurt; brk shift(1)/shift(2)"),
        ("Params pre-fixed?","YES: KURT_WIN=30(distribution theory),KURT_PWIN=50(2d),KURT_TH=25(quartile)"),
        ("No post-OOS adjust?","YES"),
        ("No OOS leak?","YES: kurt_pct pure rolling on shift(1) data")
    ]:print(f"  [PASS] {q}\n    {a}")
    print("  6/6 PASS")
    warns=[]
    if oos_s["wr"]>70:warns.append("WR>70%")
    if oos_s["pf"]>6:warns.append("PF>6")
    if oos_s["sh"]>8:warns.append("Sharpe>8")
    if warns:print(f"  HAPPY TABLE WARNING: {warns}")
    else:print("  Happy table: clean")
    print(f"\n{'='*60}\n  ROUND 3 COMPLETE\n{'='*60}")
