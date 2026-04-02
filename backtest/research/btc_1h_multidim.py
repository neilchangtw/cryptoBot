"""
1h 多維度策略 — 用多個維度交叉確認提高勝率
目標：勝率 40~45%，每筆 +$30~40，賺賠比 2:1+

進場信號（2 種基底）：
  S1. Swing High/Low 突破（前次最佳）
  S2. BB Squeeze 突破

維度 A — 方向確認：
  A1. 4h EMA 趨勢（4h close > 4h EMA50 = 多頭）
  A2. 1h EMA20 > EMA50（1h 趨勢向上）

維度 B — 資金面：
  B1. 資金費率百分位（>80 不做多 / <20 不做空）
  B2. 資金費率方向（費率下降 → 不做多，上升 → 不做空）

維度 C — 力道確認：
  C1. 成交量 > 1.5x 均量
  C2. 成交量 > 1.2x 均量
  C3. ADX > 20（有趨勢）

出場：
  EMA20 trailing（前次最佳）
  + 安全網 ±3%

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
    fr=pd.DataFrame(all_fr)
    fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    return fr.set_index("dt")["rate"].resample("1h").ffill()

print("Fetching 1h...", end=" ", flush=True)
raw1h=fetch("1h",365); print(f"{len(raw1h)}")
print("Fetching 4h...", end=" ", flush=True)
raw4h=fetch("4h",365); print(f"{len(raw4h)}")
print("Fetching FR...", end=" ", flush=True)
fr_s=fetch_funding(365); print(f"{len(fr_s)}")

# 1h 指標
df=raw1h.copy()
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean()
df["vol_ratio"]=df["volume"]/df["vol_ma20"]
# ADX
up_m=df["high"]-df["high"].shift(1);dn_m=df["low"].shift(1)-df["low"]
pdm=pd.Series(np.where((up_m>dn_m)&(up_m>0),up_m,0.0),index=df.index)
mdm=pd.Series(np.where((dn_m>up_m)&(dn_m>0),dn_m,0.0),index=df.index)
s_atr=tr.ewm(alpha=1/14,min_periods=14).mean()
pdi=100*pdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
mdi=100*mdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,1)
df["adx"]=dx.ewm(alpha=1/14,min_periods=14).mean()
# Swing
w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
for i in range(w,len(df)-w):
    if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
    if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
# 1h 趨勢
df["trend_1h_up"]=df["ema20"]>df["ema50"]

# 4h 趨勢（延遲一根對齊）
df4=raw4h.copy()
df4["ema50_4h"]=df4["close"].ewm(span=50).mean()
df4["trend_4h_up"]=(df4["close"]>df4["ema50_4h"]).shift(1)  # 延遲一根
df4_map=df4[["datetime","trend_4h_up"]].copy()
df4_map["key_4h"]=df4_map["datetime"].dt.floor("4h")+timedelta(hours=4)
df["key_4h"]=df["datetime"].dt.floor("4h")
df=df.merge(df4_map[["key_4h","trend_4h_up"]],on="key_4h",how="left")

# 資金費率
df=df.set_index("datetime")
df["fr"]=fr_s.reindex(df.index,method="ffill")
df["fr_pctile"]=df["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["fr_rising"]=df["fr"]>df["fr"].shift(1)
df=df.reset_index()
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars")

# ============================================================
# 回測（EMA20 trailing + 安全網）
# ============================================================
def run(data, entry_fn):
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*p["qty"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            elif bars>=2 and close<=row["ema20"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*p["qty"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            elif bars>=2 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        l_sig, s_sig = entry_fn(row, data, i)
        if l_sig and len(lpos)<MAX_SAME:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"ei":i,"mf":0})
        if s_sig and len(spos)<MAX_SAME:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"ei":i,"mf":0})

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
# 多維度組合定義
# ============================================================
def _swing_break(row, data, i):
    if i<1:return False,False
    l=row["close"]>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
    s=row["close"]<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
    return l,s

def _bb_squeeze(row, data, i):
    was=data.iloc[i-1]["bb_width_pctile"]<20 if i>0 else False
    l=was and row["close"]>row["bb_upper"]
    s=was and row["close"]<row["bb_lower"]
    return l,s

configs = []

# 基線
configs.append(("S1.Swing 基線", lambda r,d,i: _swing_break(r,d,i)))
configs.append(("S2.Squeeze 基線", lambda r,d,i: _bb_squeeze(r,d,i)))

# + 單一維度
for sname, sfn_base in [("S1", _swing_break), ("S2", _bb_squeeze)]:
    # A1: 4h 趨勢
    configs.append((f"{sname}+4h趨勢", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r.get("trend_4h_up",True)==True,
        sf(r,d,i)[1] and r.get("trend_4h_up",True)==False)))
    # A2: 1h EMA 趨勢
    configs.append((f"{sname}+1h趨勢", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r["trend_1h_up"]==True,
        sf(r,d,i)[1] and r["trend_1h_up"]==False)))
    # B1: FR pctile
    configs.append((f"{sname}+FR過濾", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r.get("fr_pctile",50)<80,
        sf(r,d,i)[1] and r.get("fr_pctile",50)>20)))
    # C1: Vol > 1.5x
    configs.append((f"{sname}+Vol1.5x", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r["vol_ratio"]>1.5,
        sf(r,d,i)[1] and r["vol_ratio"]>1.5)))
    # C2: Vol > 1.2x
    configs.append((f"{sname}+Vol1.2x", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r["vol_ratio"]>1.2,
        sf(r,d,i)[1] and r["vol_ratio"]>1.2)))
    # C3: ADX > 20
    configs.append((f"{sname}+ADX20", lambda r,d,i,sf=sfn_base: (
        sf(r,d,i)[0] and r["adx"]>20,
        sf(r,d,i)[1] and r["adx"]>20)))

# 多維度組合（S1 Swing 基底）
# 4h趨勢 + FR
configs.append(("S1+4h+FR", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r.get("trend_4h_up",True)==True and r.get("fr_pctile",50)<80,
    _swing_break(r,d,i)[1] and r.get("trend_4h_up",True)==False and r.get("fr_pctile",50)>20)))
# 4h趨勢 + Vol
configs.append(("S1+4h+Vol1.2", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r.get("trend_4h_up",True)==True and r["vol_ratio"]>1.2,
    _swing_break(r,d,i)[1] and r.get("trend_4h_up",True)==False and r["vol_ratio"]>1.2)))
# 4h趨勢 + FR + Vol
configs.append(("S1+4h+FR+Vol1.2", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r.get("trend_4h_up",True)==True and r.get("fr_pctile",50)<80 and r["vol_ratio"]>1.2,
    _swing_break(r,d,i)[1] and r.get("trend_4h_up",True)==False and r.get("fr_pctile",50)>20 and r["vol_ratio"]>1.2)))
# 1h趨勢 + FR
configs.append(("S1+1h+FR", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r["trend_1h_up"]==True and r.get("fr_pctile",50)<80,
    _swing_break(r,d,i)[1] and r["trend_1h_up"]==False and r.get("fr_pctile",50)>20)))
# 1h趨勢 + FR + Vol
configs.append(("S1+1h+FR+Vol1.2", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r["trend_1h_up"]==True and r.get("fr_pctile",50)<80 and r["vol_ratio"]>1.2,
    _swing_break(r,d,i)[1] and r["trend_1h_up"]==False and r.get("fr_pctile",50)>20 and r["vol_ratio"]>1.2)))
# ADX + FR
configs.append(("S1+ADX20+FR", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r["adx"]>20 and r.get("fr_pctile",50)<80,
    _swing_break(r,d,i)[1] and r["adx"]>20 and r.get("fr_pctile",50)>20)))
# 4h + ADX + FR
configs.append(("S1+4h+ADX+FR", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r.get("trend_4h_up",True)==True and r["adx"]>20 and r.get("fr_pctile",50)<80,
    _swing_break(r,d,i)[1] and r.get("trend_4h_up",True)==False and r["adx"]>20 and r.get("fr_pctile",50)>20)))
# 全維度
configs.append(("S1+4h+ADX+FR+Vol", lambda r,d,i: (
    _swing_break(r,d,i)[0] and r.get("trend_4h_up",True)==True and r["adx"]>20 and r.get("fr_pctile",50)<80 and r["vol_ratio"]>1.2,
    _swing_break(r,d,i)[1] and r.get("trend_4h_up",True)==False and r["adx"]>20 and r.get("fr_pctile",50)>20 and r["vol_ratio"]>1.2)))

# ============================================================
# 跑全部
# ============================================================
print(f"\n{'='*120}")
print(f"全樣本（1 年，{len(configs)} 組合）")
print(f"{'='*120}")

print(f"\n  {'組合':<25s} {'交易':>5s} {'損益':>8s} {'勝率':>6s} {'PF':>5s} {'回撤':>8s} {'持倉':>5s} {'W均':>6s} {'L均':>6s} {'賺賠':>5s}")
print(f"  {'-'*85}")

all_r=[]
for name, efn in configs:
    tdf=run(df, efn)
    s=calc(tdf)
    if s["n"]<3:
        print(f"  {name:<25s} {s['n']:>5d} (too few)"); continue
    all_r.append({"name":name,"efn":efn,**s})
    print(f"  {name:<25s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} "
          f"{s['avg_bars']:>4.0f}h ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# WF
print(f"\n{'='*120}")
print("Walk-Forward（前 8 / 後 4）+ 滾動 — Top 10")
print(f"{'='*120}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12)
dev_df=df.iloc[:split].reset_index(drop=True);oos_df=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'組合':<25s} {'Full':>7s} {'OOS':>7s} {'OOS WR':>7s} {'OOS PF':>7s} {'OOS N':>6s} {'賺賠':>5s} {'滾動WF':>8s} {'折':>5s}")
print(f"  {'-'*85}")

for r in all_r[:10]:
    td=run(dev_df,r["efn"]);to=run(oos_df,r["efn"])
    so=calc(to)
    # 滾動
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["efn"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<25s} ${r['pnl']:>+5,.0f} ${so['pnl']:>+5,.0f} {so['wr']:>6.1f}% {so['pf']:>6.2f} {so['n']:>6d} "
          f"{so['rr']:>4.1f}:1 ${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
