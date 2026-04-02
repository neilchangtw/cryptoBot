"""
ETH Squeeze SafeNet 優化
分析 20 筆 SafeNet 特徵 + 測試改善方案
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;MAX_SAME=2

def fetch(symbol,interval,days=365):
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

print("Fetching ETH 1h...", end=" ", flush=True)
raw=fetch("ETHUSDT","1h",365)
df=raw.copy()
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["atr_ma50"]=df["atr"].rolling(50).mean()
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
# 趨勢
df["trend_up"]=df["close"]>df["ema50"]
df["atr_rising"]=df["atr"]>df["atr_ma50"]
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"{len(df)} bars")

# ============================================================
# 先跑基線，收集 SafeNet 的詳細資訊
# ============================================================
def run(data, safenet_pct=0.03, trend_filter=False, atr_filter=False, bb_squeeze_max=20, min_hold_trail=2):
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-safenet_pct):
                ep=p["entry"]*(1-safenet_pct)-(p["entry"]*(1-safenet_pct)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf}); c=True
            elif bars>=min_hold_trail and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+safenet_pct):
                ep=p["entry"]*(1+safenet_pct)+(hi-p["entry"]*(1+safenet_pct))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf}); c=True
            elif bars>=min_hold_trail and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<bb_squeeze_max
        l=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0
        s=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0

        # 趨勢過濾
        if trend_filter:
            if not row["trend_up"]: l=False  # 非多頭不做多
            if row["trend_up"]: s=False       # 非空頭不做空

        # ATR 過濾
        if atr_filter:
            if row["atr"]>row["atr_ma50"]: l=False; s=False  # 波動上升不做

        info={"entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "entry_atr_pctile":row["atr_pctile"],"entry_bb_pctile":row["bb_width_pctile"],
              "entry_vol":row["vol_ratio"],"trend_up":row["trend_up"],"atr_rising":row["atr"]>row["atr_ma50"],
              "dt":row["datetime"],"entry_price":ep}

        if l and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0,"trail_pnl":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"];tr=tdf[tdf["type"]=="Trail"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),"trail_pnl":round(tr["pnl"].sum(),2)}

# ============================================================
# 1. 分析 SafeNet 20 筆的特徵
# ============================================================
print(f"\n{'='*100}")
print("1. SafeNet 20 筆特徵分析")
print(f"{'='*100}")

tdf_base = run(df)
sn = tdf_base[tdf_base["type"]=="SafeNet"]
trail = tdf_base[tdf_base["type"]=="Trail"]
trail_ok = trail[trail["pnl"]>0]

print(f"\n  SafeNet {len(sn)} 筆 vs Trail獲利 {len(trail_ok)} 筆:")
print(f"\n  {'Feature':<20s} {'SafeNet':>10s} {'Trail Win':>10s} {'Diff':>10s}")
print(f"  {'-'*55}")

for col, label in [("entry_hour","Hour"),("entry_weekday","Weekday"),("entry_atr_pctile","ATR pctile"),
                     ("entry_bb_pctile","BB width pctile"),("entry_vol","Volume"),("trend_up","Trend Up %"),
                     ("atr_rising","ATR Rising %"),("bars","Hold(h)")]:
    if col not in sn.columns:continue
    if col in ("trend_up","atr_rising"):
        sv=sn[col].mean()*100;tv=trail_ok[col].mean()*100
    else:
        sv=sn[col].mean();tv=trail_ok[col].mean()
    print(f"  {label:<20s} {sv:>10.1f} {tv:>10.1f} {sv-tv:>+10.1f}")

# SafeNet 多空
print(f"\n  SafeNet 多空：")
for side in ["long","short"]:
    sub=sn[sn["side"]==side]
    print(f"    {side}: {len(sub)} 筆 ${sub['pnl'].sum():+,.0f}")

# SafeNet 逐筆
print(f"\n  SafeNet 逐筆：")
for _,t in sn.iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | "
          f"ATRp {t.get('entry_atr_pctile',0):.0f} | BBp {t.get('entry_bb_pctile',0):.0f} | "
          f"Trend {'Up' if t.get('trend_up',False) else 'Dn'} | {t['dt']}")

# ============================================================
# 2. 測試改善方案
# ============================================================
print(f"\n{'='*100}")
print("2. SafeNet 改善方案測試")
print(f"{'='*100}")

configs = [
    # (名稱, safenet_pct, trend_filter, atr_filter, bb_max, min_hold)
    ("0. base (3%)", 0.03, False, False, 20, 2),

    # 縮小安全網
    ("S1. SN=2.5%", 0.025, False, False, 20, 2),
    ("S2. SN=2%", 0.02, False, False, 20, 2),
    ("S3. SN=1.5%", 0.015, False, False, 20, 2),

    # 趨勢過濾（順勢才做）
    ("T1. 趨勢過濾(EMA50)", 0.03, True, False, 20, 2),

    # ATR 過濾
    ("A1. ATR<MA50", 0.03, False, True, 20, 2),

    # BB 更嚴
    ("B1. BB<15", 0.03, False, False, 15, 2),
    ("B2. BB<10", 0.03, False, False, 10, 2),

    # 最低持倉（前 6h 不讓 EMA20 踢出）
    ("M1. min6h trail", 0.03, False, False, 20, 6),
    ("M2. min12h trail", 0.03, False, False, 20, 12),

    # 組合
    ("C1. SN2%+趨勢", 0.02, True, False, 20, 2),
    ("C2. SN2%+min6h", 0.02, False, False, 20, 6),
    ("C3. SN2.5%+趨勢+min6h", 0.025, True, False, 20, 6),
    ("C4. 趨勢+min6h", 0.03, True, False, 20, 6),
    ("C5. ATR+min6h", 0.03, False, True, 20, 6),
    ("C6. SN2%+ATR+min6h", 0.02, False, True, 20, 6),
]

print(f"\n  {'方案':<25s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>5s} {'DD':>8s} {'SN':>4s} {'SN虧':>8s} {'Trail賺':>8s}")
print(f"  {'-'*80}")

all_r=[]
for name,sp,tf,af,bbm,mh in configs:
    tdf=run(df,safenet_pct=sp,trend_filter=tf,atr_filter=af,bb_squeeze_max=bbm,min_hold_trail=mh)
    s=calc(tdf)
    if s["n"]<3:
        print(f"  {name:<25s} {s['n']:>5d} (too few)");continue
    all_r.append({"name":name,"sp":sp,"tf":tf,"af":af,"bbm":bbm,"mh":mh,**s})
    print(f"  {name:<25s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}")

# ============================================================
# 3. WF + 滾動
# ============================================================
print(f"\n{'='*100}")
print("3. Walk-Forward + Rolling")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);dev=df.iloc[:split].reset_index(drop=True);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'方案':<25s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'滾動':>8s} {'折':>5s}")
print(f"  {'-'*70}")

for r in all_r:
    to=run(oos,r["sp"],r["tf"],r["af"],r["bbm"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["sp"],r["tf"],r["af"],r["bbm"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<25s} ${r['pnl']:>+5,.0f} ${so['pnl']:>+5,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
