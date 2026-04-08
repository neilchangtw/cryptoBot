"""
Phase B: Best Factor Combinations
Phase A 晉級者組合測試

晉級指標（按排名）：
  A6. Session Timing — Block worst hours (0,1,2,12) + Mon/Sun
  A2. ETH/BTC Ratio — Ratio Z>1.0 or Mom>0.5% 方向過濾
  A5. MTF Vol Ratio — MTF<40 替換 BB<20
  A8. VP Divergence — OBV filter + VP exit
  A1. Taker Ratio — Taker>0.52 替換 vol>1.0

測試：
  B1. 2 因子組合（所有 top 配對）
  B2. 3 因子組合（Session + 最佳 2 個）
  B3. 參數敏感度分析
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
print("  Phase B: Best Factor Combinations")
print("="*100)

# Fetch all needed data
print("\nFetching ETH 1h...", end=" ", flush=True)
df=fetch("ETHUSDT","1h",365)
print(f"{len(df)} bars")

print("Fetching BTC 1h...", end=" ", flush=True)
btc=fetch("BTCUSDT","1h",365)
print(f"{len(btc)} bars")

print("Fetching ETH 4h...", end=" ", flush=True)
df_4h=fetch("ETHUSDT","4h",365)
print(f"{len(df_4h)} bars")

# ============================================================
# All indicators
# ============================================================
print("Computing indicators...", flush=True)

# BTC alignment
btc_map=btc.set_index("ot")["close"].to_dict()
df["btc_close"]=df["ot"].map(btc_map)

# 4h ATR
tr_4h=pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],"hc":abs(df_4h["high"]-df_4h["close"].shift(1)),"lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
df_4h_map=df_4h[["ot","atr_4h"]].dropna().rename(columns={"ot":"ot_4h"}).set_index("ot_4h")
mapped=pd.merge_asof(df[["ot"]].sort_values("ot"), df_4h_map.reset_index().rename(columns={"ot_4h":"ot"}).sort_values("ot"), on="ot", direction="backward")
df["atr_4h"]=mapped["atr_4h"].values

# Standard 1h indicators
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

# A1: Taker Ratio
df["taker_ratio"]=df["tbv"]/df["volume"]

# A2: ETH/BTC Ratio
df["ratio"]=df["close"]/df["btc_close"]
df["ratio_ema20"]=df["ratio"].ewm(span=20).mean()
df["ratio_ema50"]=df["ratio"].ewm(span=50).mean()
df["ratio_trend_up"]=df["ratio_ema20"]>df["ratio_ema50"]
ratio_mean=df["ratio"].rolling(50).mean()
ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std
df["ratio_mom10"]=df["ratio"].pct_change(10)*100

# A5: MTF Vol Ratio
df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# A8: OBV + VP
df["abs_pchg"]=abs(df["close"].pct_change())
df["vp_corr_20"]=df["volume"].rolling(20).corr(df["abs_pchg"])
df["obv"]=(np.sign(df["close"].diff())*df["volume"]).cumsum()
df["obv_ema20"]=df["obv"].ewm(span=20).mean()
df["obv_trend_up"]=df["obv"]>df["obv_ema20"]

# A6: Session / Hour
df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday  # 0=Mon, 6=Sun

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars with all indicators")

# ============================================================
# Parameterized backtest engine
# ============================================================
WORST_HOURS={0,1,2,12}
WORST_DAYS={0,6}  # Mon, Sun

def run(data, cfg):
    """
    cfg dict keys:
      squeeze: "bb20" | "mtf40" | "mtf30" | "none"
      vol_filter: "vol1.0" | "taker0.52" | "taker0.55" | "none"
      trend: "ema50" | "ratio_z1.0" | "ratio_z0.5" | "ratio_mom0.5" | "ratio_trend" | "both"
      session: "none" | "block_worst4" | "block_worst4_days"
      obv: True/False — add OBV trend filter
      vp_exit: True/False — exit on VP corr < -0.3
      min_hold: int
    """
    squeeze=cfg.get("squeeze","bb20")
    vol_f=cfg.get("vol_filter","vol1.0")
    trend=cfg.get("trend","ema50")
    session=cfg.get("session","none")
    use_obv=cfg.get("obv",False)
    vp_exit=cfg.get("vp_exit",False)
    min_hold=cfg.get("min_hold",6)

    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]
        vp_c=row["vp_corr_20"]

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

        # Session filter
        h=row["hour"];wd=row["weekday"]
        if session=="block_worst4" and h in WORST_HOURS:continue
        if session=="block_worst4_days" and (h in WORST_HOURS or wd in WORST_DAYS):continue

        # Squeeze condition
        was_squeeze=False
        if squeeze=="bb20":
            was_squeeze=data.iloc[i-1]["bb_width_pctile"]<20
        elif squeeze=="mtf40":
            was_squeeze=data.iloc[i-1]["mtf_ratio_pctile"]<40
        elif squeeze=="mtf30":
            was_squeeze=data.iloc[i-1]["mtf_ratio_pctile"]<30

        if not was_squeeze:continue

        # BB breakout
        bb_long=close>row["bb_upper"]
        bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue

        # Volume filter
        vol_ok=True
        if vol_f=="vol1.0":
            vol_ok=row["vol_ratio"]>1.0
        elif vol_f=="taker0.52":
            if bb_long: vol_ok=row["taker_ratio"]>0.52
            else: vol_ok=row["taker_ratio"]<0.48
        elif vol_f=="taker0.55":
            if bb_long: vol_ok=row["taker_ratio"]>0.55
            else: vol_ok=row["taker_ratio"]<0.45
        if not vol_ok:continue

        # Trend filter
        trend_long=True;trend_short=True
        if trend=="ema50":
            trend_long=row["trend_up"];trend_short=not row["trend_up"]
        elif trend=="ratio_z1.0":
            trend_long=row["ratio_zscore"]>1.0;trend_short=row["ratio_zscore"]<-1.0
        elif trend=="ratio_z0.5":
            trend_long=row["ratio_zscore"]>0.5;trend_short=row["ratio_zscore"]<-0.5
        elif trend=="ratio_mom0.5":
            trend_long=row["ratio_mom10"]>0.5;trend_short=row["ratio_mom10"]<-0.5
        elif trend=="ratio_trend":
            trend_long=row["ratio_trend_up"];trend_short=not row["ratio_trend_up"]
        elif trend=="both":
            trend_long=row["trend_up"] and row["ratio_trend_up"]
            trend_short=not row["trend_up"] and not row["ratio_trend_up"]

        # OBV filter
        if use_obv:
            if bb_long and not row["obv_trend_up"]:continue
            if bb_short and row["obv_trend_up"]:continue

        l_sig=bb_long and trend_long
        s_sig=bb_short and trend_short

        info={"dt":row["datetime"],"entry_price":ep,"entry_hour":h,
              "ratio_zscore":row["ratio_zscore"],"taker_ratio":row["taker_ratio"],
              "vol_ratio":row["vol_ratio"],"trend_up":row["trend_up"]}

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
    sn=tdf[tdf["type"]=="SafeNet"]
    non_sn=tdf[tdf["type"]!="SafeNet"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),"trail_pnl":round(non_sn["pnl"].sum(),2)}

# ============================================================
# B0. Single factor baselines (for comparison)
# ============================================================
print(f"\n{'='*100}")
print("B0. Single Factor Baselines")
print(f"{'='*100}")

baselines=[
    ("Base C4",                     {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ema50","session":"none","obv":False,"vp_exit":False}),
    ("A6: +Session block",          {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ema50","session":"block_worst4_days","obv":False,"vp_exit":False}),
    ("A2: Ratio Z>1.0",            {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"none","obv":False,"vp_exit":False}),
    ("A5: MTF<40",                  {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ema50","session":"none","obv":False,"vp_exit":False}),
    ("A8: OBV+VP exit",            {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ema50","session":"none","obv":True,"vp_exit":True}),
    ("A1: Taker>0.52",             {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ema50","session":"none","obv":False,"vp_exit":False}),
]

print(f"\n  {'Config':<40s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*96}")

for name,cfg in baselines:
    tdf=run(df,cfg);s=calc(tdf)
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<40s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# B1. 2-Factor Combinations
# ============================================================
print(f"\n{'='*100}")
print("B1. Two-Factor Combinations")
print(f"{'='*100}")

combos_2=[
    # Session + each other factor
    ("Session + Ratio Z>1.0",       {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"block_worst4_days"}),
    ("Session + Ratio Z>0.5",       {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_z0.5","session":"block_worst4_days"}),
    ("Session + Ratio Mom>0.5",     {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_mom0.5","session":"block_worst4_days"}),
    ("Session + Ratio trend",       {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_trend","session":"block_worst4_days"}),
    ("Session + Both trend",        {"squeeze":"bb20","vol_filter":"vol1.0","trend":"both","session":"block_worst4_days"}),
    ("Session + MTF<40",            {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ema50","session":"block_worst4_days"}),
    ("Session + MTF<30",            {"squeeze":"mtf30","vol_filter":"vol1.0","trend":"ema50","session":"block_worst4_days"}),
    ("Session + Taker>0.52",        {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ema50","session":"block_worst4_days"}),
    ("Session + OBV+VP",            {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ema50","session":"block_worst4_days","obv":True,"vp_exit":True}),
    # Ratio + other (no session)
    ("Ratio Z>1.0 + Taker>0.52",   {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_z1.0","session":"none"}),
    ("Ratio Z>1.0 + MTF<40",       {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"none"}),
    ("Ratio Z>1.0 + OBV+VP",       {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"none","obv":True,"vp_exit":True}),
    # MTF + other
    ("MTF<40 + Taker>0.52",        {"squeeze":"mtf40","vol_filter":"taker0.52","trend":"ema50","session":"none"}),
    ("MTF<40 + OBV+VP",            {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ema50","session":"none","obv":True,"vp_exit":True}),
    # Taker + OBV
    ("Taker>0.52 + OBV+VP",        {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ema50","session":"none","obv":True,"vp_exit":True}),
]

# Set defaults
for name,cfg in combos_2:
    cfg.setdefault("obv",False)
    cfg.setdefault("vp_exit",False)
    cfg.setdefault("min_hold",6)

print(f"\n  {'Config':<40s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*96}")

all_r=[]
for name,cfg in combos_2:
    tdf=run(df,cfg);s=calc(tdf)
    all_r.append({"name":name,"cfg":cfg,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2500 else "")
    print(f"  {name:<40s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# B2. 3-Factor Combinations (Session + best 2)
# ============================================================
print(f"\n{'='*100}")
print("B2. Three-Factor Combinations")
print(f"{'='*100}")

combos_3=[
    # Session + Ratio + Taker
    ("Sess + RatioZ1 + Taker0.52",  {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_z1.0","session":"block_worst4_days"}),
    ("Sess + RatioZ0.5 + Taker0.52",{"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_z0.5","session":"block_worst4_days"}),
    ("Sess + RatioMom + Taker0.52", {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_mom0.5","session":"block_worst4_days"}),
    ("Sess + RatioTrend + Taker",   {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_trend","session":"block_worst4_days"}),
    # Session + Ratio + MTF
    ("Sess + RatioZ1 + MTF40",      {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"block_worst4_days"}),
    ("Sess + RatioZ0.5 + MTF40",    {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ratio_z0.5","session":"block_worst4_days"}),
    ("Sess + RatioMom + MTF40",     {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ratio_mom0.5","session":"block_worst4_days"}),
    # Session + Ratio + OBV/VP
    ("Sess + RatioZ1 + OBV+VP",     {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_z1.0","session":"block_worst4_days","obv":True,"vp_exit":True}),
    ("Sess + RatioMom + OBV+VP",    {"squeeze":"bb20","vol_filter":"vol1.0","trend":"ratio_mom0.5","session":"block_worst4_days","obv":True,"vp_exit":True}),
    # Session + MTF + Taker
    ("Sess + MTF40 + Taker0.52",    {"squeeze":"mtf40","vol_filter":"taker0.52","trend":"ema50","session":"block_worst4_days"}),
    # Session + MTF + OBV
    ("Sess + MTF40 + OBV+VP",       {"squeeze":"mtf40","vol_filter":"vol1.0","trend":"ema50","session":"block_worst4_days","obv":True,"vp_exit":True}),
    # Session + Taker + OBV
    ("Sess + Taker0.52 + OBV+VP",   {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ema50","session":"block_worst4_days","obv":True,"vp_exit":True}),
    # Session block4 only (not days)
    ("Sess4h + RatioZ1 + Taker0.52",{"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_z1.0","session":"block_worst4"}),
    ("Sess4h + RatioMom + Taker",   {"squeeze":"bb20","vol_filter":"taker0.52","trend":"ratio_mom0.5","session":"block_worst4"}),
]

for name,cfg in combos_3:
    cfg.setdefault("obv",False)
    cfg.setdefault("vp_exit",False)
    cfg.setdefault("min_hold",6)

print(f"\n  {'Config':<40s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*96}")

for name,cfg in combos_3:
    tdf=run(df,cfg);s=calc(tdf)
    all_r.append({"name":name,"cfg":cfg,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2500 else "")
    print(f"  {name:<40s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# B3. Walk-Forward + Rolling (top configs)
# ============================================================
print(f"\n{'='*100}")
print("B3. Walk-Forward + Rolling (Top 20)")
print(f"{'='*100}")

# Add baselines to all_r for comparison
for name,cfg in baselines:
    tdf=run(df,cfg);s=calc(tdf)
    all_r.append({"name":"[BASE] "+name,"cfg":cfg,**s})

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<42s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*84}")

wf_results=[]
for r in all_r[:20]:
    to=run(oos,r["cfg"]);so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["cfg"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    roll_pnl=sum(fp)
    wf_results.append({**r,"oos_pnl":so["pnl"],"oos_pf":so["pf"],"oos_sn":so["sn"],"roll_pnl":roll_pnl,"folds":f"{prof}/{len(fp)}"})
    print(f"  {r['name']:<42s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${roll_pnl:>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# B4. Parameter Sensitivity (top 3 configs)
# ============================================================
print(f"\n{'='*100}")
print("B4. Parameter Sensitivity Analysis")
print(f"{'='*100}")

# Take top 3 by OOS PnL from wf_results
wf_results.sort(key=lambda x:x["oos_pnl"],reverse=True)
top3=[r for r in wf_results if not r["name"].startswith("[BASE]")][:3]

for rank,r in enumerate(top3):
    print(f"\n  --- #{rank+1}: {r['name']} (Full ${r['pnl']:+,.0f}, OOS ${r['oos_pnl']:+,.0f}) ---")
    base_cfg=r["cfg"].copy()

    # Test session variants
    print(f"  Session sensitivity:")
    for sv in ["none","block_worst4","block_worst4_days"]:
        cfg=base_cfg.copy();cfg["session"]=sv
        tdf=run(df,cfg);s=calc(tdf)
        to=run(oos,cfg);so=calc(to)
        print(f"    {sv:<25s} Full ${s['pnl']:>+6,.0f} (N={s['n']:>3d}) | OOS ${so['pnl']:>+6,.0f} PF {so['pf']:.2f}")

    # Test min_hold variants
    print(f"  Min hold sensitivity:")
    for mh in [2, 4, 6, 8, 10, 12]:
        cfg=base_cfg.copy();cfg["min_hold"]=mh
        tdf=run(df,cfg);s=calc(tdf)
        to=run(oos,cfg);so=calc(to)
        print(f"    min_hold={mh:<3d}             Full ${s['pnl']:>+6,.0f} (N={s['n']:>3d}) | OOS ${so['pnl']:>+6,.0f} PF {so['pf']:.2f}")

    # Test squeeze threshold variants
    sq_type=base_cfg.get("squeeze","bb20")
    if sq_type.startswith("mtf"):
        print(f"  MTF threshold sensitivity:")
        for mt in [20,25,30,35,40,50]:
            cfg=base_cfg.copy();cfg["squeeze"]=f"mtf{mt}" if mt!=20 else "mtf30"
            # workaround: need to handle arbitrary thresholds
            # just test mtf30 and mtf40
        for sq in ["bb20","mtf30","mtf40"]:
            cfg=base_cfg.copy();cfg["squeeze"]=sq
            tdf=run(df,cfg);s=calc(tdf)
            to=run(oos,cfg);so=calc(to)
            print(f"    squeeze={sq:<10s}        Full ${s['pnl']:>+6,.0f} (N={s['n']:>3d}) | OOS ${so['pnl']:>+6,.0f} PF {so['pf']:.2f}")
    elif sq_type=="bb20":
        print(f"  Squeeze variant sensitivity:")
        for sq in ["bb20","mtf30","mtf40"]:
            cfg=base_cfg.copy();cfg["squeeze"]=sq
            tdf=run(df,cfg);s=calc(tdf)
            to=run(oos,cfg);so=calc(to)
            print(f"    squeeze={sq:<10s}        Full ${s['pnl']:>+6,.0f} (N={s['n']:>3d}) | OOS ${so['pnl']:>+6,.0f} PF {so['pf']:.2f}")

    # Test trend variants
    print(f"  Trend filter sensitivity:")
    for tv in ["ema50","ratio_z0.5","ratio_z1.0","ratio_mom0.5","ratio_trend","both"]:
        cfg=base_cfg.copy();cfg["trend"]=tv
        tdf=run(df,cfg);s=calc(tdf)
        to=run(oos,cfg);so=calc(to)
        print(f"    trend={tv:<18s}  Full ${s['pnl']:>+6,.0f} (N={s['n']:>3d}) | OOS ${so['pnl']:>+6,.0f} PF {so['pf']:.2f}")

# ============================================================
# B5. Monthly breakdown (top 3)
# ============================================================
print(f"\n{'='*100}")
print("B5. Monthly Breakdown (Top 3 vs Base)")
print(f"{'='*100}")

base_cfg={"squeeze":"bb20","vol_filter":"vol1.0","trend":"ema50","session":"none","obv":False,"vp_exit":False,"min_hold":6}

configs_monthly=[("Base C4", base_cfg)]
for r in top3:
    configs_monthly.append((r["name"], r["cfg"]))

# Get unique months
all_months=sorted(df["month"].unique())

print(f"\n  {'Month':<10s}", end="")
for name,_ in configs_monthly:
    short=name[:18]
    print(f" {short:>18s}", end="")
print()
print(f"  {'-'*(10+19*len(configs_monthly))}")

for m in all_months:
    mdf=df[df["month"]==m].reset_index(drop=True)
    if len(mdf)<20:continue
    print(f"  {str(m):<10s}", end="")
    for name,cfg in configs_monthly:
        tdf=run(mdf,cfg);s=calc(tdf)
        print(f" ${s['pnl']:>+7,.0f}({s['n']:>3d})", end="")
    print()

# Totals
print(f"  {'TOTAL':<10s}", end="")
for name,cfg in configs_monthly:
    tdf=run(df,cfg);s=calc(tdf)
    print(f" ${s['pnl']:>+7,.0f}({s['n']:>3d})", end="")
print()

print("\nDone.")
