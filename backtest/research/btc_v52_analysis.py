"""
v5.2 深度分析 — 找出剩餘的優化空間
ATR MA50 + BB寬度<50 + TP1 1.25x + TS 8h
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2; TP1_MULT=1.25; TS_BARS=96

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

def add_ind(df):
    tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"]=tr.rolling(14).mean()
    df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["atr_ma50"]=df["atr"].rolling(50).mean()
    d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
    df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
    df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    df["vol_ma20"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    return df.dropna().reset_index(drop=True)

print("Fetching 1 year...", end=" ", flush=True)
raw5=fetch("5m",365);df5=add_ind(raw5)
raw1h=fetch("1h",365);df1h=add_ind(raw1h)
m=df1h[["datetime","rsi"]].copy();m.rename(columns={"rsi":"h1_rsi"},inplace=True)
m["h1_rsi_prev"]=m["h1_rsi"].shift(1);m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
print(f"{len(df5)} bars")

def run_detail(data):
    lpos=[];spos=[];trades=[]
    for i in range(120,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*p["qty"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"long","bars":bars,"mf":mf}); c=True
            elif bars>=TS_BARS:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"mf":mf}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*p["qty"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"short","bars":bars,"mf":mf}); c=True
            elif bars>=TS_BARS:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"mf":mf}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        atr_ma=row["atr_ma50"] if not np.isnan(row["atr_ma50"]) else atr
        info={"entry_rsi":rsi,"entry_atr":atr,"entry_ap":ap,"entry_ema21_dev":row["price_vs_ema21"],
              "entry_vol":row["vol_ratio"],"h1_rsi":row["h1_rsi"],"h1_rsi_prev":row["h1_rsi_prev"],
              "entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "bb_width_pctile":row["bb_width_pctile"],"atr_vs_ma":atr/atr_ma if atr_ma>0 else 1,
              "dt":row["datetime"],"entry_price":ep}

        l_go=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]>=row["h1_rsi_prev"] and atr<=atr_ma
              and row["bb_width_pctile"]<50 and len(lpos)<MAX_SAME)
        s_go=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]<=row["h1_rsi_prev"] and atr<=atr_ma
              and row["bb_width_pctile"]<50 and len(spos)<MAX_SAME)
        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+TP1_MULT*atr,"ei":i,"mf":0,"info":info})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-TP1_MULT*atr,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf = run_detail(df5)
tdf["month"]=pd.to_datetime(tdf["dt"]).dt.to_period("M")
tp1=tdf[tdf["type"]=="TP1"]; ts=tdf[tdf["type"]=="TimeStop"]; sn=tdf[tdf["type"]=="SafeNet"]
print(f"Trades: {len(tdf)} (TP1:{len(tp1)} TS:{len(ts)} SN:{len(sn)})")

total_fee = len(tdf) * 1.6

# ============================================================
print(f"\n{'='*100}")
print("1. 收益結構")
print(f"{'='*100}")
print(f"""
  TP1 收入：       +${tp1['pnl'].sum():,.0f} ({len(tp1)} 筆, avg +${tp1['pnl'].mean():,.2f})
  TimeStop 虧損：  -${abs(ts['pnl'].sum()):,.0f} ({len(ts)} 筆, avg -${abs(ts['pnl'].mean()):,.2f})
  SafeNet 虧損：   -${abs(sn['pnl'].sum()):,.0f} ({len(sn)} 筆)
  手續費(估)：     -${total_fee:,.0f}
  淨利：           ${tdf['pnl'].sum():+,.0f}

  TimeStop/TP1 = {abs(ts['pnl'].sum())/max(tp1['pnl'].sum(),1)*100:.0f}%
