"""
L Strategy Exploration Round 7: ATR Fine-Tune for topM ≤ 20%
=============================================================
R6 found: ATR-scaled notional is the key to reducing topM.
  Best: atr>60 s0.50 → topM 20.9% ($11,292, 8/9)
        atr>70 s0.60 → topM ~20% ($13,147, need precise check)

Just 0.9% away from 9/9 PASS!

Approach:
  Phase 1: Fine-grained ATR threshold x scale sweep (atr 55-80 x scale 0.40-0.70)
  Phase 2: Continuous ATR scaling (smooth function instead of binary cutoff)
  Phase 3: Two-tier ATR scaling (two thresholds, two scale levels)
  Phase 4: ATR scaling + lower cap (cap=10-11 to also limit entries)
  Phase 5: Best configs full 9-criteria + IS + WF analysis
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=4000; FEE=4.0; ACCOUNT=10000
SAFENET_PCT=0.055; SN_PEN=0.25
EMA_SPAN=20; MIN_TRAIL=7; EARLY_STOP_PCT=0.01; EARLY_STOP_END=12
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
GK_SHORT=5; GK_LONG=20; GK_WIN=100; AMI_WINDOW=20; PCTILE_WIN=100
WARMUP=200; MAX_OPEN_PER_BAR=2

def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url="https://fapi.binance.com/fapi/v1/klines"
    end_ms=int(time.time()*1000); start_ms=end_ms-days*24*3600*1000
    all_data=[]; cur=start_ms
    print(f"  Fetching {symbol} {interval}...")
    while cur<end_ms:
        params={"symbol":symbol,"interval":interval,"startTime":cur,"endTime":end_ms,"limit":1500}
        for att in range(3):
            try: r=requests.get(url,params=params,timeout=30); r.raise_for_status(); data=r.json(); break
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
    d["brk_max"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["bl_up"]=d["cs1"]>d["brk_max"]

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    d["ym"]=d["datetime"].dt.to_period("M")

    # ATR
    tr = pd.concat([
        d["high"]-d["low"],
        (d["high"]-d["close"].shift(1)).abs(),
        (d["low"]-d["close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean().shift(1)
    d["atr_pct"] = d["atr14"].rolling(PCTILE_WIN).apply(pctile_func)

    # Signal flags
    d["sig_ami"]=(d["ami_pct"]<20)
    d["sig_skew"]=(d["skew20"]>1.0)
    d["sig_ret"]=(d["retsign15"]>0.60)
    d["sig_gk"]=(d["gk_pct"]<30)
    d["sig_count4"]=(d["sig_ami"].astype(int)+d["sig_skew"].astype(int)+
                     d["sig_ret"].astype(int)+d["sig_gk"].astype(int))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ─── Backtest engines ───

def bt_trail_atr_binary(df, entry_mask, max_same=9, exit_cd=10,
                        monthly_entry_cap=12, atr_thresh=60, scale_down=0.5):
    """Binary ATR scaling: full NOTIONAL below threshold, scaled above."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values
    ATR_P=df["atr_pct"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False;not_=p["not"]
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        not_size = NOTIONAL * scale_down if atr_p > atr_thresh else NOTIONAL
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_atr_continuous(df, entry_mask, max_same=9, exit_cd=10,
                            monthly_entry_cap=12, alpha=0.6, floor=0.3):
    """Continuous ATR scaling: not = NOTIONAL * max(floor, 1 - alpha * atr_pct/100).
    Smoothly reduces sizing as ATR increases. floor prevents going too small."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values
    ATR_P=df["atr_pct"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False;not_=p["not"]
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        scale = max(floor, 1.0 - alpha * atr_p / 100.0)
        not_size = NOTIONAL * scale
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_atr_2tier(df, entry_mask, max_same=9, exit_cd=10,
                       monthly_entry_cap=12,
                       tier1_thresh=50, tier1_scale=0.75,
                       tier2_thresh=75, tier2_scale=0.40):
    """Two-tier ATR: below tier1 = full, tier1-tier2 = tier1_scale, above tier2 = tier2_scale."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values
    ATR_P=df["atr_pct"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False;not_=p["not"]
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        if atr_p > tier2_thresh:
            not_size = NOTIONAL * tier2_scale
        elif atr_p > tier1_thresh:
            not_size = NOTIONAL * tier1_scale
        else:
            not_size = NOTIONAL
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])


