"""
過濾規則回測：用特徵分析的結論過濾掉容易做錯的進場
基底 = 新架構（安全網 3% + 限價出場 0 滑價）
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
    return df.dropna().reset_index(drop=True)

print("Fetching...",end=" ",flush=True)
raw=fetch("5m",180);df=add_ind(raw);print(f"{len(df)} bars")

def run_new(data, long_filter_fn, short_filter_fn, safenet_pct=0.03, exit_mode="adaptive"):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"];bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            if lo<=p["entry"]*(1-safenet_pct):
                ep=p["entry"]*(1-safenet_pct)-(p["entry"]*(1-safenet_pct)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long"}); c=True
            elif p["phase"]==1 and close>=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"long"})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trhi"]=close
            elif p["phase"]==2:
                if close>p["trhi"]:p["trhi"]=close
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi>65 else bm;tsl=p["trhi"]-atr*m
                elif exit_mode=="ema9":
                    tsl = row["ema9"]  # close < ema9 就出
                    if close < row["ema9"]:
                        pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"long"}); c=True
                    tsl = p["trhi"]  # skip the trail check below
                if not c and exit_mode=="adaptive" and close<=tsl:
                    pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"long"}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            if hi>=p["entry"]*(1+safenet_pct):
                ep=p["entry"]*(1+safenet_pct)+(hi-p["entry"]*(1+safenet_pct))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short"}); c=True
            elif p["phase"]==1 and close<=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"short"})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trlo"]=close
            elif p["phase"]==2:
                if close<p["trlo"]:p["trlo"]=close
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi<35 else bm;tsl=p["trlo"]+atr*m
                elif exit_mode=="ema9":
                    if close > row["ema9"]:
                        pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"short"}); c=True
                    tsl = p["trlo"]
                if not c and exit_mode=="adaptive" and close>=tsl:
                    pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"short"}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"];notional=MARGIN*LEVERAGE
        base_long = row["rsi"]<30 and row["close"]<row["bb_lower"]
        base_short = row["rsi"]>70 and row["close"]>row["bb_upper"]

        l_go = base_long and long_filter_fn(row) and len(lpos)<3
        s_go = base_short and short_filter_fn(row) and len(spos)<3

        if l_go:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,"phase":1,"trhi":ep,"atr":atr})
        if s_go:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,"phase":1,"trlo":ep,"atr":atr})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side"])

def stats(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2)}

# ============================================================
# 過濾規則定義
# ============================================================
filters = {
    "0.無過濾（基線）": (
        lambda r: True,
        lambda r: True),
    "1.高波動不做（ATR>75）": (
        lambda r: r["atr_pctile"] <= 75,
        lambda r: r["atr_pctile"] <= 75),
    "2.偏離EMA21>2%不做": (
        lambda r: abs(r["price_vs_ema21"]) < 2,
        lambda r: abs(r["price_vs_ema21"]) < 2),
    "3.高波動+偏離組合": (
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2,
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2),
    "4.高波動+偏離+RSI更嚴": (
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2 and r["rsi"] < 25,
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2 and r["rsi"] > 75),
    "5.只做低波動（ATR<50）": (
        lambda r: r["atr_pctile"] < 50,
        lambda r: r["atr_pctile"] < 50),
    "6.偏離<1%（最嚴格）": (
        lambda r: abs(r["price_vs_ema21"]) < 1,
        lambda r: abs(r["price_vs_ema21"]) < 1),
    "7.高波動+偏離+RSI<20": (
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2 and r["rsi"] < 20,
        lambda r: r["atr_pctile"] <= 75 and abs(r["price_vs_ema21"]) < 2 and r["rsi"] > 80),
}

# ============================================================
# 全樣本測試
# ============================================================
print(f"\n{'='*120}")
print("全樣本：過濾規則效果（新架構 + adaptive 出場）")
print(f"{'='*120}")

print(f"\n  {'過濾規則':<30s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'安全網次':>8s} {'安全網虧':>12s}")
print(f"  {'-'*110}")

all_res = []
for name, (lf, sf) in filters.items():
    tdf = run_new(df, lf, sf, exit_mode="adaptive")
    s = stats(tdf)
    all_res.append({"name":name, **s})
    print(f"  {name:<30s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} "
          f"${s['dd']:>9,.2f} {s['sn']:>8d} ${s['sn_pnl']:>+10,.2f}")

# 也測 ema9 出場
print(f"\n--- EMA9 出場 ---")
print(f"\n  {'過濾規則':<30s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'安全網次':>8s}")
print(f"  {'-'*100}")

for name, (lf, sf) in filters.items():
    tdf = run_new(df, lf, sf, exit_mode="ema9")
    s = stats(tdf)
    print(f"  {name:<30s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} "
          f"${s['dd']:>9,.2f} {s['sn']:>8d}")

# ============================================================
# Walk-Forward（前 3 後 3）
# ============================================================
print(f"\n{'='*120}")
print("Walk-Forward（前 3 個月 / 後 3 個月）— adaptive 出場")
print(f"{'='*120}")

split=int(len(df)*0.5)
dev=df.iloc[:split].reset_index(drop=True)
oos=df.iloc[split:].reset_index(drop=True)

print(f"\n  {'過濾規則':<30s} {'DEV PnL':>12s} {'OOS PnL':>12s} {'OOS WR':>8s} {'OOS PF':>8s} {'OOS 安全網':>10s}")
print(f"  {'-'*85}")

for name, (lf, sf) in filters.items():
    tdf_dev=run_new(dev,lf,sf); tdf_oos=run_new(oos,lf,sf)
    s_d=stats(tdf_dev); s_o=stats(tdf_oos)
    print(f"  {name:<30s} ${s_d['pnl']:>+10,.0f} ${s_o['pnl']:>+10,.0f} {s_o['wr']:>7.1f}% {s_o['pf']:>7.2f} {s_o['sn']:>10d}")

print("\nDone.")
