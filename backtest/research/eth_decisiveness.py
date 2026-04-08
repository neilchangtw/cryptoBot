"""
A7. K 線實體比（Decisiveness / Body Ratio）
|close-open| / (high-low) 的滾動平均
大實體 = 機構決斷性進場；長影線 = 猶豫不決（假突破前兆）
完全未被測試的維度（K 線形態學）

測試：
  1. Breakout K 線 body_ratio > 0.6 過濾（確認突破品質）
  2. 突破前 5 根低 decisiveness = 真壓縮確認
  3. Body ratio 作為獨立趨勢品質過濾
  4. 影線比 (wick ratio) 作為假突破過濾
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
print("  A7. Candle Body Ratio (Decisiveness)")
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
# Novel: Body Ratio + Wick analysis
# ============================================================
hl_range=df["high"]-df["low"]
hl_range=hl_range.replace(0,np.nan)  # avoid div by zero

# Body ratio: |close-open| / (high-low), 0=doji, 1=marubozu
df["body_ratio"]=abs(df["close"]-df["open"])/hl_range
df["body_ratio"]=df["body_ratio"].fillna(0)

# Rolling average body ratio (market decisiveness over last N bars)
df["body_ratio_ma5"]=df["body_ratio"].rolling(5).mean()
df["body_ratio_ma10"]=df["body_ratio"].rolling(10).mean()

# Directional body: positive = bullish, negative = bearish
df["body_dir"]=(df["close"]-df["open"])/hl_range
df["body_dir"]=df["body_dir"].fillna(0)

# Upper wick ratio: (high - max(open,close)) / (high-low)
df["upper_wick"]=(df["high"]-df[["open","close"]].max(axis=1))/hl_range
df["upper_wick"]=df["upper_wick"].fillna(0)

# Lower wick ratio: (min(open,close) - low) / (high-low)
df["lower_wick"]=(df[["open","close"]].min(axis=1)-df["low"])/hl_range
df["lower_wick"]=df["lower_wick"].fillna(0)

# Pre-breakout indecision: average body ratio of last 5 bars (low = more indecision = better squeeze)
df["pre_decisive_5"]=df["body_ratio"].shift(1).rolling(5).mean()

# Breakout candle quality: current bar body ratio (high = decisive breakout)
# Combined with direction matching (bullish breakout should have bullish body)
df["bullish_body"]=df["close"]>df["open"]
df["bearish_body"]=df["close"]<df["open"]

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nBody Ratio stats: mean={df['body_ratio'].mean():.3f} std={df['body_ratio'].std():.3f}")
print(f"  P10={df['body_ratio'].quantile(0.1):.3f} P25={df['body_ratio'].quantile(0.25):.3f} "
      f"P50={df['body_ratio'].quantile(0.5):.3f} P75={df['body_ratio'].quantile(0.75):.3f}")
print(f"Pre-5bar decisive: mean={df['pre_decisive_5'].mean():.3f}")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", body_thresh=0.5, pre_thresh=0.4, min_hold=6):
    """
    mode:
      base           = BB Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      body_filter    = base + breakout candle body_ratio > threshold
      body_dir       = base + breakout candle direction matches signal
      pre_indecision = base + pre-5bar body_ratio < threshold (true compression)
      body_combo     = base + body>thresh + matching direction
      wick_filter    = base + low adverse wick (long: low upper_wick; short: low lower_wick)
      decisive_only  = body_ratio rising + direction + trend (no squeeze)
      full_candle    = base + body>thresh + pre<thresh + direction match
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

        # Entry signals
        l_sig=False;s_sig=False
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<20
        br=row["body_ratio"]
        pre=row["pre_decisive_5"]
        bull=row["bullish_body"]
        bear=row["bearish_body"]
        uw=row["upper_wick"]
        lw=row["lower_wick"]

        if mode=="base":
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="body_filter":
            # Breakout candle must be decisive (high body ratio)
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and br>body_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and br>body_thresh

        elif mode=="body_dir":
            # Breakout candle direction must match signal
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and bull
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and bear

        elif mode=="pre_indecision":
            # Pre-breakout bars must show indecision (low body ratio = true compression)
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and pre<pre_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and pre<pre_thresh

        elif mode=="body_combo":
            # Decisive breakout + matching direction
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and br>body_thresh and bull
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and br>body_thresh and bear

        elif mode=="wick_filter":
            # Low adverse wick: long = small upper wick, short = small lower wick
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and uw<0.2
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and lw<0.2

        elif mode=="decisive_only":
            # Body ratio MA rising + direction match + trend (no squeeze needed)
            br_rising=row["body_ratio_ma5"]>row["body_ratio_ma10"]
            l_sig=br_rising and br>body_thresh and bull and row["trend_up"] and row["vol_ratio"]>1.0
            s_sig=br_rising and br>body_thresh and bear and not row["trend_up"] and row["vol_ratio"]>1.0

        elif mode=="full_candle":
            # Full candle analysis: pre-indecision + decisive breakout + direction match
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and br>body_thresh and pre<pre_thresh and bull
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and br>body_thresh and pre<pre_thresh and bear

        info={"body_ratio":br,"pre_decisive":pre,"upper_wick":uw,"lower_wick":lw,
              "bullish":bull,"trend_up":row["trend_up"],
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
    ("0. Base C4", "base", 0.5, 0.4, 6),
    # Body ratio filters
    ("1. Body>0.4 filter", "body_filter", 0.4, 0.4, 6),
    ("2. Body>0.5 filter", "body_filter", 0.5, 0.4, 6),
    ("3. Body>0.6 filter", "body_filter", 0.6, 0.4, 6),
    ("4. Body>0.7 filter", "body_filter", 0.7, 0.4, 6),
    # Direction match
    ("5. Direction match", "body_dir", 0.5, 0.4, 6),
    # Pre-indecision
    ("6. Pre<0.45 indecision", "pre_indecision", 0.5, 0.45, 6),
    ("7. Pre<0.35 indecision", "pre_indecision", 0.5, 0.35, 6),
    ("8. Pre<0.30 indecision", "pre_indecision", 0.5, 0.30, 6),
    # Combos
    ("9. Body>0.5+direction", "body_combo", 0.5, 0.4, 6),
    ("10. Body>0.6+direction", "body_combo", 0.6, 0.4, 6),
    # Wick filter
    ("11. Low adverse wick", "wick_filter", 0.5, 0.4, 6),
    # Decisive only (no squeeze)
    ("12. Decisive only", "decisive_only", 0.5, 0.4, 6),
    # Full candle analysis
    ("13. Full candle 0.5/0.4", "full_candle", 0.5, 0.40, 6),
    ("14. Full candle 0.5/0.35", "full_candle", 0.5, 0.35, 6),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,bt,pt,mh in configs:
    tdf=run(df,mode=mode,body_thresh=bt,pre_thresh=pt,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"bt":bt,"pt":pt,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. Body Ratio at Entry: Winners vs Losers
# ============================================================
print(f"\n{'='*100}")
print("2. Body Ratio at Entry: Winners vs Losers")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<25s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*70}")
    for col,label in [("body_ratio","Body Ratio"),("pre_decisive","Pre-5bar Decisive"),
                       ("upper_wick","Upper Wick"),("lower_wick","Lower Wick"),
                       ("vol_ratio","Vol Ratio")]:
        if col not in tdf_base.columns:continue
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

for r in all_r[:12]:
    to=run(oos,r["mode"],r["bt"],r["pt"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["bt"],r["pt"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.02f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
