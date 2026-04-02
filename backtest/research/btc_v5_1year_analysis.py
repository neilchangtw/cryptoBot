"""
v5 一年回測深度分析 — 找出最大問題在哪裡
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; TP1_ATR_MULT=1.5; TIME_STOP_BARS=96; MAX_SAME=2

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
    d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["ema50"]=df["close"].ewm(span=50).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    # 趨勢判斷
    df["trend_up"]=df["close"]>df["ema50"]
    # 波動率趨勢
    df["atr_ma50"]=df["atr"].rolling(50).mean()
    df["atr_rising"]=df["atr"]>df["atr_ma50"]
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
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            max_fav=max(p.get("mf",0),(hi-p["entry"])*p["qty"]); p["mf"]=max_fav
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":p["mf"]}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"long","bars":bars,"mf":p["mf"]}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"mf":p["mf"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            max_fav=max(p.get("mf",0),(p["entry"]-lo)*p["qty"]); p["mf"]=max_fav
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":p["mf"]}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"short","bars":bars,"mf":p["mf"]}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"mf":p["mf"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        info={"entry_rsi":rsi,"entry_atr":atr,"entry_ap":ap,"entry_ema21_dev":row["price_vs_ema21"],
              "h1_rsi":row["h1_rsi"],"h1_rsi_prev":row["h1_rsi_prev"],
              "entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "dt":row["datetime"],"entry_price":ep,
              "trend_up":row["trend_up"],"atr_rising":row["atr_rising"]}

        l_go=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]>=row["h1_rsi_prev"] and len(lpos)<MAX_SAME)
        s_go=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]<=row["h1_rsi_prev"] and len(spos)<MAX_SAME)
        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+TP1_ATR_MULT*atr,"ei":i,"mf":0,"info":info})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-TP1_ATR_MULT*atr,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf = run_detail(df5)
tdf["month"] = pd.to_datetime(tdf["dt"]).dt.to_period("M")
print(f"Trades: {len(tdf)}")

tp1=tdf[tdf["type"]=="TP1"]; ts=tdf[tdf["type"]=="TimeStop"]; sn=tdf[tdf["type"]=="SafeNet"]

# ============================================================
print(f"\n{'='*100}")
print("1. 損益拆解 — 錢去了哪裡")
print(f"{'='*100}")

total_fee = len(tdf) * 1.6  # ~$1.6/筆
print(f"""
  TP1 收入：        +${tp1['pnl'].sum():>8,.0f}  ({len(tp1)} 筆, 平均 +${tp1['pnl'].mean():,.2f})
  TimeStop 虧損：   -${abs(ts['pnl'].sum()):>8,.0f}  ({len(ts)} 筆, 平均 -${abs(ts['pnl'].mean()):,.2f})
  SafeNet 虧損：    -${abs(sn['pnl'].sum()):>8,.0f}  ({len(sn)} 筆, 平均 -${abs(sn['pnl'].mean()):,.2f})
  手續費(估)：      -${total_fee:>8,.0f}  ({len(tdf)} 筆 x $1.6)
  淨損益：          ${tdf['pnl'].sum():>+8,.0f}

  TimeStop 虧損 / TP1 收入 = {abs(ts['pnl'].sum())/tp1['pnl'].sum()*100:.0f}%
  = TimeStop 吃掉了 TP1 收入的 {abs(ts['pnl'].sum())/tp1['pnl'].sum()*100:.0f}%