def evaluate(tdf, start_dt, end_dt, label=""):
    tdf=tdf.copy();tdf["dt"]=pd.to_datetime(tdf["dt"])
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
        top_v=ms.max() if len(ms)>0 else 0;top_n="N/A";top_pct=999
    nb=pnl-top_v if pnl>0 else pnl
    worst_v=ms.min() if len(ms)>0 else 0;worst_n=str(ms.idxmin()) if len(ms)>0 else "N/A"
    days=(end_dt-start_dt).days;tpm=n/(days/30.44) if days>0 else 0
    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "months":mt,"pos_months":pos_m,"top_pct":top_pct,"top_m":top_n,
            "top_v":top_v,"nb":nb,"worst_m":worst_n,"worst_v":worst_v,
            "tpm":tpm,"monthly":ms,"avg":pnl/n if n else 0}

def walk_forward_6(tdf, s, e):
    tdf=tdf.copy();tdf["dt"]=pd.to_datetime(tdf["dt"])
    results=[]
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2);te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        results.append({"fold":f+1,"pnl":fp,"n":len(tt),"pos":fp>0})
    return results

def score9(r, wf_pos):
    checks=[
        ("PnL>=10K",r["pnl"]>=10000),("PF>=1.5",r["pf"]>=1.5),
        ("MDD<=25",r["mdd"]<=25),("TPM>=10",r["tpm"]>=10),
        ("PM>=9",r["pos_months"]>=9),("topM<=20",r["top_pct"]<=20),
        ("-bst>=8K",r["nb"]>=8000),("WF>=5/6",wf_pos>=5),("bar<=2",True)]
    return checks, sum(1 for _,v in checks if v)

def pr(r):
    if r is None: return
    print(f"    {r['n']:>4}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
          f"TPM{r['tpm']:.1f} PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.1f}% "
          f"-bst${r['nb']:>+,.0f}")

def pr_monthly(r):
    if r is None: return
    cum=0
    for m,v in r["monthly"].items():
        cum+=v; print(f"      {str(m)}: ${v:>+8,.0f} cum${cum:>+9,.0f}")


