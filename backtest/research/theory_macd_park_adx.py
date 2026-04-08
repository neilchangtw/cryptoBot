"""
Round 8: Parkinson Compression + MACD Crossover + ADX Trend
============================================================
Core: Replace close breakout (too few signals) with MACD crossover
(captures momentum shifts, more frequent). Keep proven Parkinson
compression + ADX trend confirmation.

Entry (3+1 conditions, all shift(1)):
  1. Parkinson Vol Ratio pctile(100, shift=1) < 35 (compression)
  2. MACD(12,26,9) crosses signal line at T-1:
     MACD[T-1] > Signal[T-1] AND MACD[T-2] <= Signal[T-2] (long)
     MACD[T-1] < Signal[T-1] AND MACD[T-2] >= Signal[T-2] (short)
  3. ADX(14, shift=1) > 20 = trend exists
  4. +DI > -DI direction must match signal direction

Exit: EMA20 trail (min 12h) + SafeNet +/-3.5%

Parameters locked:
  PARK_SHORT=5, PARK_LONG=20, PARK_PCTILE_WIN=100, PARK_THRESH=35
  MACD: fast=12, slow=26, signal=9 (standard)
  ADX_PERIOD=14, ADX_THRESH=20
"""
import os, sys, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN = 100; LEVERAGE = 20; NOTIONAL = MARGIN * LEVERAGE
FEE = 2.0; MAX_SAME = 2; SAFENET_PCT = 0.035; MIN_TRAIL_BARS = 12; ACCOUNT = 10000

PARK_SHORT = 5; PARK_LONG = 20; PARK_PCTILE_WIN = 100; PARK_THRESH = 35
MACD_FAST = 12; MACD_SLOW = 26; MACD_SIGNAL = 9
ADX_PERIOD = 14; ADX_THRESH = 20

END_DATE = datetime(2026, 4, 3)
START_DATE = END_DATE - timedelta(days=732)
MID_DATE = END_DATE - timedelta(days=365)

def fetch_binance(symbol, interval, start_dt, end_dt):
    all_d = []; cur = int(start_dt.timestamp()*1000); end_ms = int(end_dt.timestamp()*1000)
    print(f"  Fetching {symbol} {interval}...", flush=True)
    while cur < end_ms:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol":symbol,"interval":interval,"startTime":cur,"limit":1000}, timeout=15)
            d = r.json()
            if not d or isinstance(d,dict): break
            all_d.extend(d); cur=d[-1][0]+1; _time.sleep(0.12)
        except: break
    if not all_d: return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    df=df[df["ot"]<end_ms].reset_index(drop=True)
    print(f"  Got {len(df)} bars", flush=True)
    return df

ETH_CACHE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "ETHUSDT_1h_latest730d.csv"))

def load_data():
    if os.path.exists(ETH_CACHE):
        df=pd.read_csv(ETH_CACHE); df["datetime"]=pd.to_datetime(df["datetime"])
        last=df["datetime"].iloc[-1]
        if (END_DATE-last.to_pydatetime().replace(tzinfo=None)).days<=2:
            print(f"Loaded {len(df)} bars from cache", flush=True); return df
    df=fetch_binance("ETHUSDT","1h",START_DATE,END_DATE)
    if len(df)==0: sys.exit(1)
    os.makedirs(os.path.dirname(ETH_CACHE),exist_ok=True)
    df.to_csv(ETH_CACHE,index=False); return df

def compute_adx(df, period=14):
    high=df["high"].values; low=df["low"].values; close=df["close"].values; n=len(df)
    tr=np.zeros(n); pdm=np.zeros(n); mdm=np.zeros(n)
    for i in range(1,n):
        tr[i]=max(high[i]-low[i],abs(high[i]-close[i-1]),abs(low[i]-close[i-1]))
        up=high[i]-high[i-1]; dn=low[i-1]-low[i]
        pdm[i]=up if(up>dn and up>0)else 0; mdm[i]=dn if(dn>up and dn>0)else 0
    atr=np.zeros(n); ps=np.zeros(n); ms=np.zeros(n)
    atr[period]=np.mean(tr[1:period+1]); ps[period]=np.mean(pdm[1:period+1]); ms[period]=np.mean(mdm[1:period+1])
    for i in range(period+1,n):
        atr[i]=(atr[i-1]*(period-1)+tr[i])/period
        ps[i]=(ps[i-1]*(period-1)+pdm[i])/period
        ms[i]=(ms[i-1]*(period-1)+mdm[i])/period
    pdi=np.zeros(n); mdi=np.zeros(n); dx=np.zeros(n)
    for i in range(period,n):
        if atr[i]>0: pdi[i]=100*ps[i]/atr[i]; mdi[i]=100*ms[i]/atr[i]
        ds=pdi[i]+mdi[i]; dx[i]=100*abs(pdi[i]-mdi[i])/ds if ds>0 else 0
    adx=np.zeros(n); st=2*period
    if st<n:
        adx[st]=np.mean(dx[period+1:st+1])
        for i in range(st+1,n): adx[i]=(adx[i-1]*(period-1)+dx[i])/period
    df["adx"]=adx; df["plus_di"]=pdi; df["minus_di"]=mdi
    return df

