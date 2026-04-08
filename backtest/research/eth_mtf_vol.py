"""
A5. Multi-Timeframe Volatility Ratio
1h ATR / 4h ATR: 低比值 = 1h 相對 4h 被壓縮（情境式壓縮）
跟 ATR percentile 的差異：pctile 是「1h 跟自己歷史比」，MTF ratio 是「1h 跟 4h 比」

測試：
  1. MTF ratio 低值作為 Squeeze 替代/額外過濾
  2. MTF ratio 獨立進場（低→高 = 波動率擴張）
  3. MTF ratio vs BB width percentile 正面對決
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
print("  A5. Multi-Timeframe Volatility Ratio (1h/4h)")
print("="*100)

print("\nFetching ETH 1h...", end=" ", flush=True)
df_1h=fetch("ETHUSDT","1h",365)
print(f"{len(df_1h)} bars")

print("Fetching ETH 4h...", end=" ", flush=True)
df_4h=fetch("ETHUSDT","4h",365)
print(f"{len(df_4h)} bars")

# ============================================================
# 4h ATR calculation
# ============================================================
tr_4h=pd.DataFrame({
    "hl":df_4h["high"]-df_4h["low"],
    "hc":abs(df_4h["high"]-df_4h["close"].shift(1)),
    "lc":abs(df_4h["low"]-df_4h["close"].shift(1))
}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
df_4h["atr_4h_ma50"]=df_4h["atr_4h"].rolling(50).mean()

# Map 4h ATR to 1h bars using open time alignment
# Each 4h bar covers 4 consecutive 1h bars. Map by finding the most recent 4h bar.
df_4h["ot_4h"]=df_4h["ot"]
atr_4h_series=df_4h[["ot_4h","atr_4h","atr_4h_ma50"]].dropna().set_index("ot_4h")

df=df_1h.copy()

# For each 1h bar, find the latest 4h bar with ot <= 1h ot
def map_4h_to_1h(df_1h, df_4h_vals):
    """Map 4h values to 1h bars using merge_asof"""
    df_1h_temp=df_1h[["ot"]].copy()
    df_4h_temp=df_4h_vals.reset_index()
    df_4h_temp=df_4h_temp.rename(columns={"ot_4h":"ot"})
    merged=pd.merge_asof(df_1h_temp.sort_values("ot"), df_4h_temp.sort_values("ot"), on="ot", direction="backward")
    return merged

mapped=map_4h_to_1h(df, atr_4h_series)
df["atr_4h"]=mapped["atr_4h"].values
df["atr_4h_ma50"]=mapped["atr_4h_ma50"].values

# ============================================================
# 1h indicators
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
# Novel: MTF Volatility Ratio
# ============================================================
# Ratio: 1h ATR / 4h ATR (lower = 1h compressed relative to 4h)
df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(
    lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# MTF ratio change: expanding (ratio rising) = 1h vol expanding
df["mtf_ratio_ma10"]=df["mtf_ratio"].rolling(10).mean()
df["mtf_expanding"]=df["mtf_ratio"]>df["mtf_ratio_ma10"]

# 4h trend: is 4h ATR above its own MA? (broad market vol regime)
df["atr_4h_rising"]=df["atr_4h"]>df["atr_4h_ma50"]

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nMTF Ratio stats: mean={df['mtf_ratio'].mean():.4f} std={df['mtf_ratio'].std():.4f}")
print(f"  P10={df['mtf_ratio'].quantile(0.1):.4f} P25={df['mtf_ratio'].quantile(0.25):.4f} "
      f"P50={df['mtf_ratio'].quantile(0.5):.4f} P75={df['mtf_ratio'].quantile(0.75):.4f} "
      f"P90={df['mtf_ratio'].quantile(0.9):.4f}")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", mtf_pctile_max=30, min_hold=6):
    """
    mode:
      base          = BB Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      mtf_filter    = Squeeze + vol + trend + MTF ratio pctile < threshold
      mtf_replace   = MTF ratio pctile < threshold replaces BB Squeeze<20
      mtf_expand    = MTF expanding (ratio rising) as extra breakout confirmation
      mtf_only      = MTF low + expanding → breakout (no BB squeeze)
      mtf_plus_bb   = BB Squeeze<20 AND MTF ratio < threshold (both agree)
      mtf_4h_calm   = Squeeze + vol + trend + 4h ATR not rising (broad calm)
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
        was_squeeze_bb=data.iloc[i-1]["bb_width_pctile"]<20
        mtf_low=row["mtf_ratio_pctile"]<mtf_pctile_max
        was_mtf_low=data.iloc[i-1]["mtf_ratio_pctile"]<mtf_pctile_max if i>0 else False

        if mode=="base":
            l_sig=was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="mtf_filter":
            # Add MTF low as extra filter on top of base
            l_sig=was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and mtf_low
            s_sig=was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and mtf_low

        elif mode=="mtf_replace":
            # MTF ratio low replaces BB Squeeze<20
            l_sig=was_mtf_low and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_mtf_low and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="mtf_expand":
            # Base + MTF must be expanding (breakout confirmation)
            l_sig=was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and row["mtf_expanding"]
            s_sig=was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and row["mtf_expanding"]

        elif mode=="mtf_only":
            # MTF low percentile + expanding + trend (no BB squeeze)
            l_sig=was_mtf_low and row["mtf_expanding"] and close>row["bb_upper"] and row["trend_up"]
            s_sig=was_mtf_low and row["mtf_expanding"] and close<row["bb_lower"] and not row["trend_up"]

        elif mode=="mtf_plus_bb":
            # Both BB Squeeze AND MTF low must agree
            l_sig=was_squeeze_bb and mtf_low and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze_bb and mtf_low and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="mtf_4h_calm":
            # Squeeze + vol + trend + 4h volatility not elevated
            l_sig=was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and not row["atr_4h_rising"]
            s_sig=was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and not row["atr_4h_rising"]

        info={"mtf_ratio":row["mtf_ratio"],"mtf_pctile":row["mtf_ratio_pctile"],
              "mtf_expanding":row["mtf_expanding"],"atr_4h_rising":row["atr_4h_rising"],
              "bb_pctile":row["bb_width_pctile"],"trend_up":row["trend_up"],
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
    ("0. Base C4 (BB<20)", "base", 30, 6),
    # MTF as extra filter
    ("1. +MTF pctile<30", "mtf_filter", 30, 6),
    ("2. +MTF pctile<20", "mtf_filter", 20, 6),
    ("3. +MTF pctile<40", "mtf_filter", 40, 6),
    # MTF replaces BB squeeze
    ("4. MTF<30 替換 BB<20", "mtf_replace", 30, 6),
    ("5. MTF<20 替換 BB<20", "mtf_replace", 20, 6),
    ("6. MTF<40 替換 BB<20", "mtf_replace", 40, 6),
    # MTF expanding as confirmation
    ("7. +MTF expanding", "mtf_expand", 30, 6),
    # MTF only (no BB squeeze)
    ("8. MTF<30 only + expand", "mtf_only", 30, 6),
    ("9. MTF<20 only + expand", "mtf_only", 20, 6),
    # Both must agree
    ("10. BB<20 AND MTF<30", "mtf_plus_bb", 30, 6),
    ("11. BB<20 AND MTF<20", "mtf_plus_bb", 20, 6),
    # 4h calm regime
    ("12. +4h ATR not rising", "mtf_4h_calm", 30, 6),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,mp,mh in configs:
    tdf=run(df,mode=mode,mtf_pctile_max=mp,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"mp":mp,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. MTF Ratio vs BB Width Correlation
# ============================================================
print(f"\n{'='*100}")
print("2. MTF Ratio vs BB Width Correlation")
print(f"{'='*100}")

corr=df["mtf_ratio_pctile"].corr(df["bb_width_pctile"])
print(f"\n  MTF pctile vs BB pctile correlation: {corr:.3f}")

# When they disagree
bb_low=df["bb_width_pctile"]<20
mtf_low=df["mtf_ratio_pctile"]<30
both=bb_low & mtf_low
only_bb=bb_low & ~mtf_low
only_mtf=mtf_low & ~bb_low
print(f"\n  BB<20 only:  {only_bb.sum():>5d} bars ({only_bb.mean()*100:.1f}%)")
print(f"  MTF<30 only: {only_mtf.sum():>5d} bars ({only_mtf.mean()*100:.1f}%)")
print(f"  Both:        {both.sum():>5d} bars ({both.mean()*100:.1f}%)")

# ============================================================
# 3. MTF at Entry: Winners vs Losers
# ============================================================
print(f"\n{'='*100}")
print("3. MTF at Entry: Winners vs Losers")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0 and "mtf_ratio" in tdf_base.columns:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<25s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*70}")
    for col,label in [("mtf_ratio","MTF Ratio"),("mtf_pctile","MTF Pctile"),
                       ("bb_pctile","BB Pctile"),("vol_ratio","Vol Ratio")]:
        if col not in tdf_base.columns:continue
        wv=wins[col].mean() if len(wins)>0 else 0
        lv=losses[col].mean() if len(losses)>0 else 0
        sv=sn[col].mean() if len(sn)>0 else 0
        print(f"  {label:<25s} {wv:>10.3f} {lv:>10.3f} {sv:>10.3f} {wv-lv:>+10.3f}")

# ============================================================
# 4. Walk-Forward + Rolling
# ============================================================
print(f"\n{'='*100}")
print("4. Walk-Forward + Rolling")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<35s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*80}")

for r in all_r[:10]:
    to=run(oos,r["mode"],r["mp"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["mp"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
