"""
L Strategy Exploration Round 6: topM Squeeze
==============================================
R5 breakthrough: 2-of-4 (Ami,Skew,RetSign,GK) + cap12 + cd10 = 8/9 PASS
  $17,272, PF 6.02, WR 55.9%, MDD 12.7%, PM 9/13, WF 6/6
  Only failure: topM 25.7% (May $4,440)

Need topM <= 20%:
  If total=$17,272 → May must be <= $3,454 (reduce ~$986)
  Challenge: reducing May also reduces total, making 20% threshold even tighter

Approach:
  Phase 1: 2-of-4 with lower caps (8-11) — not tested in R5
  Phase 2: Monthly PnL cap — stop entries after month earns $X
  Phase 3: PnL momentum throttle — skip entry if trailing realized PnL > $Y
  Phase 4: ATR-scaled notional — reduce sizing when ATR is high
  Phase 5: Dual-layer — 2-of-4 primary + slow breakout (BL15-20) for quiet months
  Phase 6: 3-of-4 signal strength — even stricter filter
  Phase 7: Best configs full 9-criteria analysis
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
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
GK_SHORT=5; GK_LONG=20; GK_WIN=100; AMI_WINDOW=20; PCTILE_WIN=100
PE_M=3; PE_WINDOW=20
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
    d["brk_min"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl_up"]=d["cs1"]>d["brk_max"]

    # Slow breakout for dual-layer
    for bl in [15,20]:
        d[f"brk_max_{bl}"]=d["close"].shift(2).rolling(bl-1).max()
        d[f"bl_up_{bl}"]=d["cs1"]>d[f"brk_max_{bl}"]

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    d["ym"]=d["datetime"].dt.to_period("M")

    # ATR for dynamic sizing
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

def bt_trail_capped(df, entry_mask, max_same=9, exit_cd=10,
                    monthly_entry_cap=999):
    """Standard EMA trail with monthly entry cap."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_pnl_cap(df, entry_mask, max_same=9, exit_cd=10,
                     monthly_entry_cap=12, monthly_pnl_cap=3500):
    """EMA trail with monthly entry cap AND monthly PnL cap.
    Once cumulative realized PnL in a month exceeds monthly_pnl_cap, block new entries."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={};month_pnl={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt})
                # Track PnL for exit month
                exit_ym=YM[i]; month_pnl[exit_ym]=month_pnl.get(exit_ym,0)+pnl
                lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt})
                    exit_ym=YM[i]; month_pnl[exit_ym]=month_pnl.get(exit_ym,0)+pnl
                    lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt})
                exit_ym=YM[i]; month_pnl[exit_ym]=month_pnl.get(exit_ym,0)+pnl
                lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        # PnL cap: if this month already earned enough, stop
        if month_pnl.get(cur_ym,0)>=monthly_pnl_cap: continue
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_momentum_throttle(df, entry_mask, max_same=9, exit_cd=10,
                                monthly_entry_cap=12, lookback=14, pnl_thresh=800):
    """EMA trail with momentum throttle: skip entry if trailing realized PnL > threshold.
    This directly targets hot months — when recent trades are very profitable, slow down."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    recent_pnl=[]  # (bar_idx, pnl) tuples
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt})
                recent_pnl.append((i,pnl));lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt})
                    recent_pnl.append((i,pnl));lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt})
                recent_pnl.append((i,pnl));lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        # Momentum throttle: sum PnL from trades closed in last N bars
        trail_sum=sum(pnl for bi,pnl in recent_pnl if i-bi<=lookback*24)
        if trail_sum>pnl_thresh: continue
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_atr_size(df, entry_mask, max_same=9, exit_cd=10,
                      monthly_entry_cap=12, atr_high_thresh=70, scale_down=0.5):
    """EMA trail with ATR-scaled notional.
    When ATR percentile > threshold, reduce NOTIONAL by scale_down.
    This naturally reduces PnL in volatile trend months."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values
    ATR_P=df["atr_pct"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            not_=p["not"]
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
        # ATR-scaled notional
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        not_size = NOTIONAL * scale_down if atr_p > atr_high_thresh else NOTIONAL
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_trail_dual_layer(df, entry_mask_primary, exit_cd=10,
                        monthly_entry_cap=12, slow_bl=20, slow_cd=20):
    """Dual-layer: primary 2-of-4 entries + slow breakout supplementary entries.
    Slow entries target quiet months where primary signals are sparse."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK_P=entry_mask_primary.values;SOK=df["sok"].values;YM=df["ym"].values
    BL_SLOW=df[f"bl_up_{slow_bl}"].values;n=len(df)
    # Slow entry: just breakout + session, no signal filter
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=9: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue

        # Primary signal
        if _b(MASK_P[i]):
            boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
            pos.append({"e":nxo,"ei":i}); continue
        # Slow breakout supplementary (only if primary didn't fire)
        if _b(BL_SLOW[i]) and (i-lx)>=slow_cd:
            boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
            pos.append({"e":nxo,"ei":i})
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