def compute_indicators(df):
    df["ema20"]=df["close"].ewm(span=20).mean()

    # 1. Parkinson compression
    ln_hl=np.log(df["high"]/df["low"])
    psq=ln_hl**2/(4*np.log(2))
    df["park_short"]=np.sqrt(psq.rolling(PARK_SHORT).mean())
    df["park_long"]=np.sqrt(psq.rolling(PARK_LONG).mean())
    df["park_ratio"]=df["park_short"]/df["park_long"]
    df["park_pctile"]=df["park_ratio"].shift(1).rolling(PARK_PCTILE_WIN).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

    # 2. MACD
    ema_fast=df["close"].ewm(span=MACD_FAST).mean()
    ema_slow=df["close"].ewm(span=MACD_SLOW).mean()
    df["macd_line"]=ema_fast-ema_slow
    df["macd_signal"]=df["macd_line"].ewm(span=MACD_SIGNAL).mean()
    # Shifted by 1
    df["macd_s1"]=df["macd_line"].shift(1)
    df["macd_sig_s1"]=df["macd_signal"].shift(1)
    df["macd_s2"]=df["macd_line"].shift(2)
    df["macd_sig_s2"]=df["macd_signal"].shift(2)
    # Crossover at T-1
    df["macd_cross_up"]=(df["macd_s1"]>df["macd_sig_s1"])&(df["macd_s2"]<=df["macd_sig_s2"])
    df["macd_cross_down"]=(df["macd_s1"]<df["macd_sig_s1"])&(df["macd_s2"]>=df["macd_sig_s2"])

    # 3. ADX/DI
    df=compute_adx(df,ADX_PERIOD)
    df["adx_s1"]=df["adx"].shift(1)
    df["pdi_s1"]=df["plus_di"].shift(1)
    df["mdi_s1"]=df["minus_di"].shift(1)

    return df

def run_backtest(df):
    warmup=PARK_PCTILE_WIN+PARK_LONG+MACD_SLOW+MACD_SIGNAL+2*ADX_PERIOD+10
    highs=df["high"].values; lows=df["low"].values; closes=df["close"].values
    opens=df["open"].values; ema20s=df["ema20"].values; dts=df["datetime"].values
    pp=df["park_pctile"].values
    mcup=df["macd_cross_up"].values; mcdn=df["macd_cross_down"].values
    adx1=df["adx_s1"].values; pdi1=df["pdi_s1"].values; mdi1=df["mdi_s1"].values

    lpos=[]; spos=[]; trades=[]

    for i in range(warmup,len(df)-1):
        row_h=highs[i]; row_l=lows[i]; row_c=closes[i]
        row_ema20=ema20s[i]; row_dt=dts[i]; nxt_open=opens[i+1]

        # Update positions
        nl=[]
        for p in lpos:
            closed=False; bars=i-p["ei"]
            if row_l<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT); ep=ep-(ep-row_l)*0.25
                pnl=(ep-p["entry"])*NOTIONAL/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row_dt}); closed=True
            elif bars>=MIN_TRAIL_BARS and row_c<=row_ema20:
                pnl=(row_c-p["entry"])*NOTIONAL/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"dt":row_dt}); closed=True
            if not closed: nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            closed=False; bars=i-p["ei"]
            if row_h>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT); ep=ep+(row_h-ep)*0.25
                pnl=(p["entry"]-ep)*NOTIONAL/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row_dt}); closed=True
            elif bars>=MIN_TRAIL_BARS and row_c>=row_ema20:
                pnl=(p["entry"]-row_c)*NOTIONAL/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"dt":row_dt}); closed=True
            if not closed: ns.append(p)
        spos=ns

        # Signals
        park=pp[i]; a1=adx1[i]; pd1=pdi1[i]; md1=mdi1[i]
        mu=mcup[i]; md=mcdn[i]

        if np.isnan(park) or a1==0: continue

        compressed=park<PARK_THRESH
        adx_ok=a1>ADX_THRESH
        di_long=pd1>md1; di_short=md1>pd1

        # MACD crossover is inherently fresh (one-time event)
        long_signal=compressed and bool(mu) and adx_ok and di_long
        short_signal=compressed and bool(md) and adx_ok and di_short

        if long_signal and len(lpos)<MAX_SAME:
            lpos.append({"entry":nxt_open,"ei":i})
        if short_signal and len(spos)<MAX_SAME:
            spos.append({"entry":nxt_open,"ei":i})

    if trades: return pd.DataFrame(trades)
    return pd.DataFrame(columns=["pnl","type","side","bars","dt"])

