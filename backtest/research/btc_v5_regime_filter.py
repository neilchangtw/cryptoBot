"""
市場環境偵測 + 動態策略（1 年資料）
基底 = v5 + TP1 1.25x ATR（A2 最佳版）

測試：
  1. ADX 過濾（ADX > 25 不做）
  2. BB 寬度過濾（寬度 > 75 分位不做）
  3. ATR 趨勢過濾（ATR > ATR MA50 不做）
  4. EMA50 斜率過濾（斜率太陡不做）
  5. 多空不對稱（趨勢向上只做多、向下只做空）
  6. 組合：ADX + 多空不對稱
  7. 動態 TP1（低波動 1.5x / 高波動 0.75x）
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2; TIME_STOP_BARS=96

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
    df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
    df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["ema50"]=df["close"].ewm(span=50).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    # EMA50 斜率（近 10 根的變化率 %）
    df["ema50_slope"]=(df["ema50"]-df["ema50"].shift(10))/df["ema50"].shift(10)*100
    # ATR 趨勢
    df["atr_ma50"]=df["atr"].rolling(50).mean()
    # ADX
    up_m=df["high"]-df["high"].shift(1); dn_m=df["low"].shift(1)-df["low"]
    pdm=pd.Series(np.where((up_m>dn_m)&(up_m>0),up_m,0.0),index=df.index)
    mdm=pd.Series(np.where((dn_m>up_m)&(dn_m>0),dn_m,0.0),index=df.index)
    s_atr=tr.ewm(alpha=1/14,min_periods=14).mean()
    pdi=100*pdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
    mdi=100*mdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,1)
    df["adx"]=dx.ewm(alpha=1/14,min_periods=14).mean()
    # 趨勢方向
    df["trend_up"]=df["close"]>df["ema50"]
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
print(f"{len(df5)} bars")

def run(data, tp1_mult=1.25, regime_filter=None, dynamic_tp1=False, trend_only=False):
    """
    regime_filter: None / "adx25" / "adx20" / "bb75" / "bb60" / "atr_trend" / "ema_slope"
    dynamic_tp1: True = 低波動 1.5x / 高波動 0.75x
    trend_only: True = 趨勢向上只做多、向下只做空
    """
    lpos=[];spos=[];trades=[]
    for i in range(120,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            unrealized=(close-p["entry"])*p["qty"]
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

        # 基本進場條件
        base_long=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
                   and row["h1_rsi"]>=row["h1_rsi_prev"])
        base_short=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
                    and row["h1_rsi"]<=row["h1_rsi_prev"])

        # 市場環境過濾
        regime_ok = True
        if regime_filter == "adx25":
            regime_ok = row["adx"] < 25
        elif regime_filter == "adx20":
            regime_ok = row["adx"] < 20
        elif regime_filter == "adx30":
            regime_ok = row["adx"] < 30
        elif regime_filter == "bb75":
            regime_ok = row["bb_width_pctile"] < 75
        elif regime_filter == "bb60":
            regime_ok = row["bb_width_pctile"] < 60
        elif regime_filter == "atr_trend":
            regime_ok = row["atr"] <= row["atr_ma50"]  # ATR 沒在上升
        elif regime_filter == "ema_slope":
            regime_ok = abs(row["ema50_slope"]) < 1.0  # EMA50 斜率 < 1%

        # 多空不對稱
        if trend_only:
            if row["trend_up"]:
                base_short = False  # 趨勢向上不做空
            else:
                base_long = False  # 趨勢向下不做多

        l_go = base_long and regime_ok and len(lpos)<MAX_SAME
        s_go = base_short and regime_ok and len(spos)<MAX_SAME

        # 動態 TP1
        if dynamic_tp1:
            if ap < 40:
                actual_tp1 = 1.5  # 低波動多給空間
            elif ap > 60:
                actual_tp1 = 0.75  # 高波動快走
            else:
                actual_tp1 = 1.25
        else:
            actual_tp1 = tp1_mult

        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+actual_tp1*atr,"ei":i})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-actual_tp1*atr,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","dt"])

# ============================================================
# 測試配置
# ============================================================
configs = [
    # (名稱, tp1_mult, regime_filter, dynamic_tp1, trend_only)
    ("0. A2基線(1.25x/無過濾)",        1.25, None,         False, False),

    # 方向 1：市場環境偵測
    ("1a. ADX<25(趨勢弱才做)",          1.25, "adx25",      False, False),
    ("1b. ADX<20(更嚴格)",              1.25, "adx20",      False, False),
    ("1c. ADX<30(寬鬆)",                1.25, "adx30",      False, False),
    ("1d. BB寬度<75pctile",             1.25, "bb75",       False, False),
    ("1e. BB寬度<60pctile",             1.25, "bb60",       False, False),
    ("1f. ATR<ATR_MA50",                1.25, "atr_trend",  False, False),
    ("1g. EMA50斜率<1%",                1.25, "ema_slope",  False, False),

    # 方向 2：動態 TP1
    ("2. 動態TP1(低1.5x/高0.75x)",     1.25, None,         True,  False),

    # 方向 3：多空不對稱
    ("3a. 順勢交易(上只多/下只空)",      1.25, None,         False, True),
    ("3b. 順勢+ADX<25",                 1.25, "adx25",      False, True),

    # 組合
    ("C1. ADX<25+動態TP1",              1.25, "adx25",      True,  False),
    ("C2. ADX<25+順勢+動態TP1",         1.25, "adx25",      True,  True),
    ("C3. BB<75+順勢",                  1.25, "bb75",       False, True),
    ("C4. ATR趨勢+順勢",               1.25, "atr_trend",  False, True),
    ("C5. EMA斜率+順勢",               1.25, "ema_slope",  False, True),
]

# 全樣本
print(f"\n{'='*120}")
print("全樣本（1 年）")
print(f"{'='*120}")

print(f"\n  {'方案':<30s} {'交易':>6s} {'損益':>10s} {'勝率':>7s} {'PF':>6s} {'回撤':>9s} "
      f"{'TP1':>5s} {'TS':>4s} {'SN':>4s} {'TS虧':>9s}")
print(f"  {'-'*100}")

full_results = []
for name, tp1m, rf, dtp, to in configs:
    tdf = run(df5, tp1_mult=tp1m, regime_filter=rf, dynamic_tp1=dtp, trend_only=to)
    if len(tdf)==0:
        print(f"  {name:<30s} no trades"); continue
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    tp1n=len(tdf[tdf["type"]=="TP1"]);tsn=len(tdf[tdf["type"]=="TimeStop"]);snn=len(tdf[tdf["type"]=="SafeNet"])
    ts_loss=tdf[tdf["type"]=="TimeStop"]["pnl"].sum()
    full_results.append({"name":name,"tp1m":tp1m,"rf":rf,"dtp":dtp,"to":to,
                          "n":len(tdf),"pnl":pnl,"wr":wr,"pf":pf,"dd":dd,
                          "tp1n":tp1n,"tsn":tsn,"snn":snn,"ts_loss":ts_loss})
    print(f"  {name:<30s} {len(tdf):>6d} ${pnl:>+8,.0f} {wr:>6.1f}% {pf:>5.2f} ${dd:>8,.0f} "
          f"{tp1n:>5d} {tsn:>4d} {snn:>4d} ${ts_loss:>+8,.0f}")

# Walk-Forward（前 8 / 後 4）
print(f"\n{'='*120}")
print("Walk-Forward（前 8 月 / 後 4 月）")
print(f"{'='*120}")

split=int(len(df5)*(8/12))
dev=df5.iloc[:split].reset_index(drop=True)
oos=df5.iloc[split:].reset_index(drop=True)

print(f"\n  {'方案':<30s} {'DEV':>8s} {'OOS':>8s} {'OOS WR':>7s} {'OOS PF':>7s} {'OOS N':>6s} "
      f"{'TP1':>5s} {'TS':>4s}")
print(f"  {'-'*80}")

wf_results = []
for r in full_results:
    td=run(dev,r["tp1m"],r["rf"],r["dtp"],r["to"])
    to_df=run(oos,r["tp1m"],r["rf"],r["dtp"],r["to"])
    dp=td["pnl"].sum() if len(td)>0 else 0
    op=to_df["pnl"].sum() if len(to_df)>0 else 0
    owr=(to_df["pnl"]>0).mean()*100 if len(to_df)>0 else 0
    ow=to_df[to_df["pnl"]>0];ol=to_df[to_df["pnl"]<=0]
    opf=ow["pnl"].sum()/abs(ol["pnl"].sum()) if len(ol)>0 and ol["pnl"].sum()!=0 else 999
    otp1=len(to_df[to_df["type"]=="TP1"]);ots=len(to_df[to_df["type"]=="TimeStop"])
    wf_results.append({**r,"dev":dp,"oos":op,"oos_wr":owr,"oos_pf":opf,"oos_n":len(to_df),
                        "oos_tp1":otp1,"oos_ts":ots})
    print(f"  {r['name']:<30s} ${dp:>+6,.0f} ${op:>+6,.0f} {owr:>6.1f}% {opf:>6.2f} {len(to_df):>6d} "
          f"{otp1:>5d} {ots:>4d}")

# 滾動 WF — Top 5 by OOS
print(f"\n{'='*120}")
print("滾動 WF（3m -> 1m）Top 5 by OOS PnL")
print(f"{'='*120}")

wf_results.sort(key=lambda x: x["oos"], reverse=True)
months = df5["month"].unique()

for rank, r in enumerate(wf_results[:5]):
    print(f"\n  #{rank+1} {r['name']} (OOS ${r['oos']:+,.0f})")
    fold_pnls=[]
    for fold in range(len(months)-3):
        test_m=months[fold+3]
        ft=df5[df5["month"]==test_m].reset_index(drop=True)
        if len(ft)<50:continue
        t=run(ft,r["tp1m"],r["rf"],r["dtp"],r["to"])
        if len(t)==0:continue
        fold_pnls.append(t["pnl"].sum())
    prof=sum(1 for x in fold_pnls if x>0)
    print(f"    滾動：${sum(fold_pnls):+,.0f}, 獲利 {prof}/{len(fold_pnls)} 折")

# 最終排名
print(f"\n{'='*120}")
print("最終排名（by OOS PnL）")
print(f"{'='*120}")

print(f"\n  {'#':>2s} {'方案':<30s} {'全樣本':>8s} {'OOS':>8s} {'OOS PF':>7s} {'OOS WR':>7s} {'TP1':>5s} {'TS':>4s}")
print(f"  {'-'*75}")
for i,r in enumerate(wf_results[:10]):
    print(f"  {i+1:>2d} {r['name']:<30s} ${r['pnl']:>+6,.0f} ${r['oos']:>+6,.0f} {r['oos_pf']:>6.2f} "
          f"{r['oos_wr']:>6.1f}% {r['oos_tp1']:>5d} {r['oos_ts']:>4d}")

print("\nDone.")
