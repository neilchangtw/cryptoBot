"""
A3. TTM Squeeze (Keltner Channel + Bollinger Bands)
John Carter's TTM Squeeze: BB inside KC = compression, first exit = fired
比 BB width percentile 更精確偵測「真正壓縮」

Squeeze ON:  BB_upper < KC_upper AND BB_lower > KC_lower
Squeeze OFF: 不滿足上述條件
Squeeze FIRED: 上一根 ON, 本根 OFF (壓縮釋放)
Momentum: close - midline (KC_mid + BB_mid) / 2 的方向

測試：
  1. TTM 替換 BB Squeeze<20
  2. TTM + momentum 方向
  3. TTM fired 作為進場信號
  4. TTM vs BB 正面對決
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
print("  A3. TTM Squeeze (Keltner + Bollinger)")
print("="*100)

print("\nFetching ETH 1h...", end=" ", flush=True)
df=fetch("ETHUSDT","1h",365)
print(f"{len(df)} bars")

# ============================================================
# Standard indicators
# ============================================================
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["atr10"]=tr.rolling(10).mean()  # KC uses ATR(10)
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["trend_up"]=df["close"]>df["ema50"]

# Bollinger Bands
df["bb_mid"]=df["close"].rolling(20).mean()
bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# ============================================================
# Novel: Keltner Channel + TTM Squeeze
# ============================================================
# Keltner Channel: EMA(20) +/- multiplier * ATR(10)
for kc_mult in [1.0, 1.5, 2.0]:
    df[f"kc_upper_{kc_mult}"]=df["ema20"]+kc_mult*df["atr10"]
    df[f"kc_lower_{kc_mult}"]=df["ema20"]-kc_mult*df["atr10"]

    # Squeeze ON: BB inside KC
    df[f"sqz_on_{kc_mult}"]=(df["bb_upper"]<df[f"kc_upper_{kc_mult}"]) & (df["bb_lower"]>df[f"kc_lower_{kc_mult}"])
    # Squeeze FIRED: was ON, now OFF
    df[f"sqz_fired_{kc_mult}"]=df[f"sqz_on_{kc_mult}"].shift(1).fillna(False) & ~df[f"sqz_on_{kc_mult}"]

# TTM Momentum oscillator: close - average of (donchian midline, BB midline)
hl_mid=(df["high"].rolling(20).max()+df["low"].rolling(20).min())/2
df["ttm_mom"]=df["close"]-(hl_mid+df["bb_mid"])/2
df["ttm_mom_rising"]=df["ttm_mom"]>df["ttm_mom"].shift(1)

# Consecutive squeeze bars counter
def count_consecutive(series):
    """Count consecutive True values ending at each position"""
    result=np.zeros(len(series))
    for i in range(len(series)):
        if series.iloc[i]:
            result[i]=result[i-1]+1 if i>0 else 1
    return result

df["sqz_bars_1.5"]=count_consecutive(df["sqz_on_1.5"])

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

# Stats
for m in [1.0, 1.5, 2.0]:
    on_pct=df[f"sqz_on_{m}"].mean()*100
    fired_pct=df[f"sqz_fired_{m}"].mean()*100
    print(f"  KC mult={m}: Squeeze ON {on_pct:.1f}% | Fired {fired_pct:.1f}% of bars")

print(f"  TTM Momentum: mean={df['ttm_mom'].mean():.2f} std={df['ttm_mom'].std():.2f}")
print(f"  BB Squeeze<20: {(df['bb_width_pctile']<20).mean()*100:.1f}% of bars")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", kc_mult=1.5, min_sqz_bars=1, min_hold=6):
    """
    mode:
      base          = BB Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      ttm_replace   = TTM fired replaces BB Squeeze<20 (same vol+trend)
      ttm_fired_mom = TTM fired + momentum direction (replaces squeeze+trend)
      ttm_on_break  = while squeeze ON, breakout BB (replaces BB pctile<20)
      ttm_consec    = TTM squeeze ON for N bars, then breakout
      ttm_only      = TTM fired + momentum only (no vol, no old trend)
      ttm_plus_bb   = TTM fired AND BB<20 (both must agree)
    """
    lpos=[];spos=[];trades=[]
    sqz_on_col=f"sqz_on_{kc_mult}"
    sqz_fired_col=f"sqz_fired_{kc_mult}"

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
        fired=row[sqz_fired_col] if sqz_fired_col in row.index else False
        sqz_on=row[sqz_on_col] if sqz_on_col in row.index else False
        was_sqz_on=data.iloc[i-1][sqz_on_col] if sqz_on_col in data.columns else False
        mom_up=row["ttm_mom"]>0 and row["ttm_mom_rising"]
        mom_dn=row["ttm_mom"]<0 and not row["ttm_mom_rising"]

        if mode=="base":
            l_sig=was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="ttm_replace":
            # TTM fired replaces BB Squeeze<20, keep vol + EMA50 trend
            l_sig=fired and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=fired and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="ttm_fired_mom":
            # TTM fired + momentum direction replaces both squeeze and trend filter
            l_sig=fired and close>row["bb_upper"] and row["vol_ratio"]>1.0 and mom_up
            s_sig=fired and close<row["bb_lower"] and row["vol_ratio"]>1.0 and mom_dn

        elif mode=="ttm_on_break":
            # While squeeze ON, breakout BB (like BB<20 but using KC geometry)
            l_sig=was_sqz_on and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=was_sqz_on and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="ttm_consec":
            # Squeeze ON for at least N bars, then breakout
            sqz_bars=row.get("sqz_bars_1.5", 0) if "sqz_bars_1.5" in row.index else 0
            enough_squeeze=was_sqz_on and sqz_bars>=min_sqz_bars
            l_sig=enough_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=enough_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        elif mode=="ttm_only":
            # TTM fired + momentum only — minimal filters
            l_sig=fired and mom_up and row["trend_up"]
            s_sig=fired and mom_dn and not row["trend_up"]

        elif mode=="ttm_plus_bb":
            # Both TTM fired AND BB<20 must agree
            l_sig=fired and was_squeeze_bb and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
            s_sig=fired and was_squeeze_bb and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        info={"sqz_on":bool(sqz_on),"fired":bool(fired),"ttm_mom":row["ttm_mom"],
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
    ("0. Base C4 (BB<20)", "base", 1.5, 1, 6),
    # TTM replace with different KC multipliers
    ("1. TTM fired KC=1.0", "ttm_replace", 1.0, 1, 6),
    ("2. TTM fired KC=1.5", "ttm_replace", 1.5, 1, 6),
    ("3. TTM fired KC=2.0", "ttm_replace", 2.0, 1, 6),
    # TTM fired + momentum
    ("4. TTM fired+mom KC=1.5", "ttm_fired_mom", 1.5, 1, 6),
    ("5. TTM fired+mom KC=2.0", "ttm_fired_mom", 2.0, 1, 6),
    # Squeeze ON breakout (like BB<20 but TTM geometry)
    ("6. TTM ON break KC=1.0", "ttm_on_break", 1.0, 1, 6),
    ("7. TTM ON break KC=1.5", "ttm_on_break", 1.5, 1, 6),
    ("8. TTM ON break KC=2.0", "ttm_on_break", 2.0, 1, 6),
    # Consecutive squeeze bars
    ("9. TTM ON 5bar+ break", "ttm_consec", 1.5, 5, 6),
    ("10. TTM ON 10bar+ break", "ttm_consec", 1.5, 10, 6),
    ("11. TTM ON 20bar+ break", "ttm_consec", 1.5, 20, 6),
    # TTM only (minimal)
    ("12. TTM fired+mom only", "ttm_only", 1.5, 1, 6),
    # Both TTM and BB must agree
    ("13. TTM fired AND BB<20", "ttm_plus_bb", 1.5, 1, 6),
    ("14. TTM fired AND BB<20 KC2", "ttm_plus_bb", 2.0, 1, 6),
]

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,kc,msb,mh in configs:
    tdf=run(df,mode=mode,kc_mult=kc,min_sqz_bars=msb,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"kc":kc,"msb":msb,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. TTM vs BB Squeeze Overlap Analysis
# ============================================================
print(f"\n{'='*100}")
print("2. TTM vs BB Squeeze Overlap")
print(f"{'='*100}")

bb_squeeze=df["bb_width_pctile"]<20
for m in [1.0, 1.5, 2.0]:
    ttm_on=df[f"sqz_on_{m}"]
    both=bb_squeeze & ttm_on
    only_bb=bb_squeeze & ~ttm_on
    only_ttm=ttm_on & ~bb_squeeze
    print(f"\n  KC mult={m}:")
    print(f"    BB<20 only:  {only_bb.sum():>5d} bars ({only_bb.mean()*100:.1f}%)")
    print(f"    TTM ON only: {only_ttm.sum():>5d} bars ({only_ttm.mean()*100:.1f}%)")
    print(f"    Both:        {both.sum():>5d} bars ({both.mean()*100:.1f}%)")
    print(f"    Jaccard:     {both.sum()/(bb_squeeze|ttm_on).sum()*100:.1f}%")

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
    to=run(oos,r["mode"],r["kc"],r["msb"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["kc"],r["msb"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
