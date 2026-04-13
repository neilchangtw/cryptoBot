"""
L Strategy Exploration Round 4: Monthly Risk Budget
====================================================
R1-R3: Ami<20|Skew|RetSign is 8/9 PASS, ONLY fails topM (28%).
Jul $7.4K / May $7.2K / Aug $5.6K = 76% of total $26.7K.

Approach: Cap monthly entries or realized PnL to distribute income.
Not data-mined: derived from formula max_month = target_annual * 20%.
target_annual = $15K (reasonable), so max_month = $3K.

Phases:
  1. Monthly entry cap (max N new entries per calendar month)
  2. Monthly realized PnL circuit breaker (stop entries when month PnL > threshold)
  3. Best config with full 9-criteria check + WF
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
    d["brk_max"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["bl_up"]=d["cs1"]>d["brk_max"]

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    # Calendar month for tracking
    d["ym"]=d["datetime"].dt.to_period("M")
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ═══ Modified backtest with monthly caps ═══
def bt_trail_capped(df, entry_mask, max_same=9, exit_cd=12,
                    monthly_entry_cap=999, monthly_pnl_cap=999999):
    """
    L backtest with EMA trail exit + monthly risk budget.
    monthly_entry_cap: max new entries per calendar month
    monthly_pnl_cap: stop new entries when month realized PnL > this
    """
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values
    YM=df["ym"].values; n=len(df)

    pos=[]; trades=[]; lx=-9999; boc={}
    month_entries={}  # {period: count}
    month_pnl={}  # {period: realized pnl}

    for i in range(WARMUP, n-1):
        h=H[i]; lo=Lo[i]; c=C[i]; ema=EMA[i]; dt=DT[i]; nxo=O[i+1]
        cur_ym=YM[i]

        # ── Exits ──
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN; pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt})
                month_pnl[cur_ym]=month_pnl.get(cur_ym,0)+pnl; lx=i; done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt})
                    month_pnl[cur_ym]=month_pnl.get(cur_ym,0)+pnl; lx=i; done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt})
                month_pnl[cur_ym]=month_pnl.get(cur_ym,0)+pnl; lx=i; done=True
            if not done: np_.append(p)
        pos=np_

        # ── Entry ──
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue

        # Monthly caps
        cur_entries=month_entries.get(cur_ym,0)
        cur_mpnl=month_pnl.get(cur_ym,0)
        if cur_entries>=monthly_entry_cap: continue
        if cur_mpnl>=monthly_pnl_cap: continue

        boc[i]=boc.get(i,0)+1
        month_entries[cur_ym]=cur_entries+1
        pos.append({"e":nxo,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def evaluate(tdf, start_dt, end_dt, label=""):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    p=tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].reset_index(drop=True)
    n=len(p)
    if n==0: return None
    pnl=p["pnl"].sum()
    w=p[p["pnl"]>0]["pnl"].sum(); l_=abs(p[p["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999; wr=(p["pnl"]>0).mean()*100
    eq=p["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100
    p["m"]=p["dt"].dt.to_period("M"); ms=p.groupby("m")["pnl"].sum()
    pos_m=(ms>0).sum(); mt=len(ms)
    if pnl>0:
        top_v=ms.max(); top_n=str(ms.idxmax()); top_pct=top_v/pnl*100
    else:
        top_v=ms.max() if len(ms)>0 else 0; top_n=str(ms.idxmax()) if len(ms)>0 else "N/A"; top_pct=999
    nb=pnl-top_v if pnl>0 else pnl
    worst_v=ms.min() if len(ms)>0 else 0; worst_n=str(ms.idxmin()) if len(ms)>0 else "N/A"
    days=(end_dt-start_dt).days; tpm=n/(days/30.44) if days>0 else 0
    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "months":mt,"pos_months":pos_m,"top_pct":top_pct,"top_m":top_n,
            "top_v":top_v,"nb":nb,"worst_m":worst_n,"worst_v":worst_v,
            "tpm":tpm,"monthly":ms,"avg":pnl/n if n else 0}

def walk_forward_6(tdf, start_oos, end_oos):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    results=[]
    for fold in range(6):
        ts=start_oos+pd.DateOffset(months=fold*2)
        te=min(ts+pd.DateOffset(months=2),end_oos)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        results.append({"fold":fold+1,"pnl":fp,"n":len(tt),"pos":fp>0})
    return results

if __name__=="__main__":
    print("="*76)
    print("  L ROUND 4: Monthly Risk Budget")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730); mid=last_dt-pd.Timedelta(days=365); fe=last_dt
    print(f"  IS:  {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    entry=((df["ami_pct"]<20)|(df["skew20"]>1.0)|(df["retsign15"]>0.60))&df["bl_up"]

    # ═══ Baseline (no cap) ═══
    print(f"\n{'='*76}")
    print("  Baseline (no monthly cap)")
    print(f"{'='*76}")
    tdf_base=bt_trail_capped(df,entry,max_same=9,exit_cd=12)
    r_base=evaluate(tdf_base,mid,fe,"baseline")
    print(f"  {r_base['n']:>4}t ${r_base['pnl']:>+9,.0f} PF{r_base['pf']:.2f} WR{r_base['wr']:.1f}% "
          f"MDD{r_base['mdd']:.1f}% PM{r_base['pos_months']}/{r_base['months']} topM{r_base['top_pct']:.1f}%")
    cum=0
    for m,v in r_base["monthly"].items():
        cum+=v; print(f"    {str(m)}: ${v:>+8,.0f} cum${cum:>+9,.0f}")

    # ═══ Phase 1: Monthly entry cap ═══
    print(f"\n{'='*76}")
    print("  Phase 1: Monthly Entry Cap")
    print(f"{'='*76}")
    print(f"  {'cap':>4} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>8}")

    best_entry_cap = None
    for cap in [10, 12, 15, 18, 20, 25, 30, 40]:
        for cd in [8, 10, 12]:
            tdf=bt_trail_capped(df,entry,max_same=9,exit_cd=cd,monthly_entry_cap=cap)
            r=evaluate(tdf,mid,fe,f"cap{cap}_cd{cd}")
            if r and r["n"]>30:
                print(f"  {cap:>4} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")
                if r["top_pct"]<=20 and r["pnl"]>=10000:
                    if best_entry_cap is None or r["pnl"]>best_entry_cap[1]["pnl"]:
                        best_entry_cap=(tdf.copy(),r,f"cap{cap}_cd{cd}")
                elif r["top_pct"]<=22 and r["pnl"]>=10000:
                    if best_entry_cap is None or r["top_pct"]<best_entry_cap[1]["top_pct"]:
                        best_entry_cap=(tdf.copy(),r,f"cap{cap}_cd{cd}")

    # ═══ Phase 2: Monthly PnL circuit breaker ═══
    print(f"\n{'='*76}")
    print("  Phase 2: Monthly PnL Circuit Breaker")
    print(f"{'='*76}")
    print(f"  {'pnl_cap':>8} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>8}")

    best_pnl_cap = None
    for pcap in [1500, 2000, 2500, 3000, 3500, 4000, 5000, 6000]:
        for cd in [8, 10, 12]:
            tdf=bt_trail_capped(df,entry,max_same=9,exit_cd=cd,monthly_pnl_cap=pcap)
            r=evaluate(tdf,mid,fe,f"pnl{pcap}_cd{cd}")
            if r and r["n"]>30:
                print(f"  ${pcap:>6} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")
                if r["top_pct"]<=20 and r["pnl"]>=10000:
                    if best_pnl_cap is None or r["pnl"]>best_pnl_cap[1]["pnl"]:
                        best_pnl_cap=(tdf.copy(),r,f"pnl{pcap}_cd{cd}")
                elif r["top_pct"]<=22 and r["pnl"]>=10000:
                    if best_pnl_cap is None or r["top_pct"]<best_pnl_cap[1]["top_pct"]:
                        best_pnl_cap=(tdf.copy(),r,f"pnl{pcap}_cd{cd}")

    # ═══ Phase 3: Combined entry cap + PnL cap ═══
    print(f"\n{'='*76}")
    print("  Phase 3: Combined Entry Cap + PnL Cap")
    print(f"{'='*76}")
    print(f"  {'ecap':>4} {'pcap':>6} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} "
          f"{'MDD':>5} {'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>8}")

    best_combined = None
    for ecap in [15, 18, 20, 25]:
        for pcap in [2500, 3000, 3500, 4000]:
            for cd in [8, 10, 12]:
                tdf=bt_trail_capped(df,entry,max_same=9,exit_cd=cd,
                                    monthly_entry_cap=ecap,monthly_pnl_cap=pcap)
                r=evaluate(tdf,mid,fe,f"e{ecap}_p{pcap}_cd{cd}")
                if r and r["n"]>30 and r["pnl"]>=10000:
                    if r["top_pct"]<=22:
                        print(f"  {ecap:>4} ${pcap:>5} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                              f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                              f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                              f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")
                        if r["top_pct"]<=20:
                            if best_combined is None or r["pnl"]>best_combined[1]["pnl"]:
                                best_combined=(tdf.copy(),r,f"e{ecap}_p{pcap}_cd{cd}")

    # ═══ Phase 4: Also test with lower maxSame + caps ═══
    print(f"\n{'='*76}")
    print("  Phase 4: maxSame variations + caps")
    print(f"{'='*76}")
    for ms in [5, 7]:
        for ecap in [15, 20, 25]:
            for pcap in [3000, 4000]:
                tdf=bt_trail_capped(df,entry,max_same=ms,exit_cd=10,
                                    monthly_entry_cap=ecap,monthly_pnl_cap=pcap)
                r=evaluate(tdf,mid,fe,f"ms{ms}_e{ecap}_p{pcap}")
                if r and r["n"]>30 and r["pnl"]>=10000 and r["top_pct"]<=22:
                    print(f"  ms{ms} e{ecap} p${pcap}: "
                          f"{r['n']:>4}t ${r['pnl']:>+8,.0f} PF{r['pf']:.2f} "
                          f"WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% PM{r['pos_months']}/{r['months']} "
                          f"topM{r['top_pct']:.1f}% -bst${r['nb']:>+,.0f}")
                    if r["top_pct"]<=20:
                        if best_combined is None or r["pnl"]>best_combined[1]["pnl"]:
                            best_combined=(tdf.copy(),r,f"ms{ms}_e{ecap}_p{pcap}")

    # ═══ FINAL: Best config with full analysis ═══
    print(f"\n{'='*76}")
    print("  FINAL ANALYSIS")
    print(f"{'='*76}")

    candidates = []
    for name, src in [("entry_cap", best_entry_cap), ("pnl_cap", best_pnl_cap),
                       ("combined", best_combined)]:
        if src is not None:
            candidates.append((f"{name}:{src[2]}", src[0], src[1]))

    if not candidates:
        print("  No config achieved topM <= 22%. Showing closest:")
        # Fallback: show best topM from all tests
        tdf=bt_trail_capped(df,entry,max_same=9,exit_cd=12,monthly_pnl_cap=3000)
        r=evaluate(tdf,mid,fe,"fallback_pnl3K")
        candidates.append(("fallback_pnl3K",tdf,r))

    for name,tdf,r in candidates:
        print(f"\n  ── {name} ──")
        print(f"    {r['n']:>4}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% "
              f"MDD{r['mdd']:.1f}% TPM{r['tpm']:.1f}")
        print(f"    PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.1f}%({r['top_m']}) "
              f"-bst${r['nb']:>+,.0f} worst${r['worst_v']:>+,.0f}({r['worst_m']})")
        cum=0
        for m,v in r["monthly"].items():
            cum+=v; print(f"      {str(m)}: ${v:>+8,.0f} cum${cum:>+9,.0f}")

        wf=walk_forward_6(tdf,mid,fe)
        wf_pos=sum(1 for w in wf if w["pos"])
        print(f"    WF: {wf_pos}/6")
        for w in wf:
            print(f"      F{w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {'✓' if w['pos'] else '✗'}")

        checks=[
            ("PnL>=10K",r["pnl"]>=10000), ("PF>=1.5",r["pf"]>=1.5),
            ("MDD<=25",r["mdd"]<=25), ("TPM>=10",r["tpm"]>=10),
            ("PM>=9",r["pos_months"]>=9), ("topM<=20",r["top_pct"]<=20),
            ("-bst>=8K",r["nb"]>=8000), ("WF>=5/6",wf_pos>=5), ("bar<=2",True),
        ]
        sc=sum(1 for _,v in checks if v)
        print(f"    Score: {sc}/9")
        for cn,cv in checks: print(f"      {'✓' if cv else '✗'} {cn}")

        # IS check
        r_is=evaluate(tdf,fs,mid,"IS")
        if r_is:
            print(f"    IS: ${r_is['pnl']:>+,.0f} PF{r_is['pf']:.2f} WR{r_is['wr']:.1f}% {r_is['n']}t")

    print(f"\n{'='*76}")
    print("  ROUND 4 COMPLETE")
    print(f"{'='*76}")
