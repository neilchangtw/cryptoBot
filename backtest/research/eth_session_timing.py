"""
A6. ETH Session Timing Filter
深度分析發現小時效應但從未使用。
測試不同時段作為進場過濾（免費過濾器）。

Crypto 24h 時段（UTC+8）：
  Asia:   08-15 (亞洲活躍)
  Europe: 15-23 (歐洲活躍)
  US:     21-05 (美國活躍)
  Overlap: 21-23 (歐美重疊，流動性最高)
  Dead:   05-08 (低流動性)

測試：
  S1. 只在歐美重疊 (21-23) 進場
  S2. 避開 dead zone (05-08)
  S3. 各時段分別表現
  S4. 避開最差小時（data-driven）
  S5. 只在最好小時進場
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
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

# ============================================================
# Data + Indicators
# ============================================================
print("="*100)
print("  A6. ETH Session Timing Filter")
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

# Session labels
df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday  # 0=Mon, 6=Sun

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

# ============================================================
# Backtest engine
# ============================================================
def run(data, session_mode="all", allowed_hours=None, blocked_hours=None, min_hold=6):
    """
    session_mode:
      all       = no time filter (baseline C4)
      allowed   = only enter during allowed_hours
      blocked   = enter anytime except blocked_hours
      weekday   = also block specific weekdays
    """
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        # Exits (identical to baseline)
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

        # Base signal: Squeeze + vol + trend (C4)
        was_squeeze=data.iloc[i-1]["bb_width_pctile"]<20
        l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
        s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]

        # Session filter
        h=row["hour"]
        wd=row["weekday"]

        if session_mode=="allowed":
            if allowed_hours and h not in allowed_hours:
                l_sig=False;s_sig=False
        elif session_mode=="blocked":
            if blocked_hours and h in blocked_hours:
                l_sig=False;s_sig=False
        elif session_mode=="weekday_block":
            if blocked_hours and h in blocked_hours:
                l_sig=False;s_sig=False
            if wd in (0, 6):  # Block Monday + Sunday (worst days from analysis)
                l_sig=False;s_sig=False

        info={"entry_hour":h,"entry_weekday":wd,
              "vol_ratio":row["vol_ratio"],"trend_up":row["trend_up"],
              "dt":row["datetime"],"entry_price":ep}

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
# 0. Hour-by-Hour PnL Analysis (data discovery)
# ============================================================
print(f"\n{'='*100}")
print("0. Hour-by-Hour Entry Performance (Base C4)")
print(f"{'='*100}")

# Run base and analyze by entry hour
tdf_all=run(df,session_mode="all")
if len(tdf_all)>0 and "entry_hour" in tdf_all.columns:
    print(f"\n  {'Hour':>5s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'SN':>4s} {'Avg$':>7s}")
    print(f"  {'-'*40}")
    hour_stats=[]
    for h in range(24):
        sub=tdf_all[tdf_all["entry_hour"]==h]
        if len(sub)==0:
            hour_stats.append({"h":h,"n":0,"pnl":0,"wr":0,"sn":0})
            continue
        sn_count=len(sub[sub["type"]=="SafeNet"])
        pnl=sub["pnl"].sum()
        wr=(sub["pnl"]>0).mean()*100
        avg=sub["pnl"].mean()
        hour_stats.append({"h":h,"n":len(sub),"pnl":pnl,"wr":wr,"sn":sn_count})
        flag=" <<<" if pnl<-50 else (" !!!" if pnl>100 else "")
        print(f"  {h:>5d} {len(sub):>5d} ${pnl:>+7,.0f} {wr:>5.1f}% {sn_count:>4d} ${avg:>+6.1f}{flag}")

    # Identify worst and best hours
    hour_stats_df=pd.DataFrame(hour_stats)
    worst_hours=set(hour_stats_df.nsmallest(6,"pnl")["h"].tolist())
    best_hours=set(hour_stats_df.nlargest(8,"pnl")["h"].tolist())
    print(f"\n  Worst 6 hours (by PnL): {sorted(worst_hours)}")
    print(f"  Best 8 hours (by PnL): {sorted(best_hours)}")

    # Weekday analysis
    print(f"\n  {'Weekday':>8s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'SN':>4s}")
    print(f"  {'-'*35}")
    for wd,name in enumerate(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]):
        sub=tdf_all[tdf_all["entry_weekday"]==wd]
        if len(sub)==0:continue
        sn_count=len(sub[sub["type"]=="SafeNet"])
        flag=" <<<" if sub["pnl"].sum()<-100 else (" !!!" if sub["pnl"].sum()>300 else "")
        print(f"  {name:>8s} {len(sub):>5d} ${sub['pnl'].sum():>+7,.0f} {(sub['pnl']>0).mean()*100:>5.1f}% {sn_count:>4d}{flag}")

# ============================================================
# 1. Session Filter Configs
# ============================================================
print(f"\n{'='*100}")
print("1. Session Filter Results")
print(f"{'='*100}")

# Build configs based on data
configs=[
    ("0. Base C4 (no filter)", "all", None, None, 6),
    # Session-based
    ("1. EU+US overlap (21-23)", "allowed", set(range(21,24)), None, 6),
    ("2. Europe (15-23)", "allowed", set(range(15,24)), None, 6),
    ("3. US active (21-05)", "allowed", set(list(range(21,24))+list(range(0,6))), None, 6),
    ("4. Asia (08-15)", "allowed", set(range(8,16)), None, 6),
    # Block-based
    ("5. Block dead (05-08)", "blocked", None, {5,6,7}, 6),
    ("6. Block night (01-07)", "blocked", None, set(range(1,8)), 6),
]

# Data-driven: add worst/best hours configs
if len(tdf_all)>0:
    configs.append(("7. Block worst 6 hours", "blocked", None, worst_hours, 6))
    configs.append(("8. Best 8 hours only", "allowed", best_hours, None, 6))
    configs.append(("9. Block worst 4 hours", "blocked", None, set(sorted(worst_hours)[:4]), 6))
    configs.append(("10. Block worst + Mon/Sun", "weekday_block", None, worst_hours, 6))
    configs.append(("11. Best 8h + min2h hold", "allowed", best_hours, None, 2))
    configs.append(("12. Best 8h + min12h hold", "allowed", best_hours, None, 12))

print(f"\n  {'Config':<35s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s}")
print(f"  {'-'*92}")

all_r=[]
for name,mode,allowed,blocked,mh in configs:
    tdf=run(df,session_mode=mode,allowed_hours=allowed,blocked_hours=blocked,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"allowed":allowed,"blocked":blocked,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2200 else "")
    print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f}{flag}")

# ============================================================
# 2. Walk-Forward + Rolling
# ============================================================
print(f"\n{'='*100}")
print("2. Walk-Forward + Rolling")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<35s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*82}")

for r in all_r[:10]:
    to=run(oos,r["mode"],r["allowed"],r["blocked"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["allowed"],r["blocked"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# 3. SafeNet by Session
# ============================================================
print(f"\n{'='*100}")
print("3. SafeNet Triggers by Session")
print(f"{'='*100}")

if len(tdf_all)>0:
    sn=tdf_all[tdf_all["type"]=="SafeNet"]
    trail_win=tdf_all[(tdf_all["type"]=="Trail")&(tdf_all["pnl"]>0)]
    if len(sn)>0:
        print(f"\n  SafeNet entry hours: {sorted(sn['entry_hour'].tolist())}")
        print(f"  Trail Win entry hours (sample): {sorted(trail_win['entry_hour'].tolist()[:20])}")

        # Session breakdown
        for name, hours in [("Asia 08-15", range(8,16)), ("Europe 15-21", range(15,22)),
                            ("US 21-05", list(range(21,24))+list(range(0,6))), ("Dead 05-08", range(5,8))]:
            sn_sub=sn[sn["entry_hour"].isin(hours)]
            tw_sub=trail_win[trail_win["entry_hour"].isin(hours)]
            print(f"  {name}: SafeNet={len(sn_sub)} (${sn_sub['pnl'].sum():+,.0f}) | "
                  f"Trail Win={len(tw_sub)} (${tw_sub['pnl'].sum():+,.0f})")

print("\nDone.")