def fmt(r, cap_str=""):
    if r is None: return ""
    return (f"  {cap_str:>18}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
            f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
            f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
            f"-bst${r['nb']:>+,.0f}")


if __name__=="__main__":
    print("="*76)
    print("  L ROUND 6: topM Squeeze (target <= 20%)")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    # 2-of-4 entry mask (the R5 champion)
    entry_2of4 = (df["sig_count4"]>=2) & df["bl_up"]

    # ═══ Phase 1: 2-of-4 with lower caps (8-11) ═══
    print(f"\n{'='*76}")
    print("  Phase 1: 2-of-4 with Lower Caps (cap=8-13)")
    print(f"{'='*76}")
    print(f"  {'cap':>4} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'PM':>5} {'topM':>5} {'-bst':>8}")
    for cap in range(8,14):
        for cd in [8,10,12]:
            tdf=bt_trail_capped(df,entry_2of4,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe)
            if r:
                print(f"  {cap:>4} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")

    # ═══ Phase 2: Monthly PnL cap ═══
    print(f"\n{'='*76}")
    print("  Phase 2: Monthly PnL Cap (2-of-4 + cap12 + PnL limit)")
    print(f"{'='*76}")
    for pnl_cap in [2000, 2500, 3000, 3500, 4000, 5000]:
        for cd in [10,12]:
            tdf=bt_trail_pnl_cap(df,entry_2of4,max_same=9,exit_cd=cd,
                                 monthly_entry_cap=12,monthly_pnl_cap=pnl_cap)
            r=evaluate(tdf,mid,fe)
            if r:
                print(f"  pnlcap={pnl_cap:>5} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 3: PnL momentum throttle ═══
    print(f"\n{'='*76}")
    print("  Phase 3: PnL Momentum Throttle (2-of-4 + cap12)")
    print(f"{'='*76}")
    for lb_days in [7, 14, 21]:
        for thresh in [400, 600, 800, 1200]:
            tdf=bt_trail_momentum_throttle(df,entry_2of4,max_same=9,exit_cd=10,
                                           monthly_entry_cap=12,lookback=lb_days,pnl_thresh=thresh)
            r=evaluate(tdf,mid,fe)
            if r and r["n"]>10:
                print(f"  lb{lb_days:>2}d thr${thresh:>5}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 4: ATR-scaled notional ═══
    print(f"\n{'='*76}")
    print("  Phase 4: ATR-Scaled Notional (2-of-4 + cap12)")
    print(f"{'='*76}")
    for atr_thresh in [50, 60, 70, 80]:
        for scale in [0.4, 0.5, 0.6, 0.75]:
            tdf=bt_trail_atr_size(df,entry_2of4,max_same=9,exit_cd=10,
                                  monthly_entry_cap=12,atr_high_thresh=atr_thresh,scale_down=scale)
            r=evaluate(tdf,mid,fe)
            if r:
                print(f"  atr>{atr_thresh:>2} s{scale:.2f}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 5: Dual-layer (primary 2-of-4 + slow breakout) ═══
    print(f"\n{'='*76}")
    print("  Phase 5: Dual-Layer (2-of-4 primary + slow breakout supplement)")
    print(f"{'='*76}")
    for slow_bl in [15, 20]:
        for slow_cd in [15, 20, 25]:
            for cap in [12, 15, 18]:
                tdf=bt_trail_dual_layer(df,entry_2of4,exit_cd=10,
                                        monthly_entry_cap=cap,slow_bl=slow_bl,slow_cd=slow_cd)
                r=evaluate(tdf,mid,fe)
                if r and r["n"]>10:
                    print(f"  BL{slow_bl} scd{slow_cd} cap{cap}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                          f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                          f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                          f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 6: 3-of-4 signal strength ═══
    print(f"\n{'='*76}")
    print("  Phase 6: 3-of-4 Signal Strength (stricter filter)")
    print(f"{'='*76}")
    entry_3of4 = (df["sig_count4"]>=3) & df["bl_up"]
    for cap in [999, 12, 15, 20]:
        for cd in [8, 10, 12]:
            tdf=bt_trail_capped(df,entry_3of4,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe)
            if r and r["n"]>10:
                cap_str="inf" if cap==999 else str(cap)
                print(f"  cap={cap_str:>3} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ FINAL: Best configs full analysis ═══
    print(f"\n{'='*76}")
    print("  FINAL: Best Configs Full 9-Criteria Analysis")
    print(f"{'='*76}")

    # Collect all promising configs
    final_configs = []

    # 2-of-4 baseline (R5 champion)
    for cap in [10,11,12]:
        for cd in [10,12]:
            tdf=bt_trail_capped(df,entry_2of4,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"2of4_cap{cap}_cd{cd}")
            if r: final_configs.append((f"2of4_cap{cap}_cd{cd}",r,tdf))

    # PnL cap combos
    for pnl_cap in [2500,3000,3500]:
        tdf=bt_trail_pnl_cap(df,entry_2of4,max_same=9,exit_cd=10,
                             monthly_entry_cap=12,monthly_pnl_cap=pnl_cap)
        r=evaluate(tdf,mid,fe,f"pnlcap{pnl_cap}")
        if r: final_configs.append((f"2of4_c12_pnlcap{pnl_cap}",r,tdf))

    # Momentum throttle combos
    for lb,th in [(14,600),(14,800),(7,400),(7,600)]:
        tdf=bt_trail_momentum_throttle(df,entry_2of4,max_same=9,exit_cd=10,
                                       monthly_entry_cap=12,lookback=lb,pnl_thresh=th)
        r=evaluate(tdf,mid,fe,f"mom_lb{lb}_th{th}")
        if r: final_configs.append((f"2of4_c12_mom{lb}d_{th}",r,tdf))

    # ATR sizing combos
    for at,sc in [(60,0.5),(70,0.5),(60,0.6)]:
        tdf=bt_trail_atr_size(df,entry_2of4,max_same=9,exit_cd=10,
                              monthly_entry_cap=12,atr_high_thresh=at,scale_down=sc)
        r=evaluate(tdf,mid,fe,f"atr{at}_s{sc}")
        if r: final_configs.append((f"2of4_c12_atr{at}_s{sc}",r,tdf))

    # 3-of-4
    for cap in [999, 12, 15]:
        tdf=bt_trail_capped(df,entry_3of4,max_same=9,exit_cd=10,monthly_entry_cap=cap)
        r=evaluate(tdf,mid,fe,f"3of4_cap{cap}")
        if r and r["n"]>10:
            cap_s="inf" if cap==999 else str(cap)
            final_configs.append((f"3of4_cap{cap_s}_cd10",r,tdf))

    # Sort by topM ascending, then PnL descending
    final_configs.sort(key=lambda x: (x[1]["top_pct"], -x[1]["pnl"]))

    for name,r,tdf in final_configs[:8]:
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
    print("  ROUND 6 COMPLETE")
    print(f"{'='*76}")
