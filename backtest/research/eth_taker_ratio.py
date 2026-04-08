"""
A1. ETH Taker Buy Ratio - Order Flow Proxy
Binance kline 有 taker_buy_base_volume 但從未被使用。
taker_ratio = tbv / volume: >0.55 買方掃貨, <0.45 賣方主導

測試：
  S1. 獨立進場（taker ratio 趨勢）
  S2. Squeeze<20 + taker ratio 替換 vol>1.0
  S3. Squeeze<20 + taker ratio 作為額外品質過濾
  S4. taker ratio + EMA50 趨勢獨立進場
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
# Data + Indicators
# ============================================================
print("="*100)
print("  A1. ETH Taker Buy Ratio - Order Flow Proxy")
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

# Novel: Taker Buy Ratio
df["taker_ratio"]=df["tbv"]/df["volume"]
df["taker_ratio_ma5"]=df["taker_ratio"].rolling(5).mean()
df["taker_ratio_ma20"]=df["taker_ratio"].rolling(20).mean()
df["taker_ratio_pctile"]=df["taker_ratio"].rolling(100).apply(
    lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nTaker Ratio stats: mean={df['taker_ratio'].mean():.3f} std={df['taker_ratio'].std():.3f} "
      f"min={df['taker_ratio'].min():.3f} max={df['taker_ratio'].max():.3f}")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", taker_thresh=0.55, min_hold=6):
    """
    mode:
      base        = Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      taker_vol   = Squeeze<20 + taker ratio replaces vol>1.0 + trend + min6h
      taker_extra = Squeeze<20 + vol>1.0 + taker ratio extra filter + trend + min6h
      taker_only  = taker ratio crossing + EMA50 trend (no squeeze)
      taker_mom   = taker ratio momentum (3 consecutive bars) + EMA50
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
        tr_ratio=row["taker_ratio"]
        tr_ma5=row["taker_ratio_ma5"]
        tr_ma20=row["taker_ratio_ma20"]

        if mode=="base":
            # C4 baseline: Squeeze + vol>1.0 + trend + min6h
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="taker_vol":
            # Replace vol>1.0 with taker ratio threshold
            l_sig=was_squeeze and close>row["bb_upper"] and tr_ratio>taker_thresh and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and tr_ratio<(1-taker_thresh) and not row["trend_up"]

        elif mode=="taker_extra":
            # Keep vol>1.0, ADD taker ratio as extra filter
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and tr_ratio>taker_thresh and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and tr_ratio<(1-taker_thresh) and not row["trend_up"]

        elif mode=="taker_only":
            # No squeeze, just taker ratio crossing + trend
            l_sig=tr_ma5>tr_ma20 and tr_ratio>taker_thresh and row["trend_up"]
            s_sig=tr_ma5<tr_ma20 and tr_ratio<(1-taker_thresh) and not row["trend_up"]

        elif mode=="taker_mom":
            # 3 consecutive bars of taker dominance + trend
            if i>=3:
                l_3=all(data.iloc[i-j]["taker_ratio"]>taker_thresh for j in range(3))
                s_3=all(data.iloc[i-j]["taker_ratio"]<(1-taker_thresh) for j in range(3))
                l_sig=l_3 and row["trend_up"]
                s_sig=s_3 and not row["trend_up"]

        info={"entry_hour":row["datetime"].hour,"taker_ratio":tr_ratio,
              "taker_ma5":tr_ma5,"vol_ratio":row["vol_ratio"],
              "trend_up":row["trend_up"],"dt":row["datetime"],"entry_price":ep,
              "entry_atr_pctile":row.get("atr_pctile",50) if "atr_pctile" in row.index else 50}

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
    ("0. Base C4 (Squeeze+vol+trend+6h)", "base", 0.55, 6),
    ("1. Taker>0.50 替換 vol>1.0", "taker_vol", 0.50, 6),
    ("2. Taker>0.52 替換 vol>1.0", "taker_vol", 0.52, 6),
    ("3. Taker>0.55 替換 vol>1.0", "taker_vol", 0.55, 6),
    ("4. Taker>0.58 替換 vol>1.0", "taker_vol", 0.58, 6),
    ("5. Taker>0.55 額外過濾", "taker_extra", 0.55, 6),
    ("6. Taker>0.52 額外過濾", "taker_extra", 0.52, 6),
    ("7. Taker Only + trend", "taker_only", 0.55, 6),
    ("8. Taker Only 寬鬆 0.52", "taker_only", 0.52, 6),
    ("9. Taker 3bar momentum", "taker_mom", 0.55, 6),
    ("10. Taker 3bar 寬鬆 0.52", "taker_mom", 0.52, 6),
    # min hold variations
    ("11. Taker>0.55 替換 +2h hold", "taker_vol", 0.55, 2),
    ("12. Taker>0.55 替換 +12h hold", "taker_vol", 0.55, 12),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,thresh,mh in configs:
    tdf=run(df,mode=mode,taker_thresh=thresh,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"thresh":thresh,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. Win vs Loss: Taker Ratio 分析
# ============================================================
print(f"\n{'='*100}")
print("2. Taker Ratio at Entry: Winners vs Losers")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0 and "taker_ratio" in tdf_base.columns:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<25s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*70}")
    for col,label in [("taker_ratio","Taker Ratio"),("taker_ma5","Taker MA5"),("vol_ratio","Vol Ratio")]:
        if col in tdf_base.columns:
            wv=wins[col].mean() if len(wins)>0 else 0
            lv=losses[col].mean() if len(losses)>0 else 0
            sv=sn[col].mean() if len(sn)>0 else 0
            print(f"  {label:<25s} {wv:>10.3f} {lv:>10.3f} {sv:>10.3f} {wv-lv:>+10.3f}")

# ============================================================
# 3. Walk-Forward + Rolling
# ============================================================
print(f"\n{'='*100}")
print("3. Walk-Forward + Rolling (top configs)")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);dev=df.iloc[:split].reset_index(drop=True);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<35s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*80}")

for r in all_r[:10]:
    to=run(oos,r["mode"],r["thresh"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["thresh"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# 4. Taker Ratio Distribution Analysis
# ============================================================
print(f"\n{'='*100}")
print("4. Taker Ratio Distribution")
print(f"{'='*100}")

for q in [10,25,50,75,90]:
    v=df["taker_ratio"].quantile(q/100)
    print(f"  P{q}: {v:.3f}")

# By hour
print(f"\n  Hour avg taker_ratio:")
hourly=df.groupby(df["datetime"].dt.hour)["taker_ratio"].mean()
for h,v in hourly.items():
    flag=" <<<" if v<0.48 else (" !!!" if v>0.52 else "")
    print(f"    {h:02d}h: {v:.3f}{flag}")

print("\nDone.")
