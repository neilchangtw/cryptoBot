"""
Champion + A7 Decisiveness + A8 VP Divergence
測試把 A7/A8 加入 Optimized Champion (Sess+RatioZ1+MTF40+mh12+SN3.5%+blockSat)

A7 最佳: Body>0.6 filter (PF 1.54) — 突破 K 線決斷力
A8 最佳: OBV filter + VP exit (PF 1.44) — OBV 趨勢 + VP 背離出場

測試：
  1. Champion + Body ratio filter
  2. Champion + OBV trend filter
  3. Champion + VP exit
  4. Champion + OBV + VP exit
  5. Champion + Body + OBV
  6. 各種組合
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;MAX_SAME=2;FEE=1.6
WORST_HOURS={0,1,2,12}
WORST_DAYS={0,5,6}

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
    for c in ["open","high","low","close","volume","tbv","qv"]:df[c]=pd.to_numeric(df[c])
    df["trades"]=pd.to_numeric(df["trades"])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

print("="*100)
print("  Champion + A7/A8 Combination Tests")
print("="*100)

print("\nFetching data...", flush=True)
df_1h=fetch("ETHUSDT","1h",365)
btc=fetch("BTCUSDT","1h",365)
df_4h=fetch("ETHUSDT","4h",365)

btc_map=btc.set_index("ot")["close"].to_dict()
df=df_1h.copy()
df["btc_close"]=df["ot"].map(btc_map)

tr_4h=pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],"hc":abs(df_4h["high"]-df_4h["close"].shift(1)),"lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
mapped=pd.merge_asof(df[["ot"]].sort_values("ot"),df_4h[["ot","atr_4h"]].dropna().sort_values("ot"),on="ot",direction="backward")
df["atr_4h"]=mapped["atr_4h"].values

tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["trend_up"]=df["close"]>df["ema50"]

# ETH/BTC ratio
df["ratio"]=df["close"]/df["btc_close"]
ratio_mean=df["ratio"].rolling(50).mean();ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std

# MTF
df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# A7: Body ratio
hl_range=(df["high"]-df["low"]).replace(0,np.nan)
df["body_ratio"]=abs(df["close"]-df["open"])/hl_range
df["body_ratio"]=df["body_ratio"].fillna(0)
df["bullish_body"]=df["close"]>df["open"]
df["bearish_body"]=df["close"]<df["open"]
df["body_ratio_ma5"]=df["body_ratio"].rolling(5).mean()

# A8: OBV + VP
df["obv"]=(np.sign(df["close"].diff())*df["volume"]).cumsum()
df["obv_ema20"]=df["obv"].ewm(span=20).mean()
df["obv_trend_up"]=df["obv"]>df["obv_ema20"]
df["abs_pchg"]=abs(df["close"].pct_change())
df["vp_corr_20"]=df["volume"].rolling(20).corr(df["abs_pchg"])

# Taker
df["taker_ratio"]=df["tbv"]/df["volume"]

df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars")

# ============================================================
def run(data, cfg):
    body_min=cfg.get("body_min",0)
    body_dir=cfg.get("body_dir",False)
    obv_filter=cfg.get("obv_filter",False)
    vp_exit=cfg.get("vp_exit",False)
    taker_filter=cfg.get("taker_filter",0)
    pre_indecision=cfg.get("pre_indecision",0)

    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]
        vp_c=row["vp_corr_20"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-0.035):
                ep=p["entry"]*(1-0.035)-(p["entry"]*(1-0.035)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif vp_exit and bars>=6 and vp_c<-0.3:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"VP_Exit","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif bars>=12 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+0.035):
                ep=p["entry"]*(1+0.035)+(hi-p["entry"]*(1+0.035))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif vp_exit and bars>=6 and vp_c<-0.3:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"VP_Exit","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif bars>=12 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        h=row["hour"];wd=row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:continue
        if h==10:continue  # block h10

        if data.iloc[i-1]["mtf_ratio_pctile"]>=40:continue
        bb_long=close>row["bb_upper"];bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue
        if row["vol_ratio"]<=1.0:continue

        rzv=row["ratio_zscore"]
        l_sig=bb_long and rzv>1.0
        s_sig=bb_short and rzv<-1.0
        if not l_sig and not s_sig:continue

        # A7: Body ratio filters
        if body_min>0 and row["body_ratio"]<body_min:continue
        if body_dir:
            if l_sig and not row["bullish_body"]:continue
            if s_sig and not row["bearish_body"]:continue
        if pre_indecision>0 and row["body_ratio_ma5"]>=pre_indecision:continue

        # A8: OBV filter
        if obv_filter:
            if l_sig and not row["obv_trend_up"]:continue
            if s_sig and row["obv_trend_up"]:continue

        # Taker filter
        if taker_filter>0:
            if l_sig and row["taker_ratio"]<taker_filter:continue
            if s_sig and row["taker_ratio"]>(1-taker_filter):continue

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":{}})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":{}})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    dd=(tdf["pnl"].cumsum()-(tdf["pnl"].cumsum()).cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2)}

# ============================================================
print(f"\n{'='*100}")
print("1. Full Sample Results")
print(f"{'='*100}")

BASE={}  # optimized champion defaults

configs=[
    ("Optimized Champion [BASE]", {}),
    # A7: Body ratio
    ("+ Body>0.3", {"body_min":0.3}),
    ("+ Body>0.4", {"body_min":0.4}),
    ("+ Body>0.5", {"body_min":0.5}),
    ("+ Body>0.6", {"body_min":0.6}),
    ("+ Body direction match", {"body_dir":True}),
    ("+ Body>0.5 + direction", {"body_min":0.5,"body_dir":True}),
    ("+ Pre indecision<0.4", {"pre_indecision":0.4}),
    ("+ Pre indecision<0.35", {"pre_indecision":0.35}),
    # A8: OBV
    ("+ OBV trend filter", {"obv_filter":True}),
    ("+ VP exit", {"vp_exit":True}),
    ("+ OBV + VP exit", {"obv_filter":True,"vp_exit":True}),
    # Taker
    ("+ Taker>0.52", {"taker_filter":0.52}),
    ("+ Taker>0.55", {"taker_filter":0.55}),
    # Combos
    ("+ Body>0.5 + OBV", {"body_min":0.5,"obv_filter":True}),
    ("+ Body>0.4 + OBV + VP", {"body_min":0.4,"obv_filter":True,"vp_exit":True}),
    ("+ Body dir + OBV", {"body_dir":True,"obv_filter":True}),
    ("+ OBV + Taker>0.52", {"obv_filter":True,"taker_filter":0.52}),
    ("+ Body>0.5 + Taker>0.52", {"body_min":0.5,"taker_filter":0.52}),
    ("+ Body>0.4+OBV+Taker", {"body_min":0.4,"obv_filter":True,"taker_filter":0.52}),
    ("+ Body dir+OBV+VP", {"body_dir":True,"obv_filter":True,"vp_exit":True}),
]

print(f"\n  {'Config':<40s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s}")
print(f"  {'-'*84}")

all_r=[]
for name,cfg in configs:
    tdf=run(df,cfg);s=calc(tdf)
    all_r.append({"name":name,"cfg":cfg,**s})
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>4000 else "")
    print(f"  {name:<40s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f}{flag}")

# ============================================================
print(f"\n{'='*100}")
print("2. Walk-Forward + Rolling (All configs)")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<40s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*82}")

wf_results=[]
for r in all_r[:15]:
    to=run(oos,r["cfg"]);so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["cfg"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    roll=sum(fp)
    wf_results.append({**r,"oos_pnl":so["pnl"],"oos_pf":so["pf"],"oos_sn":so["sn"],"roll":roll,"folds_str":f"{prof}/{len(fp)}"})
    print(f"  {r['name']:<40s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${roll:>+6,.0f} {prof}/{len(fp)}")

# ============================================================
print(f"\n{'='*100}")
print("3. Hold Time Comparison (Best A7/A8 combo vs Base)")
print(f"{'='*100}")

wf_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
top_combo=wf_results[0]

# Compare hold time distributions
tdf_base=run(df,{})
tdf_best=run(df,top_combo["cfg"])

print(f"\n  Best: {top_combo['name']}")
print(f"\n  {'Hold':<10s} {'Base N':>7s} {'Base$':>8s} {'Best N':>7s} {'Best$':>8s} {'Diff$':>8s}")
print(f"  {'-'*52}")

for lo_h,hi_h,label in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
    b_sub=tdf_base[(tdf_base["bars"]>=lo_h)&(tdf_base["bars"]<hi_h)]
    t_sub=tdf_best[(tdf_best["bars"]>=lo_h)&(tdf_best["bars"]<hi_h)]
    bp=b_sub["pnl"].sum() if len(b_sub)>0 else 0
    tp=t_sub["pnl"].sum() if len(t_sub)>0 else 0
    print(f"  {label:<10s} {len(b_sub):>7d} ${bp:>+7,.0f} {len(t_sub):>7d} ${tp:>+7,.0f} ${tp-bp:>+7,.0f}")

# ============================================================
print(f"\n{'='*100}")
print("SUMMARY")
print(f"{'='*100}")

wf_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
print(f"\n  Top 5 by OOS PnL:")
print(f"  {'#':>2s} {'Config':<40s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*76}")
for i,r in enumerate(wf_results[:5]):
    print(f"  {i+1:>2d} {r['name']:<40s} ${r['pnl']:>+6,.0f} ${r['oos_pnl']:>+6,.0f} {r['oos_pf']:>6.2f} "
          f"${r['roll']:>+6,.0f} {r['folds_str']}")

print("\nDone.")
