"""
C2. 時間框架混搭（4h 方向 + 1h 進場）
D1. 結構出場（到下一個 Swing 才出）
D2. 部分 TP + trailing（1h 版）

基底進場：1h Swing 突破 + 資金費率過濾（前次最佳）
1 年資料，嚴格回測
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

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

def fetch_funding(days=365):
    all_fr=[];cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000);end=int(datetime.now().timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://fapi.binance.com/fapi/v1/fundingRate",params={"symbol":"BTCUSDT","startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_fr.extend(d);cur=d[-1]["fundingTime"]+1;_time.sleep(0.1)
        except:break
    if not all_fr:return pd.Series(dtype=float)
    fr=pd.DataFrame(all_fr);fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    return fr.set_index("dt")["rate"].resample("1h").ffill()

print("Fetching 1h...", end=" ", flush=True)
raw1h=fetch("1h",365); print(f"{len(raw1h)}")
print("Fetching 4h...", end=" ", flush=True)
raw4h=fetch("4h",365); print(f"{len(raw4h)}")
print("Fetching 15m...", end=" ", flush=True)
raw15m=fetch("15m",365); print(f"{len(raw15m)}")
print("Fetching FR...", end=" ", flush=True)
fr_s=fetch_funding(365); print(f"{len(fr_s)}")

# 1h 指標
df=raw1h.copy()
tr1h=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr1h.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["vol_ma20"]=df["volume"].rolling(20).mean()
df["vol_ratio"]=df["volume"]/df["vol_ma20"]
# Swing (1h)
w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
for i in range(w,len(df)-w):
    if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
    if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
# 前一個 swing（不是當前的，是再前一個 → 用 shift 跳過）
df["prev_swing_hi"]=sh.ffill().shift(1)
df["prev_swing_lo"]=sl.ffill().shift(1)

# 4h 趨勢（延遲一根）
df4=raw4h.copy()
df4["ema20_4h"]=df4["close"].ewm(span=20).mean()
df4["ema50_4h"]=df4["close"].ewm(span=50).mean()
df4["trend_4h_up"]=(df4["close"]>df4["ema50_4h"]).shift(1)
# 4h Swing
w4=3;sh4=pd.Series(np.nan,index=df4.index);sl4=pd.Series(np.nan,index=df4.index)
for i in range(w4,len(df4)-w4):
    if df4["high"].iloc[i]==df4["high"].iloc[i-w4:i+w4+1].max():sh4.iloc[i]=df4["high"].iloc[i]
    if df4["low"].iloc[i]==df4["low"].iloc[i-w4:i+w4+1].min():sl4.iloc[i]=df4["low"].iloc[i]
df4["swing_hi_4h"]=sh4.ffill();df4["swing_lo_4h"]=sl4.ffill()
df4_map=df4[["datetime","trend_4h_up","swing_hi_4h","swing_lo_4h"]].copy()
df4_map["key_4h"]=df4_map["datetime"].dt.floor("4h")+timedelta(hours=4)
df["key_4h"]=df["datetime"].dt.floor("4h")
df=df.merge(df4_map[["key_4h","trend_4h_up","swing_hi_4h","swing_lo_4h"]],on="key_4h",how="left")

# 15m 指標（用於 C2 精準進場）
df15=raw15m.copy()
tr15=pd.DataFrame({"hl":df15["high"]-df15["low"],"hc":abs(df15["high"]-df15["close"].shift(1)),"lc":abs(df15["low"]-df15["close"].shift(1))}).max(axis=1)
df15["atr_15m"]=tr15.rolling(14).mean()
d15=df15["close"].diff();g15=d15.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l15=(-d15.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df15["rsi_15m"]=100-100/(1+g15/l15)
df15["ema9_15m"]=df15["close"].ewm(span=9).mean()
df15["ema20_15m"]=df15["close"].ewm(span=20).mean()

# FR
df=df.set_index("datetime")
df["fr"]=fr_s.reindex(df.index,method="ffill")
df["fr_pctile"]=df["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df=df.reset_index()
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"1h ready: {len(df)} bars")

# ============================================================
# 回測引擎
# ============================================================
def run(data, entry_fn, exit_fn):
    """exit_fn(pos, row, data, i) -> None(hold) or exit_price"""
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(row["high"]-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            # 安全網
            if row["low"]<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-row["low"])*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-ep*(MARGIN*LEVERAGE)/p["entry"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            else:
                result = exit_fn(p, row, data, i, "long")
                if result is not None:
                    ep=result["price"]; reason=result["reason"]
                    pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]*result.get("pct",1.0)-ep*(MARGIN*LEVERAGE)/p["entry"]*result.get("pct",1.0)*0.0004*2
                    # 如果是部分平倉
                    if result.get("partial",False):
                        trades.append({"pnl":pnl,"type":reason,"side":"long","bars":bars,"mf":mf,"dt":row["datetime"]})
                        p["partial_done"]=True; p["partial_pnl"]=pnl
                    else:
                        total_pnl = pnl + p.get("partial_pnl",0)
                        trades.append({"pnl":total_pnl if not p.get("partial_done") else pnl,
                                       "type":reason,"side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-row["low"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if row["high"]>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(row["high"]-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-ep*(MARGIN*LEVERAGE)/p["entry"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            else:
                result = exit_fn(p, row, data, i, "short")
                if result is not None:
                    ep=result["price"]; reason=result["reason"]
                    pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]*result.get("pct",1.0)-ep*(MARGIN*LEVERAGE)/p["entry"]*result.get("pct",1.0)*0.0004*2
                    if result.get("partial",False):
                        trades.append({"pnl":pnl,"type":reason,"side":"short","bars":bars,"mf":mf,"dt":row["datetime"]})
                        p["partial_done"]=True; p["partial_pnl"]=pnl
                    else:
                        total_pnl = pnl + p.get("partial_pnl",0)
                        trades.append({"pnl":total_pnl if not p.get("partial_done") else pnl,
                                       "type":reason,"side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        l_sig,s_sig=entry_fn(row,data,i)
        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"trhi":ep,"trlo":ep,"partial_done":False,"partial_pnl":0})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"trhi":ep,"trlo":ep,"partial_done":False,"partial_pnl":0})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf","dt"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"win_avg":0,"loss_avg":0,"rr":0,"avg_bars":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    wa=w["pnl"].mean() if len(w)>0 else 0;la=l["pnl"].mean() if len(l)>0 else 0
    rr=abs(wa/la) if la!=0 else 999
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "win_avg":round(wa,2),"loss_avg":round(la,2),"rr":round(rr,2),"avg_bars":round(tdf["bars"].mean(),1)}

# ============================================================
# 進場函數
# ============================================================

# 基線：1h Swing 突破 + FR
def entry_swing_fr(row, data, i):
    if i<1:return False,False
    l=row["close"]>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
    s=row["close"]<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
    fr_p=row.get("fr_pctile",50)
    if not np.isnan(fr_p):
        if fr_p>80:l=False
        if fr_p<20:s=False
    return l,s

# C2: 4h 方向 + 1h Swing 突破 + FR
def entry_4h_swing_fr(row, data, i):
    if i<1:return False,False
    l=row["close"]>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
    s=row["close"]<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
    # 4h 方向確認
    t4=row.get("trend_4h_up",None)
    if t4 is not None and not np.isnan(t4):
        if not t4: l=False  # 4h 非多頭不做多
        if t4: s=False      # 4h 非空頭不做空
    fr_p=row.get("fr_pctile",50)
    if not np.isnan(fr_p):
        if fr_p>80:l=False
        if fr_p<20:s=False
    return l,s

# ============================================================
# 出場函數
# ============================================================

# 基線：EMA20 trailing
def exit_ema20(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    if side=="long":
        if row["close"]<=row["ema20"]:
            return {"price":row["close"],"reason":"EMA20","pct":1.0}
    else:
        if row["close"]>=row["ema20"]:
            return {"price":row["close"],"reason":"EMA20","pct":1.0}
    return None

# D1: 結構出場（到下一個 Swing High/Low）
def exit_structure(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    if side=="long":
        # 做多 → 到前一個 Swing High 才出（結構目標）
        target = data["swing_hi"].iloc[i]  # 最新的已確認 swing high
        if target > pos["entry"] and row["close"] >= target:
            return {"price":row["close"],"reason":"Structure","pct":1.0}
        # 安全出場：跌破 EMA50 = 趨勢結束
        if bars>=5 and row["close"]<row["ema50"]:
            return {"price":row["close"],"reason":"EMA50_exit","pct":1.0}
    else:
        target = data["swing_lo"].iloc[i]
        if target < pos["entry"] and row["close"] <= target:
            return {"price":row["close"],"reason":"Structure","pct":1.0}
        if bars>=5 and row["close"]>row["ema50"]:
            return {"price":row["close"],"reason":"EMA50_exit","pct":1.0}
    return None

# D1b: 結構出場 + 時間止損
def exit_structure_ts(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    r = exit_structure(pos, row, data, i, side)
    if r:return r
    # 時間止損 48h
    if bars>=48:
        return {"price":row["close"],"reason":"TimeStop48h","pct":1.0}
    return None

# D2: 部分 TP + EMA50 trailing（1h 版）
def exit_partial_trail(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    atr=row["atr"] if not np.isnan(row["atr"]) else 500
    if side=="long":
        # 部分 TP：到 1x ATR 平 50%
        if not pos.get("partial_done") and row["close"]>=pos["entry"]+1.0*atr:
            return {"price":row["close"],"reason":"TP50%","pct":0.5,"partial":True}
        # 剩餘 50% 用 EMA50 trailing
        if pos.get("partial_done"):
            if row["close"]<row["ema50"]:
                return {"price":row["close"],"reason":"EMA50_trail","pct":0.5}
    else:
        if not pos.get("partial_done") and row["close"]<=pos["entry"]-1.0*atr:
            return {"price":row["close"],"reason":"TP50%","pct":0.5,"partial":True}
        if pos.get("partial_done"):
            if row["close"]>row["ema50"]:
                return {"price":row["close"],"reason":"EMA50_trail","pct":0.5}
    return None

# D2b: 部分 TP + EMA20 trailing
def exit_partial_ema20(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    atr=row["atr"] if not np.isnan(row["atr"]) else 500
    if side=="long":
        if not pos.get("partial_done") and row["close"]>=pos["entry"]+1.0*atr:
            return {"price":row["close"],"reason":"TP50%","pct":0.5,"partial":True}
        if pos.get("partial_done"):
            if row["close"]<row["ema20"]:
                return {"price":row["close"],"reason":"EMA20_trail","pct":0.5}
    else:
        if not pos.get("partial_done") and row["close"]<=pos["entry"]-1.0*atr:
            return {"price":row["close"],"reason":"TP50%","pct":0.5,"partial":True}
        if pos.get("partial_done"):
            if row["close"]>row["ema20"]:
                return {"price":row["close"],"reason":"EMA20_trail","pct":0.5}
    return None

# EMA50 trailing（不分批）
def exit_ema50(pos, row, data, i, side):
    bars=i-pos["ei"]
    if bars<2:return None
    if side=="long":
        if row["close"]<=row["ema50"]:
            return {"price":row["close"],"reason":"EMA50","pct":1.0}
    else:
        if row["close"]>=row["ema50"]:
            return {"price":row["close"],"reason":"EMA50","pct":1.0}
    return None

# ============================================================
# 測試
# ============================================================
configs = [
    # (名稱, entry_fn, exit_fn)
    ("base.Swing+FR/EMA20",      entry_swing_fr,    exit_ema20),
    ("base.Swing+FR/EMA50",      entry_swing_fr,    exit_ema50),
    ("C2.4h+Swing+FR/EMA20",     entry_4h_swing_fr, exit_ema20),
    ("C2.4h+Swing+FR/EMA50",     entry_4h_swing_fr, exit_ema50),
    ("D1.Swing+FR/Structure",    entry_swing_fr,    exit_structure),
    ("D1b.Swing+FR/Struct+TS48h",entry_swing_fr,    exit_structure_ts),
    ("D2.Swing+FR/TP50%+EMA50",  entry_swing_fr,    exit_partial_trail),
    ("D2b.Swing+FR/TP50%+EMA20", entry_swing_fr,    exit_partial_ema20),
    ("C2+D1.4h/Structure",       entry_4h_swing_fr, exit_structure),
    ("C2+D1b.4h/Struct+TS",      entry_4h_swing_fr, exit_structure_ts),
    ("C2+D2.4h/TP50%+EMA50",     entry_4h_swing_fr, exit_partial_trail),
    ("C2+D2b.4h/TP50%+EMA20",    entry_4h_swing_fr, exit_partial_ema20),
]

# 全樣本
print(f"\n{'='*110}")
print(f"全樣本（1 年，{len(configs)} 組合）")
print(f"{'='*110}")

print(f"\n  {'組合':<28s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>5s} {'DD':>8s} {'Bars':>5s} {'W均':>6s} {'L均':>6s} {'RR':>5s}")
print(f"  {'-'*90}")

all_r=[]
for name,efn,xfn in configs:
    tdf=run(df,efn,xfn)
    s=calc(tdf)
    if s["n"]<3:
        print(f"  {name:<28s} {s['n']:>5d} (too few)"); continue
    all_r.append({"name":name,"efn":efn,"xfn":xfn,**s})
    print(f"  {name:<28s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} "
          f"{s['avg_bars']:>4.0f}h ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# WF
print(f"\n{'='*110}")
print("Walk-Forward + 滾動（Top 全部）")
print(f"{'='*110}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);dev_df=df.iloc[:split].reset_index(drop=True);oos_df=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'組合':<28s} {'Full':>7s} {'OOS':>7s} {'OOS WR':>7s} {'OOS PF':>7s} {'OOS RR':>6s} {'滾動':>8s} {'折':>5s}")
print(f"  {'-'*80}")

for r in all_r:
    td=run(dev_df,r["efn"],r["xfn"]);to=run(oos_df,r["efn"],r["xfn"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["efn"],r["xfn"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<28s} ${r['pnl']:>+5,.0f} ${so['pnl']:>+5,.0f} {so['wr']:>6.1f}% {so['pf']:>6.2f} "
          f"{so['rr']:>5.1f}:1 ${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
