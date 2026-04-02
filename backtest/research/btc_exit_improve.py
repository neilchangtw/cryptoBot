"""
出場改善測試：解決利潤回吐 97% 的問題
基底 = 方案 3（1h RSI 方向 + 安全網 + 限價 + 過濾）

測試：
  1. 收緊 trail（降低 base_mult 上限）
  2. TP1 後改 EMA9 追蹤
  3. 增加 TP1 比例（10% → 30% / 50%）
  + 各種組合
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20

def fetch(interval,days=180):
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
    w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
    for i in range(w,len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill();df["swing_low"]=sl.ffill()
    d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
    df["ema9"]=df["close"].ewm(span=9).mean()
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    df["rsi_prev"]=df["rsi"].shift(1)
    return df.dropna().reset_index(drop=True)

print("Fetching...",end=" ",flush=True)
raw5=fetch("5m",180);df5=add_ind(raw5)
raw1h=fetch("1h",180);df1h=add_ind(raw1h)
df1h_map=df1h[["datetime","rsi"]].copy()
df1h_map.rename(columns={"rsi":"h1_rsi"},inplace=True)
df1h_map["h1_rsi_prev"]=df1h_map["h1_rsi"].shift(1)
df1h_map["hour_key"]=df1h_map["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(df1h_map[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
print(f"{len(df5)} bars")

def base_filter(row):
    return row["atr_pctile"]<=75 and abs(row["price_vs_ema21"])<2

def v3_long(row):
    if not (row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)):return False
    return row["h1_rsi"]>=row["h1_rsi_prev"]
def v3_short(row):
    if not (row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)):return False
    return row["h1_rsi"]<=row["h1_rsi_prev"]

# ============================================================
# 回測引擎（可配置 trail / TP1 比例 / EMA 出場）
# ============================================================
def run(data, trail_max_mult=2.5, tp1_pct=0.10, exit_mode="adaptive", safenet_pct=0.03):
    """
    trail_max_mult: base_mult 上限（原本 2.5，收緊到 1.5 或 1.0）
    tp1_pct: TP1 平倉比例（原本 10%）
    exit_mode: "adaptive" / "ema9" / "adaptive_tight"
    """
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]
        # trail mult 上限控制
        bm=min(1.0+(ap/100)*1.5, trail_max_mult)

        nl=[]
        for p in lpos:
            c=False
            if lo<=p["entry"]*(1-safenet_pct):
                ep=p["entry"]*(1-safenet_pct)-(p["entry"]*(1-safenet_pct)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet"}); c=True
            elif p["phase"]==1 and close>=p["tp1"]:
                out=p["oqty"]*tp1_pct;pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*(1-tp1_pct);p["phase"]=2;p["trhi"]=close
            elif p["phase"]==2:
                if close>p["trhi"]:p["trhi"]=close
                if exit_mode=="ema9":
                    if close<row["ema9"]:
                        pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                else:  # adaptive or adaptive_tight
                    m=bm*0.6 if rsi>65 else bm
                    tsl=p["trhi"]-atr*m
                    if close<=tsl:
                        pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            if hi>=p["entry"]*(1+safenet_pct):
                ep=p["entry"]*(1+safenet_pct)+(hi-p["entry"]*(1+safenet_pct))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet"}); c=True
            elif p["phase"]==1 and close<=p["tp1"]:
                out=p["oqty"]*tp1_pct;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*(1-tp1_pct);p["phase"]=2;p["trlo"]=close
            elif p["phase"]==2:
                if close<p["trlo"]:p["trlo"]=close
                if exit_mode=="ema9":
                    if close>row["ema9"]:
                        pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                else:
                    m=bm*0.6 if rsi<35 else bm
                    tsl=p["trlo"]+atr*m
                    if close>=tsl:
                        pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"];notional=MARGIN*LEVERAGE
        if v3_long(row) and len(lpos)<3:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,"phase":1,"trhi":ep})
        if v3_short(row) and len(spos)<3:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,"phase":1,"trlo":ep})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type"])

def stats(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=len(tdf[tdf["type"]=="SafeNet"])
    tp1_n=len(tdf[tdf["type"]=="TP1"])
    trail_n=len(tdf[tdf["type"]=="Trail"])
    trail_wr=(tdf[tdf["type"]=="Trail"]["pnl"]>0).mean()*100 if trail_n>0 else 0
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"sn":sn,"tp1_n":tp1_n,"trail_n":trail_n,"trail_wr":round(trail_wr,1)}

# ============================================================
# 測試矩陣
# ============================================================
configs = [
    # (名稱, trail_max_mult, tp1_pct, exit_mode)
    ("0.基線(2.5x/10%/adaptive)", 2.5, 0.10, "adaptive"),

    # 改善 1：收緊 trail
    ("1a.trail上限1.5x",           1.5, 0.10, "adaptive"),
    ("1b.trail上限1.0x",           1.0, 0.10, "adaptive"),
    ("1c.trail上限0.75x",          0.75, 0.10, "adaptive"),

    # 改善 2：TP1 後改 EMA9
    ("2a.TP1後EMA9(10%)",          2.5, 0.10, "ema9"),
    ("2b.TP1後EMA9(30%)",          2.5, 0.30, "ema9"),
    ("2c.TP1後EMA9(50%)",          2.5, 0.50, "ema9"),

    # 改善 3：加大 TP1 比例
    ("3a.TP1=30%/adaptive",        2.5, 0.30, "adaptive"),
    ("3b.TP1=50%/adaptive",        2.5, 0.50, "adaptive"),
    ("3c.TP1=70%/adaptive",        2.5, 0.70, "adaptive"),

    # 組合：收緊 trail + 加大 TP1
    ("combo1.trail1.5x+TP30%",     1.5, 0.30, "adaptive"),
    ("combo2.trail1.0x+TP30%",     1.0, 0.30, "adaptive"),
    ("combo3.trail1.0x+TP50%",     1.0, 0.50, "adaptive"),
    ("combo4.EMA9+TP30%",          2.5, 0.30, "ema9"),
    ("combo5.EMA9+TP50%",          2.5, 0.50, "ema9"),
    ("combo6.trail1.5x+EMA9+TP30%",1.5, 0.30, "ema9"),
]

# 全樣本
print(f"\n{'='*120}")
print("全樣本：出場改善方案（方案 3 進場 + 安全網 + 限價）")
print(f"{'='*120}")

print(f"\n  {'方案':<30s} {'交易':>6s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'安全網':>6s} {'TP1觸發':>7s} {'Trail':>6s} {'Trail勝率':>10s}")
print(f"  {'-'*120}")

full_res = []
for name, tm, tp, ex in configs:
    tdf = run(df5, trail_max_mult=tm, tp1_pct=tp, exit_mode=ex)
    s = stats(tdf)
    full_res.append({"name":name, "tm":tm, "tp":tp, "ex":ex, **s})
    print(f"  {name:<30s} {s['n']:>6d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} "
          f"${s['dd']:>9,.2f} {s['sn']:>6d} {s['tp1_n']:>7d} {s['trail_n']:>6d} {s['trail_wr']:>9.1f}%")

# Walk-Forward
print(f"\n{'='*120}")
print("Walk-Forward（前 3 後 3）")
print(f"{'='*120}")

split=int(len(df5)*0.5)
dev=df5.iloc[:split].reset_index(drop=True)
oos=df5.iloc[split:].reset_index(drop=True)

print(f"\n  {'方案':<30s} {'DEV PnL':>12s} {'OOS PnL':>12s} {'OOS WR':>8s} {'OOS PF':>8s} {'OOS交易':>7s}")
print(f"  {'-'*80}")

for name, tm, tp, ex in configs:
    td=run(dev,trail_max_mult=tm,tp1_pct=tp,exit_mode=ex)
    to=run(oos,trail_max_mult=tm,tp1_pct=tp,exit_mode=ex)
    sd=stats(td);so=stats(to)
    print(f"  {name:<30s} ${sd['pnl']:>+10,.0f} ${so['pnl']:>+10,.0f} {so['wr']:>7.1f}% {so['pf']:>7.2f} {so['n']:>7d}")

print("\nDone.")