""")

# ============================================================
print(f"{'='*100}")
print("2. TimeStop 深度分析 — 這 59 筆到底怎麼了")
print(f"{'='*100}")

# 曾經浮盈多少
print(f"\n  TimeStop 曾經的最大浮盈：")
print(f"    平均：${ts['mf'].mean():,.2f}")
print(f"    中位數：${ts['mf'].median():,.2f}")
print(f"    最大：${ts['mf'].max():,.2f}")
print(f"    = 0（完全沒浮盈）：{(ts['mf']<0.1).sum()} 筆")
print(f"    > $3（接近 TP1）：{(ts['mf']>3).sum()} 筆")
print(f"    > $5（幾乎到 TP1）：{(ts['mf']>5).sum()} 筆")

# 按浮盈分組
print(f"\n  TimeStop 按最大浮盈分組：")
for lo,hi,label in [(0,1,"$0~1(沒動)"),(1,3,"$1~3(走了一點)"),(3,5,"$3~5(接近TP1)"),(5,99,"$5+(幾乎到)")]:
    sub=ts[(ts["mf"]>=lo)&(ts["mf"]<hi)]
    if len(sub)>0:
        print(f"    {label:<20s}: {len(sub):>3d} 筆, 平均虧 ${sub['pnl'].mean():+,.2f}")

# 多空
print(f"\n  多空拆解：")
print(f"    做多 TimeStop：{len(ts[ts['side']=='long'])} 筆, ${ts[ts['side']=='long']['pnl'].sum():+,.2f}")
print(f"    做空 TimeStop：{len(ts[ts['side']=='short'])} 筆, ${ts[ts['side']=='short']['pnl'].sum():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("3. 前半年 vs 後半年 — 到底差在哪裡")
print(f"{'='*100}")

# 按月份分前後
months_all = sorted(tdf["month"].unique())
mid = len(months_all) // 2
first_half = months_all[:mid+1]  # 前 7 個月
second_half = months_all[mid+1:]  # 後 5 個月

h1 = tdf[tdf["month"].isin(first_half)]
h2 = tdf[tdf["month"].isin(second_half)]

for label, sub in [("前半年(4~10月)", h1), ("後半年(11~3月)", h2)]:
    t1=sub[sub["type"]=="TP1"]; t2=sub[sub["type"]=="TimeStop"]; t3=sub[sub["type"]=="SafeNet"]
    tp1_rate = len(t1)/(len(t1)+len(t2)+len(t3))*100 if len(sub)>0 else 0
    print(f"\n  {label}:")
    print(f"    交易：{len(sub)} 筆, 淨損益 ${sub['pnl'].sum():+,.2f}")
    print(f"    TP1：{len(t1)} 筆 (+${t1['pnl'].sum():,.0f}), TimeStop：{len(t2)} 筆 (-${abs(t2['pnl'].sum()):,.0f})")
    print(f"    TP1 率：{tp1_rate:.0f}%")
    print(f"    TimeStop/TP1 虧損比：{abs(t2['pnl'].sum())/max(t1['pnl'].sum(),1)*100:.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("4. TimeStop vs TP1 的進場特徵對比（1 年全量）")
print(f"{'='*100}")

print(f"\n  {'特徵':<20s} {'TimeStop':>12s} {'TP1':>12s} {'差異':>12s} {'含義':>20s}")
print(f"  {'-'*80}")

features = [
    ("entry_rsi", "進場 RSI"),
    ("entry_ap", "ATR 百分位"),
    ("entry_ema21_dev", "EMA21 偏離%"),
    ("h1_rsi", "1h RSI"),
    ("entry_hour", "進場小時"),
]

for col, label in features:
    if col in ts.columns and col in tp1.columns:
        t_mean = ts[col].mean(); p_mean = tp1[col].mean(); diff = t_mean - p_mean
        # 判斷含義
        if col == "entry_rsi":
            meaning = "TS的RSI更低" if diff < 0 else "TS的RSI更高"
        elif col == "entry_ap":
            meaning = "TS波動更低" if diff < 0 else "TS波動更高"
        elif col == "entry_ema21_dev":
            meaning = "TS偏離更大" if abs(t_mean) > abs(p_mean) else "差不多"
        elif col == "h1_rsi":
            meaning = "TS 1h趨勢更弱" if diff < 0 else "差不多"
        else:
            meaning = ""
        print(f"  {label:<20s} {t_mean:>12.2f} {p_mean:>12.2f} {diff:>+12.2f} {meaning:>20s}")

# 趨勢環境
if "trend_up" in ts.columns:
    ts_trend_up = ts["trend_up"].mean() * 100
    tp1_trend_up = tp1["trend_up"].mean() * 100
    print(f"  {'趨勢向上比例':<20s} {ts_trend_up:>11.0f}% {tp1_trend_up:>11.0f}% {ts_trend_up-tp1_trend_up:>+11.0f}%")

# ATR 趨勢
if "atr_rising" in ts.columns:
    ts_atr_r = ts["atr_rising"].mean() * 100
    tp1_atr_r = tp1["atr_rising"].mean() * 100
    print(f"  {'ATR上升中比例':<20s} {ts_atr_r:>11.0f}% {tp1_atr_r:>11.0f}% {ts_atr_r-tp1_atr_r:>+11.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("5. 做多 vs 做空 — 哪邊問題更大")
print(f"{'='*100}")

for side, label in [("long","做多"),("short","做空")]:
    sub = tdf[tdf["side"]==side]
    t1=sub[sub["type"]=="TP1"]; t2=sub[sub["type"]=="TimeStop"]; t3=sub[sub["type"]=="SafeNet"]
    print(f"\n  {label}：{len(sub)} 筆, 淨損益 ${sub['pnl'].sum():+,.2f}")
    print(f"    TP1：{len(t1)} 筆 ${t1['pnl'].sum():+,.2f}  |  TimeStop：{len(t2)} 筆 ${t2['pnl'].sum():+,.2f}  |  SafeNet：{len(t3)} 筆 ${t3['pnl'].sum():+,.2f}")
    if len(t1)+len(t2)>0:
        print(f"    TP1 率：{len(t1)/(len(t1)+len(t2)+len(t3))*100:.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("6. 時段 + 星期分析（1 年）")
print(f"{'='*100}")

print(f"\n  {'小時':<8s} {'TP1':>5s} {'TS':>5s} {'SN':>5s} {'淨損益':>10s} {'TP1率':>8s}")
print(f"  {'-'*45}")
for h in range(24):
    sub=tdf[tdf["entry_hour"]==h]
    if len(sub)==0:continue
    t1n=len(sub[sub["type"]=="TP1"]);tsn=len(sub[sub["type"]=="TimeStop"]);snn=len(sub[sub["type"]=="SafeNet"])
    net=sub["pnl"].sum(); rate=t1n/len(sub)*100
    flag=" <<<" if net<-30 else (" !!!" if net>30 else "")
    print(f"  {h:02d}:00   {t1n:>5d} {tsn:>5d} {snn:>5d} ${net:>+8,.0f} {rate:>7.0f}%{flag}")

print(f"\n  {'星期':<8s} {'TP1':>5s} {'TS':>5s} {'淨損益':>10s} {'TP1率':>8s}")
print(f"  {'-'*40}")
for d in range(7):
    name=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]
    sub=tdf[tdf["entry_weekday"]==d]
    if len(sub)==0:continue
    t1n=len(sub[sub["type"]=="TP1"]);tsn=len(sub[sub["type"]=="TimeStop"])
    net=sub["pnl"].sum(); rate=t1n/len(sub)*100
    flag=" <<<" if net<-50 else (" !!!" if net>50 else "")
    print(f"  {name:<8s} {t1n:>5d} {tsn:>5d} ${net:>+8,.0f} {rate:>7.0f}%{flag}")

# ============================================================
print(f"\n{'='*100}")
print("7. TP1 距離敏感度（1 年）")
print(f"{'='*100}")

print(f"\n  如果改變 TP1 距離，TimeStop 會怎樣？（用最大浮盈估算）")
print(f"\n  {'TP1距離':<12s} {'能到TP1':>8s} {'變TimeStop':>10s} {'估算TP1收入':>14s} {'估算TS虧損':>14s} {'估算淨利':>12s}")
print(f"  {'-'*75}")

for tp_mult in [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
    tp1_ok = 0; ts_ok = 0; tp1_rev = 0; ts_rev = 0
    for _, t in tdf.iterrows():
        atr_val = t.get("entry_atr", 200)
        tp_dist = tp_mult * atr_val * (MARGIN*LEVERAGE) / t.get("entry_price", 80000)
        if t["mf"] >= tp_dist:
            tp1_ok += 1
            tp1_rev += tp_dist - 1.6  # 扣手續費
        else:
            ts_ok += 1
            ts_rev += t["pnl"]  # 用實際虧損
    mark = " <-- now" if tp_mult == 1.5 else ""
    print(f"  {tp_mult:.2f}x ATR   {tp1_ok:>8d} {ts_ok:>10d} ${tp1_rev:>+12,.0f} ${ts_rev:>+12,.0f} ${tp1_rev+ts_rev:>+10,.0f}{mark}")

# ============================================================
print(f"\n{'='*100}")
print("SUMMARY: 問題優先順序")
print(f"{'='*100}")

ts_cost = abs(ts["pnl"].sum())
sn_cost = abs(sn["pnl"].sum())
tp1_income = tp1["pnl"].sum()

print(f"""
  === 收益結構（1 年）===
  TP1 收入：       +${tp1_income:,.0f}
  TimeStop 虧損：  -${ts_cost:,.0f}（佔 TP1 的 {ts_cost/tp1_income*100:.0f}%）
  SafeNet 虧損：   -${sn_cost:,.0f}（佔 TP1 的 {sn_cost/tp1_income*100:.0f}%）
  手續費：         -${total_fee:,.0f}（佔 TP1 的 {total_fee/tp1_income*100:.0f}%）
  淨損益：         ${tdf['pnl'].sum():+,.0f}

  === 問題優先排序 ===

  #1 TimeStop 太多（-${ts_cost:,.0f}，佔 TP1 的 {ts_cost/tp1_income*100:.0f}%）
     59 筆 TimeStop，每筆平均虧 ${ts['pnl'].mean():,.0f}
     其中 {(ts['mf']>3).sum()} 筆曾接近 TP1 但沒到

  #2 手續費（-${total_fee:,.0f}，佔 TP1 的 {total_fee/tp1_income*100:.0f}%）
     347 筆 x $1.6/筆，無法消除但可以透過減少交易次數降低

  #3 SafeNet（-${sn_cost:,.0f}，佔 TP1 的 {sn_cost/tp1_income*100:.0f}%）
     5 筆，數量少，不是主要問題

  #4 前半年策略失效
     TP1 率 62~85%（後半年 84~94%）
     市場環境不同導致 TimeStop 暴增
""")

print("Done.")
