"""
A8. Volume-Price Divergence (量價趨勢背離)
rolling_corr(volume, |price_change|, 20)
正相關 = 量價齊升（健康趨勢），負相關 = 量價背離（耗竭）
跟 Volume filter 的差異：vol filter 測「量大不大」，這個測「量跟價的關係品質」

測試：
  1. corr > 0.3 作為 breakout 確認（量價齊升）
  2. corr 轉負作為出場信號（耗竭偵測）
  3. 量價方向一致性作為趨勢品質過濾
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
print("  A8. Volume-Price Divergence")
print("="*100)

print("\nFetching ETH 1h...", end=" ", flush=True)
df=fetch("ETHUSDT","1h",365)
print(f"{len(df)} bars")

# Standard indicators
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
# Novel: Volume-Price Divergence indicators
# ============================================================
# Absolute price change
df["abs_pchg"]=abs(df["close"].pct_change())

# Rolling correlation: volume vs |price change| over N bars
df["vp_corr_10"]=df["volume"].rolling(10).corr(df["abs_pchg"])
df["vp_corr_20"]=df["volume"].rolling(20).corr(df["abs_pchg"])
df["vp_corr_30"]=df["volume"].rolling(30).corr(df["abs_pchg"])

# Directional: volume vs signed price change (positive = vol rises with price up)
df["signed_pchg"]=df["close"].pct_change()
df["vp_dir_corr_20"]=df["volume"].rolling(20).corr(df["signed_pchg"])

# Volume trend vs Price trend divergence
# Volume MA slope vs Price MA slope
df["vol_slope"]=(df["vol_ma20"]-df["vol_ma20"].shift(5))/df["vol_ma20"].shift(5)
df["price_slope"]=(df["ema20"]-df["ema20"].shift(5))/df["ema20"].shift(5)
df["vp_slope_agree"]=((df["vol_slope"]>0) & (df["price_slope"].abs()>df["price_slope"].abs().rolling(20).mean()))

# OBV (On Balance Volume) trend
df["obv"]=(np.sign(df["close"].diff())*df["volume"]).cumsum()
df["obv_ema20"]=df["obv"].ewm(span=20).mean()
df["obv_trend_up"]=df["obv"]>df["obv_ema20"]

# Volume-weighted price momentum
df["vwpm"]=(df["close"].pct_change()*df["volume"]).rolling(10).sum()/df["volume"].rolling(10).sum()

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nVP Corr(20) stats: mean={df['vp_corr_20'].mean():.3f} std={df['vp_corr_20'].std():.3f}")
print(f"  P10={df['vp_corr_20'].quantile(0.1):.3f} P25={df['vp_corr_20'].quantile(0.25):.3f} "
      f"P50={df['vp_corr_20'].quantile(0.5):.3f} P75={df['vp_corr_20'].quantile(0.75):.3f}")
print(f"OBV trend up: {df['obv_trend_up'].mean()*100:.1f}%")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", corr_thresh=0.3, corr_period=20, min_hold=6, vp_exit=False):
    """
    mode:
      base           = BB Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      vp_corr_pos    = base + VP correlation > threshold (volume confirms price)
      vp_corr_neg    = base + VP correlation < -threshold (contrarian: divergence = reversal)
      vp_replace_vol = VP corr > threshold replaces vol>1.0
      obv_filter     = base + OBV trend matches trade direction
      obv_replace    = OBV trend replaces EMA50 trend filter
      vp_combo       = base + VP corr > 0 + OBV trend match
      vwpm_filter    = base + VWPM direction matches trade
    vp_exit: if True, exit when VP corr turns negative (exhaustion)
    """
    lpos=[];spos=[];trades=[]
    corr_col=f"vp_corr_{corr_period}" if f"vp_corr_{corr_period}" in data.columns else "vp_corr_20"

    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]
        vp_c=row[corr_col] if corr_col in row.index else 0

        # Exits
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            elif vp_exit and bars>=4 and vp_c<-0.3:
                # VP divergence exit: volume and price diverging
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"VP_Exit","side":"long","bars":bars,"mf":mf});c=True
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
            elif vp_exit and bars>=4 and vp_c<-0.3:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"VP_Exit","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=min_hold and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue

        # Entry signals
        l_sig=False;s_sig=False
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<20

        if mode=="base":
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="vp_corr_pos":
            # Volume confirms price movement (healthy breakout)
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and vp_c>corr_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and vp_c>corr_thresh

        elif mode=="vp_corr_neg":
            # Contrarian: divergence before breakout might mean reversal is coming
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and vp_c<-corr_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and vp_c<-corr_thresh

        elif mode=="vp_replace_vol":
            # VP correlation replaces vol>1.0 filter
            l_sig=was_squeeze and close>row["bb_upper"] and row["trend_up"] and vp_c>corr_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and not row["trend_up"] and vp_c>corr_thresh

        elif mode=="obv_filter":
            # OBV trend must match direction
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and row["obv_trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and not row["obv_trend_up"]

        elif mode=="obv_replace":
            # OBV trend replaces EMA50 trend
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["obv_trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["obv_trend_up"]

        elif mode=="vp_combo":
            # VP correlation positive + OBV trend match
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and vp_c>0 and row["obv_trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and vp_c>0 and not row["obv_trend_up"]

        elif mode=="vwpm_filter":
            # Volume-weighted price momentum direction match
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and row["vwpm"]>0
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and row["vwpm"]<0

        info={"vp_corr":vp_c,"obv_trend_up":row["obv_trend_up"],
              "vwpm":row["vwpm"],"trend_up":row["trend_up"],
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
    sn=tdf[tdf["type"]=="SafeNet"];tr=tdf[tdf["type"]!="SafeNet"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),"trail_pnl":round(tr["pnl"].sum(),2)}

# ============================================================
# 1. Full Sample Results
# ============================================================
print(f"\n{'='*100}")
print("1. Full Sample Results")
print(f"{'='*100}")

configs=[
    ("0. Base C4", "base", 0.3, 20, 6, False),
    # VP correlation positive (volume confirms)
    ("1. VP corr(20)>0.1", "vp_corr_pos", 0.1, 20, 6, False),
    ("2. VP corr(20)>0.2", "vp_corr_pos", 0.2, 20, 6, False),
    ("3. VP corr(20)>0.3", "vp_corr_pos", 0.3, 20, 6, False),
    ("4. VP corr(10)>0.2", "vp_corr_pos", 0.2, 10, 6, False),
    ("5. VP corr(30)>0.2", "vp_corr_pos", 0.2, 30, 6, False),
    # VP correlation negative (contrarian)
    ("6. VP corr<-0.2 contrarian", "vp_corr_neg", 0.2, 20, 6, False),
    # Replace vol>1.0
    ("7. VP corr>0.2 替換 vol", "vp_replace_vol", 0.2, 20, 6, False),
    ("8. VP corr>0.1 替換 vol", "vp_replace_vol", 0.1, 20, 6, False),
    # OBV
    ("9. OBV trend filter", "obv_filter", 0.3, 20, 6, False),
    ("10. OBV 替換 EMA50 trend", "obv_replace", 0.3, 20, 6, False),
    # Combo
    ("11. VP>0 + OBV combo", "vp_combo", 0.3, 20, 6, False),
    # VWPM
    ("12. VWPM direction", "vwpm_filter", 0.3, 20, 6, False),
    # VP exit
    ("13. Base + VP exit", "base", 0.3, 20, 6, True),
    ("14. OBV filter + VP exit", "obv_filter", 0.3, 20, 6, True),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,ct,cp,mh,vpe in configs:
    tdf=run(df,mode=mode,corr_thresh=ct,corr_period=cp,min_hold=mh,vp_exit=vpe)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"ct":ct,"cp":cp,"mh":mh,"vpe":vpe,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. VP Corr at Entry: Winners vs Losers
# ============================================================
print(f"\n{'='*100}")
print("2. VP Indicators at Entry: Winners vs Losers")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<25s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*70}")
    for col,label in [("vp_corr","VP Corr(20)"),("vwpm","VWPM"),("vol_ratio","Vol Ratio")]:
        if col not in tdf_base.columns:continue
        wv=wins[col].mean() if len(wins)>0 else 0
        lv=losses[col].mean() if len(losses)>0 else 0
        sv=sn[col].mean() if len(sn)>0 else 0
        print(f"  {label:<25s} {wv:>10.4f} {lv:>10.4f} {sv:>10.4f} {wv-lv:>+10.4f}")
    for col,label in [("obv_trend_up","OBV Trend Up%")]:
        if col not in tdf_base.columns:continue
        wv=wins[col].mean()*100 if len(wins)>0 else 0
        lv=losses[col].mean()*100 if len(losses)>0 else 0
        sv=sn[col].mean()*100 if len(sn)>0 else 0
        print(f"  {label:<25s} {wv:>10.1f} {lv:>10.1f} {sv:>10.1f} {wv-lv:>+10.1f}")

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
    to=run(oos,r["mode"],r["ct"],r["cp"],r["mh"],r["vpe"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["ct"],r["cp"],r["mh"],r["vpe"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.02f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