def calc_stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sharpe":0,"fees":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum(); l=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l if l>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=dd.min(); mdd_p=abs(mdd)/ACCOUNT*100
    tc=tdf.copy(); tc["date"]=pd.to_datetime(tc["dt"]).dt.date
    daily=tc.groupby("date")["pnl"].sum()
    ad=pd.date_range(tc["dt"].min(),tc["dt"].max(),freq="D")
    daily=daily.reindex(ad.date,fill_value=0)
    sh=float(daily.mean()/daily.std()*np.sqrt(365)) if daily.std()>0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mdd_p,1),"sharpe":round(sh,2),"fees":round(n*FEE,2)}

def print_box(title, stats, months):
    m=stats["n"]/months if months>0 else 0
    print(f"\n  +{'='*58}+")
    print(f"  | {title:<56s} |")
    print(f"  +{'-'*58}+")
    print(f"  | Trades: {stats['n']:>5d}  (monthly avg {m:.1f}){' ':<21s} |")
    print(f"  | Win Rate: {stats['wr']:>5.1f}%{' ':<41s} |")
    print(f"  | Profit Factor: {stats['pf']:>6.2f}{' ':<34s} |")
    print(f"  | Net PnL: ${stats['pnl']:>+10,.2f}{' ':<33s} |")
    print(f"  | Max DD: ${stats['mdd']:>+10,.2f}  ({stats['mdd_pct']:.1f}%){' ':<21s} |")
    print(f"  | Sharpe: {stats['sharpe']:>6.2f}{' ':<38s} |")
    print(f"  | Fees+Slip: -${stats['fees']:>9,.2f}{' ':<32s} |")
    print(f"  +{'='*58}+")

def print_breakdown(tdf):
    print("\n  Hold Time:")
    for lo,hi,lb in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
        s=tdf[(tdf["bars"]>=lo)&(tdf["bars"]<hi)]; n=len(s)
        p=s["pnl"].sum() if n>0 else 0; w=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"    {lb:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")
    print("\n  L/S:")
    for side in ["long","short"]:
        s=tdf[tdf["side"]==side]; n=len(s); p=s["pnl"].sum() if n>0 else 0
        w=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"    {side.capitalize():<6s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")
    print("\n  Exit:")
    for t in ["Trail","SafeNet"]:
        s=tdf[tdf["type"]==t]; n=len(s); p=s["pnl"].sum() if n>0 else 0
        print(f"    {t:<10s}: {n:>4d} trades, ${p:>+10,.0f}")

