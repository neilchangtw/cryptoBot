"""
A2. ETH/BTC Relative Strength
ETH_close / BTC_close ratio: EMA trend, Z-score, momentum
測的是「ETH 相對 BTC 表現」，跟 EMA50 測的「ETH 絕對趨勢」正交

測試：
  1. Ratio trend (EMA20>EMA50) 替換 EMA50 trend filter
  2. Ratio Z-score 做方向過濾
  3. Ratio momentum (10-bar) 做進場過濾
  4. Ratio breakout 獨立進場
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;MAX_SAME=2;SAFENET_PCT=0.03;FEE=1.6

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

# ============================================================
print("="*100)
print("  A2. ETH/BTC Relative Strength")
print("="*100)

print("\nFetching ETH 1h...", end=" ", flush=True)
eth=fetch("ETHUSDT","1h",365)
print(f"{len(eth)} bars")

print("Fetching BTC 1h...", end=" ", flush=True)
btc=fetch("BTCUSDT","1h",365)
print(f"{len(btc)} bars")

# Align by open time
df=eth.copy()
btc_map=btc.set_index("ot")["close"].to_dict()
df["btc_close"]=df["ot"].map(btc_map)
df=df.dropna(subset=["btc_close"]).reset_index(drop=True)
print(f"Aligned: {len(df)} bars")

# ============================================================
# Standard indicators
# ============================================================
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

# ============================================================
# Novel: ETH/BTC Ratio indicators
# ============================================================
df["ratio"]=df["close"]/df["btc_close"]
df["ratio_ema20"]=df["ratio"].ewm(span=20).mean()
df["ratio_ema50"]=df["ratio"].ewm(span=50).mean()
df["ratio_trend_up"]=df["ratio_ema20"]>df["ratio_ema50"]  # ETH outperforming BTC

# Z-score: how far ratio is from 50-bar mean
ratio_mean=df["ratio"].rolling(50).mean()
ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std

# 10-bar momentum: ratio change over 10 bars
df["ratio_mom10"]=df["ratio"].pct_change(10)*100  # as percentage

# 20-bar momentum
df["ratio_mom20"]=df["ratio"].pct_change(20)*100

# Ratio breakout: ratio crosses above/below EMA20
df["ratio_above_ema20"]=df["ratio"]>df["ratio_ema20"]
df["ratio_cross_up"]=(df["ratio_above_ema20"]) & (~df["ratio_above_ema20"].shift(1).fillna(False))
df["ratio_cross_dn"]=(~df["ratio_above_ema20"]) & (df["ratio_above_ema20"].shift(1).fillna(True))

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nRatio stats: mean={df['ratio'].mean():.6f} std={df['ratio'].std():.6f}")
print(f"Ratio Z-score: mean={df['ratio_zscore'].mean():.3f} std={df['ratio_zscore'].std():.3f}")
print(f"Ratio Mom10: mean={df['ratio_mom10'].mean():.3f}% std={df['ratio_mom10'].std():.3f}%")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", z_thresh=0.5, mom_thresh=1.0, min_hold=6):
    """
    mode:
      base         = Squeeze<20 + vol>1.0 + EMA50 trend + min6h (baseline C4)
      ratio_trend  = Squeeze + vol + ratio_trend replaces EMA50 trend
      ratio_z      = Squeeze + vol + ratio Z-score > threshold for long direction
      ratio_mom    = Squeeze + vol + ratio momentum > threshold for long direction
      ratio_combo  = Squeeze + vol + ratio_trend + ratio_mom > 0
      ratio_only   = Ratio breakout + ratio_trend (no squeeze)
      both_trend   = Squeeze + vol + EMA50 trend AND ratio_trend must agree
    """
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        # Exits
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            elif bars>=min_hold and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=min_hold and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue

        # Entry signals by mode
        l_sig=False;s_sig=False
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<20

        if mode=="base":
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="ratio_trend":
            # Replace EMA50 trend with ratio trend
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["ratio_trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["ratio_trend_up"]

        elif mode=="ratio_z":
            # Use ratio Z-score for direction filter
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["ratio_zscore"]>z_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and row["ratio_zscore"]<-z_thresh

        elif mode=="ratio_mom":
            # Use ratio momentum for direction filter
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["ratio_mom10"]>mom_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and row["ratio_mom10"]<-mom_thresh

        elif mode=="ratio_combo":
            # Ratio trend + positive momentum
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["ratio_trend_up"] and row["ratio_mom10"]>0
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["ratio_trend_up"] and row["ratio_mom10"]<0

        elif mode=="ratio_only":
            # Ratio breakout + ratio trend, no squeeze needed
            l_sig=row["ratio_cross_up"] and row["ratio_trend_up"] and row["trend_up"]
            s_sig=row["ratio_cross_dn"] and not row["ratio_trend_up"] and not row["trend_up"]

        elif mode=="both_trend":
            # Both EMA50 trend AND ratio trend must agree
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and row["ratio_trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and not row["ratio_trend_up"]

        info={"ratio_zscore":row["ratio_zscore"],"ratio_mom10":row["ratio_mom10"],
              "ratio_trend_up":row["ratio_trend_up"],"trend_up":row["trend_up"],
              "vol_ratio":row["vol_ratio"],"dt":row["datetime"],"entry_price":ep,
              "entry_hour":row["datetime"].hour}

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s_sig and len(spos)<MAX_SAME:
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
# 1. Full Sample Results
# ============================================================
print(f"\n{'='*100}")
print("1. Full Sample Results")
print(f"{'='*100}")

configs=[
    ("0. Base C4 (EMA50 trend)", "base", 0, 0, 6),
    # Replace EMA50 trend with ratio trend
    ("1. Ratio trend 替換 EMA50", "ratio_trend", 0, 0, 6),
    # Z-score filters
    ("2. Ratio Z>0.3 方向過濾", "ratio_z", 0.3, 0, 6),
    ("3. Ratio Z>0.5 方向過濾", "ratio_z", 0.5, 0, 6),
    ("4. Ratio Z>1.0 方向過濾", "ratio_z", 1.0, 0, 6),
    # Momentum filters
    ("5. Ratio Mom10>0.5% 過濾", "ratio_mom", 0, 0.5, 6),
    ("6. Ratio Mom10>1% 過濾", "ratio_mom", 0, 1.0, 6),
    ("7. Ratio Mom10>2% 過濾", "ratio_mom", 0, 2.0, 6),
    # Combo
    ("8. Ratio trend + mom>0", "ratio_combo", 0, 0, 6),
    # Both trends must agree
    ("9. EMA50 AND ratio trend", "both_trend", 0, 0, 6),
    # Ratio only
    ("10. Ratio breakout only", "ratio_only", 0, 0, 6),
    # Hold variations
    ("11. Ratio trend + min2h", "ratio_trend", 0, 0, 2),
    ("12. Both trend + min2h", "both_trend", 0, 0, 2),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,z,m,mh in configs:
    tdf=run(df,mode=mode,z_thresh=z,mom_thresh=m,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"z":z,"m":m,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. Ratio at Entry: Winners vs Losers
# ============================================================
print(f"\n{'='*100}")
print("2. Ratio Indicators at Entry: Winners vs Losers")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<25s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*70}")
    for col,label in [("ratio_zscore","Ratio Z-score"),("ratio_mom10","Ratio Mom10%"),
                       ("ratio_trend_up","Ratio Trend Up%"),("trend_up","EMA50 Trend Up%"),
                       ("vol_ratio","Vol Ratio")]:
        if col not in tdf_base.columns:continue
        if col in ("ratio_trend_up","trend_up"):
            wv=wins[col].mean()*100 if len(wins)>0 else 0
            lv=losses[col].mean()*100 if len(losses)>0 else 0
            sv=sn[col].mean()*100 if len(sn)>0 else 0
        else:
            wv=wins[col].mean() if len(wins)>0 else 0
            lv=losses[col].mean() if len(losses)>0 else 0
            sv=sn[col].mean() if len(sn)>0 else 0
        print(f"  {label:<25s} {wv:>10.3f} {lv:>10.3f} {sv:>10.3f} {wv-lv:>+10.3f}")

# ============================================================
# 3. Walk-Forward + Rolling
# ============================================================
print(f"\n{'='*100}")
print("3. Walk-Forward + Rolling")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<35s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*80}")

for r in all_r[:10]:
    to=run(oos,r["mode"],r["z"],r["m"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["z"],r["m"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# 4. Ratio Regime Analysis
# ============================================================
print(f"\n{'='*100}")
print("4. ETH/BTC Ratio Regime Analysis")
print(f"{'='*100}")

# Monthly ratio performance vs strategy performance
tdf_base=run(df,mode="base")
if len(tdf_base)>0 and "dt" in tdf_base.columns:
    tdf_base["month"]=tdf_base["dt"].dt.to_period("M")
    monthly_pnl=tdf_base.groupby("month")["pnl"].sum()

    # Monthly ratio change
    monthly_ratio=df.groupby("month")["ratio"].agg(["first","last"])
    monthly_ratio["change"]=(monthly_ratio["last"]/monthly_ratio["first"]-1)*100

    print(f"\n  {'Month':<12s} {'PnL':>8s} {'Ratio Chg%':>12s} {'Ratio Trend':>12s}")
    print(f"  {'-'*50}")
    for m in monthly_pnl.index:
        pnl=monthly_pnl[m]
        rchg=monthly_ratio.loc[m,"change"] if m in monthly_ratio.index else 0
        trend="ETH>BTC" if rchg>0 else "BTC>ETH"
        flag=" !!!" if pnl>0 and rchg>0 else (" <<<" if pnl<-100 else "")
        print(f"  {str(m):<12s} ${pnl:>+7,.0f} {rchg:>+11.2f}% {trend:>12s}{flag}")

print("\nDone.")
