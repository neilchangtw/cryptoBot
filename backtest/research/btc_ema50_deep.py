"""
1h Swing+FR / EMA50 trailing 深入分析
全年 +$891 但滾動 4/10 → 找出哪裡不穩、能不能修
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;SAFENET_PCT=0.03;MAX_SAME=2

def fetch(interval,days=365):
    all_d=[];end=int(datetime.now().timestamp()*1000);cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":"BTCUSDT","interval":interval,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_d.extend(d);cur=d[-1][0]+1;_time.sleep(0.1)
        except:break
    if not all_d:return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

def fetch_funding(days=365):
    all_fr=[];cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000);end=int(datetime.now().timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://fapi.binance.com/fapi/v1/fundingRate",params={"symbol":"BTCUSDT","startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_fr.extend(d);cur=d[-1]["fundingTime"]+1;_time.sleep(0.1)
        except:break
    if not all_fr:return pd.Series(dtype=float)
    fr=pd.DataFrame(all_fr);fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    return fr.set_index("dt")["rate"].resample("1h").ffill()

print("Fetching...", end=" ", flush=True)
raw=fetch("1h",365);fr_s=fetch_funding(365)

df=raw.copy()
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
for i in range(w,len(df)-w):
    if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
    if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
# ATR 趨勢
df["atr_ma50"]=df["atr"].rolling(50).mean()
df["atr_rising"]=df["atr"]>df["atr_ma50"]
# BB
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_width"]=(df["bb_mid"]+2*bbs-(df["bb_mid"]-2*bbs))/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
# FR
df=df.set_index("datetime");df["fr"]=fr_s.reindex(df.index,method="ffill")
df["fr_pctile"]=df["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df=df.reset_index().dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"{len(df)} bars")

def run_detail(data):
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
            elif bars>=2 and close<=row["ema50"]:
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
            elif bars>=2 and close>=row["ema50"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        # Swing 突破
        if i<1:continue
        l=close>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
        s=close<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
        fr_p=row.get("fr_pctile",50)
        if not np.isnan(fr_p):
            if fr_p>80:l=False
            if fr_p<20:s=False

        info={"entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "entry_atr":row["atr"],"entry_vol":row["vol_ratio"],"atr_rising":row["atr_rising"],
              "bb_width_pctile":row["bb_width_pctile"],"fr_pctile":fr_p,
              "dt":row["datetime"],"entry_price":ep}

        if l and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf=run_detail(df)
tdf["month"]=pd.to_datetime(tdf["dt"]).dt.to_period("M")
wins=tdf[tdf["pnl"]>0];losses=tdf[tdf["pnl"]<=0]
print(f"Trades: {len(tdf)}, W: {len(wins)}, L: {len(losses)}")

# ============================================================
print(f"\n{'='*100}")
print("1. 收益結構")
print(f"{'='*100}")

trail=tdf[tdf["type"]=="Trail"];sn=tdf[tdf["type"]=="SafeNet"]
print(f"""
  Trail 出場：{len(trail)} 筆, ${trail['pnl'].sum():+,.0f} (avg ${trail['pnl'].mean():+,.2f})
  SafeNet：{len(sn)} 筆, ${sn['pnl'].sum():+,.0f} (avg ${sn['pnl'].mean():+,.2f})
  總計：${tdf['pnl'].sum():+,.0f}

  勝率：{(tdf['pnl']>0).mean()*100:.1f}%
  平均獲利：${wins['pnl'].mean():+,.2f}
  平均虧損：${losses['pnl'].mean():+,.2f}
  賺賠比：{abs(wins['pnl'].mean()/losses['pnl'].mean()):.2f}:1
  平均持倉：{tdf['bars'].mean():.0f} bars ({tdf['bars'].mean():.0f}h)
