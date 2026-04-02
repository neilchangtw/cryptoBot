"""
ETH 波動率 Squeeze<20 深入分析
全年 +$1,620, OOS +$786 — 拆解每個環節
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

def fetch(symbol, interval, days=365):
    all_d=[];end=int(datetime.now().timestamp()*1000);cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":symbol,"interval":interval,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_d.extend(d);cur=d[-1][0]+1;_time.sleep(0.1)
        except:break
    if not all_d:return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

print("Fetching ETH 1h (1 year)...", end=" ", flush=True)
raw=fetch("ETHUSDT","1h",365)
df=raw.copy()
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["atr_ma50"]=df["atr"].rolling(50).mean()
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"{len(df)} bars")

def run_detail(data, squeeze_pctile=20):
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf}); c=True
            elif bars>=2 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf}); c=True
            elif bars>=2 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<squeeze_pctile
        l=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0
        s=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0

        info={"entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "entry_atr":row["atr"],"entry_vol":row["vol_ratio"],"entry_rsi":row["rsi"],
              "entry_bb_pctile":row["bb_width_pctile"],"entry_atr_pctile":row["atr_pctile"],
              "entry_price":ep,"dt":row["datetime"]}

        if l and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf=run_detail(df)
tdf["month"]=pd.to_datetime(tdf["dt"]).dt.to_period("M")
wins=tdf[tdf["pnl"]>0];losses=tdf[tdf["pnl"]<=0]
trail=tdf[tdf["type"]=="Trail"];sn=tdf[tdf["type"]=="SafeNet"]
print(f"Trades: {len(tdf)} (W:{len(wins)} L:{len(losses)} Trail:{len(trail)} SN:{len(sn)})")

# ============================================================
print(f"\n{'='*100}")
print("1. 收益結構")
print(f"{'='*100}")

total_fee=len(tdf)*1.6
print(f"""
  Trail: {len(trail)} trades, ${trail['pnl'].sum():+,.0f} (avg ${trail['pnl'].mean():+,.2f})
  SafeNet: {len(sn)} trades, ${sn['pnl'].sum():+,.0f} (avg ${sn['pnl'].mean():+,.2f})
  Fee(est): ${total_fee:,.0f}
  Net: ${tdf['pnl'].sum():+,.0f}

  WR: {(tdf['pnl']>0).mean()*100:.1f}%
  W avg: ${wins['pnl'].mean():+,.2f}
  L avg: ${losses['pnl'].mean():+,.2f}
  RR: {abs(wins['pnl'].mean()/losses['pnl'].mean()):.2f}:1
  Avg hold: {tdf['bars'].mean():.0f}h