""")

# ============================================================
print(f"{'='*100}")
print("2. TimeStop 分析（{} 筆）".format(len(ts)))
print(f"{'='*100}")

if len(ts) > 0:
    print(f"\n  損益分佈：avg ${ts['pnl'].mean():+,.2f}, min ${ts['pnl'].min():+,.2f}, max ${ts['pnl'].max():+,.2f}")
    print(f"\n  最大浮盈：avg ${ts['mf'].mean():,.2f}, max ${ts['mf'].max():,.2f}")
    print(f"    浮盈 = 0：{(ts['mf']<0.1).sum()} 筆（完全沒走對）")
    print(f"    浮盈 > $3：{(ts['mf']>3).sum()} 筆（接近 TP1）")

    print(f"\n  多空：做多 {len(ts[ts['side']=='long'])} ${ts[ts['side']=='long']['pnl'].sum():+,.0f} / 做空 {len(ts[ts['side']=='short'])} ${ts[ts['side']=='short']['pnl'].sum():+,.0f}")

    # TimeStop vs TP1 特徵
    print(f"\n  TimeStop vs TP1 進場特徵：")
    for col, label in [("entry_rsi","RSI"),("entry_ap","ATR pctile"),("bb_width_pctile","BB寬度pctile"),
                        ("entry_ema21_dev","EMA21偏離"),("atr_vs_ma","ATR/MA比"),("entry_hour","小時")]:
        if col in ts.columns and col in tp1.columns:
            t=ts[col].mean();p=tp1[col].mean()
            print(f"    {label:<15s}: TS {t:>8.2f} vs TP1 {p:>8.2f} (diff {t-p:>+.2f})")

# ============================================================
print(f"\n{'='*100}")
print("3. TP1 效率分析")
print(f"{'='*100}")

if len(tp1) > 0:
    print(f"\n  到達速度：avg {tp1['bars'].mean():.0f} bars ({tp1['bars'].mean()*5:.0f}min), median {tp1['bars'].median():.0f} bars")
    print(f"  最快 {tp1['bars'].min()} bars / 最慢 {tp1['bars'].max()} bars")
    print(f"\n  最大浮盈 vs 實際：avg mf ${tp1['mf'].mean():,.2f} vs pnl ${tp1['pnl'].mean():+,.2f}")
    print(f"  = TP1 抓到了浮盈的 {tp1['pnl'].mean()/max(tp1['mf'].mean(),0.01)*100:.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("4. 時段分析")
print(f"{'='*100}")

print(f"\n  {'小時':<8s} {'TP1':>4s} {'TS':>4s} {'SN':>4s} {'淨損益':>8s} {'TP1率':>6s}")
print(f"  {'-'*38}")
for h in range(24):
    sub=tdf[tdf["entry_hour"]==h]
    if len(sub)==0:continue
    t1=len(sub[sub["type"]=="TP1"]);tsn=len(sub[sub["type"]=="TimeStop"]);snn=len(sub[sub["type"]=="SafeNet"])
    net=sub["pnl"].sum();rate=t1/len(sub)*100
    flag=" <<<" if net<-15 else(" !!!" if net>15 else "")
    print(f"  {h:02d}:00   {t1:>4d} {tsn:>4d} {snn:>4d} ${net:>+6,.0f} {rate:>5.0f}%{flag}")

print(f"\n  {'星期':<8s} {'TP1':>4s} {'TS':>4s} {'淨損益':>8s} {'TP1率':>6s}")
print(f"  {'-'*32}")
for d in range(7):
    name=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]
    sub=tdf[tdf["entry_weekday"]==d]
    if len(sub)==0:continue
    t1=len(sub[sub["type"]=="TP1"]);tsn=len(sub[sub["type"]=="TimeStop"])
    net=sub["pnl"].sum();rate=t1/len(sub)*100
    flag=" <<<" if net<-20 else(" !!!" if net>20 else "")
    print(f"  {name:<8s} {t1:>4d} {tsn:>4d} ${net:>+6,.0f} {rate:>5.0f}%{flag}")

# ============================================================
print(f"\n{'='*100}")
print("5. 逐月拆解")
print(f"{'='*100}")

print(f"\n  {'月份':<10s} {'筆':>4s} {'損益':>8s} {'TP1':>4s} {'TS':>4s} {'SN':>4s} {'TP1率':>6s} {'TP1賺':>8s} {'TS虧':>8s}")
print(f"  {'-'*65}")
for m in sorted(tdf["month"].unique()):
    sub=tdf[tdf["month"]==m]
    t1=sub[sub["type"]=="TP1"];tsub=sub[sub["type"]=="TimeStop"];snsub=sub[sub["type"]=="SafeNet"]
    rate=len(t1)/len(sub)*100 if len(sub)>0 else 0
    print(f"  {str(m):<10s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {len(t1):>4d} {len(tsub):>4d} {len(snsub):>4d} "
          f"{rate:>5.0f}% ${t1['pnl'].sum():>+6,.0f} ${tsub['pnl'].sum():>+6,.0f}")

# ============================================================
print(f"\n{'='*100}")
print("6. 多空分析")
print(f"{'='*100}")

for side,label in [("long","做多"),("short","做空")]:
    sub=tdf[tdf["side"]==side]
    t1=sub[sub["type"]=="TP1"];tsub=sub[sub["type"]=="TimeStop"];snsub=sub[sub["type"]=="SafeNet"]
    print(f"\n  {label}：{len(sub)} 筆, ${sub['pnl'].sum():+,.2f}")
    print(f"    TP1：{len(t1)} ${t1['pnl'].sum():+,.0f} | TS：{len(tsub)} ${tsub['pnl'].sum():+,.0f} | SN：{len(snsub)} ${snsub['pnl'].sum():+,.0f}")
    if len(t1)+len(tsub)>0:
        print(f"    TP1 率：{len(t1)/(len(t1)+len(tsub)+len(snsub))*100:.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("7. 每筆手續費佔比")
print(f"{'='*100}")

if len(tp1) > 0:
    avg_fee_per_trade = 1.6
    avg_tp1_gross = tp1["pnl"].mean() + avg_fee_per_trade  # 加回手續費 = 毛利
    print(f"\n  TP1 平均毛利：${avg_tp1_gross:,.2f}")
    print(f"  手續費/筆：${avg_fee_per_trade:,.2f}")
    print(f"  手續費佔 TP1 毛利：{avg_fee_per_trade/avg_tp1_gross*100:.0f}%")
    print(f"  TP1 淨利（扣手續費後）：${tp1['pnl'].mean():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("SUMMARY")
print(f"{'='*100}")

print(f"""
  === v5.2 收益結構（1 年）===
  TP1：+${tp1['pnl'].sum():,.0f}（{len(tp1)} 筆 x ${tp1['pnl'].mean():+,.2f}）
  TS： -${abs(ts['pnl'].sum()):,.0f}（{len(ts)} 筆 x ${ts['pnl'].mean():+,.2f}）
  SN： -${abs(sn['pnl'].sum()) if len(sn)>0 else 0:,.0f}（{len(sn)} 筆）
  Fee：-${total_fee:,.0f}
  Net：${tdf['pnl'].sum():+,.0f}

  === 可優化的方向 ===
  1. TimeStop {len(ts)} 筆仍虧 ${abs(ts['pnl'].sum()):,.0f}
  2. 手續費 ${total_fee:,.0f} 佔 TP1 的 {total_fee/max(tp1['pnl'].sum(),1)*100:.0f}%
  3. 虧損時段/星期（見上方 <<< 標記）
  4. 多空是否不對稱
""")

print("Done.")
