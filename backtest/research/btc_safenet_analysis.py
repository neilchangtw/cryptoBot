"""
分析 115 次安全網觸發的共同特徵
找出「什麼情況容易做錯方向」
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
            d=r.json();
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
    df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    # 連續紅K
    count=0;consec=[]
    for red in (df["close"]<df["open"]):count=count+1 if red else 0;consec.append(count)
    df["consec_red"]=consec
    # 連續綠K
    count=0;consec=[]
    for green in (df["close"]>df["open"]):count=count+1 if green else 0;consec.append(count)
    df["consec_green"]=consec
    # 價格vs EMA 距離
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    # BB width
    df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
    return df.dropna().reset_index(drop=True)

print("Fetching...",end=" ",flush=True)
raw=fetch("5m",180);df=add_ind(raw);print(f"{len(df)} bars")

# 跑新架構，記錄每筆進場的完整指標
def run_with_detail(data, safenet_pct=0.03):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"];bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            safenet_sl=p["entry"]*(1-safenet_pct)
            if lo<=safenet_sl:
                gap=safenet_sl-lo;ep=safenet_sl-gap*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["entry_info"],"pnl":pnl,"type":"SafeNet","side":"long",
                               "exit_i":i,"hold_bars":i-p["entry_i"]}); c=True
            elif p["phase"]==1 and close>=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({**p["entry_info"],"pnl":pnl,"type":"TP1","side":"long",
                               "exit_i":i,"hold_bars":i-p["entry_i"]})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trhi"]=close
            elif p["phase"]==2:
                if close>p["trhi"]:p["trhi"]=close
                m=bm*0.6 if rsi>65 else bm;trail_sl=p["trhi"]-atr*m
                if close<=trail_sl:
                    pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({**p["entry_info"],"pnl":pnl,"type":"Trail","side":"long",
                                   "exit_i":i,"hold_bars":i-p["entry_i"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            safenet_sl=p["entry"]*(1+safenet_pct)
            if hi>=safenet_sl:
                gap=hi-safenet_sl;ep=safenet_sl+gap*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["entry_info"],"pnl":pnl,"type":"SafeNet","side":"short",
                               "exit_i":i,"hold_bars":i-p["entry_i"]}); c=True
            elif p["phase"]==1 and close<=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({**p["entry_info"],"pnl":pnl,"type":"TP1","side":"short",
                               "exit_i":i,"hold_bars":i-p["entry_i"]})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trlo"]=close
            elif p["phase"]==2:
                if close<p["trlo"]:p["trlo"]=close
                m=bm*0.6 if rsi<35 else bm;trail_sl=p["trlo"]+atr*m
                if close>=trail_sl:
                    pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({**p["entry_info"],"pnl":pnl,"type":"Trail","side":"short",
                                   "exit_i":i,"hold_bars":i-p["entry_i"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"];notional=MARGIN*LEVERAGE
        l_go=row["rsi"]<30 and row["close"]<row["bb_lower"]
        s_go=row["rsi"]>70 and row["close"]>row["bb_upper"]

        # 記錄進場時的完整指標
        entry_info = {
            "entry_rsi":rsi, "entry_atr":atr, "entry_atr_pctile":ap,
            "entry_vol_ratio":row["vol_ratio"], "entry_bb_width":row["bb_width"],
            "entry_consec_red":row["consec_red"], "entry_consec_green":row["consec_green"],
            "entry_price_vs_ema21":row["price_vs_ema21"],
            "entry_hour":row["datetime"].hour, "entry_weekday":row["datetime"].weekday(),
            "entry_dt":row["datetime"], "entry_i":i,
        }

        if l_go and len(lpos)<3:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,
                         "phase":1,"trhi":ep,"atr":atr,"entry_i":i,"entry_info":entry_info})
        if s_go and len(spos)<3:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,
                         "phase":1,"trlo":ep,"atr":atr,"entry_i":i,"entry_info":entry_info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf = run_with_detail(df)
print(f"Total trades: {len(tdf)}")

# 分出安全網 vs 正常出場
safenet = tdf[tdf["type"]=="SafeNet"].copy()
normal = tdf[tdf["type"].isin(["TP1","Trail"])].copy()

print(f"SafeNet: {len(safenet)}, Normal: {len(normal)}")

# ============================================================
# 特徵分析
# ============================================================
print(f"\n{'='*100}")
print(f"安全網觸發 ({len(safenet)} 筆) vs 正常出場 ({len(normal)} 筆) 特徵對比")
print(f"{'='*100}")

features = [
    ("entry_rsi", "進場RSI"),
    ("entry_atr_pctile", "ATR百分位"),
    ("entry_vol_ratio", "成交量比"),
    ("entry_bb_width", "BB寬度(%)"),
    ("entry_consec_red", "連續紅K"),
    ("entry_consec_green", "連續綠K"),
    ("entry_price_vs_ema21", "價格vs EMA21(%)"),
    ("hold_bars", "持倉根數(5m)"),
]

print(f"\n  {'特徵':<20s} {'安全網(做錯方向)':>20s} {'正常出場(做對)':>20s} {'差異':>15s}")
print(f"  {'-'*80}")

for col, label in features:
    if col not in safenet.columns or col not in normal.columns: continue
    s_mean = safenet[col].mean()
    n_mean = normal[col].mean()
    diff = s_mean - n_mean
    print(f"  {label:<20s} {s_mean:>20.2f} {n_mean:>20.2f} {diff:>+14.2f}")

# 時段分析
print(f"\n{'='*100}")
print("時段分析：哪個時段最容易做錯？")
print(f"{'='*100}")

print(f"\n  {'小時(UTC+8)':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")

for h in range(24):
    sn = len(safenet[safenet["entry_hour"]==h])
    nn = len(normal[normal["entry_hour"]==h])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    if total > 0:
        flag = " <<<" if pct > 15 else ""
        print(f"  {h:02d}:00           {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

# 星期分析
print(f"\n  {'星期':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")
for d in range(7):
    name = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]
    sn = len(safenet[safenet["entry_weekday"]==d])
    nn = len(normal[normal["entry_weekday"]==d])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    if total > 0:
        flag = " <<<" if pct > 15 else ""
        print(f"  {name:<15s} {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

# 多空分析
print(f"\n{'='*100}")
print("多空分析：做多還是做空更容易錯？")
print(f"{'='*100}")

for side in ["long","short"]:
    sn = len(safenet[safenet["side"]==side])
    nn = len(normal[normal["side"]==side])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    direction = "做多" if side=="long" else "做空"
    print(f"  {direction}: 安全網 {sn} / 正常 {nn} / 安全網佔比 {pct:.1f}%")

# ATR 百分位分組
print(f"\n{'='*100}")
print("波動環境：高波動還是低波動更容易做錯？")
print(f"{'='*100}")

print(f"\n  {'ATR百分位':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")

for lo_pct, hi_pct, label in [(0,25,"低(0-25)"),(25,50,"中低(25-50)"),(50,75,"中高(50-75)"),(75,100,"高(75-100)")]:
    sn = len(safenet[(safenet["entry_atr_pctile"]>=lo_pct)&(safenet["entry_atr_pctile"]<hi_pct)])
    nn = len(normal[(normal["entry_atr_pctile"]>=lo_pct)&(normal["entry_atr_pctile"]<hi_pct)])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    flag = " <<<" if pct > 15 else ""
    print(f"  {label:<15s} {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

# 連續紅K 分組
print(f"\n{'='*100}")
print("連續紅K：急跌中進場更容易做錯嗎？")
print(f"{'='*100}")

print(f"\n  {'連續紅K數':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")

for red_n in [0, 1, 2, 3, 4, 5]:
    if red_n < 5:
        sn = len(safenet[safenet["entry_consec_red"]==red_n])
        nn = len(normal[normal["entry_consec_red"]==red_n])
        label = f"{red_n}根"
    else:
        sn = len(safenet[safenet["entry_consec_red"]>=red_n])
        nn = len(normal[normal["entry_consec_red"]>=red_n])
        label = f"{red_n}+根"
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    if total > 0:
        flag = " <<<" if pct > 15 else ""
        print(f"  {label:<15s} {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

# RSI 分組
print(f"\n{'='*100}")
print("進場 RSI：RSI 越低越安全嗎？")
print(f"{'='*100}")

print(f"\n  {'進場RSI':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")

for lo_r, hi_r, label in [(0,15,"<15"),(15,20,"15-20"),(20,25,"20-25"),(25,30,"25-30")]:
    sn = len(safenet[(safenet["entry_rsi"]>=lo_r)&(safenet["entry_rsi"]<hi_r)])
    nn = len(normal[(normal["entry_rsi"]>=lo_r)&(normal["entry_rsi"]<hi_r)])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    if total > 0:
        flag = " <<<" if pct > 15 else ""
        print(f"  {label:<15s} {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

# 價格 vs EMA21 分組
print(f"\n{'='*100}")
print("價格偏離 EMA21：偏離越大越危險嗎？")
print(f"{'='*100}")

print(f"\n  {'偏離EMA21':<15s} {'安全網次數':>10s} {'正常次數':>10s} {'安全網佔比':>12s}")
print(f"  {'-'*50}")

for lo_d, hi_d, label in [(-99,-3,"< -3%"),(-3,-2,"-3~-2%"),(-2,-1,"-2~-1%"),(-1,0,"-1~0%"),(0,99,"> 0%")]:
    sn = len(safenet[(safenet["entry_price_vs_ema21"]>=lo_d)&(safenet["entry_price_vs_ema21"]<hi_d)])
    nn = len(normal[(normal["entry_price_vs_ema21"]>=lo_d)&(normal["entry_price_vs_ema21"]<hi_d)])
    total = sn + nn
    pct = sn/total*100 if total > 0 else 0
    if total > 0:
        flag = " <<<" if pct > 15 else ""
        print(f"  {label:<15s} {sn:>10d} {nn:>10d} {pct:>11.1f}%{flag}")

print(f"\n{'='*100}")
print("SUMMARY: 可能的過濾規則")
print(f"{'='*100}")
print("\n(看上面哪些 <<< 標記的最明顯，就是最該過濾的條件)")
print("\nDone.")
