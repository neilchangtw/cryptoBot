"""
方案 3 獲利分析：方向對了但為什麼不賺錢？
拆解每筆交易的生命週期
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

# 用後 3 個月（OOS）
split=int(len(df5)*0.5)
oos=df5.iloc[split:].reset_index(drop=True)
print(f"OOS: {len(oos)} bars")

def base_filter(row):
    return row["atr_pctile"]<=75 and abs(row["price_vs_ema21"])<2

# 方案 3 進場
def v3_long(row):
    if not (row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)): return False
    return row["h1_rsi"] >= row["h1_rsi_prev"]
def v3_short(row):
    if not (row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)): return False
    return row["h1_rsi"] <= row["h1_rsi_prev"]

# 詳細回測：記錄每筆交易的完整生命週期
def run_detail(data, safenet_pct=0.03):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"];bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            # 追蹤最大浮盈
            max_favorable = max(p.get("max_fav",0), (hi-p["entry"])*p["oqty"])
            p["max_fav"] = max_favorable

            if lo<=p["entry"]*(1-safenet_pct):
                ep=p["entry"]*(1-safenet_pct)-(p["entry"]*(1-safenet_pct)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","phase":p["phase"],
                               "hold":i-p["ei"],"max_fav":p["max_fav"],
                               "entry":p["entry"],"exit":ep}); c=True
            elif p["phase"]==1 and close>=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"long","phase":1,
                               "hold":i-p["ei"],"max_fav":p["max_fav"],
                               "entry":p["entry"],"exit":close})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trhi"]=close
            elif p["phase"]==2:
                if close>p["trhi"]:p["trhi"]=close
                m=bm*0.6 if rsi>65 else bm;tsl=p["trhi"]-atr*m
                if close<=tsl:
                    pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"long","phase":2,
                                   "hold":i-p["ei"],"max_fav":p["max_fav"],
                                   "entry":p["entry"],"exit":close,
                                   "trhi":p["trhi"],"trail_gave_back":p["trhi"]-close}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            max_favorable = max(p.get("max_fav",0), (p["entry"]-lo)*p["oqty"])
            p["max_fav"] = max_favorable

            if hi>=p["entry"]*(1+safenet_pct):
                ep=p["entry"]*(1+safenet_pct)+(hi-p["entry"]*(1+safenet_pct))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","phase":p["phase"],
                               "hold":i-p["ei"],"max_fav":p["max_fav"],
                               "entry":p["entry"],"exit":ep}); c=True
            elif p["phase"]==1 and close<=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"short","phase":1,
                               "hold":i-p["ei"],"max_fav":p["max_fav"],
                               "entry":p["entry"],"exit":close})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trlo"]=close
            elif p["phase"]==2:
                if close<p["trlo"]:p["trlo"]=close
                m=bm*0.6 if rsi<35 else bm;tsl=p["trlo"]+atr*m
                if close>=tsl:
                    pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"short","phase":2,
                                   "hold":i-p["ei"],"max_fav":p["max_fav"],
                                   "entry":p["entry"],"exit":close,
                                   "trlo":p["trlo"],"trail_gave_back":close-p["trlo"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"];notional=MARGIN*LEVERAGE
        if v3_long(row) and len(lpos)<3:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,"phase":1,"trhi":ep,"atr":atr,"ei":i,"max_fav":0})
        if v3_short(row) and len(spos)<3:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,"phase":1,"trlo":ep,"atr":atr,"ei":i,"max_fav":0})

    return pd.DataFrame(trades)

print("Running OOS...",flush=True)
tdf = run_detail(oos)
print(f"Trades: {len(tdf)}")

# ============================================================
# 分析
# ============================================================
print(f"\n{'='*100}")
print("方案 3 OOS 獲利拆解：錢去了哪裡？")
print(f"{'='*100}")

# 出場類型
print(f"\n--- 出場類型統計 ---")
print(f"  {'類型':<12s} {'次數':>6s} {'損益':>12s} {'平均/筆':>10s} {'勝率':>8s}")
print(f"  {'-'*50}")
for t in ["SafeNet","TP1","Trail"]:
    sub=tdf[tdf["type"]==t]
    if len(sub)==0:continue
    pnl=sub["pnl"].sum();avg=sub["pnl"].mean();wr=(sub["pnl"]>0).mean()*100
    print(f"  {t:<12s} {len(sub):>6d} ${pnl:>+10,.2f} ${avg:>+8.2f} {wr:>7.1f}%")
print(f"  {'TOTAL':<12s} {len(tdf):>6d} ${tdf['pnl'].sum():>+10,.2f}")

# TP1 → Phase 2 的交易流向
tp1_trades = tdf[tdf["type"]=="TP1"]
trail_trades = tdf[tdf["type"]=="Trail"]
print(f"\n--- Phase 流向 ---")
print(f"  進場 → Phase 1 等 TP1")
print(f"    觸發 TP1 進入 Phase 2：{len(tp1_trades)} 次 (TP1 賺 ${tp1_trades['pnl'].sum():+,.2f})")
print(f"  Phase 2 → Trail 出場：{len(trail_trades)} 次 (Trail 賺 ${trail_trades['pnl'].sum():+,.2f})")

# Trail 出場的盈虧分佈
if len(trail_trades)>0:
    trail_win = trail_trades[trail_trades["pnl"]>0]
    trail_lose = trail_trades[trail_trades["pnl"]<=0]
    print(f"\n--- Trail 出場分析（Phase 2）---")
    print(f"  獲利出場：{len(trail_win)} 次，平均 ${trail_win['pnl'].mean():+,.2f}")
    print(f"  虧損出場：{len(trail_lose)} 次，平均 ${trail_lose['pnl'].mean():+,.2f}")
    print(f"  = Trail 出場中 {len(trail_lose)/len(trail_trades)*100:.0f}% 是虧錢的")

    if len(trail_lose) > 0:
        print(f"\n  Trail 虧損的原因：Phase 2 應該已經保本了為什麼會虧？")
        print(f"    → 因為 Trail close 出場時 close 可能 < entry（限價出場用 close）")
        print(f"    → 自適應 trail_sl 允許 >= entry（保本），但 close 可能跌破 trail_sl")

# 最大浮盈 vs 實際獲利
print(f"\n--- 最大浮盈 vs 實際出場 ---")
if "max_fav" in tdf.columns:
    tdf["realized"] = tdf["pnl"]
    tdf["gave_back"] = tdf["max_fav"] - tdf["realized"]
    avg_max_fav = tdf["max_fav"].mean()
    avg_realized = tdf["realized"].mean()
    avg_gave_back = tdf["gave_back"].mean()

    print(f"  平均最大浮盈：${avg_max_fav:+,.2f}")
    print(f"  平均實際獲利：${avg_realized:+,.2f}")
    print(f"  平均回吐：    ${avg_gave_back:,.2f} ({avg_gave_back/avg_max_fav*100:.0f}% 回吐)")

# Trail 的回吐分析
if "trail_gave_back" in trail_trades.columns:
    gb = trail_trades["trail_gave_back"].dropna()
    if len(gb) > 0:
        print(f"\n--- Trail 出場的利潤回吐 ---")
        print(f"  Trail 極值 vs 出場的差距（= 回吐的價差）：")
        print(f"    平均回吐：${gb.mean():,.2f}")
        print(f"    中位數：  ${gb.median():,.2f}")
        print(f"    最大回吐：${gb.max():,.2f}")

# 持倉時間
print(f"\n--- 持倉時間（5m bar 數）---")
for t in ["TP1","Trail","SafeNet"]:
    sub=tdf[tdf["type"]==t]
    if len(sub)==0:continue
    print(f"  {t}: 平均 {sub['hold'].mean():.0f} 根 ({sub['hold'].mean()*5:.0f} 分鐘), 最長 {sub['hold'].max()} 根 ({sub['hold'].max()*5} 分鐘)")

# 手續費
total_fee = 0
for _, row in tdf.iterrows():
    qty = MARGIN * LEVERAGE / row["entry"] if row["entry"]>0 else 0
    fee = row["entry"] * qty * 0.0004 + row["exit"] * qty * 0.0004
    total_fee += fee

print(f"\n--- 手續費 ---")
print(f"  總手續費：${total_fee:,.2f}")
print(f"  佔交易數比：${total_fee/len(tdf):.2f}/筆")
print(f"  如果沒手續費，損益 = ${tdf['pnl'].sum() + total_fee:+,.2f}")

print("\nDone.")
