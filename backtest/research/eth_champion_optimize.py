"""
Champion Strategy Deep Optimization
Base: Sess + RatioZ1 + MTF40 ($+3,571, 42.2% WR, PF 3.85 OOS)

Phase C 發現的改善空間：
  1. 6-12h 持倉 42 筆全虧 (-$1,349) → 增加 min_hold
  2. 利潤留存只有 32% → 改善出場方式（EMA trail 太慢）
  3. SafeNet 12 次 -$796 → ATR-based 動態安全網
  4. 閾值微調 → Ratio Z, MTF pctile, session

優化維度：
  O1. Min hold optimization (6→12→24)
  O2. Trail exit variants (EMA10/15/20/30, ATR trail, Chandelier)
  O3. TP1 partial exit (take profit + trail rest)
  O4. Dynamic SafeNet (ATR-based instead of fixed 3%)
  O5. Entry threshold fine-tuning
  O6. Combined best-of-each
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;MAX_SAME=2;FEE=1.6
WORST_HOURS={0,1,2,12}
WORST_DAYS={0,6}

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
print("  Champion Strategy Deep Optimization")
print("="*100)

print("\nFetching ETH 1h...", end=" ", flush=True)
df_1h=fetch("ETHUSDT","1h",365)
print(f"{len(df_1h)} bars")
print("Fetching BTC 1h...", end=" ", flush=True)
btc=fetch("BTCUSDT","1h",365)
print(f"{len(btc)} bars")
print("Fetching ETH 4h...", end=" ", flush=True)
df_4h=fetch("ETHUSDT","4h",365)
print(f"{len(df_4h)} bars")

# Indicators
btc_map=btc.set_index("ot")["close"].to_dict()
df=df_1h.copy()
df["btc_close"]=df["ot"].map(btc_map)

tr_4h=pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],"hc":abs(df_4h["high"]-df_4h["close"].shift(1)),"lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
mapped=pd.merge_asof(df[["ot"]].sort_values("ot"),df_4h[["ot","atr_4h"]].dropna().sort_values("ot"),on="ot",direction="backward")
df["atr_4h"]=mapped["atr_4h"].values

tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema10"]=df["close"].ewm(span=10).mean()
df["ema15"]=df["close"].ewm(span=15).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema30"]=df["close"].ewm(span=30).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["trend_up"]=df["close"]>df["ema50"]

df["ratio"]=df["close"]/df["btc_close"]
ratio_mean=df["ratio"].rolling(50).mean();ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std

df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars")

# ============================================================
# Flexible backtest engine
# ============================================================
def run(data, cfg):
    """
    cfg keys:
      min_hold: int (minimum bars before trail exit)
      trail: "ema10"|"ema15"|"ema20"|"ema30"|"atr2"|"atr3"|"chandelier2.5"|"chandelier3"
      safenet_pct: float (0.03=3%, 0=use atr-based)
      safenet_atr: float (if >0, use N*ATR as safenet instead of fixed pct)
      rz_thresh: float (ratio z-score threshold, default 1.0)
      mtf_thresh: float (mtf pctile threshold, default 40)
      tp1_atr: float (if >0, take profit at N*ATR, then trail rest)
      tp1_pct: float (0-1, fraction to close at TP1, rest trails)
      block_sat: bool (also block Saturday)
      block_h10: bool (also block hour 10)
      time_stop: int (if >0, force exit after N hours)
    """
    min_hold=cfg.get("min_hold",6)
    trail_type=cfg.get("trail","ema20")
    safenet_pct=cfg.get("safenet_pct",0.03)
    safenet_atr_mult=cfg.get("safenet_atr",0)
    rz=cfg.get("rz_thresh",1.0)
    mtf_t=cfg.get("mtf_thresh",40)
    tp1_atr=cfg.get("tp1_atr",0)
    tp1_pct=cfg.get("tp1_pct",0)
    block_sat=cfg.get("block_sat",False)
    block_h10=cfg.get("block_h10",False)
    time_stop=cfg.get("time_stop",0)

    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]
        atr_now=row["atr"]

        # Exits
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            sz=p["entry"]*safenet_pct if safenet_pct>0 else safenet_atr_mult*p.get("entry_atr",atr_now)
            # TP1
            if tp1_atr>0 and not p.get("tp1_done") and hi>=p["entry"]+tp1_atr*p.get("entry_atr",atr_now):
                tp_price=p["entry"]+tp1_atr*p.get("entry_atr",atr_now)
                partial_pnl=(tp_price-p["entry"])*(MARGIN*LEVERAGE*tp1_pct)/p["entry"]-FEE*tp1_pct
                trades.append({**p.get("info",{}),"pnl":partial_pnl,"type":"TP1","side":"long","bars":bars,"mf":mf})
                p["tp1_done"]=True
                p["remaining"]=1-tp1_pct
                # Move SL to breakeven
                p["be_stop"]=p["entry"]
            # SafeNet
            sn_price=p["entry"]-sz
            if lo<=sn_price:
                ep=sn_price-(sn_price-lo)*0.25
                rem=p.get("remaining",1)
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            # BE stop after TP1
            elif p.get("tp1_done") and p.get("be_stop") and lo<=p["be_stop"]:
                rem=p.get("remaining",1)
                pnl=(p["be_stop"]-p["entry"])*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"BE_Stop","side":"long","bars":bars,"mf":mf});c=True
            # Time stop
            elif time_stop>0 and bars>=time_stop:
                rem=p.get("remaining",1)
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"mf":mf});c=True
            # Trail
            elif bars>=min_hold:
                trail_hit=False
                if trail_type=="ema10":trail_hit=close<=row["ema10"]
                elif trail_type=="ema15":trail_hit=close<=row["ema15"]
                elif trail_type=="ema20":trail_hit=close<=row["ema20"]
                elif trail_type=="ema30":trail_hit=close<=row["ema30"]
                elif trail_type.startswith("atr"):
                    mult=float(trail_type[3:])
                    peak_price=p["entry"]+mf*p["entry"]/(MARGIN*LEVERAGE)
                    trail_hit=close<=peak_price-mult*atr_now
                elif trail_type.startswith("chandelier"):
                    mult=float(trail_type[10:])
                    hh=data.iloc[max(0,p["ei"]):i+1]["high"].max()
                    trail_hit=close<=hh-mult*atr_now
                if trail_hit:
                    rem=p.get("remaining",1)
                    pnl=(close-p["entry"])*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                    trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            sz=p["entry"]*safenet_pct if safenet_pct>0 else safenet_atr_mult*p.get("entry_atr",atr_now)
            # TP1
            if tp1_atr>0 and not p.get("tp1_done") and lo<=p["entry"]-tp1_atr*p.get("entry_atr",atr_now):
                tp_price=p["entry"]-tp1_atr*p.get("entry_atr",atr_now)
                partial_pnl=(p["entry"]-tp_price)*(MARGIN*LEVERAGE*tp1_pct)/p["entry"]-FEE*tp1_pct
                trades.append({**p.get("info",{}),"pnl":partial_pnl,"type":"TP1","side":"short","bars":bars,"mf":mf})
                p["tp1_done"]=True
                p["remaining"]=1-tp1_pct
                p["be_stop"]=p["entry"]
            sn_price=p["entry"]+sz
            if hi>=sn_price:
                ep=sn_price+(hi-sn_price)*0.25
                rem=p.get("remaining",1)
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            elif p.get("tp1_done") and p.get("be_stop") and hi>=p["be_stop"]:
                rem=p.get("remaining",1)
                pnl=(p["entry"]-p["be_stop"])*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"BE_Stop","side":"short","bars":bars,"mf":mf});c=True
            elif time_stop>0 and bars>=time_stop:
                rem=p.get("remaining",1)
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=min_hold:
                trail_hit=False
                if trail_type=="ema10":trail_hit=close>=row["ema10"]
                elif trail_type=="ema15":trail_hit=close>=row["ema15"]
                elif trail_type=="ema20":trail_hit=close>=row["ema20"]
                elif trail_type=="ema30":trail_hit=close>=row["ema30"]
                elif trail_type.startswith("atr"):
                    mult=float(trail_type[3:])
                    trough_price=p["entry"]-mf*p["entry"]/(MARGIN*LEVERAGE)
                    trail_hit=close>=trough_price+mult*atr_now
                elif trail_type.startswith("chandelier"):
                    mult=float(trail_type[10:])
                    ll=data.iloc[max(0,p["ei"]):i+1]["low"].min()
                    trail_hit=close>=ll+mult*atr_now
                if trail_hit:
                    rem=p.get("remaining",1)
                    pnl=(p["entry"]-close)*(MARGIN*LEVERAGE*rem)/p["entry"]-FEE*rem
                    trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        h=row["hour"];wd=row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:continue
        if block_sat and wd==5:continue
        if block_h10 and h==10:continue

        if data.iloc[i-1]["mtf_ratio_pctile"]>=mtf_t:continue
        bb_long=close>row["bb_upper"];bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue
        if row["vol_ratio"]<=1.0:continue

        rzv=row["ratio_zscore"]
        l_sig=bb_long and rzv>rz
        s_sig=bb_short and rzv<-rz

        info={"entry_hour":h,"entry_weekday":wd,"entry_atr":row["atr"],
              "entry_vol":row["vol_ratio"],"entry_rsi":row["rsi"],
              "entry_bb_pctile":row["bb_width_pctile"],"entry_price":ep,
              "dt":row["datetime"],"ratio_zscore":rzv,"mtf_pctile":row["mtf_ratio_pctile"]}

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"entry_atr":row["atr"],"info":info})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"entry_atr":row["atr"],"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0,"trail_pnl":0,"tp1_pnl":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"]
    tp1=tdf[tdf["type"]=="TP1"]
    non_sn=tdf[~tdf["type"].isin(["SafeNet"])]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),
            "trail_pnl":round(non_sn["pnl"].sum(),2),
            "tp1_pnl":round(tp1["pnl"].sum(),2) if len(tp1)>0 else 0}

BASE_CFG={"min_hold":6,"trail":"ema20","safenet_pct":0.03,"safenet_atr":0,
           "rz_thresh":1.0,"mtf_thresh":40,"tp1_atr":0,"tp1_pct":0,
           "block_sat":False,"block_h10":False,"time_stop":0}

# ============================================================
# O1. Min Hold Optimization
# ============================================================
print(f"\n{'='*100}")
print("O1. Min Hold Optimization")
print(f"{'='*100}")

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

for mh in [2,4,6,8,10,12,16,20,24]:
    cfg={**BASE_CFG,"min_hold":mh}
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {'min_hold='+str(mh):<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# O2. Trail Exit Variants
# ============================================================
print(f"\n{'='*100}")
print("O2. Trail Exit Variants")
print(f"{'='*100}")

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

trail_tests=[
    ("EMA10 trail (mh6)", {"trail":"ema10","min_hold":6}),
    ("EMA15 trail (mh6)", {"trail":"ema15","min_hold":6}),
    ("EMA20 trail (mh6) [BASE]", {"trail":"ema20","min_hold":6}),
    ("EMA30 trail (mh6)", {"trail":"ema30","min_hold":6}),
    ("EMA10 trail (mh12)", {"trail":"ema10","min_hold":12}),
    ("EMA15 trail (mh12)", {"trail":"ema15","min_hold":12}),
    ("EMA20 trail (mh12)", {"trail":"ema20","min_hold":12}),
    ("EMA30 trail (mh12)", {"trail":"ema30","min_hold":12}),
    ("ATR 2x trail (mh6)", {"trail":"atr2","min_hold":6}),
    ("ATR 2.5x trail (mh6)", {"trail":"atr2.5","min_hold":6}),
    ("ATR 3x trail (mh6)", {"trail":"atr3","min_hold":6}),
    ("ATR 2x trail (mh12)", {"trail":"atr2","min_hold":12}),
    ("ATR 3x trail (mh12)", {"trail":"atr3","min_hold":12}),
    ("Chandelier 2.5x (mh6)", {"trail":"chandelier2.5","min_hold":6}),
    ("Chandelier 3x (mh6)", {"trail":"chandelier3","min_hold":6}),
    ("Chandelier 2.5x (mh12)", {"trail":"chandelier2.5","min_hold":12}),
    ("Chandelier 3x (mh12)", {"trail":"chandelier3","min_hold":12}),
]

for name,overrides in trail_tests:
    cfg={**BASE_CFG,**overrides}
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# O3. TP1 Partial Exit + Trail Rest
# ============================================================
print(f"\n{'='*100}")
print("O3. TP1 Partial Exit + Trail Rest")
print(f"{'='*100}")

print(f"\n  {'Config':<40s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'TP1$':>8s}")
print(f"  {'-'*92}")

tp1_tests=[
    ("No TP1 [BASE]", {"tp1_atr":0}),
    ("TP1 1.5xATR close 50%", {"tp1_atr":1.5,"tp1_pct":0.5}),
    ("TP1 2.0xATR close 50%", {"tp1_atr":2.0,"tp1_pct":0.5}),
    ("TP1 2.5xATR close 50%", {"tp1_atr":2.5,"tp1_pct":0.5}),
    ("TP1 3.0xATR close 50%", {"tp1_atr":3.0,"tp1_pct":0.5}),
    ("TP1 1.5xATR close 33%", {"tp1_atr":1.5,"tp1_pct":0.33}),
    ("TP1 2.0xATR close 33%", {"tp1_atr":2.0,"tp1_pct":0.33}),
    ("TP1 3.0xATR close 33%", {"tp1_atr":3.0,"tp1_pct":0.33}),
    ("TP1 2.0xATR close 75%", {"tp1_atr":2.0,"tp1_pct":0.75}),
    # TP1 + better trail
    ("TP1 2x50% + EMA15 mh12", {"tp1_atr":2.0,"tp1_pct":0.5,"trail":"ema15","min_hold":12}),
    ("TP1 2x50% + Chand3 mh12", {"tp1_atr":2.0,"tp1_pct":0.5,"trail":"chandelier3","min_hold":12}),
]

for name,overrides in tp1_tests:
    cfg={**BASE_CFG,**overrides}
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {name:<40s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['tp1_pnl']:>+7,.0f}{flag}")

# ============================================================
# O4. SafeNet Optimization
# ============================================================
print(f"\n{'='*100}")
print("O4. SafeNet Optimization")
print(f"{'='*100}")

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s}")
print(f"  {'-'*80}")

sn_tests=[
    ("SN 3.0% [BASE]", {"safenet_pct":0.03}),
    ("SN 2.5%", {"safenet_pct":0.025}),
    ("SN 2.0%", {"safenet_pct":0.02}),
    ("SN 3.5%", {"safenet_pct":0.035}),
    ("SN 4.0%", {"safenet_pct":0.04}),
    ("SN 2xATR", {"safenet_pct":0,"safenet_atr":2.0}),
    ("SN 2.5xATR", {"safenet_pct":0,"safenet_atr":2.5}),
    ("SN 3xATR", {"safenet_pct":0,"safenet_atr":3.0}),
    ("SN 3.5xATR", {"safenet_pct":0,"safenet_atr":3.5}),
    ("SN 4xATR", {"safenet_pct":0,"safenet_atr":4.0}),
]

for name,overrides in sn_tests:
    cfg={**BASE_CFG,**overrides}
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f}{flag}")

# ============================================================
# O5. Entry Threshold Fine-tuning
# ============================================================
print(f"\n{'='*100}")
print("O5. Entry Threshold Fine-tuning")
print(f"{'='*100}")

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s}")
print(f"  {'-'*72}")

entry_tests=[
    ("RZ>1.0 MTF<40 [BASE]", {"rz_thresh":1.0,"mtf_thresh":40}),
    ("RZ>0.5 MTF<40", {"rz_thresh":0.5,"mtf_thresh":40}),
    ("RZ>0.8 MTF<40", {"rz_thresh":0.8,"mtf_thresh":40}),
    ("RZ>1.2 MTF<40", {"rz_thresh":1.2,"mtf_thresh":40}),
    ("RZ>1.5 MTF<40", {"rz_thresh":1.5,"mtf_thresh":40}),
    ("RZ>1.0 MTF<30", {"rz_thresh":1.0,"mtf_thresh":30}),
    ("RZ>1.0 MTF<35", {"rz_thresh":1.0,"mtf_thresh":35}),
    ("RZ>1.0 MTF<45", {"rz_thresh":1.0,"mtf_thresh":45}),
    ("RZ>1.0 MTF<50", {"rz_thresh":1.0,"mtf_thresh":50}),
    ("RZ>0.8 MTF<35", {"rz_thresh":0.8,"mtf_thresh":35}),
    ("RZ>1.2 MTF<35", {"rz_thresh":1.2,"mtf_thresh":35}),
    ("+Block Sat", {"block_sat":True}),
    ("+Block H10", {"block_h10":True}),
    ("+Block Sat+H10", {"block_sat":True,"block_h10":True}),
    ("Time stop 72h", {"time_stop":72}),
    ("Time stop 96h", {"time_stop":96}),
]

for name,overrides in entry_tests:
    cfg={**BASE_CFG,**overrides}
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d}{flag}")

# ============================================================
# O6. Best Combinations
# ============================================================
print(f"\n{'='*100}")
print("O6. Best Combinations")
print(f"{'='*100}")

print(f"\n  {'Config':<45s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s}")
print(f"  {'-'*92}")

combo_tests=[
    ("Champion BASE (mh6 ema20 sn3%)", BASE_CFG),
    # Best min_hold + trail combos
    ("mh12 + EMA15", {**BASE_CFG,"min_hold":12,"trail":"ema15"}),
    ("mh12 + EMA20", {**BASE_CFG,"min_hold":12,"trail":"ema20"}),
    ("mh12 + Chandelier3", {**BASE_CFG,"min_hold":12,"trail":"chandelier3"}),
    ("mh16 + EMA20", {**BASE_CFG,"min_hold":16,"trail":"ema20"}),
    # + SafeNet optimization
    ("mh12 + EMA20 + SN3.5%", {**BASE_CFG,"min_hold":12,"safenet_pct":0.035}),
    ("mh12 + EMA20 + SN3xATR", {**BASE_CFG,"min_hold":12,"safenet_pct":0,"safenet_atr":3.0}),
    ("mh12 + Chand3 + SN3.5%", {**BASE_CFG,"min_hold":12,"trail":"chandelier3","safenet_pct":0.035}),
    # + TP1
    ("mh12 + EMA20 + TP1 2x50%", {**BASE_CFG,"min_hold":12,"tp1_atr":2.0,"tp1_pct":0.5}),
    ("mh12 + Chand3 + TP1 2x50%", {**BASE_CFG,"min_hold":12,"trail":"chandelier3","tp1_atr":2.0,"tp1_pct":0.5}),
    # + Entry
    ("mh12 + EMA20 + blockSat", {**BASE_CFG,"min_hold":12,"block_sat":True}),
    ("mh12 + EMA20 + RZ>0.8 MTF<35", {**BASE_CFG,"min_hold":12,"rz_thresh":0.8,"mtf_thresh":35}),
    # Kitchen sink (carefully selected)
    ("mh12+Chand3+SN3.5%+blockSat", {**BASE_CFG,"min_hold":12,"trail":"chandelier3","safenet_pct":0.035,"block_sat":True}),
    ("mh12+EMA20+SN3.5%+blockSat", {**BASE_CFG,"min_hold":12,"safenet_pct":0.035,"block_sat":True}),
    ("mh12+EMA15+SN3xATR+TP1 2x50%", {**BASE_CFG,"min_hold":12,"trail":"ema15","safenet_pct":0,"safenet_atr":3.0,"tp1_atr":2.0,"tp1_pct":0.5}),
    ("mh16+EMA20+SN3.5%", {**BASE_CFG,"min_hold":16,"safenet_pct":0.035}),
]

all_r=[]
for name,cfg in combo_tests:
    tdf=run(df,cfg);s=calc(tdf)
    all_r.append({"name":name,"cfg":cfg,**s})
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>4000 else "")
    print(f"  {name:<45s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f}{flag}")

# ============================================================
# O7. Walk-Forward + Rolling (Top combos)
# ============================================================
print(f"\n{'='*100}")
print("O7. Walk-Forward + Rolling (Top Combos)")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<45s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*86}")

wf_results=[]
for r in all_r[:12]:
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
    print(f"  {r['name']:<45s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${roll:>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# O8. Hold Time Analysis for Best Combo
# ============================================================
print(f"\n{'='*100}")
print("O8. Hold Time Analysis (Top 3 combos)")
print(f"{'='*100}")

wf_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
for rank,r in enumerate(wf_results[:3]):
    tdf=run(df,r["cfg"])
    print(f"\n  --- #{rank+1}: {r['name']} ---")
    print(f"  {'Hold':>8s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'Avg$':>7s}")
    print(f"  {'-'*38}")
    for lo_h,hi_h,label in [(0,6,"<6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
        sub=tdf[(tdf["bars"]>=lo_h)&(tdf["bars"]<hi_h)]
        if len(sub)==0:continue
        ws=sub[sub["pnl"]>0]
        wr=len(ws)/len(sub)*100
        avg=sub["pnl"].mean()
        print(f"  {label:>8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {wr:>5.0f}% ${avg:>+5.0f}")

    # Profit efficiency
    if len(tdf)>0 and tdf["mf"].mean()>0:
        wins=tdf[tdf["pnl"]>0]
        print(f"\n  Profit retention: {tdf['pnl'].mean()/tdf['mf'].mean()*100:.0f}%")
        if len(wins)>0:
            print(f"  Win retention: {wins['pnl'].mean()/wins['mf'].mean()*100:.0f}%")

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*100}")
print("SUMMARY")
print(f"{'='*100}")

wf_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
print(f"\n  Top 5 by OOS PnL:")
print(f"  {'#':>2s} {'Config':<45s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*82}")
for i,r in enumerate(wf_results[:5]):
    print(f"  {i+1:>2d} {r['name']:<45s} ${r['pnl']:>+6,.0f} ${r['oos_pnl']:>+6,.0f} {r['oos_pf']:>6.2f} "
          f"${r['roll']:>+6,.0f} {r['folds_str']}")

print("\nDone.")
