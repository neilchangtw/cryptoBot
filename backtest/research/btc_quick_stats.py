"""
快速統計出場類型（只跑 6 組，不到 2 分鐘）
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
    return df.dropna().reset_index(drop=True)

print("Fetching...",end=" ",flush=True)
raw=fetch("5m",180);df=add_ind(raw);print(f"{len(df)} bars")

# 只用 RSI30+BB + adaptive 跑一次舊 vs 新，詳細記錄每筆
def run_detailed(data, mode="old", safenet_pct=0.03, sl_slip=0.25):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"];bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            if mode=="old":
                if lo<=p["sl"]:
                    gap=p["sl"]-lo;ep=p["sl"]-gap*sl_slip
                    pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"SL","side":"long"});c=True
                elif p["phase"]==1 and hi>=p["tp1"]:
                    out=p["oqty"]*0.1;pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                    trades.append({"pnl":pnl,"type":"TP1","side":"long"})
                    p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["sl"]=p["entry"];p["trhi"]=hi
                elif p["phase"]==2:
                    if hi>p["trhi"]:p["trhi"]=hi
                    m=bm*0.6 if rsi>65 else bm;sl=max(p["trhi"]-atr*m,p["entry"])
                    if lo<=sl:
                        gap=sl-lo;ep=sl-gap*sl_slip;ep=max(ep,p["entry"])
                        pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"long"});c=True
            else:  # new
                safenet_sl=p["entry"]*(1-safenet_pct)
                if lo<=safenet_sl:
                    gap=safenet_sl-lo;ep=safenet_sl-gap*0.25
                    pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"SafeNet","side":"long"});c=True
                elif p["phase"]==1 and close>=p["tp1"]:
                    out=p["oqty"]*0.1;pnl=(close-p["entry"])*out-close*out*0.0004*2
                    trades.append({"pnl":pnl,"type":"TP1","side":"long"})
                    p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trhi"]=close
                elif p["phase"]==2:
                    if close>p["trhi"]:p["trhi"]=close
                    m=bm*0.6 if rsi>65 else bm;trail_sl=p["trhi"]-atr*m
                    if close<=trail_sl:
                        pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"long"});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            if mode=="old":
                if hi>=p["sl"]:
                    gap=hi-p["sl"];ep=p["sl"]+gap*sl_slip
                    pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"SL","side":"short"});c=True
                elif p["phase"]==1 and lo<=p["tp1"]:
                    out=p["oqty"]*0.1;pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                    trades.append({"pnl":pnl,"type":"TP1","side":"short"})
                    p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["sl"]=p["entry"];p["trlo"]=lo
                elif p["phase"]==2:
                    if lo<p["trlo"]:p["trlo"]=lo
                    m=bm*0.6 if rsi<35 else bm;sl=min(p["trlo"]+atr*m,p["entry"])
                    if hi>=sl:
                        gap=hi-sl;ep=sl+gap*sl_slip;ep=min(ep,p["entry"])
                        pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"short"});c=True
            else:
                safenet_sl=p["entry"]*(1+safenet_pct)
                if hi>=safenet_sl:
                    gap=hi-safenet_sl;ep=safenet_sl+gap*0.25
                    pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"SafeNet","side":"short"});c=True
                elif p["phase"]==1 and close<=p["tp1"]:
                    out=p["oqty"]*0.1;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                    trades.append({"pnl":pnl,"type":"TP1","side":"short"})
                    p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trlo"]=close
                elif p["phase"]==2:
                    if close<p["trlo"]:p["trlo"]=close
                    m=bm*0.6 if rsi<35 else bm;trail_sl=p["trlo"]+atr*m
                    if close>=trail_sl:
                        pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail","side":"short"});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"];notional=MARGIN*LEVERAGE
        l_go=row["rsi"]<30 and row["close"]<row["bb_lower"]
        s_go=row["rsi"]>70 and row["close"]>row["bb_upper"]

        if l_go and len(lpos)<3:
            qty=notional/ep
            if mode=="old":
                slp=row["swing_low"]-atr*0.3
                if slp>=ep:slp=ep-atr*1.5
                lpos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,"tp1":ep+atr,"phase":1,"trhi":ep,"atr":atr})
            else:
                lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,"phase":1,"trhi":ep,"atr":atr})

        if s_go and len(spos)<3:
            qty=notional/ep
            if mode=="old":
                slp=row["swing_high"]+atr*0.3
                if slp<=ep:slp=ep+atr*1.5
                spos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,"tp1":ep-atr,"phase":1,"trlo":ep,"atr":atr})
            else:
                spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,"phase":1,"trlo":ep,"atr":atr})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side"])

# 跑兩個版本
print("\nRunning OLD...",flush=True)
tdf_old = run_detailed(df, "old")
print("Running NEW...",flush=True)
tdf_new = run_detailed(df, "new")

# 統計
for label, tdf in [("OLD (STOP_MARKET + 25% slippage)", tdf_old), ("NEW (SafeNet 3% + limit order 0 slippage)", tdf_new)]:
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")

    total_n = len(tdf)
    total_pnl = tdf["pnl"].sum()

    print(f"\n  {'出場類型':<12s} {'次數':>6s} {'佔比':>8s} {'損益':>14s} {'平均/筆':>12s}")
    print(f"  {'-'*55}")

    for t in ["SL","SafeNet","TP1","Trail"]:
        sub = tdf[tdf["type"]==t]
        if len(sub)==0: continue
        pct = len(sub)/total_n*100
        pnl = sub["pnl"].sum()
        avg = sub["pnl"].mean()
        print(f"  {t:<12s} {len(sub):>6d} {pct:>7.1f}% ${pnl:>+12,.2f} ${avg:>+10.2f}")

    print(f"  {'TOTAL':<12s} {total_n:>6d}          ${total_pnl:>+12,.2f}")

    # Phase 1 vs Phase 2 觸發次數
    p1_exits = len(tdf[tdf["type"].isin(["SL","SafeNet"])])  # Phase 1 止損出場
    tp1_n = len(tdf[tdf["type"]=="TP1"])  # TP1 觸發（進入 Phase 2）
    p2_exits = len(tdf[tdf["type"]=="Trail"])  # Phase 2 trail 出場

    print(f"\n  Phase 分析：")
    print(f"    Phase 1 止損出場：{p1_exits} 次 (${tdf[tdf['type'].isin(['SL','SafeNet'])]['pnl'].sum():+,.2f})")
    print(f"    TP1 觸發（→Phase2）：{tp1_n} 次 (${tdf[tdf['type']=='TP1']['pnl'].sum():+,.2f})")
    print(f"    Phase 2 Trail 出場：{p2_exits} 次 (${tdf[tdf['type']=='Trail']['pnl'].sum():+,.2f})")

    if tp1_n > 0:
        tp1_rate = tp1_n / (p1_exits + tp1_n) * 100
        print(f"    TP1 觸發率：{tp1_rate:.1f}% (= {tp1_n} / {p1_exits + tp1_n})")

print("\nDone.")
