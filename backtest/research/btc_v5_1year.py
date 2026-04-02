"""
v5 程式邏輯回測 — 1 年資料（2025/04 ~ 2026/03）
完全對齊 cryptoBot v5 實際程式
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; TP1_ATR_MULT=1.5; TIME_STOP_BARS=96; MAX_SAME=2

def fetch(interval, days=365):
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
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    return df.dropna().reset_index(drop=True)

print("Fetching 5m (1 year)...", end=" ", flush=True)
raw5=fetch("5m",365); df5=add_ind(raw5); print(f"{len(df5)} bars")

print("Fetching 1h (1 year)...", end=" ", flush=True)
raw1h=fetch("1h",365); df1h=add_ind(raw1h)
m=df1h[["datetime","rsi"]].copy(); m.rename(columns={"rsi":"h1_rsi"},inplace=True)
m["h1_rsi_prev"]=m["h1_rsi"].shift(1); m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
df5["month"]=df5["datetime"].dt.to_period("M")
print(f"Aligned: {len(df5)} bars ({df5['datetime'].iloc[0].date()} ~ {df5['datetime'].iloc[-1].date()})")

def run(data):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        l_go=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]>=row["h1_rsi_prev"] and len(lpos)<MAX_SAME)
        s_go=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]<=row["h1_rsi_prev"] and len(spos)<MAX_SAME)
        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+TP1_ATR_MULT*atr,"ei":i})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-TP1_ATR_MULT*atr,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","dt"])

def stats(tdf, label=""):
    if len(tdf)<1:return
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    print(f"\n  {label}:")
    print(f"    交易數：{len(tdf)}")
    print(f"    損益(PnL)：${pnl:+,.2f}")
    print(f"    勝率(WR)：{wr:.1f}%")
    print(f"    獲利因子(PF)：{pf:.2f}")
    print(f"    回撤(DD)：${dd:,.2f}")
    for t in ["TP1","TimeStop","SafeNet"]:
        sub=tdf[tdf["type"]==t]
        if len(sub)==0:continue
        print(f"    {t}: {len(sub)} 筆, ${sub['pnl'].sum():+,.2f}, "
              f"勝率 {(sub['pnl']>0).mean()*100:.0f}%, 平均 ${sub['pnl'].mean():+,.2f}")
    lt=tdf[tdf["side"]=="long"];st=tdf[tdf["side"]=="short"]
    print(f"    做多：{len(lt)} 筆 ${lt['pnl'].sum():+,.2f} / 做空：{len(st)} 筆 ${st['pnl'].sum():+,.2f}")

# ============================================================
# 全樣本（1 年）
# ============================================================
print("\nRunning full year...", flush=True)
tdf = run(df5)

print(f"\n{'='*100}")
print("v5 回測 — 1 年資料")
print(f"{'='*100}")
stats(tdf, "全樣本（1 年）")

# ============================================================
# Walk-Forward：前 8 個月 / 後 4 個月
# ============================================================
print(f"\n{'='*100}")
print("Walk-Forward（前 8 個月 / 後 4 個月）")
print(f"{'='*100}")

split = int(len(df5) * (8/12))
dev = df5.iloc[:split].reset_index(drop=True)
oos = df5.iloc[split:].reset_index(drop=True)
print(f"\n  DEV: {dev['datetime'].iloc[0].date()} ~ {dev['datetime'].iloc[-1].date()}")
print(f"  OOS: {oos['datetime'].iloc[0].date()} ~ {oos['datetime'].iloc[-1].date()}")

tdf_dev = run(dev); stats(tdf_dev, "DEV（前 8 個月）")
tdf_oos = run(oos); stats(tdf_oos, "OOS（後 4 個月）")

# ============================================================
# 滾動 WF：3m 訓練 → 1m 測試
# ============================================================
print(f"\n{'='*100}")
print("滾動 Walk-Forward（3 個月訓練 → 1 個月測試）")
print(f"{'='*100}")

months = df5["month"].unique()
print(f"\n  可用月份：{[str(m) for m in months]}")

print(f"\n  {'折':>4s} {'測試月':>10s} {'損益':>12s} {'交易':>6s} {'勝率':>8s} {'PF':>8s} {'TP1':>5s} {'TS':>5s} {'SN':>5s}")
print(f"  {'-'*75}")

fold_results = []
for fold in range(len(months)-3):
    test_m = months[fold+3]
    fold_test = df5[df5["month"]==test_m].reset_index(drop=True)
    if len(fold_test) < 50: continue
    t = run(fold_test)
    if len(t)==0: continue
    p=t["pnl"].sum();w_r=(t["pnl"]>0).mean()*100
    ww=t[t["pnl"]>0];ll=t[t["pnl"]<=0]
    p_f=ww["pnl"].sum()/abs(ll["pnl"].sum()) if len(ll)>0 and ll["pnl"].sum()!=0 else 999
    tp1_n=len(t[t["type"]=="TP1"]);ts_n=len(t[t["type"]=="TimeStop"]);sn_n=len(t[t["type"]=="SafeNet"])
    fold_results.append({"month":str(test_m),"pnl":p,"n":len(t),"wr":w_r,"pf":p_f})
    print(f"  {fold+1:>4d} {str(test_m):>10s} ${p:>+10,.2f} {len(t):>6d} {w_r:>7.1f}% {p_f:>7.2f} {tp1_n:>5d} {ts_n:>5d} {sn_n:>5d}")

if fold_results:
    total = sum(r["pnl"] for r in fold_results)
    prof = sum(1 for r in fold_results if r["pnl"]>0)
    print(f"\n  合計：${total:+,.2f}, 獲利 {prof}/{len(fold_results)} 折")

# ============================================================
# 逐月損益
# ============================================================
print(f"\n{'='*100}")
print("逐月損益")
print(f"{'='*100}")

if len(tdf) > 0:
    tdf["month"] = pd.to_datetime(tdf["dt"]).dt.to_period("M")
    monthly = tdf.groupby("month").agg(
        pnl=("pnl","sum"), n=("pnl","count"),
        tp1=("type", lambda x: (x=="TP1").sum()),
        ts=("type", lambda x: (x=="TimeStop").sum()),
        sn=("type", lambda x: (x=="SafeNet").sum()),
    )
    print(f"\n  {'月份':<10s} {'損益':>12s} {'交易':>6s} {'TP1':>5s} {'TS':>5s} {'SN':>5s} {'TP1率':>8s}")
    print(f"  {'-'*55}")
    profit_months = 0
    for m, row in monthly.iterrows():
        tp1_rate = row["tp1"]/(row["tp1"]+row["ts"]+row["sn"])*100 if row["n"]>0 else 0
        flag = " <<<" if row["pnl"]<-50 else (" !!!" if row["pnl"]>50 else "")
        if row["pnl"]>0: profit_months += 1
        print(f"  {str(m):<10s} ${row['pnl']:>+10,.2f} {int(row['n']):>6d} {int(row['tp1']):>5d} "
              f"{int(row['ts']):>5d} {int(row['sn']):>5d} {tp1_rate:>7.0f}%{flag}")
    print(f"\n  獲利月份：{profit_months}/{len(monthly)}")

print("\nDone.")
