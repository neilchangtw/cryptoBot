"""
L Strategy Exploration Round 5: Signal Quality + Fine-grained Caps
==================================================================
R4 found: entry cap=12 gives topM 23% ($13,384). Need topM ≤ 20%.
Per-trade profitability varies by month (trend months earn more per trade).

Approach:
  Phase 1: Fine-tune entry cap (cap=11,13,14)
  Phase 2: 2-of-3 signal strength filter (require 2+ OR conditions true)
  Phase 3: Per-signal monthly cap (each signal capped independently)
  Phase 4: Multi-signal OR with total cap (add GK, PE)
  Phase 5: Best config full analysis
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

_M_FACT=factorial(PE_M); _MAX_ENT=np.log(_M_FACT)
_PERM_CODES={}
for _i,_p in enumerate(perms(range(PE_M))):
    _PERM_CODES[_p[0]*9+_p[1]*3+_p[2]]=_i

def calc_pe_fast(ret_arr):
    n=len(ret_arr)
    if n<PE_WINDOW: return np.full(n,np.nan)
    x0=ret_arr[:-2];x1=ret_arr[1:-1];x2=ret_arr[2:]
    valid=~(np.isnan(x0)|np.isnan(x1)|np.isnan(x2))
    r01=(x0>x1).astype(int);r02=(x0>x2).astype(int);r12=(x1>x2).astype(int)
    rank0=r01+r02;rank1=(1-r01)+r12;rank2=(1-r02)+(1-r12)
    raw_codes=rank0*9+rank1*3+rank2
    mapped=np.full(len(raw_codes),-1,dtype=int)
    for j in range(len(raw_codes)):
        if valid[j]: mapped[j]=_PERM_CODES.get(int(raw_codes[j]),-1)
    n_per_win=PE_WINDOW-PE_M+1
    pe_result=np.full(n,np.nan)
    counts=np.zeros(_M_FACT,dtype=int); total=0
    for j in range(min(n_per_win,len(mapped))):
        if mapped[j]>=0: counts[mapped[j]]+=1; total+=1
    ret_idx=n_per_win+PE_M-2
    if ret_idx<n and total>=n_per_win//2:
        probs=counts[counts>0]/total
        pe_result[ret_idx]=-np.sum(probs*np.log(probs))/_MAX_ENT
    for s in range(1,len(mapped)-n_per_win+1):
        old=mapped[s-1]
        if old>=0: counts[old]-=1; total-=1
        new=mapped[s+n_per_win-1]
        if new>=0: counts[new]+=1; total+=1
        ret_idx=s+n_per_win+PE_M-2
        if ret_idx<n and total>=n_per_win//2:
            probs=counts[counts>0]/total
            pe_result[ret_idx]=-np.sum(probs*np.log(probs))/_MAX_ENT
    return pe_result

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

    t0=time.time()
    d["pe_raw"]=calc_pe_fast(d["ret"].values)
    d["pe"]=d["pe_raw"].shift(1)
    d["pe_pct"]=d["pe"].rolling(PCTILE_WIN).apply(pctile_func)
    print(f"  PE: {time.time()-t0:.1f}s")

    d["cs1"]=d["close"].shift(1)
    d["brk_max"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["bl_up"]=d["cs1"]>d["brk_max"]

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    d["ym"]=d["datetime"].dt.to_period("M")

    # Individual signal flags
    d["sig_ami"]=(d["ami_pct"]<20)
    d["sig_skew"]=(d["skew20"]>1.0)
    d["sig_ret"]=(d["retsign15"]>0.60)
    d["sig_gk"]=(d["gk_pct"]<30)
    d["sig_pe"]=(d["pe_pct"]<25)

    # Count how many signals are true
    d["sig_count"]=(d["sig_ami"].astype(int)+d["sig_skew"].astype(int)+
                    d["sig_ret"].astype(int))
    d["sig_count4"]=(d["sig_ami"].astype(int)+d["sig_skew"].astype(int)+
                     d["sig_ret"].astype(int)+d["sig_gk"].astype(int))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_trail_capped(df, entry_mask, max_same=9, exit_cd=12,
                    monthly_entry_cap=999):
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        h=H[i];lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
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

# Per-signal cap: each signal tracked independently
def bt_trail_persig_cap(df, sig_cols, max_same=9, exit_cd=12,
                        per_sig_cap=5):
    """Each signal source has its own monthly entry cap."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    SOK=df["sok"].values;YM=df["ym"].values;BL=df["bl_up"].values;n=len(df)

    sig_arrays = {name: df[col].values for name, col in sig_cols}

    pos=[];trades=[];lx=-9999;boc={}
    sig_month_entries = {}  # {(sig_name, ym): count}

    for i in range(WARMUP,n-1):
        h=H[i];lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
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
        if not _b(SOK[i]) or not _b(BL[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue

        # Find first signal with budget remaining
        entered = False
        for sig_name, sig_arr in sig_arrays.items():
            if not _b(sig_arr[i]): continue
            key = (sig_name, cur_ym)
            if sig_month_entries.get(key, 0) >= per_sig_cap: continue
            sig_month_entries[key] = sig_month_entries.get(key, 0) + 1
            boc[i]=boc.get(i,0)+1
            pos.append({"e":nxo,"ei":i})
            entered = True
            break
        # If no per-sig budget, skip
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
        top_v=ms.max() if len(ms)>0 else 0;top_n=str(ms.idxmax()) if len(ms)>0 else "N/A";top_pct=999
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
    print("  L ROUND 5: Signal Quality + Fine-grained Caps")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    # ═══ Phase 1: Fine-tune entry cap ═══
    print(f"\n{'='*76}")
    print("  Phase 1: Fine-tune Entry Cap (cap=9-14)")
    print(f"{'='*76}")

    entry_ami=((df["ami_pct"]<20)|(df["skew20"]>1.0)|(df["retsign15"]>0.60))&df["bl_up"]
    entry_gk=((df["gk_pct"]<30)|(df["skew20"]>1.0)|(df["retsign15"]>0.60))&df["bl_up"]

    print(f"  --- Ami<20|Skew|RetSign ---")
    print(f"  {'cap':>4} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'PM':>5} {'topM':>5} {'-bst':>8}")
    for cap in range(9,15):
        for cd in [10,12]:
            tdf=bt_trail_capped(df,entry_ami,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"ami_cap{cap}_cd{cd}")
            if r:
                print(f"  {cap:>4} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")

    print(f"\n  --- GK<30|Skew|RetSign (original Triple OR) ---")
    for cap in range(9,15):
        for cd in [10,12]:
            tdf=bt_trail_capped(df,entry_gk,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"gk_cap{cap}_cd{cd}")
            if r:
                print(f"  {cap:>4} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")

    # ═══ Phase 2: Signal strength filter (2-of-3) ═══
    print(f"\n{'='*76}")
    print("  Phase 2: Signal Strength Filter (2-of-3, 2-of-4)")
    print(f"{'='*76}")

    # 2-of-3: (Ami AND Skew) OR (Ami AND RetSign) OR (Skew AND RetSign)
    entry_2of3 = (df["sig_count"]>=2) & df["bl_up"]
    # 2-of-4: at least 2 of {Ami, Skew, RetSign, GK}
    entry_2of4 = (df["sig_count4"]>=2) & df["bl_up"]

    print(f"  --- 2-of-3 (Ami,Skew,RetSign) ---")
    for cap in [999, 12, 15, 20]:
        for cd in [8, 10, 12]:
            tdf=bt_trail_capped(df,entry_2of3,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"2of3_cap{cap}_cd{cd}")
            if r and r["n"]>10:
                cap_str = "inf" if cap==999 else str(cap)
                print(f"  cap={cap_str:>3} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    print(f"\n  --- 2-of-4 (Ami,Skew,RetSign,GK) ---")
    for cap in [999, 12, 15, 20]:
        for cd in [8, 10, 12]:
            tdf=bt_trail_capped(df,entry_2of4,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"2of4_cap{cap}_cd{cd}")
            if r and r["n"]>10:
                cap_str = "inf" if cap==999 else str(cap)
                print(f"  cap={cap_str:>3} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 3: Per-signal monthly cap ═══
    print(f"\n{'='*76}")
    print("  Phase 3: Per-Signal Monthly Cap")
    print(f"{'='*76}")

    sig_cols_3 = [("Ami","sig_ami"),("Skew","sig_skew"),("RetSign","sig_ret")]
    sig_cols_4 = sig_cols_3 + [("GK","sig_gk")]

    print(f"  --- 3 signals (Ami,Skew,RetSign) ---")
    for psc in [3,4,5,6,8]:
        for cd in [8,10,12]:
            tdf=bt_trail_persig_cap(df,sig_cols_3,max_same=9,exit_cd=cd,per_sig_cap=psc)
            r=evaluate(tdf,mid,fe,f"3sig_psc{psc}_cd{cd}")
            if r and r["n"]>10:
                print(f"  psc{psc} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    print(f"\n  --- 4 signals (Ami,Skew,RetSign,GK) ---")
    for psc in [3,4,5,6]:
        for cd in [8,10,12]:
            tdf=bt_trail_persig_cap(df,sig_cols_4,max_same=9,exit_cd=cd,per_sig_cap=psc)
            r=evaluate(tdf,mid,fe,f"4sig_psc{psc}_cd{cd}")
            if r and r["n"]>10:
                print(f"  psc{psc} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ Phase 4: 5-signal OR with cap ═══
    print(f"\n{'='*76}")
    print("  Phase 4: 5-signal OR with Entry Cap")
    print(f"{'='*76}")

    entry_5way=((df["ami_pct"]<20)|(df["skew20"]>1.0)|(df["retsign15"]>0.60)|
                (df["gk_pct"]<30)|(df["pe_pct"]<25)) & df["bl_up"]
    for cap in [10,12,14,15,20,999]:
        for cd in [10,12]:
            tdf=bt_trail_capped(df,entry_5way,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"5way_cap{cap}_cd{cd}")
            if r and r["n"]>10:
                cap_str="inf" if cap==999 else str(cap)
                print(f"  cap={cap_str:>3} cd{cd}: {r['n']:>4}t ${r['pnl']:>+8,.0f} "
                      f"PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% "
                      f"PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% "
                      f"-bst${r['nb']:>+,.0f}")

    # ═══ FINAL: Best configs full analysis ═══
    print(f"\n{'='*76}")
    print("  FINAL: Best Configs Full Analysis")
    print(f"{'='*76}")

    # Collect promising configs that pass topM or come closest
    candidates = []

    # Test all promising configs
    test_configs = [
        ("ami_cap12_cd12", entry_ami, 12, 12),
        ("ami_cap11_cd12", entry_ami, 11, 12),
        ("ami_cap11_cd10", entry_ami, 11, 10),
        ("gk_cap12_cd12", entry_gk, 12, 12),
        ("2of3_cap15_cd10", entry_2of3, 15, 10),
        ("2of4_cap12_cd10", entry_2of4, 12, 10),
        ("5way_cap12_cd10", entry_5way, 12, 10),
        ("5way_cap12_cd12", entry_5way, 12, 12),
    ]

    for name,entry,cap,cd in test_configs:
        tdf=bt_trail_capped(df,entry,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
        r=evaluate(tdf,mid,fe,name)
        r_is=evaluate(tdf,fs,mid,"IS")
        wf=walk_forward_6(tdf,mid,fe)
        wf_pos=sum(1 for w in wf if w["pos"])
        if r and r["n"]>10:
            candidates.append((name,tdf,r,r_is,wf,wf_pos))

    # Sort by topM ascending
    candidates.sort(key=lambda x: x[2]["top_pct"])

    for name,tdf,r,r_is,wf,wf_pos in candidates[:5]:
        checks=[
            ("PnL>=10K",r["pnl"]>=10000),("PF>=1.5",r["pf"]>=1.5),
            ("MDD<=25",r["mdd"]<=25),("TPM>=10",r["tpm"]>=10),
            ("PM>=9",r["pos_months"]>=9),("topM<=20",r["top_pct"]<=20),
            ("-bst>=8K",r["nb"]>=8000),("WF>=5/6",wf_pos>=5),("bar<=2",True)]
        sc=sum(1 for _,v in checks if v)
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
    print("  ROUND 5 COMPLETE")
    print(f"{'='*76}")
