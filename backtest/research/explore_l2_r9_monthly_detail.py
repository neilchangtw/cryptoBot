"""
L+S Monthly Detail Report
==========================
Detailed per-month breakdown: trades, WR, PF, avg PnL, max win/loss, exit types.
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
S_SUBS = [
    {"name":"S1","gk_thresh":40,"bl":8, "max_same":5,"exit_cd":6},
    {"name":"S2","gk_thresh":40,"bl":15,"max_same":5,"exit_cd":6},
    {"name":"S3","gk_thresh":30,"bl":10,"max_same":5,"exit_cd":6},
    {"name":"S4","gk_thresh":40,"bl":12,"max_same":5,"exit_cd":6},
]
S_TP=0.02; S_MAX_HOLD=12

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
    d["brk_min_8"]=d["close"].shift(2).rolling(7).min()
    d["bl_dn_8"]=d["cs1"]<d["brk_min_8"]
    d["brk_min_10"]=d["close"].shift(2).rolling(9).min()
    d["bl_dn_10"]=d["cs1"]<d["brk_min_10"]
    d["brk_min_12"]=d["close"].shift(2).rolling(11).min()
    d["bl_dn_12"]=d["cs1"]<d["brk_min_12"]
    d["brk_min_15"]=d["close"].shift(2).rolling(14).min()
    d["bl_dn_15"]=d["cs1"]<d["brk_min_15"]
    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    d["ym"]=d["datetime"].dt.to_period("M")
    tr=pd.concat([d["high"]-d["low"],(d["high"]-d["close"].shift(1)).abs(),(d["low"]-d["close"].shift(1)).abs()],axis=1).max(axis=1)
    d["atr14"]=tr.rolling(14).mean().shift(1)
    d["atr_pct"]=d["atr14"].rolling(PCTILE_WIN).apply(pctile_func)
    d["sig_ami"]=(d["ami_pct"]<20); d["sig_skew"]=(d["skew20"]>1.0)
    d["sig_ret"]=(d["retsign15"]>0.60); d["sig_gk"]=(d["gk_pct"]<30)
    d["sig_count4"]=(d["sig_ami"].astype(int)+d["sig_skew"].astype(int)+d["sig_ret"].astype(int)+d["sig_gk"].astype(int))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_long_atr(df, atr_thresh=76, scale_down=0.60, max_same=9, exit_cd=10, monthly_entry_cap=12):
    entry_mask=(df["sig_count4"]>=2)&df["bl_up"]
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
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":"L","entry":p["e"],"exit":ep,"not":not_});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"side":"L","entry":p["e"],"exit":c,"not":not_});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"side":"L","entry":p["e"],"exit":c,"not":not_});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        not_size=NOTIONAL*scale_down if atr_p>atr_thresh else NOTIONAL
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","side","entry","exit","not"])

def bt_short_cmp(df):
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;DT=df["datetime"].values
    SOK=df["sok"].values;YM=df["ym"].values;GK_P=df["gk_pct"].values;n=len(df)
    bl_arrays={}
    for sub in S_SUBS:
        bl_arrays[sub["name"]]=df[f"bl_dn_{sub['bl']}"].values
    all_trades=[]
    for sub in S_SUBS:
        sname=sub["name"];gk_t=sub["gk_thresh"];ms=sub["max_same"];ecd=sub["exit_cd"]
        BL_DN=bl_arrays[sname]
        pos=[];lx=-9999;boc={}
        for i in range(WARMUP,n-1):
            h=H[i];lo=Lo[i];c=C[i];dt=DT[i];nxo=O[i+1]
            np_=[]
            for p in pos:
                bh=i-p["ei"];done=False
                sn=p["e"]*(1+SAFENET_PCT)
                if h>=sn:
                    ep=sn+(h-sn)*SN_PEN
                    pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                    all_trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":sname,"entry":p["e"],"exit":ep,"not":NOTIONAL});lx=i;done=True
                if not done:
                    tp_price=p["e"]*(1-S_TP)
                    if lo<=tp_price:
                        pnl=(p["e"]-tp_price)*NOTIONAL/p["e"]-FEE
                        all_trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt,"side":sname,"entry":p["e"],"exit":tp_price,"not":NOTIONAL});lx=i;done=True
                if not done and bh>=S_MAX_HOLD:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    all_trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt,"side":sname,"entry":p["e"],"exit":c,"not":NOTIONAL});lx=i;done=True
                if not done: np_.append(p)
            pos=np_
            if not _b(SOK[i]) or not _b(BL_DN[i]): continue
            gk_v=GK_P[i]
            if np.isnan(gk_v) or gk_v>=gk_t: continue
            if (i-lx)<ecd or len(pos)>=ms: continue
            if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
            boc[i]=boc.get(i,0)+1
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(all_trades) if all_trades else pd.DataFrame(columns=["pnl","t","b","dt","side","entry","exit","not"])


def monthly_detail(tdf, start_dt, end_dt, label=""):
    """Rich per-month stats."""
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    p=tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].copy()
    if len(p)==0: return []
    p["m"]=p["dt"].dt.to_period("M")
    rows=[]
    for m, g in p.groupby("m"):
        n=len(g); pnl=g["pnl"].sum()
        wins=g[g["pnl"]>0]; losses=g[g["pnl"]<=0]
        nw=len(wins); nl=len(losses)
        wr=nw/n*100 if n>0 else 0
        w_sum=wins["pnl"].sum(); l_sum=abs(losses["pnl"].sum())
        pf=w_sum/l_sum if l_sum>0 else (999 if w_sum>0 else 0)
        avg=pnl/n if n>0 else 0
        mx=g["pnl"].max(); mn=g["pnl"].min()
        # exit type breakdown
        exit_counts=g["t"].value_counts().to_dict()
        # avg holding bars
        avg_b=g["b"].mean() if "b" in g.columns else 0
        rows.append({
            "month":str(m),"n":n,"pnl":pnl,"nw":nw,"nl":nl,"wr":wr,
            "pf":pf,"avg":avg,"max_w":mx,"max_l":mn,"avg_bars":avg_b,
            "exits":exit_counts
        })
    return rows


if __name__=="__main__":
    print("="*100)
    print("  L+S MONTHLY DETAIL REPORT")
    print("="*100)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    l_trades=bt_long_atr(df)
    s_trades=bt_short_cmp(df)
    combined=pd.concat([l_trades,s_trades],ignore_index=True)

    # ── L strategy monthly detail ──
    print(f"\n{'='*100}")
    print("  L STRATEGY — Monthly Detail (OOS)")
    print(f"{'='*100}")
    l_months=monthly_detail(l_trades,mid,fe)
    print(f"  {'Month':>8} {'#':>4} {'W':>3} {'L':>3} {'WR':>6} {'PnL':>9} {'PF':>6} "
          f"{'Avg':>7} {'MaxW':>7} {'MaxL':>8} {'AvgBar':>6}  Exit Breakdown")
    print(f"  {'-'*8} {'-'*4} {'-'*3} {'-'*3} {'-'*6} {'-'*9} {'-'*6} "
          f"{'-'*7} {'-'*7} {'-'*8} {'-'*6}  {'-'*25}")
    l_total_n=0; l_total_pnl=0; l_total_w=0; l_total_l=0
    for r in l_months:
        ex_str=" ".join(f"{k}:{v}" for k,v in sorted(r["exits"].items()))
        print(f"  {r['month']:>8} {r['n']:>4} {r['nw']:>3} {r['nl']:>3} {r['wr']:>5.1f}% "
              f"${r['pnl']:>+8,.0f} {r['pf']:>5.1f}x "
              f"${r['avg']:>+6,.0f} ${r['max_w']:>+6,.0f} ${r['max_l']:>+7,.0f} "
              f"{r['avg_bars']:>5.1f}  {ex_str}")
        l_total_n+=r["n"]; l_total_pnl+=r["pnl"]; l_total_w+=r["nw"]; l_total_l+=r["nl"]
    print(f"  {'TOTAL':>8} {l_total_n:>4} {l_total_w:>3} {l_total_l:>3} "
          f"{l_total_w/l_total_n*100 if l_total_n else 0:>5.1f}% "
          f"${l_total_pnl:>+8,.0f}")

    # ── S strategy monthly detail ──
    print(f"\n{'='*100}")
    print("  S STRATEGY — Monthly Detail (OOS)")
    print(f"{'='*100}")
    s_months=monthly_detail(s_trades,mid,fe)
    print(f"  {'Month':>8} {'#':>4} {'W':>3} {'L':>3} {'WR':>6} {'PnL':>9} {'PF':>6} "
          f"{'Avg':>7} {'MaxW':>7} {'MaxL':>8} {'AvgBar':>6}  Exit Breakdown")
    print(f"  {'-'*8} {'-'*4} {'-'*3} {'-'*3} {'-'*6} {'-'*9} {'-'*6} "
          f"{'-'*7} {'-'*7} {'-'*8} {'-'*6}  {'-'*25}")
    s_total_n=0; s_total_pnl=0; s_total_w=0; s_total_l=0
    for r in s_months:
        ex_str=" ".join(f"{k}:{v}" for k,v in sorted(r["exits"].items()))
        print(f"  {r['month']:>8} {r['n']:>4} {r['nw']:>3} {r['nl']:>3} {r['wr']:>5.1f}% "
              f"${r['pnl']:>+8,.0f} {r['pf']:>5.1f}x "
              f"${r['avg']:>+6,.0f} ${r['max_w']:>+6,.0f} ${r['max_l']:>+7,.0f} "
              f"{r['avg_bars']:>5.1f}  {ex_str}")
        s_total_n+=r["n"]; s_total_pnl+=r["pnl"]; s_total_w+=r["nw"]; s_total_l+=r["nl"]
    print(f"  {'TOTAL':>8} {s_total_n:>4} {s_total_w:>3} {s_total_l:>3} "
          f"{s_total_w/s_total_n*100 if s_total_n else 0:>5.1f}% "
          f"${s_total_pnl:>+8,.0f}")

    # ── S sub-strategy monthly breakdown ──
    print(f"\n{'='*100}")
    print("  S SUB-STRATEGY Monthly PnL Breakdown (OOS)")
    print(f"{'='*100}")
    s_trades_oos=s_trades.copy(); s_trades_oos["dt"]=pd.to_datetime(s_trades_oos["dt"])
    s_trades_oos=s_trades_oos[(s_trades_oos["dt"]>=mid)&(s_trades_oos["dt"]<fe)].copy()
    s_trades_oos["m"]=s_trades_oos["dt"].dt.to_period("M")
    sub_names=["S1","S2","S3","S4"]
    sub_monthly={}
    for sn in sub_names:
        sg=s_trades_oos[s_trades_oos["side"]==sn]
        sub_monthly[sn]=sg.groupby("m")["pnl"].sum() if len(sg)>0 else pd.Series(dtype=float)
    all_m=sorted(set(s_trades_oos["m"]))
    print(f"  {'Month':>8} {'S1':>8} {'S2':>8} {'S3':>8} {'S4':>8} {'Total':>9}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*9}")
    s1t=s2t=s3t=s4t=0
    for m in all_m:
        v1=sub_monthly["S1"].get(m,0); v2=sub_monthly["S2"].get(m,0)
        v3=sub_monthly["S3"].get(m,0); v4=sub_monthly["S4"].get(m,0)
        s1t+=v1;s2t+=v2;s3t+=v3;s4t+=v4
        print(f"  {str(m):>8} ${v1:>+7,.0f} ${v2:>+7,.0f} ${v3:>+7,.0f} ${v4:>+7,.0f} ${v1+v2+v3+v4:>+8,.0f}")
    print(f"  {'TOTAL':>8} ${s1t:>+7,.0f} ${s2t:>+7,.0f} ${s3t:>+7,.0f} ${s4t:>+7,.0f} ${s1t+s2t+s3t+s4t:>+8,.0f}")

    # ── Combined L+S monthly ──
    print(f"\n{'='*100}")
    print("  COMBINED L+S — Monthly Detail (OOS)")
    print(f"{'='*100}")
    c_months=monthly_detail(combined,mid,fe)
    print(f"  {'Month':>8} {'#':>4} {'W':>3} {'L':>3} {'WR':>6} {'PnL':>9} {'PF':>6} "
          f"{'Avg':>7} {'MaxW':>7} {'MaxL':>8} {'AvgBar':>6}  Exit Breakdown")
    print(f"  {'-'*8} {'-'*4} {'-'*3} {'-'*3} {'-'*6} {'-'*9} {'-'*6} "
          f"{'-'*7} {'-'*7} {'-'*8} {'-'*6}  {'-'*25}")
    c_total_n=0; c_total_pnl=0; c_total_w=0; c_total_l=0
    for r in c_months:
        ex_str=" ".join(f"{k}:{v}" for k,v in sorted(r["exits"].items()))
        print(f"  {r['month']:>8} {r['n']:>4} {r['nw']:>3} {r['nl']:>3} {r['wr']:>5.1f}% "
              f"${r['pnl']:>+8,.0f} {r['pf']:>5.1f}x "
              f"${r['avg']:>+6,.0f} ${r['max_w']:>+6,.0f} ${r['max_l']:>+7,.0f} "
              f"{r['avg_bars']:>5.1f}  {ex_str}")
        c_total_n+=r["n"]; c_total_pnl+=r["pnl"]; c_total_w+=r["nw"]; c_total_l+=r["nl"]
    print(f"  {'TOTAL':>8} {c_total_n:>4} {c_total_w:>3} {c_total_l:>3} "
          f"{c_total_w/c_total_n*100 if c_total_n else 0:>5.1f}% "
          f"${c_total_pnl:>+8,.0f}")

    # ── Equity curve and drawdown per month ──
    print(f"\n{'='*100}")
    print("  EQUITY CURVE + DRAWDOWN (OOS)")
    print(f"{'='*100}")
    comb_oos=combined.copy(); comb_oos["dt"]=pd.to_datetime(comb_oos["dt"])
    comb_oos=comb_oos[(comb_oos["dt"]>=mid)&(comb_oos["dt"]<fe)].sort_values("dt").reset_index(drop=True)
    comb_oos["eq"]=ACCOUNT+comb_oos["pnl"].cumsum()
    comb_oos["peak"]=comb_oos["eq"].cummax()
    comb_oos["dd"]=comb_oos["eq"]-comb_oos["peak"]
    comb_oos["dd_pct"]=comb_oos["dd"]/comb_oos["peak"]*100
    comb_oos["m"]=comb_oos["dt"].dt.to_period("M")
    print(f"  {'Month':>8} {'EndEq':>10} {'Peak':>10} {'MaxDD$':>9} {'MaxDD%':>7}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*9} {'-'*7}")
    for m, g in comb_oos.groupby("m"):
        end_eq=g["eq"].iloc[-1]
        peak=g["peak"].max()
        mdd=g["dd"].min()
        mdd_pct=g["dd_pct"].min()
        print(f"  {str(m):>8} ${end_eq:>9,.0f} ${peak:>9,.0f} ${mdd:>+8,.0f} {mdd_pct:>+6.1f}%")
    print(f"\n  Final equity: ${comb_oos['eq'].iloc[-1]:>,.0f}")
    print(f"  Overall peak: ${comb_oos['peak'].max():>,.0f}")
    print(f"  Max drawdown: ${comb_oos['dd'].min():>,.0f} ({comb_oos['dd_pct'].min():.1f}%)")

    # ── L exit type summary ──
    print(f"\n{'='*100}")
    print("  EXIT TYPE SUMMARY (OOS)")
    print(f"{'='*100}")
    for label,tdf_src in [("L",l_trades),("S",s_trades),("L+S",combined)]:
        tdf_src=tdf_src.copy(); tdf_src["dt"]=pd.to_datetime(tdf_src["dt"])
        oos=tdf_src[(tdf_src["dt"]>=mid)&(tdf_src["dt"]<fe)]
        if len(oos)==0: continue
        print(f"\n  {label}:")
        print(f"    {'Type':>6} {'#':>5} {'%':>6} {'PnL':>9} {'Avg':>7} {'WR':>6}")
        for t, g in oos.groupby("t"):
            n=len(g); pnl=g["pnl"].sum(); avg=pnl/n; wr=(g["pnl"]>0).mean()*100
            print(f"    {t:>6} {n:>5} {n/len(oos)*100:>5.1f}% ${pnl:>+8,.0f} ${avg:>+6,.0f} {wr:>5.1f}%")

    print(f"\n{'='*100}")
    print("  REPORT COMPLETE")
    print(f"{'='*100}")