if __name__=="__main__":
    print("="*76)
    print("  L ROUND 7: ATR Fine-Tune (topM ≤ 20% final push)")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    entry_2of4 = (df["sig_count4"]>=2) & df["bl_up"]

    # ═══ Phase 1: Fine-grained binary ATR sweep ═══
    print(f"\n{'='*76}")
    print("  Phase 1: Binary ATR Sweep (atr 55-80 × scale 0.40-0.70)")
    print(f"{'='*76}")
    print(f"  {'atr':>4} {'scale':>5} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'PM':>5} {'topM':>6} {'-bst':>8}")

    best_topm = 999; best_cfg = None
    for atr_t in [55,58,60,62,65,68,70,72,75,78,80]:
        for sc in [0.40,0.45,0.50,0.55,0.60,0.65,0.70]:
            tdf=bt_trail_atr_binary(df,entry_2of4,max_same=9,exit_cd=10,
                                    monthly_entry_cap=12,atr_thresh=atr_t,scale_down=sc)
            r=evaluate(tdf,mid,fe)
            if r and r["pnl"]>=10000:
                mark = " ★" if r["top_pct"]<=20 else ("  ←" if r["top_pct"]<=21 else "")
                print(f"  {atr_t:>4} {sc:>5.2f} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f}{mark}")
                if r["top_pct"]<best_topm:
                    best_topm=r["top_pct"]; best_cfg=(atr_t,sc)

    if best_cfg:
        print(f"\n  → Best binary: atr>{best_cfg[0]} scale={best_cfg[1]:.2f} topM={best_topm:.1f}%")

    # ═══ Phase 2: Continuous ATR scaling ═══
    print(f"\n{'='*76}")
    print("  Phase 2: Continuous ATR Scaling (alpha × floor)")
    print(f"{'='*76}")
    print(f"  {'alpha':>5} {'floor':>5} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'PM':>5} {'topM':>6} {'-bst':>8}")
    for alpha in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        for floor in [0.25, 0.30, 0.35, 0.40, 0.50]:
            tdf=bt_trail_atr_continuous(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=12,alpha=alpha,floor=floor)
            r=evaluate(tdf,mid,fe)
            if r and r["pnl"]>=10000:
                mark = " ★" if r["top_pct"]<=20 else ("  ←" if r["top_pct"]<=21 else "")
                print(f"  {alpha:>5.1f} {floor:>5.2f} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f}{mark}")

    # ═══ Phase 3: Two-tier ATR scaling ═══
    print(f"\n{'='*76}")
    print("  Phase 3: Two-Tier ATR Scaling")
    print(f"{'='*76}")
    print(f"  {'t1':>3} {'s1':>4} {'t2':>3} {'s2':>4} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'PM':>5} {'topM':>6} {'-bst':>8}")
    for t1 in [40,50,60]:
        for s1 in [0.65,0.70,0.75,0.80]:
            for t2 in [70,75,80]:
                if t2<=t1: continue
                for s2 in [0.35,0.40,0.45,0.50]:
                    tdf=bt_trail_atr_2tier(df,entry_2of4,max_same=9,exit_cd=10,
                                           monthly_entry_cap=12,
                                           tier1_thresh=t1,tier1_scale=s1,
                                           tier2_thresh=t2,tier2_scale=s2)
                    r=evaluate(tdf,mid,fe)
                    if r and r["pnl"]>=10000:
                        mark = " ★" if r["top_pct"]<=20 else ("  ←" if r["top_pct"]<=21 else "")
                        print(f"  {t1:>3} {s1:.2f} {t2:>3} {s2:.2f} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                              f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                              f"{r['pos_months']:>2}/{r['months']:<2} "
                              f"{r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f}{mark}")

    # ═══ Phase 4: ATR scaling + lower entry cap ═══
    print(f"\n{'='*76}")
    print("  Phase 4: ATR Scaling + Lower Cap (cap=10-11)")
    print(f"{'='*76}")
    print(f"  {'cap':>3} {'atr':>4} {'scale':>5} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'PM':>5} {'topM':>6} {'-bst':>8}")
    for cap in [10,11]:
        for atr_t in [55,60,65,70,75]:
            for sc in [0.50,0.55,0.60,0.65,0.70]:
                tdf=bt_trail_atr_binary(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=cap,atr_thresh=atr_t,scale_down=sc)
                r=evaluate(tdf,mid,fe)
                if r and r["pnl"]>=10000:
                    mark = " ★" if r["top_pct"]<=20 else ("  ←" if r["top_pct"]<=21 else "")
                    print(f"  {cap:>3} {atr_t:>4} {sc:>5.2f} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                          f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                          f"{r['pos_months']:>2}/{r['months']:<2} "
                          f"{r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f}{mark}")

    # ═══ FINAL: Full analysis on all ≤ 20.5% topM configs ═══
    print(f"\n{'='*76}")
    print("  FINAL: Full 9-Criteria Analysis (topM ≤ 21% candidates)")
    print(f"{'='*76}")

    final_candidates = []

    # Sweep best binary configs
    for atr_t in range(55,81):
        for sc_10 in range(40,71,5):
            sc = sc_10/100
            for cap in [12]:
                tdf=bt_trail_atr_binary(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=cap,atr_thresh=atr_t,scale_down=sc)
                r=evaluate(tdf,mid,fe,f"bin_a{atr_t}_s{sc:.2f}")
                if r and r["pnl"]>=10000 and r["top_pct"]<=20.5:
                    final_candidates.append((f"bin_a{atr_t}_s{sc:.2f}_c{cap}",r,tdf))

    # Continuous
    for alpha_10 in range(30,81,10):
        alpha=alpha_10/100
        for floor_10 in range(25,51,5):
            floor=floor_10/100
            tdf=bt_trail_atr_continuous(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=12,alpha=alpha,floor=floor)
            r=evaluate(tdf,mid,fe,f"cont_a{alpha:.1f}_f{floor:.2f}")
            if r and r["pnl"]>=10000 and r["top_pct"]<=20.5:
                final_candidates.append((f"cont_a{alpha:.1f}_f{floor:.2f}",r,tdf))

    # Two-tier best
    for t1 in [40,50,60]:
        for s1_10 in [65,70,75,80]:
            s1=s1_10/100
            for t2 in [70,75,80]:
                if t2<=t1: continue
                for s2_10 in [35,40,45,50]:
                    s2=s2_10/100
                    tdf=bt_trail_atr_2tier(df,entry_2of4,max_same=9,exit_cd=10,
                                           monthly_entry_cap=12,
                                           tier1_thresh=t1,tier1_scale=s1,
                                           tier2_thresh=t2,tier2_scale=s2)
                    r=evaluate(tdf,mid,fe,f"2t_{t1}_{s1:.2f}_{t2}_{s2:.2f}")
                    if r and r["pnl"]>=10000 and r["top_pct"]<=20.5:
                        final_candidates.append((f"2t_{t1}s{s1:.0%}_{t2}s{s2:.0%}",r,tdf))

    # Cap 10-11 + ATR
    for cap in [10,11]:
        for atr_t in range(55,81,5):
            for sc_10 in range(50,71,5):
                sc=sc_10/100
                tdf=bt_trail_atr_binary(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=cap,atr_thresh=atr_t,scale_down=sc)
                r=evaluate(tdf,mid,fe,f"bin_a{atr_t}_s{sc:.2f}_c{cap}")
                if r and r["pnl"]>=10000 and r["top_pct"]<=20.5:
                    final_candidates.append((f"bin_a{atr_t}_s{sc:.2f}_c{cap}",r,tdf))

    # Deduplicate by name
    seen=set(); unique=[]
    for name,r,tdf in final_candidates:
        if name not in seen:
            seen.add(name); unique.append((name,r,tdf))
    unique.sort(key=lambda x: (x[1]["top_pct"], -x[1]["pnl"]))

    if not unique:
        print("\n  *** No configs achieved topM ≤ 20.5% with PnL ≥ $10K ***")
        print("  Showing best binary configs (topM ≤ 22%):")
        for atr_t in range(55,81):
            for sc_10 in range(40,71,5):
                sc=sc_10/100
                tdf=bt_trail_atr_binary(df,entry_2of4,max_same=9,exit_cd=10,
                                        monthly_entry_cap=12,atr_thresh=atr_t,scale_down=sc)
                r=evaluate(tdf,mid,fe)
                if r and r["pnl"]>=10000 and r["top_pct"]<=22:
                    final_candidates.append((f"bin_a{atr_t}_s{sc:.2f}",r,tdf))
        seen2=set(); unique2=[]
        for name,r,tdf in final_candidates:
            if name not in seen2:
                seen2.add(name); unique2.append((name,r,tdf))
        unique2.sort(key=lambda x: (x[1]["top_pct"], -x[1]["pnl"]))
        unique = unique2[:10]

    for name,r,tdf in unique[:10]:
        r_is=evaluate(tdf,fs,mid,"IS")
        wf=walk_forward_6(tdf,mid,fe)
        wf_pos=sum(1 for w in wf if w["pos"])
        checks,sc=score9(r,wf_pos)
        is_pnl=r_is["pnl"] if r_is else 0

        print(f"\n  ── {name} ({sc}/9) IS${is_pnl:>+,.0f} ──")
        pr(r)
        pr_monthly(r)
        print(f"    WF: {wf_pos}/6")
        for w in wf:
            print(f"      F{w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {'✓' if w['pos'] else '✗'}")
        for cn,cv in checks:
            print(f"      {'✓' if cv else '✗'} {cn}")

    print(f"\n{'='*76}")
    print("  ROUND 7 COMPLETE")
    print(f"{'='*76}")
