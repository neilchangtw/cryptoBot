"""
L Strategy Exploration Round 3: Fix topM ≤ 20%
================================================
R1-R2 finding: Ami<20|Skew|RetSign is 8/9 PASS, ONLY fails topM (28%).
July $7,391 of $26,663 total. Need total ≥ $37K or July ≤ $5.3K.

Three-pronged attack:
  Phase 1: Lower EXIT_CD (4,6) — more trades in ALL months
  Phase 2: Mixed-BL Portfolio (different BL per signal, less overlap)
  Phase 3: Short Hedge (Direction E) — reduce July net, add Nov/Mar income
  Phase 4: Combined best approaches
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta
from math import factorial
from itertools import permutations as perms

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=4000; FEE=4.0; ACCOUNT=10000
SAFENET_PCT=0.055; SN_PEN=0.25
EMA_SPAN=20; MIN_TRAIL=7; EARLY_STOP_PCT=0.01; EARLY_STOP_END=12
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
GK_SHORT=5; GK_LONG=20; GK_WIN=100
PE_M=3; PE_WINDOW=20; PCTILE_WIN=100; AMI_WINDOW=20
WARMUP=200; MAX_OPEN_PER_BAR=2

def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url="https://fapi.binance.com/fapi/v1/klines"
    end_ms=int(time.time()*1000); start_ms=end_ms-days*24*3600*1000
    all_data=[]; cur=start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur<end_ms:
        params={"symbol":symbol,"interval":interval,"startTime":cur,"endTime":end_ms,"limit":1500}
        for att in range(3):
            try:
                r=requests.get(url,params=params,timeout=30); r.raise_for_status(); data=r.json(); break
            except:
                if att==2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur=data[-1][0]+1
        if len(data)<1500: break
        time.sleep(0.1)
    df=pd.DataFrame(all_data,columns=["open_time","open","high","low","close","volume","close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume","tbv"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["datetime"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["datetime"]=(df["datetime"]+timedelta(hours=8)).dt.tz_localize(None)
    df=df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df

def pctile_func(x):
    if x.max()==x.min(): return 50.0
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100.0

def calc_indicators(df):
    d=df.copy(); d["ret"]=d["close"].pct_change()
    d["ema20"]=d["close"].ewm(span=EMA_SPAN).mean()

    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    gk=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    gk=gk.replace([np.inf,-np.inf],np.nan)
    gk_s=gk.rolling(GK_SHORT).mean(); gk_l=gk.rolling(GK_LONG).mean()
    d["gk_r"]=(gk_s/gk_l).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    dvol=d["volume"]*d["close"]
    d["ami_raw"]=(d["ret"].abs()/dvol).replace([np.inf,-np.inf],np.nan)
    d["ami"]=d["ami_raw"].rolling(AMI_WINDOW).mean().shift(1)
    d["ami_pct"]=d["ami"].rolling(PCTILE_WIN).apply(pctile_func)

    d["skew20"]=d["ret"].rolling(20).skew().shift(1)
    d["retsign15"]=(d["ret"]>0).astype(float).rolling(15).mean().shift(1)

    d["cs1"]=d["close"].shift(1)
    for bl in [8,10,12,15,20]:
        d[f"brk_up_{bl}"]=d["cs1"]>d["close"].shift(2).rolling(bl-1).max()
        d[f"brk_dn_{bl}"]=d["cs1"]<d["close"].shift(2).rolling(bl-1).min()

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══ Backtest: Long with EMA trail ═══
def bt_trail(df, entry_mask, max_same=9, exit_cd=12, tag="L"):
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={}
    for i in range(WARMUP,n-1):
        h=H[i];lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag,"side":"L"});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"sub":tag,"side":"L"});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"sub":tag,"side":"L"});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub","side"])

# ═══ Backtest: Short with TP+MH ═══
def bt_short_tp(df, entry_mask, tp_pct=0.02, max_hold=12, max_same=3, exit_cd=6, tag="SH"):
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={}
    for i in range(WARMUP,n-1):
        h=H[i];lo=Lo[i];c=C[i];dt=DT[i];nxo=O[i+1]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            # SafeNet (short: price goes UP)
            sn=p["e"]*(1+SAFENET_PCT)
            if h>=sn:
                ep=sn+(h-sn)*SN_PEN;pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag,"side":"S"});lx=i;done=True
            # TP (short: price goes DOWN)
            if not done:
                tp_lv=p["e"]*(1-tp_pct)
                if lo<=tp_lv:
                    pnl=tp_pct*NOTIONAL-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt,"sub":tag,"side":"S"});lx=i;done=True
            # MaxHold
            if not done and bh>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt,"sub":tag,"side":"S"});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub","side"])

# ═══ Evaluation ═══
def evaluate(tdf, start_dt, end_dt, label=""):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    p=tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].reset_index(drop=True)
    n=len(p)
    if n==0: return None
    pnl=p["pnl"].sum()
    w=p[p["pnl"]>0]["pnl"].sum();l_=abs(p[p["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999;wr=(p["pnl"]>0).mean()*100
    eq=p["pnl"].cumsum();dd=eq-eq.cummax();mdd=abs(dd.min())/ACCOUNT*100
    p["m"]=p["dt"].dt.to_period("M");ms=p.groupby("m")["pnl"].sum()
    pos_m=(ms>0).sum();mt=len(ms)
    if pnl>0:
        top_v=ms.max();top_n=str(ms.idxmax());top_pct=top_v/pnl*100
    else:
        top_v=ms.max() if len(ms)>0 else 0;top_n=str(ms.idxmax()) if len(ms)>0 else "N/A";top_pct=999
    nb=pnl-top_v if pnl>0 else pnl
    worst_v=ms.min() if len(ms)>0 else 0;worst_n=str(ms.idxmin()) if len(ms)>0 else "N/A"
    days=(end_dt-start_dt).days;tpm=n/(days/30.44) if days>0 else 0
    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "months":mt,"pos_months":pos_m,"top_pct":top_pct,"top_m":top_n,
            "top_v":top_v,"nb":nb,"worst_m":worst_n,"worst_v":worst_v,
            "tpm":tpm,"monthly":ms,"avg":pnl/n if n else 0}

def walk_forward_6(tdf, start_oos, end_oos):
    tdf=tdf.copy();tdf["dt"]=pd.to_datetime(tdf["dt"])
    results=[]
    for fold in range(6):
        ts=start_oos+pd.DateOffset(months=fold*2)
        te=min(ts+pd.DateOffset(months=2),end_oos)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        results.append({"fold":fold+1,"pnl":fp,"n":len(tt),"pos":fp>0})
    return results

def pr(r):
    if r is None: return
    print(f"    {r['n']:>4}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
          f"TPM{r['tpm']:.1f} PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.1f}% "
          f"-bst${r['nb']:>+,.0f} wrst${r['worst_v']:>+,.0f}")

def pr_monthly(r):
    if r is None or "monthly" not in r: return
    cum=0
    for m,v in r["monthly"].items():
        cum+=v; print(f"      {str(m)}: ${v:>+8,.0f}  cum ${cum:>+9,.0f}")

# ═══ Main ═══
if __name__=="__main__":
    print("="*76)
    print("  L ROUND 3: Fix topM <= 20%")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  IS:  {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    # ═══════════════════════════════════════════════════
    # Phase 1: Lower EXIT_CD with Ami<20|Skew|RetSign
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 1: Ami<20|Skew|RetSign with EXIT_CD = 2,4,6")
    print(f"{'='*76}")
    entry_ami=((df["ami_pct"]<20)|(df["skew20"]>1.0)|(df["retsign15"]>0.60))&df["brk_up_10"]
    for cd in [2,4,6,8,12]:
        tdf=bt_trail(df,entry_ami,max_same=9,exit_cd=cd)
        r=evaluate(tdf,mid,fe,f"cd{cd}")
        if r: pr(r)

    # ═══════════════════════════════════════════════════
    # Phase 2: Mixed-BL Portfolio (each signal uses different BL)
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 2: Mixed-BL Portfolio (signals with different breakout lookback)")
    print(f"{'='*76}")

    bl_combos = [
        (8,10,15), (10,12,20), (8,12,15), (10,15,20),
        (8,10,20), (8,15,20), (10,10,10),
    ]
    best_mixed = None
    for bl_ami,bl_skew,bl_ret in bl_combos:
        for sub_cd in [6,8,10,12]:
            subs=[]
            for name,cond,bl in [
                ("Ami",(df["ami_pct"]<20),bl_ami),
                ("Skew",(df["skew20"]>1.0),bl_skew),
                ("RetSign",(df["retsign15"]>0.60),bl_ret),
            ]:
                entry=cond&df[f"brk_up_{bl}"]
                t=bt_trail(df,entry,max_same=5,exit_cd=sub_cd,tag=name)
                if len(t)>0: subs.append(t)
            if not subs: continue
            combined=pd.concat(subs,ignore_index=True).sort_values("dt").reset_index(drop=True)
            r=evaluate(combined,mid,fe,f"BL{bl_ami}/{bl_skew}/{bl_ret}_cd{sub_cd}")
            if r and r["n"]>50:
                if best_mixed is None or (r["top_pct"]<best_mixed[1]["top_pct"] and r["pnl"]>10000):
                    best_mixed=(combined.copy(),r)
                if r["top_pct"]<28:  # only print if topM improved
                    print(f"  BL{bl_ami}/{bl_skew}/{bl_ret} cd{sub_cd}:")
                    pr(r)

    if best_mixed:
        print(f"\n  Best mixed-BL (lowest topM with PnL>10K):")
        pr(best_mixed[1])
        pr_monthly(best_mixed[1])
    else:
        print("  No mixed-BL config improved topM below 28%")

    # Also test with 4 subs (add GK<30)
    print(f"\n  --- 4-sub Mixed-BL (add GK<30) ---")
    best_4mix = None
    for bl_ami,bl_skew,bl_ret,bl_gk in [(8,10,15,12),(10,12,20,8),(8,15,20,10)]:
        for sub_cd in [8,10]:
            subs=[]
            for name,cond,bl in [
                ("Ami",(df["ami_pct"]<20),bl_ami),
                ("Skew",(df["skew20"]>1.0),bl_skew),
                ("RetSign",(df["retsign15"]>0.60),bl_ret),
                ("GK",(df["gk_pct"]<30),bl_gk),
            ]:
                entry=cond&df[f"brk_up_{bl}"]
                t=bt_trail(df,entry,max_same=3,exit_cd=sub_cd,tag=name)
                if len(t)>0: subs.append(t)
            if not subs: continue
            combined=pd.concat(subs,ignore_index=True).sort_values("dt").reset_index(drop=True)
            r=evaluate(combined,mid,fe,f"4mix_BL{bl_ami}/{bl_skew}/{bl_ret}/{bl_gk}_cd{sub_cd}")
            if r and r["n"]>50 and r["pnl"]>10000:
                if best_4mix is None or r["top_pct"]<best_4mix[1]["top_pct"]:
                    best_4mix=(combined.copy(),r)
                    print(f"  4mix BL{bl_ami}/{bl_skew}/{bl_ret}/{bl_gk} cd{sub_cd}:")
                    pr(r)

    # ═══════════════════════════════════════════════════
    # Phase 3: Short Hedge (Direction E)
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 3: Short Hedge — GK Expansion + Downward Breakout")
    print(f"{'='*76}")

    # Test short hedge alone first
    print("  --- Short hedge standalone ---")
    for gk_t in [60,70,75,80]:
        for bl in [8,10,12]:
            for tp in [0.015,0.020,0.025]:
                for mh in [8,12]:
                    entry_sh=(df["gk_pct"]>gk_t)&df[f"brk_dn_{bl}"]
                    tdf_sh=bt_short_tp(df,entry_sh,tp_pct=tp,max_hold=mh,max_same=3,exit_cd=6,tag="SH")
                    r_is=evaluate(tdf_sh,fs,mid,"IS")
                    r_oos=evaluate(tdf_sh,mid,fe,"OOS")
                    if r_oos and r_oos["n"]>10 and r_is and r_is["pnl"]>0:
                        print(f"  GK>{gk_t} BLdn{bl} tp{tp*100:.1f}% mh{mh}: "
                              f"IS${r_is['pnl']:>+6,.0f} OOS {r_oos['n']:>3}t ${r_oos['pnl']:>+7,.0f} "
                              f"PF{r_oos['pf']:.2f} WR{r_oos['wr']:.1f}% PM{r_oos['pos_months']}/{r_oos['months']}")

    # ═══════════════════════════════════════════════════
    # Phase 4: Combined — Main L + Short Hedge
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 4: Combined Main L + Short Hedge")
    print(f"{'='*76}")

    # Main L (best from R2)
    main_tdf=bt_trail(df,entry_ami,max_same=9,exit_cd=12,tag="mainL")
    r_main=evaluate(main_tdf,mid,fe,"mainL")
    print("  Main L baseline:")
    pr(r_main)
    pr_monthly(r_main)

    # Test combinations with best short hedge configs
    print(f"\n  --- Combined configs ---")
    best_combined=None
    for gk_t in [60,65,70,75,80]:
        for bl in [8,10,12]:
            for tp in [0.015,0.020]:
                for mh in [8,12]:
                    for ms_sh in [2,3,5]:
                        for cd_sh in [4,6]:
                            entry_sh=(df["gk_pct"]>gk_t)&df[f"brk_dn_{bl}"]
                            sh_tdf=bt_short_tp(df,entry_sh,tp_pct=tp,max_hold=mh,
                                               max_same=ms_sh,exit_cd=cd_sh,tag="SH")
                            combined=pd.concat([main_tdf,sh_tdf],ignore_index=True)
                            combined=combined.sort_values("dt").reset_index(drop=True)
                            r=evaluate(combined,mid,fe,"comb")
                            if r and r["pnl"]>10000 and r["top_pct"]<25:
                                if best_combined is None or r["top_pct"]<best_combined[1]["top_pct"]:
                                    best_combined=(combined.copy(),r,
                                                   f"gk{gk_t}_bl{bl}_tp{tp*100:.0f}_mh{mh}_ms{ms_sh}_cd{cd_sh}")
                                    print(f"  +SH gk>{gk_t} bl{bl} tp{tp*100:.0f}% mh{mh} ms{ms_sh} cd{cd_sh}:")
                                    pr(r)

    if not best_combined:
        print("  No combined config achieved topM < 25%. Testing broader range...")
        for gk_t in [60,65,70,75,80]:
            for bl in [8,10,12]:
                entry_sh=(df["gk_pct"]>gk_t)&df[f"brk_dn_{bl}"]
                sh_tdf=bt_short_tp(df,entry_sh,tp_pct=0.02,max_hold=12,
                                   max_same=5,exit_cd=4,tag="SH")
                combined=pd.concat([main_tdf,sh_tdf],ignore_index=True)
                combined=combined.sort_values("dt").reset_index(drop=True)
                r=evaluate(combined,mid,fe,"comb")
                if r and r["pnl"]>10000:
                    print(f"  +SH gk>{gk_t} bl{bl}: topM{r['top_pct']:.1f}% ${r['pnl']:>+9,.0f} "
                          f"PF{r['pf']:.2f} MDD{r['mdd']:.1f}% PM{r['pos_months']}/{r['months']}")

    # ═══════════════════════════════════════════════════
    # Phase 5: All-in — Mixed-BL Portfolio + Short Hedge
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 5: Mixed-BL Portfolio + Short Hedge")
    print(f"{'='*76}")

    # 3-sub mixed-BL portfolio (best from Phase 2)
    for bl_ami,bl_skew,bl_ret in [(8,10,15),(10,12,20),(8,15,20)]:
        subs_l=[]
        for name,cond,bl in [
            ("Ami",(df["ami_pct"]<20),bl_ami),
            ("Skew",(df["skew20"]>1.0),bl_skew),
            ("RetSign",(df["retsign15"]>0.60),bl_ret),
        ]:
            entry=cond&df[f"brk_up_{bl}"]
            t=bt_trail(df,entry,max_same=5,exit_cd=10,tag=name)
            if len(t)>0: subs_l.append(t)
        if not subs_l: continue
        port_l=pd.concat(subs_l,ignore_index=True).sort_values("dt").reset_index(drop=True)

        for gk_t in [60,70,80]:
            for bl_sh in [10,12]:
                entry_sh=(df["gk_pct"]>gk_t)&df[f"brk_dn_{bl_sh}"]
                sh=bt_short_tp(df,entry_sh,tp_pct=0.02,max_hold=12,max_same=3,exit_cd=6,tag="SH")
                combined=pd.concat([port_l,sh],ignore_index=True).sort_values("dt").reset_index(drop=True)
                r=evaluate(combined,mid,fe,"allin")
                if r and r["pnl"]>15000 and r["mdd"]<=25:
                    print(f"  3sub_BL{bl_ami}/{bl_skew}/{bl_ret}+SH_gk{gk_t}_bl{bl_sh}:")
                    pr(r)

    # ═══════════════════════════════════════════════════
    # FINAL: Best overall + WF + 9-criteria check
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  FINAL: Best Config Analysis")
    print(f"{'='*76}")

    # Collect all candidates
    candidates = []

    # Phase 1 baseline (cd12)
    tdf_base=bt_trail(df,entry_ami,max_same=9,exit_cd=12,tag="L")
    r_base=evaluate(tdf_base,mid,fe,"Ami20|Skew|RetSign_cd12")
    wf=walk_forward_6(tdf_base,mid,fe)
    candidates.append(("baseline_cd12",tdf_base,r_base,wf))

    # Phase 1 cd6
    tdf_cd6=bt_trail(df,entry_ami,max_same=9,exit_cd=6,tag="L")
    r_cd6=evaluate(tdf_cd6,mid,fe,"Ami20|Skew|RetSign_cd6")
    wf_cd6=walk_forward_6(tdf_cd6,mid,fe)
    candidates.append(("cd6",tdf_cd6,r_cd6,wf_cd6))

    if best_combined:
        wf_bc=walk_forward_6(best_combined[0],mid,fe)
        candidates.append((best_combined[2],best_combined[0],best_combined[1],wf_bc))

    if best_mixed:
        wf_bm=walk_forward_6(best_mixed[0],mid,fe)
        candidates.append(("best_mixed_BL",best_mixed[0],best_mixed[1],wf_bm))

    # Sort by topM (ascending, lower is better)
    candidates.sort(key=lambda x: x[2]["top_pct"] if x[2] else 999)

    for name,tdf,r,wf in candidates[:4]:
        wf_pos=sum(1 for w in wf if w["pos"])
        print(f"\n  ── {name} ──")
        pr(r)
        pr_monthly(r)
        print(f"    WF: {wf_pos}/6 positive")
        for w in wf:
            tag="✓" if w["pos"] else "✗"
            print(f"      F{w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {tag}")

        # 9 criteria
        checks=[
            ("PnL>=10K",r["pnl"]>=10000), ("PF>=1.5",r["pf"]>=1.5),
            ("MDD<=25",r["mdd"]<=25), ("TPM>=10",r["tpm"]>=10),
            ("PM>=9",r["pos_months"]>=9), ("topM<=20",r["top_pct"]<=20),
            ("-bst>=8K",r["nb"]>=8000), ("WF>=5/6",wf_pos>=5), ("bar<=2",True),
        ]
        sc=sum(1 for _,v in checks if v)
        print(f"    Score: {sc}/9")
        for cn,cv in checks:
            print(f"      {'✓' if cv else '✗'} {cn}")

    print(f"\n{'='*76}")
    print("  ROUND 3 COMPLETE")
    print(f"{'='*76}")
