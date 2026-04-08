"""
ETH Trend Pullback Rider (TPR) — 全新策略設計

核心邏輯：在已確立的 4h 趨勢中，偵測 1h RSI 回拉後動量恢復，進場做趨勢延續。
與 v6 Champion 的根本差異：v6 在壓縮突破時進場，TPR 在趨勢回拉恢復時進場。

進場（Long）：
  1. 4h 上升趨勢：Close > EMA20 > EMA50
  2. 1h 回拉：RSI 最近 5 bar 內曾 < 45
  3. 1h 恢復：RSI 從下穿越 50
  4. 價格確認：Close > 1h EMA20
  5. Volume > 1.2x MA20
  6. ETH/BTC ratio > ratio EMA20
  7. Session: 非 UTC+8 0-3 時、非 Sunday

出場：
  SafeNet ±3.5% / TimeStop 48h / EMA20 trail (min 6h) / 無 TP1

Phase 1: 核心消融測試（5 配置）
Phase 2: 參數敏感度（8 維度）
Phase 3: 倉位大小（100/150/200U）
Phase 4: 完整驗證（WF + 滾動 + 月度 + 交易清單）
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ============================================================
# Constants
# ============================================================
MARGIN=100;LEVERAGE=20;MAX_SAME=2;FEE=1.6
WORST_HOURS={0,1,2,3}   # UTC+8
WORST_DAYS={6}           # Sunday

# ============================================================
# Data fetch
# ============================================================
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
print("  ETH Trend Pullback Rider (TPR) — Full Backtest")
print("="*100)

# Fetch data
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
# Indicators
# ============================================================
print("Computing indicators...", flush=True)

# BTC alignment for ETH/BTC ratio
btc_map=btc.set_index("ot")["close"].to_dict()
df["btc_close"]=df["ot"].map(btc_map)

# 4h EMAs (lag by 1 bar to prevent lookahead)
df_4h["ema20_4h"]=df_4h["close"].ewm(span=20).mean().shift(1)
df_4h["ema50_4h"]=df_4h["close"].ewm(span=50).mean().shift(1)
df_4h["close_4h"]=df_4h["close"].shift(1)
mapped=pd.merge_asof(
    df[["ot"]].sort_values("ot"),
    df_4h[["ot","ema20_4h","ema50_4h","close_4h"]].dropna().sort_values("ot"),
    on="ot", direction="backward"
)
df["ema20_4h"]=mapped["ema20_4h"].values
df["ema50_4h"]=mapped["ema50_4h"].values
df["close_4h"]=mapped["close_4h"].values

# 1h standard indicators
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema10"]=df["close"].ewm(span=10).mean()
df["ema15"]=df["close"].ewm(span=15).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema25"]=df["close"].ewm(span=25).mean()
df["ema50"]=df["close"].ewm(span=50).mean()

# RSI(14) - Wilder's
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)

# RSI rolling min/max for pullback detection (various lookback windows)
for lb in [3,5,7]:
    df[f"rsi_min{lb}"]=df["rsi"].rolling(lb).min()
    df[f"rsi_max{lb}"]=df["rsi"].rolling(lb).max()

# RSI cross 50 detection
df["rsi_cross_above_50"]=(df["rsi"]>=50)&(df["rsi"].shift(1)<50)
df["rsi_cross_below_50"]=(df["rsi"]<50)&(df["rsi"].shift(1)>=50)

# Volume
df["vol_ma20"]=df["volume"].rolling(20).mean()
df["vol_ratio"]=df["volume"]/df["vol_ma20"]

# Taker ratio (for info tracking)
df["taker_ratio"]=df["tbv"]/df["volume"]

# ETH/BTC ratio
df["ratio"]=df["close"]/df["btc_close"]
df["ratio_ema20"]=df["ratio"].ewm(span=20).mean()

# Session
df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday  # 0=Mon, 6=Sun

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars with all indicators")
print(f"Date range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

# ============================================================
# Parameterized backtest engine
# ============================================================
def run(data, cfg):
    """
    cfg dict keys:
      trend_mode: "ema20_50_close" | "ema20_50" | "close_ema50"
      pullback_depth: int (RSI threshold, e.g. 45)
      pullback_lookback: int (bars, e.g. 5)
      use_volume: bool
      vol_thresh: float (e.g. 1.2)
      use_ratio: bool
      use_session: bool
      use_price_confirm: bool
      trail: "ema10" | "ema15" | "ema20" | "ema25"
      min_hold: int (hours/bars)
      safenet_pct: float (e.g. 0.035)
      time_stop: int (0=disabled)
      margin: int (100/150/200)
    """
    trend_mode=cfg.get("trend_mode","ema20_50_close")
    pb_depth=cfg.get("pullback_depth",45)
    pb_lb=cfg.get("pullback_lookback",5)
    use_vol=cfg.get("use_volume",True)
    vol_th=cfg.get("vol_thresh",1.2)
    use_ratio=cfg.get("use_ratio",True)
    use_sess=cfg.get("use_session",True)
    use_price=cfg.get("use_price_confirm",True)
    trail=cfg.get("trail","ema20")
    min_hold=cfg.get("min_hold",6)
    sn_pct=cfg.get("safenet_pct",0.035)
    time_stop=cfg.get("time_stop",48)
    margin=cfg.get("margin",MARGIN)

    rsi_min_col=f"rsi_min{pb_lb}" if f"rsi_min{pb_lb}" in data.columns else "rsi_min5"
    rsi_max_col=f"rsi_max{pb_lb}" if f"rsi_max{pb_lb}" in data.columns else "rsi_max5"

    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        # --- EXITS ---
        # Long exits
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(margin*LEVERAGE)/p["entry"]);p["mf"]=mf
            # SafeNet
            if lo<=p["entry"]*(1-sn_pct):
                ep=p["entry"]*(1-sn_pct)-(p["entry"]*(1-sn_pct)-lo)*0.25
                pnl=(ep-p["entry"])*(margin*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            # TimeStop
            elif time_stop>0 and bars>=time_stop:
                pnl=(close-p["entry"])*(margin*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"mf":mf});c=True
            # Trail
            elif bars>=min_hold:
                trail_val=row[trail] if trail in row.index else row["ema20"]
                if close<=trail_val:
                    pnl=(close-p["entry"])*(margin*LEVERAGE)/p["entry"]-FEE
                    trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        # Short exits
        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(margin*LEVERAGE)/p["entry"]);p["mf"]=mf
            # SafeNet
            if hi>=p["entry"]*(1+sn_pct):
                ep=p["entry"]*(1+sn_pct)+(hi-p["entry"]*(1+sn_pct))*0.25
                pnl=(p["entry"]-ep)*(margin*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            # TimeStop
            elif time_stop>0 and bars>=time_stop:
                pnl=(p["entry"]-close)*(margin*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"mf":mf});c=True
            # Trail
            elif bars>=min_hold:
                trail_val=row[trail] if trail in row.index else row["ema20"]
                if close>=trail_val:
                    pnl=(p["entry"]-close)*(margin*LEVERAGE)/p["entry"]-FEE
                    trades.append({**p.get("info",{}),"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        # --- ENTRY ---
        if i<1:continue
        ep=nxt["open"]
        h=row["hour"];wd=row["weekday"]

        # Session filter
        if use_sess:
            if h in WORST_HOURS:continue
            if wd in WORST_DAYS:continue

        # 4h trend
        e20_4h=row["ema20_4h"];e50_4h=row["ema50_4h"];c4h=row["close_4h"]
        if pd.isna(e20_4h) or pd.isna(e50_4h) or pd.isna(c4h):continue

        trend_long=False;trend_short=False
        if trend_mode=="ema20_50_close":
            trend_long=(c4h>e20_4h) and (e20_4h>e50_4h)
            trend_short=(c4h<e20_4h) and (e20_4h<e50_4h)
        elif trend_mode=="ema20_50":
            trend_long=e20_4h>e50_4h
            trend_short=e20_4h<e50_4h
        elif trend_mode=="close_ema50":
            trend_long=c4h>e50_4h
            trend_short=c4h<e50_4h

        if not trend_long and not trend_short:continue

        # Pullback + Recovery (RSI)
        rsi_min_v=row[rsi_min_col] if rsi_min_col in row.index else 100
        rsi_max_v=row[rsi_max_col] if rsi_max_col in row.index else 0

        pullback_long=(rsi_min_v<pb_depth) if not pd.isna(rsi_min_v) else False
        pullback_short=(rsi_max_v>(100-pb_depth)) if not pd.isna(rsi_max_v) else False

        recovery_long=bool(row["rsi_cross_above_50"])
        recovery_short=bool(row["rsi_cross_below_50"])

        # Price confirmation
        price_long=close>row["ema20"] if use_price else True
        price_short=close<row["ema20"] if use_price else True

        # Volume
        vol_ok=row["vol_ratio"]>vol_th if use_vol else True

        # ETH/BTC ratio
        if use_ratio and not pd.isna(row.get("ratio",np.nan)) and not pd.isna(row.get("ratio_ema20",np.nan)):
            ratio_long=row["ratio"]>row["ratio_ema20"]
            ratio_short=row["ratio"]<row["ratio_ema20"]
        elif use_ratio:
            ratio_long=False;ratio_short=False
        else:
            ratio_long=True;ratio_short=True

        # Combine signals
        long_sig=trend_long and pullback_long and recovery_long and price_long and vol_ok and ratio_long
        short_sig=trend_short and pullback_short and recovery_short and price_short and vol_ok and ratio_short

        info={"dt":str(row["datetime"]),"entry_price":round(ep,2),"entry_hour":h,"entry_wd":wd,
              "entry_rsi":round(row["rsi"],1),"entry_vol_ratio":round(row["vol_ratio"],2)}

        if long_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if short_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame()

# ============================================================
# Metrics
# ============================================================
def calc(tdf):
    if len(tdf)==0:
        return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0,"trail_pnl":0,"ts_pnl":0,
                "avg_w":0,"avg_l":0,"rr":0,"sharpe":0,"max_consec_loss":0,"max_consec_loss_pnl":0}
    pnl=tdf["pnl"].sum()
    wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 99.9
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"]
    ts=tdf[tdf["type"]=="TimeStop"]
    trail=tdf[tdf["type"]=="Trail"]
    avg_w=w["pnl"].mean() if len(w)>0 else 0
    avg_l=l["pnl"].mean() if len(l)>0 else 0
    rr=abs(avg_w/avg_l) if avg_l!=0 else 99.9
    sharpe=tdf["pnl"].mean()/tdf["pnl"].std()*np.sqrt(len(tdf)) if tdf["pnl"].std()>0 else 0
    # Max consecutive losses
    is_loss=(tdf["pnl"]<=0).values;mcl=0;cur_streak=0;mcl_pnl=0;cur_pnl=0
    for j in range(len(is_loss)):
        if is_loss[j]:
            cur_streak+=1;cur_pnl+=tdf.iloc[j]["pnl"]
            if cur_streak>mcl:mcl=cur_streak;mcl_pnl=cur_pnl
        else:
            cur_streak=0;cur_pnl=0
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),"trail_pnl":round(trail["pnl"].sum(),2),
            "ts_pnl":round(ts["pnl"].sum(),2),"avg_w":round(avg_w,2),"avg_l":round(avg_l,2),
            "rr":round(rr,2),"sharpe":round(sharpe,2),"max_consec_loss":mcl,"max_consec_loss_pnl":round(mcl_pnl,2)}

def print_row(name,s):
    print(f"  {name:<40s} {s['n']:>4d}  ${s['pnl']:>+8.0f}  {s['wr']:>5.1f}%  {s['pf']:>5.2f}  ${s['dd']:>7.0f}  {s['sn']:>3d}  ${s['sn_pnl']:>7.0f}  ${s['trail_pnl']:>7.0f}  ${s['ts_pnl']:>7.0f}")

def print_header():
    print(f"  {'Config':<40s} {'N':>4s}  {'PnL':>9s}  {'WR':>6s}  {'PF':>5s}  {'DD':>8s}  {'SN':>3s}  {'SN$':>8s}  {'Trail$':>8s}  {'TS$':>8s}")
    print("  "+"-"*110)

# ============================================================
# BASE CONFIG
# ============================================================
BASE_CFG={
    "trend_mode":"ema20_50_close","pullback_depth":45,"pullback_lookback":5,
    "use_volume":True,"vol_thresh":1.2,"use_ratio":True,"use_session":True,
    "use_price_confirm":True,"trail":"ema20","min_hold":6,
    "safenet_pct":0.035,"time_stop":48,"margin":100
}

# ============================================================
# Phase 1: Core Ablation
# ============================================================
print("\n"+"="*100)
print("  Phase 1: Core Ablation (5 configs)")
print("="*100)
print_header()

phase1_cfgs=[
    ("P1.1 Base (all conditions)",  {**BASE_CFG}),
    ("P1.2 No volume filter",      {**BASE_CFG,"use_volume":False}),
    ("P1.3 No ETH/BTC ratio",      {**BASE_CFG,"use_ratio":False}),
    ("P1.4 No session filter",      {**BASE_CFG,"use_session":False}),
    ("P1.5 Minimal (trend+RSI only)",{**BASE_CFG,"use_volume":False,"use_ratio":False,"use_session":False}),
]

phase1_results={}
for name,cfg in phase1_cfgs:
    tdf=run(df,cfg);s=calc(tdf)
    print_row(name,s)
    phase1_results[name]=s

# ============================================================
# Phase 2: Sensitivity Analysis
# ============================================================
print("\n"+"="*100)
print("  Phase 2: Sensitivity Analysis")
print("="*100)

# P2.1 RSI pullback depth
print("\n--- P2.1 RSI Pullback Depth ---")
print_header()
for depth in [40,45,50]:
    cfg={**BASE_CFG,"pullback_depth":depth}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if depth==45 else ""
    print_row(f"  pullback_depth={depth}{tag}",s)

# P2.2 Trail EMA
print("\n--- P2.2 Trail EMA ---")
print_header()
for trail in ["ema10","ema15","ema20","ema25"]:
    cfg={**BASE_CFG,"trail":trail}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if trail=="ema20" else ""
    print_row(f"  trail={trail}{tag}",s)

# P2.3 Min hold
print("\n--- P2.3 Min Hold ---")
print_header()
for mh in [4,6,8,12]:
    cfg={**BASE_CFG,"min_hold":mh}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if mh==6 else ""
    print_row(f"  min_hold={mh}h{tag}",s)

# P2.4 SafeNet
print("\n--- P2.4 SafeNet % ---")
print_header()
for sn in [0.03,0.035,0.04]:
    cfg={**BASE_CFG,"safenet_pct":sn}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if sn==0.035 else ""
    print_row(f"  safenet={sn*100:.1f}%{tag}",s)

# P2.5 4h trend mode
print("\n--- P2.5 4h Trend Mode ---")
print_header()
for tm in ["ema20_50_close","ema20_50","close_ema50"]:
    cfg={**BASE_CFG,"trend_mode":tm}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if tm=="ema20_50_close" else ""
    print_row(f"  trend={tm}{tag}",s)

# P2.6 Volume threshold
print("\n--- P2.6 Volume Threshold ---")
print_header()
for vt in [1.0,1.2,1.5]:
    cfg={**BASE_CFG,"vol_thresh":vt}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if vt==1.2 else ""
    print_row(f"  vol_thresh={vt}x{tag}",s)

# P2.7 Pullback lookback
print("\n--- P2.7 Pullback Lookback ---")
print_header()
for lb in [3,5,7]:
    cfg={**BASE_CFG,"pullback_lookback":lb}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if lb==5 else ""
    print_row(f"  lookback={lb} bars{tag}",s)

# P2.8 Time stop
print("\n--- P2.8 Time Stop ---")
print_header()
for ts in [0,24,48,72]:
    cfg={**BASE_CFG,"time_stop":ts}
    tdf=run(df,cfg);s=calc(tdf)
    tag=" ***" if ts==48 else ""
    label="off" if ts==0 else f"{ts}h"
    print_row(f"  time_stop={label}{tag}",s)

# ============================================================
# Phase 3: Position Sizing
# ============================================================
print("\n"+"="*100)
print("  Phase 3: Position Sizing")
print("="*100)
print(f"  {'Margin':<10s} {'Notional':<10s} {'N':>4s}  {'PnL':>9s}  {'WR':>6s}  {'PF':>5s}  {'DD':>8s}  {'RR':>5s}  {'Sharpe':>7s}")
print("  "+"-"*75)
for m in [100,150,200]:
    cfg={**BASE_CFG,"margin":m}
    tdf=run(df,cfg);s=calc(tdf)
    print(f"  {m}U{'':<6s} ${m*LEVERAGE:,}{'':<4s} {s['n']:>4d}  ${s['pnl']:>+8.0f}  {s['wr']:>5.1f}%  {s['pf']:>5.2f}  ${s['dd']:>7.0f}  {s['rr']:>5.2f}  {s['sharpe']:>7.2f}")

# ============================================================
# Phase 4: Full Validation (using BASE_CFG)
# ============================================================
print("\n"+"="*100)
print("  Phase 4: Full Validation (Base Config)")
print("="*100)

# --- 4a. Walk-Forward 8m/4m ---
print("\n--- 4a. Walk-Forward (8m train / 4m test) ---")
split=int(len(df)*8/12)
df_dev=df.iloc[:split].reset_index(drop=True)
df_oos=df.iloc[split:].reset_index(drop=True)
tdf_dev=run(df_dev,BASE_CFG);s_dev=calc(tdf_dev)
tdf_oos=run(df_oos,BASE_CFG);s_oos=calc(tdf_oos)
print(f"  DEV(8m): {s_dev['n']} trades, ${s_dev['pnl']:+.0f}, WR {s_dev['wr']:.1f}%, PF {s_dev['pf']:.2f}")
print(f"  OOS(4m): {s_oos['n']} trades, ${s_oos['pnl']:+.0f}, WR {s_oos['wr']:.1f}%, PF {s_oos['pf']:.2f}, DD ${s_oos['dd']:.0f}, SN {s_oos['sn']}")

# --- 4b. Rolling 3m/1m ---
print("\n--- 4b. Rolling Validation (3m train / 1m test) ---")
months=sorted(df["month"].unique())
fold_pnls=[]
print(f"  {'Fold':<6s} {'Test Month':<12s} {'N':>4s}  {'PnL':>9s}")
print("  "+"-"*40)
for fi in range(len(months)-4):
    test_m=months[fi+3]
    ft=df[df["month"]==test_m].reset_index(drop=True)
    if len(ft)<20:
        fold_pnls.append(0);continue
    tdf_f=run(ft,BASE_CFG)
    fp=tdf_f["pnl"].sum() if len(tdf_f)>0 else 0
    fold_pnls.append(fp)
    print(f"  {fi+1:<6d} {str(test_m):<12s} {len(tdf_f):>4d}  ${fp:>+8.0f}")
prof=sum(1 for x in fold_pnls if x>0)
print(f"\n  Rolling total: ${sum(fold_pnls):+.0f} ({prof}/{len(fold_pnls)} folds profitable)")

# --- 4c. Monthly Breakdown ---
print("\n--- 4c. Monthly Breakdown ---")
tdf_full=run(df,BASE_CFG)
if len(tdf_full)>0:
    tdf_full["month_str"]=tdf_full["dt"].apply(lambda x:x[:7])
    all_months_str=sorted(tdf_full["month_str"].unique())
    cum_eq=0
    print(f"  {'Month':<10s} {'N':>4s}  {'PnL':>9s}  {'WR':>6s}  {'SN':>3s}  {'Equity':>9s}  {'Curve'}")
    print("  "+"-"*75)
    for m in all_months_str:
        mt=tdf_full[tdf_full["month_str"]==m]
        mpnl=mt["pnl"].sum();mwr=(mt["pnl"]>0).mean()*100 if len(mt)>0 else 0
        msn=len(mt[mt["type"]=="SafeNet"])
        cum_eq+=mpnl
        bar_len=max(0,int(cum_eq/50)) if cum_eq>0 else 0
        bar="="*min(bar_len,40)
        print(f"  {m:<10s} {len(mt):>4d}  ${mpnl:>+8.0f}  {mwr:>5.1f}%  {msn:>3d}  ${cum_eq:>+8.0f}  |{bar}")

# --- 4d. Hour/Weekday Analysis ---
print("\n--- 4d. Hour/Weekday Analysis ---")
if len(tdf_full)>0:
    print(f"\n  {'Hour':>6s}  {'N':>4s}  {'PnL':>9s}  {'WR':>6s}  {'SN':>3s}")
    print("  "+"-"*40)
    for h in range(24):
        ht=tdf_full[tdf_full["entry_hour"]==h]
        if len(ht)==0:continue
        hpnl=ht["pnl"].sum();hwr=(ht["pnl"]>0).mean()*100
        hsn=len(ht[ht["type"]=="SafeNet"])
        print(f"  {h:>6d}  {len(ht):>4d}  ${hpnl:>+8.0f}  {hwr:>5.1f}%  {hsn:>3d}")

    day_names=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    print(f"\n  {'Day':>6s}  {'N':>4s}  {'PnL':>9s}  {'WR':>6s}  {'SN':>3s}")
    print("  "+"-"*40)
    for wd in range(7):
        wt=tdf_full[tdf_full["entry_wd"]==wd]
        if len(wt)==0:continue
        wpnl=wt["pnl"].sum();wwr=(wt["pnl"]>0).mean()*100
        wsn=len(wt[wt["type"]=="SafeNet"])
        print(f"  {day_names[wd]:>6s}  {len(wt):>4d}  ${wpnl:>+8.0f}  {wwr:>5.1f}%  {wsn:>3d}")

# --- 4e. Consecutive Loss + Drawdown ---
print("\n--- 4e. Consecutive Loss & Drawdown ---")
if len(tdf_full)>0:
    s_full=calc(tdf_full)
    cum=tdf_full["pnl"].cumsum()
    dd_series=cum-cum.cummax()
    max_dd_idx=dd_series.idxmin()
    # Find recovery length
    peak_before=cum.iloc[:max_dd_idx+1].idxmax()
    recovery_after=None
    peak_val=cum.iloc[peak_before]
    for j in range(max_dd_idx,len(cum)):
        if cum.iloc[j]>=peak_val:
            recovery_after=j;break
    recovery_trades=(recovery_after-peak_before) if recovery_after else "未恢復"

    print(f"  Total trades:           {s_full['n']}")
    print(f"  Win Rate:               {s_full['wr']:.1f}%")
    print(f"  Profit Factor:          {s_full['pf']:.2f}")
    print(f"  Risk/Reward:            {s_full['rr']:.2f}:1")
    print(f"  Avg Winner:             ${s_full['avg_w']:+.2f}")
    print(f"  Avg Loser:              ${s_full['avg_l']:+.2f}")
    print(f"  Annual PnL:             ${s_full['pnl']:+.0f}")
    print(f"  Max Drawdown:           ${s_full['dd']:.0f}")
    print(f"  Max Consecutive Losses: {s_full['max_consec_loss']} (${s_full['max_consec_loss_pnl']:.0f})")
    print(f"  Sharpe-like:            {s_full['sharpe']:.2f}")
    print(f"  Recovery (trades):      {recovery_trades}")
    print(f"  SafeNet:                {s_full['sn']} trades, ${s_full['sn_pnl']:.0f}")
    print(f"  Trail:                  ${s_full['trail_pnl']:.0f}")
    print(f"  TimeStop:               ${s_full['ts_pnl']:.0f}")

    # Long/Short breakdown
    longs=tdf_full[tdf_full["side"]=="long"]
    shorts=tdf_full[tdf_full["side"]=="short"]
    if len(longs)>0:
        lwr=(longs["pnl"]>0).mean()*100
        print(f"\n  Long:  {len(longs)} trades, ${longs['pnl'].sum():+.0f}, WR {lwr:.1f}%")
    if len(shorts)>0:
        swr=(shorts["pnl"]>0).mean()*100
        print(f"  Short: {len(shorts)} trades, ${shorts['pnl'].sum():+.0f}, WR {swr:.1f}%")

    # Hold time analysis
    print(f"\n  --- Hold Time vs PnL ---")
    print(f"  {'Duration':<12s} {'N':>4s}  {'PnL':>9s}  {'WR':>6s}")
    print("  "+"-"*40)
    for lo_h,hi_h,label in [(0,6,"<6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,200,"48h+")]:
        ht=tdf_full[(tdf_full["bars"]>=lo_h)&(tdf_full["bars"]<hi_h)]
        if len(ht)==0:continue
        hpnl=ht["pnl"].sum();hwr=(ht["pnl"]>0).mean()*100
        print(f"  {label:<12s} {len(ht):>4d}  ${hpnl:>+8.0f}  {hwr:>5.1f}%")

# --- 4f. Full Trade List ---
print("\n--- 4f. Full Trade List ---")
if len(tdf_full)>0:
    print(f"  {'#':>4s}  {'Date':<20s} {'Side':<6s} {'Entry':>9s}  {'PnL':>9s}  {'Bars':>5s}  {'Type':<10s} {'RSI':>5s}  {'Vol':>5s}")
    print("  "+"-"*90)
    cum_pnl=0
    for idx,t in tdf_full.iterrows():
        cum_pnl+=t["pnl"]
        print(f"  {idx+1:>4d}  {t.get('dt',''):<20s} {t['side']:<6s} ${t.get('entry_price',0):>8.1f}  ${t['pnl']:>+8.2f}  {t['bars']:>5.0f}  {t['type']:<10s} {t.get('entry_rsi',0):>5.1f}  {t.get('entry_vol_ratio',0):>5.2f}")
    print(f"\n  Cumulative PnL: ${cum_pnl:+.2f}")

# --- 4g. Drawdown Journey ---
print("\n--- 4g. Drawdown Journey (Equity Curve) ---")
if len(tdf_full)>0:
    cum=tdf_full["pnl"].cumsum().values
    dd_vals=(pd.Series(cum)-pd.Series(cum).cummax()).values
    print(f"  {'Trade#':>7s}  {'CumPnL':>9s}  {'DD':>9s}  {'Visual'}")
    print("  "+"-"*60)
    # Print every 5th trade + key points (max equity, max drawdown)
    max_eq_idx=np.argmax(cum)
    max_dd_idx=np.argmin(dd_vals)
    for j in range(len(cum)):
        is_key=(j==0 or j==len(cum)-1 or j==max_eq_idx or j==max_dd_idx or (j+1)%10==0)
        if not is_key:continue
        bar_len=int(cum[j]/30) if cum[j]>0 else 0
        bar="+"*min(bar_len,50) if cum[j]>0 else "-"*min(int(abs(cum[j])/30),20)
        marker=""
        if j==max_eq_idx:marker=" <-- Peak"
        if j==max_dd_idx:marker=" <-- Max DD"
        print(f"  {j+1:>7d}  ${cum[j]:>+8.0f}  ${dd_vals[j]:>+8.0f}  |{bar}{marker}")

# ============================================================
# Final Summary
# ============================================================
print("\n"+"="*100)
print("  FINAL SUMMARY — ETH Trend Pullback Rider (TPR)")
print("="*100)
if len(tdf_full)>0:
    s=calc(tdf_full)
    print(f"""
  Core Metrics (100U margin):
    Total trades:           {s['n']}
    Win Rate:               {s['wr']:.1f}%
    Profit Factor:          {s['pf']:.2f}
    Risk/Reward:            {s['rr']:.2f}:1
    Annual PnL:             ${s['pnl']:+,.0f}
    Max Drawdown:           ${s['dd']:,.0f}
    Max Consecutive Losses: {s['max_consec_loss']} (${s['max_consec_loss_pnl']:.0f})
    Sharpe-like:            {s['sharpe']:.2f}

  Exit Breakdown:
    Trail:    ${s['trail_pnl']:+,.0f}
    SafeNet:  ${s['sn_pnl']:+,.0f} ({s['sn']} trades)
    TimeStop: ${s['ts_pnl']:+,.0f}

  OOS (4m):  ${s_oos['pnl']:+,.0f} (PF {s_oos['pf']:.2f})
  Rolling:   ${sum(fold_pnls):+,.0f} ({prof}/{len(fold_pnls)} folds)

  Position Scaling:
    100U margin: ${s['pnl']:+,.0f}
    150U margin: ${s['pnl']*1.5:+,.0f} (estimated)
    200U margin: ${s['pnl']*2:+,.0f} (estimated)

  vs v6 Champion:
    v6: 97 trades, +$3,582, WR 47.4%, PF 2.93, OOS +$1,795
    TPR: {s['n']} trades, ${s['pnl']:+,.0f}, WR {s['wr']:.1f}%, PF {s['pf']:.2f}, OOS ${s_oos['pnl']:+,.0f}
""")
else:
    print("\n  NO TRADES GENERATED. Strategy has no edge under these conditions.\n")

print("="*100)
print("  Backtest complete.")
print("="*100)
