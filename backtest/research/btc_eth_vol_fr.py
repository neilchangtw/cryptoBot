"""
三個新方向：
1. ETH 用同一策略（v5.2 均值回歸 + 1h Swing 趨勢）
2. 波動率策略（BB 收窄後雙向突破）
3. 資金費率套利（極端費率反向做）

全部 1 年，嚴格回測
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

def fetch(symbol, interval, days=365):
    all_d=[];end=int(datetime.now().timestamp()*1000);cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":symbol,"interval":interval,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_d.extend(d);cur=d[-1][0]+1;_time.sleep(0.1)
        except:break
    if not all_d:return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

def fetch_funding(symbol, days=365):
    all_fr=[];cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000);end=int(datetime.now().timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://fapi.binance.com/fapi/v1/fundingRate",params={"symbol":symbol,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_fr.extend(d);cur=d[-1]["fundingTime"]+1;_time.sleep(0.1)
        except:break
    if not all_fr:return pd.Series(dtype=float), pd.DataFrame()
    fr=pd.DataFrame(all_fr);fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    return fr.set_index("dt")["rate"].resample("1h").ffill(), fr

def add_ind(df):
    tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"]=tr.rolling(14).mean()
    df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["atr_ma50"]=df["atr"].rolling(50).mean()
    d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["ema20"]=df["close"].ewm(span=20).mean()
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["ema50"]=df["close"].ewm(span=50).mean()
    df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
    df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
    df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
    for i in range(w,len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
    df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
    return df.dropna().reset_index(drop=True)

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"win_avg":0,"loss_avg":0,"rr":0,"avg_bars":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    wa=w["pnl"].mean() if len(w)>0 else 0;la=l["pnl"].mean() if len(l)>0 else 0
    rr=abs(wa/la) if la!=0 else 999
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "win_avg":round(wa,2),"loss_avg":round(la,2),"rr":round(rr,2),"avg_bars":round(tdf["bars"].mean(),1) if "bars" in tdf.columns and len(tdf)>0 else 0}

# ============================================================
# 抓資料
# ============================================================
print("=== Fetching data ===")
# BTC
print("BTC 1h...", end=" ", flush=True)
btc_1h=add_ind(fetch("BTCUSDT","1h",365));print(f"{len(btc_1h)}")
print("BTC 5m...", end=" ", flush=True)
btc_5m_raw=fetch("BTCUSDT","5m",365);btc_5m=add_ind(btc_5m_raw);print(f"{len(btc_5m)}")
print("BTC FR...", end=" ", flush=True)
btc_fr_s, btc_fr_raw=fetch_funding("BTCUSDT",365);print(f"{len(btc_fr_s)}")

# ETH
print("ETH 1h...", end=" ", flush=True)
eth_1h=add_ind(fetch("ETHUSDT","1h",365));print(f"{len(eth_1h)}")
print("ETH 5m...", end=" ", flush=True)
eth_5m=add_ind(fetch("ETHUSDT","5m",365));print(f"{len(eth_5m)}")
print("ETH FR...", end=" ", flush=True)
eth_fr_s, eth_fr_raw=fetch_funding("ETHUSDT",365);print(f"{len(eth_fr_s)}")

# FR 合併
for df_main, fr_s in [(btc_1h, btc_fr_s),(btc_5m, btc_fr_s),(eth_1h, eth_fr_s),(eth_5m, eth_fr_s)]:
    df_main.set_index("datetime",inplace=True)
    df_main["fr"]=fr_s.reindex(df_main.index,method="ffill")
    df_main["fr_pctile"]=df_main["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df_main.reset_index(inplace=True)
    df_main.dropna(inplace=True)
    df_main.reset_index(drop=True,inplace=True)

# 1h RSI for 5m
for df5, df1h in [(btc_5m, btc_1h),(eth_5m, eth_1h)]:
    m=df1h[["datetime","rsi"]].copy();m.rename(columns={"rsi":"h1_rsi"},inplace=True)
    m["h1_rsi_prev"]=m["h1_rsi"].shift(1);m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
    df5["hour_key"]=df5["datetime"].dt.floor("h")
    merged=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
    for c in ["h1_rsi","h1_rsi_prev"]:df5[c]=merged[c]
    df5.dropna(subset=["h1_rsi"],inplace=True)
    df5.reset_index(drop=True,inplace=True)

print("Data ready.")

# ============================================================
# 策略 1: v5.2 均值回歸（5m）
# ============================================================
def run_v52(data, tp1_mult=1.25, ts_bars=96):
    lpos=[];spos=[];trades=[]
    for i in range(120,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];close=row["close"];hi=row["high"];lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        atr_ma=row["atr_ma50"] if not np.isnan(row.get("atr_ma50",np.nan)) else atr

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                pnl=(p["entry"]*(1-SAFENET_PCT)-p["entry"])*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"TP1","bars":bars}); c=True
            elif bars>=ts_bars:
                pnl=(close-p["entry"])*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"TimeStop","bars":bars}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                pnl=(p["entry"]-p["entry"]*(1+SAFENET_PCT))*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"TP1","bars":bars}); c=True
            elif bars>=ts_bars:
                pnl=(p["entry"]-close)*p["qty"]-1.6
                trades.append({"pnl":pnl,"type":"TimeStop","bars":bars}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        l_go=(row["rsi"]<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row.get("h1_rsi",50)>=row.get("h1_rsi_prev",50) and atr<=atr_ma
              and row["bb_width_pctile"]<50 and len(lpos)<MAX_SAME)
        s_go=(row["rsi"]>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row.get("h1_rsi",50)<=row.get("h1_rsi_prev",50) and atr<=atr_ma
              and row["bb_width_pctile"]<50 and len(spos)<MAX_SAME)
        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+tp1_mult*atr,"ei":i})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-tp1_mult*atr,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","bars"])

# ============================================================
# 策略 2: 1h Swing + EMA50 趨勢
# ============================================================
def run_swing_ema50(data, fr_filter=True):
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                pnl=(p["entry"]*(1-SAFENET_PCT)-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close<=row["ema50"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                pnl=(p["entry"]-p["entry"]*(1+SAFENET_PCT))*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close>=row["ema50"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        l=close>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
        s=close<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
        if fr_filter:
            fr_p=row.get("fr_pctile",50)
            if not np.isnan(fr_p):
                if fr_p>80:l=False
                if fr_p<20:s=False
        if l and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i})
        if s and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","bars"])

# ============================================================
# 策略 3: 波動率策略（BB 收窄後雙向突破）
# ============================================================
def run_volatility(data, squeeze_pctile=20):
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                pnl=(p["entry"]*(1-SAFENET_PCT)-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                pnl=(p["entry"]-p["entry"]*(1+SAFENET_PCT))*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        # BB 極度收窄 → 上根是 squeeze → 本根突破
        if i<1:continue
        was_squeeze = data.iloc[i-1]["bb_width_pctile"] < squeeze_pctile
        l = was_squeeze and close > row["bb_upper"] and row["vol_ratio"] > 1.0
        s = was_squeeze and close < row["bb_lower"] and row["vol_ratio"] > 1.0
        if l and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i})
        if s and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","bars"])

# ============================================================
# 策略 4: 資金費率套利（極端費率反向做）
# ============================================================
def run_fr_arb(data, fr_raw_df, high_pctile=90, low_pctile=10):
    """費率極高做空收費率 / 費率極低做多收費率"""
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                pnl=(p["entry"]*(1-SAFENET_PCT)-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                pnl=(p["entry"]-p["entry"]*(1+SAFENET_PCT))*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","bars":bars}); c=True
            elif bars>=2 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","bars":bars}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        fr_p=row.get("fr_pctile",50)
        if np.isnan(fr_p):continue
        # 費率極高 → 做空（收費率 + 預期回落）
        s = fr_p > high_pctile and len(spos)<MAX_SAME
        # 費率極低/負 → 做多（收費率 + 預期反彈）
        l = fr_p < low_pctile and len(lpos)<MAX_SAME
        if l:lpos.append({"entry":ep,"ei":i})
        if s:spos.append({"entry":ep,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","bars"])

# ============================================================
# 跑全部
# ============================================================
print(f"\n{'='*110}")
print("全樣本（1 年）")
print(f"{'='*110}")

results = []

print(f"\n  {'策略':<35s} {'幣種':>6s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>5s} {'DD':>8s} {'W均':>6s} {'L均':>6s} {'RR':>5s}")
print(f"  {'-'*95}")

# 1. v5.2 均值回歸
for sym, d5 in [("BTC", btc_5m), ("ETH", eth_5m)]:
    tdf=run_v52(d5)
    s=calc(tdf)
    results.append({"name":f"v5.2 均值回歸(5m)","sym":sym,**s})
    print(f"  {'v5.2 均值回歸(5m)':<35s} {sym:>6s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# 2. 1h Swing + EMA50
for sym, d1h in [("BTC", btc_1h), ("ETH", eth_1h)]:
    tdf=run_swing_ema50(d1h)
    s=calc(tdf)
    results.append({"name":"1h Swing+EMA50+FR","sym":sym,**s})
    print(f"  {'1h Swing+EMA50+FR':<35s} {sym:>6s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# 3. 波動率
for sym, d1h in [("BTC", btc_1h), ("ETH", eth_1h)]:
    for sq in [10, 15, 20]:
        tdf=run_volatility(d1h, sq)
        s=calc(tdf)
        results.append({"name":f"波動率 Squeeze<{sq}","sym":sym,**s})
        if s["n"]>=3:
            print(f"  {f'波動率 Squeeze<{sq}':<35s} {sym:>6s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# 4. 資金費率套利
for sym, d1h, fr_raw in [("BTC", btc_1h, btc_fr_raw), ("ETH", eth_1h, eth_fr_raw)]:
    for hi_p, lo_p in [(90,10),(85,15),(80,20),(95,5)]:
        tdf=run_fr_arb(d1h, fr_raw, hi_p, lo_p)
        s=calc(tdf)
        results.append({"name":f"FR套利 >{hi_p}/<{lo_p}","sym":sym,**s})
        if s["n"]>=3:
            print(f"  {f'FR套利 >{hi_p}/<{lo_p}':<35s} {sym:>6s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# 排名
print(f"\n{'='*110}")
print("排名（by PnL）")
print(f"{'='*110}")

valid=[r for r in results if r["n"]>=3]
valid.sort(key=lambda x:x["pnl"],reverse=True)

print(f"\n  {'#':>2s} {'策略':<35s} {'幣種':>6s} {'PnL':>8s} {'WR':>6s} {'PF':>5s} {'RR':>5s} {'N':>5s}")
print(f"  {'-'*75}")
for i,r in enumerate(valid[:15]):
    print(f"  {i+1:>2d} {r['name']:<35s} {r['sym']:>6s} ${r['pnl']:>+6,.0f} {r['wr']:>5.1f}% {r['pf']:>4.2f} {r['rr']:>4.1f}:1 {r['n']:>5d}")

# WF for Top 5
print(f"\n{'='*110}")
print("Walk-Forward Top 5")
print(f"{'='*110}")

for rank, r in enumerate(valid[:5]):
    sym=r["sym"]
    if "5m" in r["name"]:
        d = btc_5m if sym=="BTC" else eth_5m
        split=int(len(d)*8/12);dev=d.iloc[:split].reset_index(drop=True);oos=d.iloc[split:].reset_index(drop=True)
        to=run_v52(oos);so=calc(to)
    elif "Swing" in r["name"]:
        d = btc_1h if sym=="BTC" else eth_1h
        split=int(len(d)*8/12);oos=d.iloc[split:].reset_index(drop=True)
        to=run_swing_ema50(oos);so=calc(to)
    elif "Squeeze" in r["name"]:
        sq=int(r["name"].split("<")[1])
        d = btc_1h if sym=="BTC" else eth_1h
        split=int(len(d)*8/12);oos=d.iloc[split:].reset_index(drop=True)
        to=run_volatility(oos,sq);so=calc(to)
    elif "FR" in r["name"]:
        parts=r["name"].split(">")[1].split("/")
        hi_p=int(parts[0]);lo_p=int(parts[1].replace("<",""))
        d = btc_1h if sym=="BTC" else eth_1h
        fr_raw = btc_fr_raw if sym=="BTC" else eth_fr_raw
        split=int(len(d)*8/12);oos=d.iloc[split:].reset_index(drop=True)
        to=run_fr_arb(oos,fr_raw,hi_p,lo_p);so=calc(to)
    else:
        continue

    print(f"\n  #{rank+1} {r['name']} ({r['sym']}): Full ${r['pnl']:+,.0f} → OOS ${so['pnl']:+,.0f} (WR {so['wr']}%, PF {so['pf']})")

print("\nDone.")
