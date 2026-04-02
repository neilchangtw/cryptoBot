"""
進場修正回測：5 種改善方向
基底 = 安全網架構（±3% + 限價出場）+ 過濾規則（ATR<75 + 偏離<2%）

方向 1：動能衰減確認（當前跌幅 < 前一根跌幅）
方向 2：RSI 回升確認（RSI 跌破 30 後回到 30 以上才進）
方向 3：1h RSI 方向確認（1h RSI 不再下降）
方向 4：底背離（RSI 不再創新低 但價格更低）
方向 5：分批進場（RSI<30 開 50U，RSI<25 再加 50U）

注意：
  - 進場用 next-open
  - 不偷看未來
  - 1h 指標用上一根已收盤的
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

def fetch(interval, days=180):
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
    # 單根跌幅
    df["bar_chg"]=df["close"]-df["open"]
    df["bar_chg_prev"]=df["bar_chg"].shift(1)
    # RSI 前值
    df["rsi_prev"]=df["rsi"].shift(1)
    df["rsi_prev2"]=df["rsi"].shift(2)
    # 成交量
    df["vol_ma20"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    df["vol_prev"]=df["volume"].shift(1)
    # RSI 最低（近 5 根）
    df["rsi_min5"]=df["rsi"].rolling(5).min()
    return df.dropna().reset_index(drop=True)

print("Fetching 5m...",end=" ",flush=True)
raw=fetch("5m",180);df5=add_ind(raw);print(f"{len(df5)} bars")

# 1h 指標（延遲一根）
print("Fetching 1h...",end=" ",flush=True)
raw1h=fetch("1h",180)
df1h=add_ind(raw1h)
df1h_map=df1h[["datetime","rsi"]].copy()
df1h_map.rename(columns={"rsi":"h1_rsi"},inplace=True)
df1h_map["h1_rsi_prev"]=df1h_map["h1_rsi"].shift(1)
# 延遲一根：10:00 的 1h 數據 → 對齊到 11:xx 的 5m bars
df1h_map["hour_key"]=df1h_map["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(df1h_map[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
print(f"Aligned: {len(df5)} bars")

MARGIN=100; LEVERAGE=20

# ============================================================
# 回測引擎（安全網 + 限價 + 過濾）
# ============================================================
def run(data, long_entry_fn, short_entry_fn, safenet_pct=0.03,
        margin_per_trade=100, max_c=3):
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
                trades.append({"pnl":pnl,"type":"SafeNet"}); c=True
            elif p["phase"]==1 and close>=p["tp1"]:
                out=p["oqty"]*0.1;pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trhi"]=close
            elif p["phase"]==2:
                if close>p["trhi"]:p["trhi"]=close
                m=bm*0.6 if rsi>65 else bm;tsl=p["trhi"]-atr*m
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
                out=p["oqty"]*0.1;pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9;p["phase"]=2;p["trlo"]=close
            elif p["phase"]==2:
                if close<p["trlo"]:p["trlo"]=close
                m=bm*0.6 if rsi<35 else bm;tsl=p["trlo"]+atr*m
                if close>=tsl:
                    pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]; notional=margin_per_trade*LEVERAGE
        l_go = long_entry_fn(row, i, data) and len(lpos)<max_c
        s_go = short_entry_fn(row, i, data) and len(spos)<max_c

        if l_go:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep+atr,
                         "phase":1,"trhi":ep,"atr":atr,"margin":margin_per_trade})
        if s_go:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"tp1":ep-atr,
                         "phase":1,"trlo":ep,"atr":atr,"margin":margin_per_trade})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type"])

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
# 共用過濾（ATR ≤ 75 + 偏離 < 2%）
# ============================================================
def base_filter(row):
    return row["atr_pctile"] <= 75 and abs(row["price_vs_ema21"]) < 2

# ============================================================
# 進場方案定義
# ============================================================

# 0. 基線：RSI<30 + BB（加過濾）
def base_long(row, i, data):
    return row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)
def base_short(row, i, data):
    return row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)

# 1. 動能衰減：當前 bar 跌幅的絕對值 < 前一根（下跌正在減速）
def v1_long(row, i, data):
    if not (row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)): return False
    # 當根紅K跌幅 < 前一根紅K跌幅 = 減速
    if row["bar_chg"]<0 and row["bar_chg_prev"]<0:
        return abs(row["bar_chg"]) < abs(row["bar_chg_prev"])  # 跌得比前一根少
    return row["bar_chg"] >= 0  # 當根是綠K = 已經在反彈
def v1_short(row, i, data):
    if not (row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)): return False
    if row["bar_chg"]>0 and row["bar_chg_prev"]>0:
        return abs(row["bar_chg"]) < abs(row["bar_chg_prev"])
    return row["bar_chg"] <= 0

# 2. RSI 回升確認：RSI 前一根 < 30，當根 >= 30（從超賣回來）
def v2_long(row, i, data):
    if not base_filter(row): return False
    return row["rsi"] >= 30 and row["rsi_prev"] < 30 and row["close"] < row["bb_lower"]
def v2_short(row, i, data):
    if not base_filter(row): return False
    return row["rsi"] <= 70 and row["rsi_prev"] > 70 and row["close"] > row["bb_upper"]

# 2b. RSI 回升寬鬆版：RSI 前一根 < 30，當根 > 前一根（RSI 開始回升，不用回到 30）
def v2b_long(row, i, data):
    if not base_filter(row): return False
    return row["rsi_prev"] < 30 and row["rsi"] > row["rsi_prev"] and row["close"] < row["bb_lower"]
def v2b_short(row, i, data):
    if not base_filter(row): return False
    return row["rsi_prev"] > 70 and row["rsi"] < row["rsi_prev"] and row["close"] > row["bb_upper"]

# 3. 1h RSI 方向確認：1h RSI 不再下降（延遲一根）
def v3_long(row, i, data):
    if not (row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)): return False
    h1_rsi = row.get("h1_rsi", 50)
    h1_rsi_prev = row.get("h1_rsi_prev", 50)
    return h1_rsi >= h1_rsi_prev  # 1h RSI 沒有繼續下降
def v3_short(row, i, data):
    if not (row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)): return False
    h1_rsi = row.get("h1_rsi", 50)
    h1_rsi_prev = row.get("h1_rsi_prev", 50)
    return h1_rsi <= h1_rsi_prev

# 4. RSI 底背離：價格更低但 RSI 更高（近 5 根）
def v4_long(row, i, data):
    if not (row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)): return False
    if i < 5: return False
    # 當前 RSI > 近 5 根最低 RSI = RSI 沒有跟著價格破底
    return row["rsi"] > row["rsi_min5"]
def v4_short(row, i, data):
    if not (row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)): return False
    return True  # 空單不好做頂背離，先跳過

# 5. 分批進場：RSI<30 開 50U，RSI<25 再加 50U
# 用 margin_per_trade=50 跑兩次模擬
def v5_long_first(row, i, data):
    return row["rsi"]<30 and row["close"]<row["bb_lower"] and base_filter(row)
def v5_long_second(row, i, data):
    return row["rsi"]<25 and row["close"]<row["bb_lower"] and base_filter(row)
def v5_short_first(row, i, data):
    return row["rsi"]>70 and row["close"]>row["bb_upper"] and base_filter(row)
def v5_short_second(row, i, data):
    return row["rsi"]>75 and row["close"]>row["bb_upper"] and base_filter(row)

# 組合：方向 1 + 2b（動能衰減 + RSI 開始回升）
def combo_long(row, i, data):
    if not base_filter(row): return False
    if not (row["close"] < row["bb_lower"]): return False
    # RSI 曾經 < 30 且現在回升中
    if row["rsi_prev"] < 30 and row["rsi"] > row["rsi_prev"]:
        # 且跌幅在減速或已反彈
        if row["bar_chg"] >= 0 or (row["bar_chg"]<0 and row["bar_chg_prev"]<0 and abs(row["bar_chg"])<abs(row["bar_chg_prev"])):
            return True
    return False
def combo_short(row, i, data):
    if not base_filter(row): return False
    if not (row["close"] > row["bb_upper"]): return False
    if row["rsi_prev"] > 70 and row["rsi"] < row["rsi_prev"]:
        if row["bar_chg"] <= 0 or (row["bar_chg"]>0 and row["bar_chg_prev"]>0 and abs(row["bar_chg"])<abs(row["bar_chg_prev"])):
            return True
    return False

# ============================================================
# 測試
# ============================================================
strategies = [
    ("0.基線(RSI30+BB+過濾)", base_long, base_short, 100),
    ("1.動能衰減確認", v1_long, v1_short, 100),
    ("2.RSI回升確認(嚴格)", v2_long, v2_short, 100),
    ("2b.RSI回升確認(寬鬆)", v2b_long, v2b_short, 100),
    ("3.1h RSI方向確認", v3_long, v3_short, 100),
    ("4.RSI底背離", v4_long, v4_short, 100),
    ("combo.動能+RSI回升", combo_long, combo_short, 100),
]

print(f"\n{'='*110}")
print("全樣本：各進場修正方案（安全網 3% + 限價出場 + ATR<75 + 偏離<2%）")
print(f"{'='*110}")

print(f"\n  {'方案':<25s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'安全網次':>8s} {'安全網虧':>12s}")
print(f"  {'-'*105}")

all_res = []
for name, lf, sf, margin in strategies:
    tdf = run(df5, lf, sf, margin_per_trade=margin)
    s = stats(tdf)
    all_res.append({"name":name, **s})
    print(f"  {name:<25s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} "
          f"${s['dd']:>9,.2f} {s['sn']:>8d} ${s['sn_pnl']:>+10,.2f}")

# 方案 5：分批進場（兩次 50U 合併）
print(f"\n  --- 方案 5：分批進場 ---")
tdf_5a = run(df5, v5_long_first, v5_short_first, margin_per_trade=50)
tdf_5b = run(df5, v5_long_second, v5_short_second, margin_per_trade=50)
tdf_5 = pd.concat([tdf_5a, tdf_5b], ignore_index=True)
s5 = stats(tdf_5)
print(f"  {'5.分批(50U+50U)':<25s} {s5['n']:>7d} ${s5['pnl']:>+12,.2f} {s5['wr']:>9.1f}% {s5['pf']:>13.2f} "
      f"${s5['dd']:>9,.2f} {s5['sn']:>8d} ${s5['sn_pnl']:>+10,.2f}")

# ============================================================
# Walk-Forward（前 3 後 3）
# ============================================================
print(f"\n{'='*110}")
print("Walk-Forward（前 3 個月 / 後 3 個月）")
print(f"{'='*110}")

split=int(len(df5)*0.5)
dev=df5.iloc[:split].reset_index(drop=True)
oos=df5.iloc[split:].reset_index(drop=True)

print(f"\n  {'方案':<25s} {'DEV PnL':>12s} {'OOS PnL':>12s} {'OOS WR':>8s} {'OOS PF':>8s} {'OOS交易':>7s} {'OOS安全網':>8s}")
print(f"  {'-'*85}")

for name, lf, sf, margin in strategies:
    tdf_d=run(dev,lf,sf,margin_per_trade=margin)
    tdf_o=run(oos,lf,sf,margin_per_trade=margin)
    sd=stats(tdf_d);so=stats(tdf_o)
    print(f"  {name:<25s} ${sd['pnl']:>+10,.0f} ${so['pnl']:>+10,.0f} {so['wr']:>7.1f}% {so['pf']:>7.2f} {so['n']:>7d} {so['sn']:>8d}")

# 分批
tdf_5d = pd.concat([run(dev,v5_long_first,v5_short_first,margin_per_trade=50),
                     run(dev,v5_long_second,v5_short_second,margin_per_trade=50)])
tdf_5o = pd.concat([run(oos,v5_long_first,v5_short_first,margin_per_trade=50),
                     run(oos,v5_long_second,v5_short_second,margin_per_trade=50)])
sd5=stats(tdf_5d);so5=stats(tdf_5o)
print(f"  {'5.分批(50U+50U)':<25s} ${sd5['pnl']:>+10,.0f} ${so5['pnl']:>+10,.0f} {so5['wr']:>7.1f}% {so5['pf']:>7.2f} {so5['n']:>7d} {so5['sn']:>8d}")

print("\nDone.")
