"""
TimeStop 優化：A / D / E 三方案（1 年資料）
完全對齊 v5 程式邏輯，next-open 進場，iloc[-2] 指標，1h 延遲一根
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

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
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    return df.dropna().reset_index(drop=True)

print("Fetching 1 year...", end=" ", flush=True)
raw5=fetch("5m",365);df5=add_ind(raw5)
raw1h=fetch("1h",365);df1h=add_ind(raw1h)
m=df1h[["datetime","rsi"]].copy();m.rename(columns={"rsi":"h1_rsi"},inplace=True)
m["h1_rsi_prev"]=m["h1_rsi"].shift(1);m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
df5["month"]=df5["datetime"].dt.to_period("M")
print(f"{len(df5)} bars ({df5['datetime'].iloc[0].date()} ~ {df5['datetime'].iloc[-1].date()})")

# ============================================================
# 回測引擎（可配置 TP1 距離、時間止損、浮盈保護）
# ============================================================
def run(data, tp1_mult=1.5, time_stop_bars=96, profit_protect_bars=0, profit_protect_threshold=0):
    """
    tp1_mult: TP1 距離（ATR 倍數）
    time_stop_bars: 時間止損（5m bar 數，96=8h）
    profit_protect_bars: 浮盈保護啟動時間（0=不啟用）
    profit_protect_threshold: 浮盈保護閾值（0=任何浮盈就出）
    """
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            # 追蹤浮盈
            unrealized=(close-p["entry"])*p["qty"]

            # 1. 安全網
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row["datetime"]}); c=True

            # 2. TP1
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"long","bars":bars,"dt":row["datetime"]}); c=True

            # 3. 浮盈保護（到了指定時間且有浮盈 → 鎖利出場）
            elif profit_protect_bars > 0 and bars >= profit_protect_bars and unrealized > profit_protect_threshold:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"ProfitProtect","side":"long","bars":bars,"dt":row["datetime"]}); c=True

            # 4. 時間止損
            elif bars>=time_stop_bars:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"dt":row["datetime"]}); c=True

            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            unrealized=(p["entry"]-close)*p["qty"]

            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row["datetime"]}); c=True

            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"short","bars":bars,"dt":row["datetime"]}); c=True

            elif profit_protect_bars > 0 and bars >= profit_protect_bars and unrealized > profit_protect_threshold:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"ProfitProtect","side":"short","bars":bars,"dt":row["datetime"]}); c=True

            elif bars>=time_stop_bars:
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
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+tp1_mult*atr,"ei":i})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-tp1_mult*atr,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","dt"])

def stats(tdf, label=""):
    if len(tdf)<1:
        print(f"\n  {label}: No trades"); return
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    print(f"\n  {label}:")
    print(f"    交易：{len(tdf)}, 損益(PnL)：${pnl:+,.2f}, 勝率(WR)：{wr:.1f}%, 獲利因子(PF)：{pf:.2f}, 回撤(DD)：${dd:,.2f}")
    for t in ["TP1","ProfitProtect","TimeStop","SafeNet"]:
        sub=tdf[tdf["type"]==t]
        if len(sub)==0:continue
        print(f"    {t}: {len(sub)} 筆 ${sub['pnl'].sum():+,.2f} (avg ${sub['pnl'].mean():+,.2f}, wr {(sub['pnl']>0).mean()*100:.0f}%)")

# ============================================================
# 測試配置
# ============================================================
configs = [
    # (名稱, tp1_mult, time_stop_bars, profit_protect_bars, profit_protect_threshold)
    ("v5 現行 (1.5x/8h/無保護)",     1.5, 96, 0, 0),

    # 方案 A：縮短 TP1
    ("A1. TP1=1.0x ATR",             1.0, 96, 0, 0),
    ("A2. TP1=1.25x ATR",            1.25, 96, 0, 0),
    ("A3. TP1=1.1x ATR",             1.1, 96, 0, 0),

    # 方案 D：浮盈保護
    ("D1. 4h浮盈>0出場",             1.5, 96, 48, 0),
    ("D2. 3h浮盈>0出場",             1.5, 96, 36, 0),
    ("D3. 4h浮盈>$1出場",            1.5, 96, 48, 1.0),
    ("D4. 6h浮盈>0出場",             1.5, 96, 72, 0),

    # 方案 E：A + D 組合
    ("E1. TP1=1.25x + 4h保護",       1.25, 96, 48, 0),
    ("E2. TP1=1.0x + 4h保護",        1.0, 96, 48, 0),
    ("E3. TP1=1.25x + 3h保護",       1.25, 96, 36, 0),
    ("E4. TP1=1.1x + 4h保護",        1.1, 96, 48, 0),
    ("E5. TP1=1.25x + 4h保護>$1",    1.25, 96, 48, 1.0),
]

# ============================================================
# 全樣本（1 年）
# ============================================================
print(f"\n{'='*120}")
print("全樣本（1 年）")
print(f"{'='*120}")

print(f"\n  {'方案':<30s} {'交易':>6s} {'損益(PnL)':>12s} {'勝率':>8s} {'PF':>7s} {'回撤':>10s} "
      f"{'TP1':>5s} {'PP':>5s} {'TS':>5s} {'SN':>5s} {'TS虧損':>10s}")
print(f"  {'-'*115}")

full_results = []
for name, tp1m, tsb, ppb, ppt in configs:
    tdf = run(df5, tp1_mult=tp1m, time_stop_bars=tsb, profit_protect_bars=ppb, profit_protect_threshold=ppt)
    if len(tdf)==0: continue
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    tp1n=len(tdf[tdf["type"]=="TP1"]);ppn=len(tdf[tdf["type"]=="ProfitProtect"])
    tsn=len(tdf[tdf["type"]=="TimeStop"]);snn=len(tdf[tdf["type"]=="SafeNet"])
    ts_loss=tdf[tdf["type"]=="TimeStop"]["pnl"].sum()

    full_results.append({"name":name,"tp1m":tp1m,"tsb":tsb,"ppb":ppb,"ppt":ppt,
                          "n":len(tdf),"pnl":pnl,"wr":wr,"pf":pf,"dd":dd,
                          "tp1n":tp1n,"ppn":ppn,"tsn":tsn,"snn":snn,"ts_loss":ts_loss,"tdf":tdf})

    print(f"  {name:<30s} {len(tdf):>6d} ${pnl:>+10,.2f} {wr:>7.1f}% {pf:>6.2f} ${dd:>9,.2f} "
          f"{tp1n:>5d} {ppn:>5d} {tsn:>5d} {snn:>5d} ${ts_loss:>+9,.0f}")

# ============================================================
# Walk-Forward（前 8 月 / 後 4 月）
# ============================================================
print(f"\n{'='*120}")
print("Walk-Forward（前 8 月 / 後 4 月）")
print(f"{'='*120}")

split = int(len(df5) * (8/12))
dev = df5.iloc[:split].reset_index(drop=True)
oos = df5.iloc[split:].reset_index(drop=True)

print(f"\n  {'方案':<30s} {'DEV PnL':>10s} {'OOS PnL':>10s} {'OOS WR':>8s} {'OOS PF':>8s} {'OOS交易':>7s} "
      f"{'OOS TP1':>7s} {'OOS PP':>7s} {'OOS TS':>7s}")
print(f"  {'-'*100}")

for r in full_results:
    td = run(dev, r["tp1m"], r["tsb"], r["ppb"], r["ppt"])
    to = run(oos, r["tp1m"], r["tsb"], r["ppb"], r["ppt"])
    dp = td["pnl"].sum() if len(td)>0 else 0
    op = to["pnl"].sum() if len(to)>0 else 0
    owr = (to["pnl"]>0).mean()*100 if len(to)>0 else 0
    ow=to[to["pnl"]>0];ol=to[to["pnl"]<=0]
    opf = ow["pnl"].sum()/abs(ol["pnl"].sum()) if len(ol)>0 and ol["pnl"].sum()!=0 else 999
    otp1=len(to[to["type"]=="TP1"]);opp=len(to[to["type"]=="ProfitProtect"]);ots=len(to[to["type"]=="TimeStop"])
    print(f"  {r['name']:<30s} ${dp:>+8,.0f} ${op:>+8,.0f} {owr:>7.1f}% {opf:>7.2f} {len(to):>7d} "
          f"{otp1:>7d} {opp:>7d} {ots:>7d}")

# ============================================================
# 滾動 WF（3m → 1m）— Top 3 方案
# ============================================================
print(f"\n{'='*120}")
print("滾動 Walk-Forward（3m -> 1m）— Top 3 by OOS PnL")
print(f"{'='*120}")

# 先按 OOS PnL 排序
oos_pnls = []
for r in full_results:
    to = run(oos, r["tp1m"], r["tsb"], r["ppb"], r["ppt"])
    oos_pnls.append({"name":r["name"],"oos_pnl":to["pnl"].sum() if len(to)>0 else 0, **r})
oos_pnls.sort(key=lambda x: x["oos_pnl"], reverse=True)

months = df5["month"].unique()
for rank, r in enumerate(oos_pnls[:3]):
    print(f"\n  #{rank+1} {r['name']}")
    print(f"  {'折':>4s} {'月份':>10s} {'損益':>10s} {'交易':>6s} {'TP1':>5s} {'PP':>5s} {'TS':>5s}")
    print(f"  {'-'*50}")
    fold_pnls = []
    for fold in range(len(months)-3):
        test_m = months[fold+3]
        ft = df5[df5["month"]==test_m].reset_index(drop=True)
        if len(ft)<50:continue
        t = run(ft, r["tp1m"], r["tsb"], r["ppb"], r["ppt"])
        if len(t)==0:continue
        p=t["pnl"].sum()
        fold_pnls.append(p)
        tp1n=len(t[t["type"]=="TP1"]);ppn=len(t[t["type"]=="ProfitProtect"]);tsn=len(t[t["type"]=="TimeStop"])
        print(f"  {fold+1:>4d} {str(test_m):>10s} ${p:>+8,.2f} {len(t):>6d} {tp1n:>5d} {ppn:>5d} {tsn:>5d}")
    prof=sum(1 for x in fold_pnls if x>0)
    print(f"  合計：${sum(fold_pnls):+,.2f}, 獲利 {prof}/{len(fold_pnls)} 折")

print("\nDone.")