""")

# ============================================================
print(f"{'='*100}")
print("2. 逐月分析（找不穩定的月份）")
print(f"{'='*100}")

print(f"\n  {'月份':<10s} {'筆':>4s} {'損益':>8s} {'勝率':>6s} {'W均':>7s} {'L均':>7s} {'持倉':>5s}")
print(f"  {'-'*50}")
for m in sorted(tdf["month"].unique()):
    sub=tdf[tdf["month"]==m]
    w_s=sub[sub["pnl"]>0];l_s=sub[sub["pnl"]<=0]
    wr=len(w_s)/len(sub)*100 if len(sub)>0 else 0
    wa=w_s["pnl"].mean() if len(w_s)>0 else 0
    la=l_s["pnl"].mean() if len(l_s)>0 else 0
    flag=" <<<" if sub["pnl"].sum()<-50 else(" !!!" if sub["pnl"].sum()>100 else "")
    print(f"  {str(m):<10s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {wr:>5.0f}% ${wa:>+5.0f} ${la:>+5.0f} {sub['bars'].mean():>4.0f}h{flag}")

# ============================================================
print(f"\n{'='*100}")
print("3. 虧損交易分析（這些交易有什麼共同特徵？）")
print(f"{'='*100}")

print(f"\n  {'特徵':<20s} {'虧損筆':>10s} {'獲利筆':>10s} {'差異':>10s}")
print(f"  {'-'*55}")
for col,label in [("entry_hour","進場小時"),("entry_atr","ATR"),("entry_vol","成交量比"),
                    ("atr_rising","ATR上升中%"),("bb_width_pctile","BB寬度pctile"),("fr_pctile","FR pctile"),("bars","持倉hours")]:
    if col in losses.columns and col in wins.columns:
        if col=="atr_rising":
            lv=losses[col].mean()*100;wv=wins[col].mean()*100
        else:
            lv=losses[col].mean();wv=wins[col].mean()
        print(f"  {label:<20s} {lv:>10.1f} {wv:>10.1f} {lv-wv:>+10.1f}")

# ============================================================
print(f"\n{'='*100}")
print("4. 大賺 vs 大虧的交易")
print(f"{'='*100}")

# Top 5 獲利
print(f"\n  Top 5 獲利交易：")
top_w=tdf.nlargest(5,"pnl")
for _,t in top_w.iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | {t['dt']}")

print(f"\n  Top 5 虧損交易：")
top_l=tdf.nsmallest(5,"pnl")
for _,t in top_l.iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | {t['dt']}")

# ============================================================
print(f"\n{'='*100}")
print("5. 浮盈效率（抓到多少行情？）")
print(f"{'='*100}")

print(f"\n  平均最大浮盈：${tdf['mf'].mean():,.2f}")
print(f"  平均實際獲利：${tdf['pnl'].mean():+,.2f}")
print(f"  利潤保留率：{tdf['pnl'].mean()/tdf['mf'].mean()*100:.0f}%")

print(f"\n  獲利筆：mf avg ${wins['mf'].mean():,.2f} → 實際 ${wins['pnl'].mean():+,.2f} ({wins['pnl'].mean()/wins['mf'].mean()*100:.0f}%)")
print(f"  虧損筆：mf avg ${losses['mf'].mean():,.2f} → 實際 ${losses['pnl'].mean():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("6. 持倉時間 vs 獲利")
print(f"{'='*100}")

print(f"\n  {'持倉':>8s} {'筆':>4s} {'損益':>8s} {'勝率':>6s} {'W均':>7s}")
print(f"  {'-'*35}")
for lo,hi,label in [(0,6,"<6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
    sub=tdf[(tdf["bars"]>=lo)&(tdf["bars"]<hi)]
    if len(sub)==0:continue
    ws=sub[sub["pnl"]>0]
    print(f"  {label:>8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {len(ws)/len(sub)*100:>5.0f}% ${ws['pnl'].mean() if len(ws)>0 else 0:>+5.0f}")

# ============================================================
print(f"\n{'='*100}")
print("7. 多空分析")
print(f"{'='*100}")

for side,label in [("long","做多"),("short","做空")]:
    sub=tdf[tdf["side"]==side];ws=sub[sub["pnl"]>0];ls=sub[sub["pnl"]<=0]
    print(f"\n  {label}：{len(sub)} 筆, ${sub['pnl'].sum():+,.0f}, 勝率 {len(ws)/len(sub)*100:.0f}%")
    print(f"    W均 ${ws['pnl'].mean():+,.2f}, L均 ${ls['pnl'].mean():+,.2f}, RR {abs(ws['pnl'].mean()/ls['pnl'].mean()) if len(ls)>0 and ls['pnl'].mean()!=0 else 999:.1f}:1")

# ============================================================
print(f"\n{'='*100}")
print("8. ATR 趨勢 vs 績效（能不能加 ATR 過濾改善穩定性？）")
print(f"{'='*100}")

atr_up=tdf[tdf["atr_rising"]==True];atr_dn=tdf[tdf["atr_rising"]==False]
print(f"\n  ATR 上升中進場：{len(atr_up)} 筆, ${atr_up['pnl'].sum():+,.0f}, 勝率 {(atr_up['pnl']>0).mean()*100:.0f}%")
print(f"  ATR 下降中進場：{len(atr_dn)} 筆, ${atr_dn['pnl'].sum():+,.0f}, 勝率 {(atr_dn['pnl']>0).mean()*100:.0f}%")

# BB 寬度
print(f"\n  BB 寬度分組：")
for lo,hi,label in [(0,30,"窄<30"),(30,50,"中30-50"),(50,70,"寬50-70"),(70,100,"很寬>70")]:
    sub=tdf[(tdf["bb_width_pctile"]>=lo)&(tdf["bb_width_pctile"]<hi)]
    if len(sub)==0:continue
    print(f"    {label}: {len(sub)} 筆, ${sub['pnl'].sum():+,.0f}, 勝率 {(sub['pnl']>0).mean()*100:.0f}%")

print(f"\n{'='*100}")
print("SUMMARY")
print(f"{'='*100}")
print(f"""
  全年 +$891 的拆解：
  - Trail 出場：{len(trail)} 筆 ${trail['pnl'].sum():+,.0f}
  - SafeNet：{len(sn)} 筆 ${sn['pnl'].sum():+,.0f}
  - 賺賠比 {abs(wins['pnl'].mean()/losses['pnl'].mean()):.1f}:1
  - 每筆平均 ${tdf['pnl'].mean():+,.2f}
  - 持倉平均 {tdf['bars'].mean():.0f}h

  可能的穩定性改善方向：
  （看上面哪些指標虧損筆 vs 獲利筆差異最大）
""")
print("Done.")