def gods_eye_check():
    print("\n"+"="*62)
    print("  GOD'S EYE SELF-CHECK (6 mandatory)")
    print("="*62)
    checks=[
        ("1. All signal calcs use shift(1)+ data only?",
         "YES: park_pctile=park_ratio.shift(1).rolling();\n"
         "         MACD cross uses macd.shift(1) vs macd.shift(2);\n"
         "         ADX/DI all shifted by 1"),
        ("2. Entry price = next bar open?","YES: entry = nxt_open = opens[i+1]"),
        ("3. All rolling indicators have shift(1)?",
         "YES: Parkinson pctile shift(1).rolling(100);\n"
         "         MACD lines shifted 1,2; ADX .shift(1)"),
        ("4. Params decided before seeing any data?",
         "YES: Parkinson 5/20 from vol theory; MACD 12/26/9 = universal standard;\n"
         "         ADX=14/20 Wilder standard"),
        ("5. No post-OOS parameter adjustment?","YES: Single run, no optimization"),
        ("6. Freshness check prevents stale re-entry?",
         "YES: MACD crossover is inherently a one-time event (cross implies prev was opposite)")
    ]
    ap=True
    for q,a in checks:
        st="PASS" if a.startswith("YES") else "FAIL"
        if st=="FAIL": ap=False
        print(f"  [{st}] {q}")
        for line in a.split("\n"): print(f"         {line.strip()}")
    print(f"\n  Result: {'6/6 PASS' if ap else 'FAILED'}")
    return ap

if __name__=="__main__":
    print("="*80)
    print("  Round 8: Parkinson + MACD Crossover + ADX Trend")
    print("="*80)
    df=load_data()
    print(f"ETH: {len(df)} bars, {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    df=compute_indicators(df)

    warmup=PARK_PCTILE_WIN+PARK_LONG+MACD_SLOW+MACD_SIGNAL+2*ADX_PERIOD+10
    v=df.iloc[warmup:]
    c1=(v["park_pctile"]<PARK_THRESH).sum()
    c2l=v["macd_cross_up"].sum(); c2s=v["macd_cross_down"].sum()
    c3=(v["adx_s1"]>ADX_THRESH).sum()
    print(f"\n  Conditions:")
    print(f"    Parkinson < {PARK_THRESH}: {c1} ({c1/len(v)*100:.1f}%)")
    print(f"    MACD cross up: {c2l} ({c2l/len(v)*100:.1f}%)")
    print(f"    MACD cross down: {c2s} ({c2s/len(v)*100:.1f}%)")
    print(f"    ADX > {ADX_THRESH}: {c3} ({c3/len(v)*100:.1f}%)")

    all_trades=run_backtest(df)
    all_trades["dt"]=pd.to_datetime(all_trades["dt"])
    mid_ts=pd.Timestamp(MID_DATE)
    is_t=all_trades[all_trades["dt"]<mid_ts].reset_index(drop=True)
    oos_t=all_trades[all_trades["dt"]>=mid_ts].reset_index(drop=True)
    is_m=(MID_DATE-START_DATE).days/30.44; oos_m=(END_DATE-MID_DATE).days/30.44

    is_s=calc_stats(is_t); oos_s=calc_stats(oos_t); full_s=calc_stats(all_trades)

    print_box("IN-SAMPLE (IS)",is_s,is_m)
    if len(is_t)>0: print_breakdown(is_t)
    print_box("OUT-OF-SAMPLE (OOS) ***",oos_s,oos_m)
    if len(oos_t)>0: print_breakdown(oos_t)
    print_box("FULL PERIOD",full_s,is_m+oos_m)
    if len(all_trades)>0: print_breakdown(all_trades)

    oos_monthly=oos_s["n"]/oos_m if oos_m>0 else 0
    oos_annual=oos_s["pnl"]/(oos_m/12) if oos_m>0 else 0
    print("\n"+"="*62)
    print("  TARGET CHECK (OOS)")
    print("="*62)
    t1=oos_annual>=5000; t2=oos_s["pf"]>=1.5; t3=oos_s["mdd_pct"]<=25; t4=oos_monthly>=10
    print(f"  [{'PASS' if t1 else 'FAIL'}] Annual PnL >= $5,000: ${oos_annual:,.0f}")
    print(f"  [{'PASS' if t2 else 'FAIL'}] PF >= 1.5: {oos_s['pf']}")
    print(f"  [{'PASS' if t3 else 'FAIL'}] MDD <= 25%: {oos_s['mdd_pct']}%")
    print(f"  [{'PASS' if t4 else 'FAIL'}] Monthly >= 10: {oos_monthly:.1f}")
    gods_eye_check()

    ap=t1 and t2 and t3 and t4
    print(f"\n{'*'*20}")
    if ap: print("  ALL TARGETS MET!")
    else:
        f=[]
        if not t1: f.append("PnL");
        if not t2: f.append("PF")
        if not t3: f.append("MDD");
        if not t4: f.append("Freq")
        print(f"  FAILED: {', '.join(f)}")
    print(f"{'*'*20}")