""")

# ============================================================
print(f"{'='*100}")
print("2. Monthly")
print(f"{'='*100}")

print(f"\n  {'Month':<10s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'W avg':>7s} {'L avg':>7s} {'Hold':>5s}")
print(f"  {'-'*50}")
for m in sorted(tdf["month"].unique()):
    sub=tdf[tdf["month"]==m];ws=sub[sub["pnl"]>0];ls=sub[sub["pnl"]<=0]
    wr=len(ws)/len(sub)*100 if len(sub)>0 else 0
    wa=ws["pnl"].mean() if len(ws)>0 else 0;la=ls["pnl"].mean() if len(ls)>0 else 0
    flag=" <<<" if sub["pnl"].sum()<-100 else(" !!!" if sub["pnl"].sum()>100 else "")
    print(f"  {str(m):<10s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {wr:>5.0f}% ${wa:>+5.0f} ${la:>+5.0f} {sub['bars'].mean():>4.0f}h{flag}")

prof_m=sum(1 for m in tdf.groupby("month")["pnl"].sum() if m>0)
tot_m=len(tdf["month"].unique())
print(f"\n  Profitable months: {prof_m}/{tot_m}")

# ============================================================
print(f"\n{'='*100}")
print("3. Win vs Loss characteristics")
print(f"{'='*100}")

print(f"\n  {'Feature':<20s} {'Losses':>10s} {'Wins':>10s} {'Diff':>10s}")
print(f"  {'-'*55}")
for col,label in [("entry_hour","Hour"),("entry_atr","ATR"),("entry_vol","Volume"),
                    ("entry_rsi","RSI"),("entry_bb_pctile","BB width pctile"),
                    ("entry_atr_pctile","ATR pctile"),("bars","Hold(h)")]:
    if col in losses.columns and col in wins.columns:
        lv=losses[col].mean();wv=wins[col].mean()
        print(f"  {label:<20s} {lv:>10.1f} {wv:>10.1f} {lv-wv:>+10.1f}")

# ============================================================
print(f"\n{'='*100}")
print("4. Hold time vs profit")
print(f"{'='*100}")

print(f"\n  {'Hold':>8s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'W avg':>7s}")
print(f"  {'-'*38}")
for lo,hi,label in [(0,3,"<3h"),(3,6,"3-6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
    sub=tdf[(tdf["bars"]>=lo)&(tdf["bars"]<hi)]
    if len(sub)==0:continue
    ws=sub[sub["pnl"]>0]
    print(f"  {label:>8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {len(ws)/len(sub)*100:>5.0f}% ${ws['pnl'].mean() if len(ws)>0 else 0:>+5.0f}")

# ============================================================
print(f"\n{'='*100}")
print("5. Long vs Short")
print(f"{'='*100}")

for side,label in [("long","LONG"),("short","SHORT")]:
    sub=tdf[tdf["side"]==side];ws=sub[sub["pnl"]>0];ls=sub[sub["pnl"]<=0]
    rr=abs(ws["pnl"].mean()/ls["pnl"].mean()) if len(ls)>0 and ls["pnl"].mean()!=0 else 999
    print(f"\n  {label}: {len(sub)} trades, ${sub['pnl'].sum():+,.0f}, WR {len(ws)/len(sub)*100:.0f}%, RR {rr:.1f}:1")
    print(f"    W avg ${ws['pnl'].mean():+,.2f}, L avg ${ls['pnl'].mean():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("6. Profit efficiency (max float vs actual)")
print(f"{'='*100}")

print(f"\n  Avg max float: ${tdf['mf'].mean():,.2f}")
print(f"  Avg actual PnL: ${tdf['pnl'].mean():+,.2f}")
print(f"  Retention: {tdf['pnl'].mean()/tdf['mf'].mean()*100:.0f}%")
print(f"\n  Wins: mf ${wins['mf'].mean():,.2f} -> pnl ${wins['pnl'].mean():+,.2f} ({wins['pnl'].mean()/wins['mf'].mean()*100:.0f}%)")
print(f"  Losses: mf ${losses['mf'].mean():,.2f} -> pnl ${losses['pnl'].mean():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("7. Top trades")
print(f"{'='*100}")

print(f"\n  Top 5 wins:")
for _,t in tdf.nlargest(5,"pnl").iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | {t['dt']}")

print(f"\n  Top 5 losses:")
for _,t in tdf.nsmallest(5,"pnl").iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | {t['dt']}")

# ============================================================
print(f"\n{'='*100}")
print("8. Hour / Weekday")
print(f"{'='*100}")

print(f"\n  {'Hour':<8s} {'N':>4s} {'PnL':>8s} {'WR':>6s}")
print(f"  {'-'*30}")
for h in range(24):
    sub=tdf[tdf["entry_hour"]==h]
    if len(sub)==0:continue
    flag=" <<<" if sub["pnl"].sum()<-50 else(" !!!" if sub["pnl"].sum()>50 else "")
    print(f"  {h:02d}:00   {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {(sub['pnl']>0).mean()*100:>5.0f}%{flag}")

print(f"\n  {'Day':<8s} {'N':>4s} {'PnL':>8s} {'WR':>6s}")
print(f"  {'-'*30}")
for d in range(7):
    name=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]
    sub=tdf[tdf["entry_weekday"]==d]
    if len(sub)==0:continue
    flag=" <<<" if sub["pnl"].sum()<-80 else(" !!!" if sub["pnl"].sum()>80 else "")
    print(f"  {name:<8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {(sub['pnl']>0).mean()*100:>5.0f}%{flag}")

# ============================================================
print(f"\n{'='*100}")
print("9. Walk-Forward (8m/4m) + Rolling (3m->1m)")
print(f"{'='*100}")

split=int(len(df)*8/12);dev=df.iloc[:split].reset_index(drop=True);oos=df.iloc[split:].reset_index(drop=True)
td=run_detail(dev);to=run_detail(oos)
dp=td["pnl"].sum();op=to["pnl"].sum()
owr=(to["pnl"]>0).mean()*100 if len(to)>0 else 0
ow=to[to["pnl"]>0];ol=to[to["pnl"]<=0]
opf=ow["pnl"].sum()/abs(ol["pnl"].sum()) if len(ol)>0 and ol["pnl"].sum()!=0 else 999
print(f"\n  DEV(8m): {len(td)} trades, ${dp:+,.0f}")
print(f"  OOS(4m): {len(to)} trades, ${op:+,.0f}, WR {owr:.1f}%, PF {opf:.2f}")

months=df["month"].unique()
print(f"\n  Rolling 3m->1m:")
fold_pnls=[]
for fold in range(len(months)-3):
    test_m=months[fold+3];ft=df[df["month"]==test_m].reset_index(drop=True)
    if len(ft)<20:continue
    t=run_detail(ft)
    p=t["pnl"].sum() if len(t)>0 else 0
    fold_pnls.append(p)
    print(f"    {str(test_m)}: ${p:>+8,.0f} ({len(t)} trades)")
prof=sum(1 for x in fold_pnls if x>0)
print(f"  Total: ${sum(fold_pnls):+,.0f}, Profitable {prof}/{len(fold_pnls)} folds")

# ============================================================
print(f"\n{'='*100}")
print("SUMMARY")
print(f"{'='*100}")

print(f"""
  ETH Squeeze<20 + EMA20 trailing (1h, 1 year):
  PnL: ${tdf['pnl'].sum():+,.0f} ({len(tdf)} trades)
  WR: {(tdf['pnl']>0).mean()*100:.1f}%, RR: {abs(wins['pnl'].mean()/losses['pnl'].mean()):.1f}:1
  W avg: ${wins['pnl'].mean():+,.0f}, L avg: ${losses['pnl'].mean():+,.0f}
  Hold avg: {tdf['bars'].mean():.0f}h
  OOS: ${op:+,.0f} (PF {opf:.2f})
  Rolling: ${sum(fold_pnls):+,.0f} ({prof}/{len(fold_pnls)} folds)
  Monthly: {prof_m}/{tot_m} profitable
""")

print("Done.")
