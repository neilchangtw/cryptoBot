"""
Phase C: Deep Analysis — Champion Strategy
Sess + RatioZ1 + MTF40

規格：
  進場：MTF ratio pctile<40 + BB突破 + Vol>1.0 + ETH/BTC Z>1.0(多)/<-1.0(空)
        Block hours 0,1,2,12 + Block Mon/Sun
  出場：SafeNet ±3% / EMA20 trail (min hold 6h)

9 段深度分析 + 市場環境分析 + vs Base C4 對比
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;SAFENET_PCT=0.03;MAX_SAME=2;FEE=1.6
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

# ============================================================
print("="*100)
print("  Phase C: Deep Analysis — Sess + RatioZ1 + MTF40")
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

# ============================================================
# All indicators
# ============================================================
btc_map=btc.set_index("ot")["close"].to_dict()
df=df_1h.copy()
df["btc_close"]=df["ot"].map(btc_map)

# 4h ATR
tr_4h=pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],"hc":abs(df_4h["high"]-df_4h["close"].shift(1)),"lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
mapped=pd.merge_asof(df[["ot"]].sort_values("ot"),df_4h[["ot","atr_4h"]].dropna().sort_values("ot"),on="ot",direction="backward")
df["atr_4h"]=mapped["atr_4h"].values

# 1h indicators
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["trend_up"]=df["close"]>df["ema50"]

# ETH/BTC ratio
df["ratio"]=df["close"]/df["btc_close"]
ratio_mean=df["ratio"].rolling(50).mean()
ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std

# MTF ratio
df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars")

# ============================================================
# Backtest engines
# ============================================================
def run_champion(data):
    """Champion: Sess + RatioZ1 + MTF40"""
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            elif bars>=6 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=6 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue

        h=row["hour"];wd=row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:continue

        was_mtf_low=data.iloc[i-1]["mtf_ratio_pctile"]<40
        if not was_mtf_low:continue

        bb_long=close>row["bb_upper"]
        bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue
        if row["vol_ratio"]<=1.0:continue

        rz=row["ratio_zscore"]
        l_sig=bb_long and rz>1.0
        s_sig=bb_short and rz<-1.0

        info={"entry_hour":h,"entry_weekday":wd,"entry_atr":row["atr"],"entry_vol":row["vol_ratio"],
              "entry_rsi":row["rsi"],"entry_bb_pctile":row["bb_width_pctile"],"entry_atr_pctile":row["atr_pctile"],
              "entry_price":ep,"dt":row["datetime"],"ratio_zscore":rz,
              "mtf_pctile":row["mtf_ratio_pctile"],"trend_up":row["trend_up"]}

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def run_base(data):
    """Base C4: BB<20 + vol>1.0 + EMA50 trend + min6h"""
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif bars>=6 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            elif bars>=6 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        was_sq=data.iloc[i-1]["bb_width_pctile"]<20
        l=was_sq and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"]
        s=was_sq and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"]
        if l and len(lpos)<MAX_SAME:lpos.append({"entry":ep,"ei":i,"mf":0,"info":{}})
        if s and len(spos)<MAX_SAME:spos.append({"entry":ep,"ei":i,"mf":0,"info":{}})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

# ============================================================
# Run
# ============================================================
print("Running champion...", flush=True)
tdf=run_champion(df)
tdf["month"]=pd.to_datetime(tdf["dt"]).dt.to_period("M")
wins=tdf[tdf["pnl"]>0];losses=tdf[tdf["pnl"]<=0]
trail=tdf[tdf["type"]=="Trail"];sn=tdf[tdf["type"]=="SafeNet"]
print(f"Champion: {len(tdf)} trades (W:{len(wins)} L:{len(losses)} Trail:{len(trail)} SN:{len(sn)})")

print("Running base C4...", flush=True)
tdf_base=run_base(df)
tdf_base["month"]=pd.to_datetime(tdf_base["dt"]).dt.to_period("M")
print(f"Base C4: {len(tdf_base)} trades")

# ============================================================
print(f"\n{'='*100}")
print("1. 收益結構")
print(f"{'='*100}")

total_fee=len(tdf)*FEE
rr=abs(wins["pnl"].mean()/losses["pnl"].mean()) if len(losses)>0 and losses["pnl"].mean()!=0 else 999
print(f"""
  Trail: {len(trail)} trades, ${trail['pnl'].sum():+,.0f} (avg ${trail['pnl'].mean():+,.2f})
  SafeNet: {len(sn)} trades, ${sn['pnl'].sum():+,.0f} (avg ${sn['pnl'].mean():+,.2f})
  Fee(est): ${total_fee:,.0f}
  Net: ${tdf['pnl'].sum():+,.0f}

  WR: {(tdf['pnl']>0).mean()*100:.1f}%
  W avg: ${wins['pnl'].mean():+,.2f}
  L avg: ${losses['pnl'].mean():+,.2f}
  RR: {rr:.2f}:1
  Avg hold: {tdf['bars'].mean():.0f}h
  Max DD: ${(tdf['pnl'].cumsum()-(tdf['pnl'].cumsum()).cummax()).min():,.0f}

  vs Base C4:
    PnL: ${tdf['pnl'].sum():+,.0f} vs ${tdf_base['pnl'].sum():+,.0f} ({(tdf['pnl'].sum()/tdf_base['pnl'].sum()-1)*100:+.0f}%)
    Trades: {len(tdf)} vs {len(tdf_base)} ({(len(tdf)/len(tdf_base)-1)*100:+.0f}%)
    SafeNet: {len(sn)} vs {len(tdf_base[tdf_base['type']=='SafeNet'])} ({(len(sn)/max(1,len(tdf_base[tdf_base['type']=='SafeNet']))-1)*100:+.0f}%)
""")

# ============================================================
print(f"{'='*100}")
print("2. Monthly Comparison (Champion vs Base)")
print(f"{'='*100}")

all_months=sorted(set(tdf["month"].unique()) | set(tdf_base["month"].unique()))
print(f"\n  {'Month':<10s} {'C:N':>5s} {'C:PnL':>8s} {'C:WR':>6s} {'B:N':>5s} {'B:PnL':>8s} {'Diff':>8s}")
print(f"  {'-'*55}")

c_prof_m=0;b_prof_m=0
for m in all_months:
    cs=tdf[tdf["month"]==m];bs=tdf_base[tdf_base["month"]==m]
    cpnl=cs["pnl"].sum();bpnl=bs["pnl"].sum()
    cwr=(cs["pnl"]>0).mean()*100 if len(cs)>0 else 0
    diff=cpnl-bpnl
    if cpnl>0:c_prof_m+=1
    if bpnl>0:b_prof_m+=1
    flag=" !!!" if diff>100 else (" <<<" if diff<-100 else "")
    print(f"  {str(m):<10s} {len(cs):>5d} ${cpnl:>+7,.0f} {cwr:>5.0f}% {len(bs):>5d} ${bpnl:>+7,.0f} ${diff:>+7,.0f}{flag}")

print(f"\n  Profitable months: Champion {c_prof_m}/{len(all_months)} | Base {b_prof_m}/{len(all_months)}")

# ============================================================
print(f"\n{'='*100}")
print("3. Win vs Loss Characteristics")
print(f"{'='*100}")

print(f"\n  {'Feature':<22s} {'Wins':>10s} {'Losses':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
print(f"  {'-'*68}")
for col,label in [("entry_hour","Hour"),("entry_atr","ATR"),("entry_vol","Volume"),
                    ("entry_rsi","RSI"),("entry_bb_pctile","BB width pctile"),
                    ("entry_atr_pctile","ATR pctile"),("ratio_zscore","Ratio Z-score"),
                    ("mtf_pctile","MTF pctile"),("bars","Hold(h)")]:
    if col not in tdf.columns:continue
    wv=wins[col].mean() if len(wins)>0 else 0
    lv=losses[col].mean() if len(losses)>0 else 0
    sv=sn[col].mean() if len(sn)>0 else 0
    print(f"  {label:<22s} {wv:>10.1f} {lv:>10.1f} {sv:>10.1f} {wv-lv:>+10.1f}")

# ============================================================
print(f"\n{'='*100}")
print("4. Hold Time vs Profit")
print(f"{'='*100}")

print(f"\n  {'Hold':>8s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'W avg':>7s}")
print(f"  {'-'*38}")
for lo_h,hi_h,label in [(0,3,"<3h"),(3,6,"3-6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,999,">96h")]:
    sub=tdf[(tdf["bars"]>=lo_h)&(tdf["bars"]<hi_h)]
    if len(sub)==0:continue
    ws=sub[sub["pnl"]>0]
    print(f"  {label:>8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {len(ws)/len(sub)*100:>5.0f}% ${ws['pnl'].mean() if len(ws)>0 else 0:>+5.0f}")

# ============================================================
print(f"\n{'='*100}")
print("5. Long vs Short")
print(f"{'='*100}")

for side,label in [("long","LONG"),("short","SHORT")]:
    sub=tdf[tdf["side"]==side];ws=sub[sub["pnl"]>0];ls=sub[sub["pnl"]<=0]
    rr_s=abs(ws["pnl"].mean()/ls["pnl"].mean()) if len(ls)>0 and ls["pnl"].mean()!=0 else 999
    sn_s=sub[sub["type"]=="SafeNet"]
    print(f"\n  {label}: {len(sub)} trades, ${sub['pnl'].sum():+,.0f}, WR {len(ws)/len(sub)*100 if len(sub)>0 else 0:.0f}%, RR {rr_s:.1f}:1")
    print(f"    W avg ${ws['pnl'].mean() if len(ws)>0 else 0:+,.2f}, L avg ${ls['pnl'].mean() if len(ls)>0 else 0:+,.2f}")
    print(f"    SafeNet: {len(sn_s)}, ${sn_s['pnl'].sum():+,.0f}")

# ============================================================
print(f"\n{'='*100}")
print("6. Profit Efficiency (Max Float vs Actual)")
print(f"{'='*100}")

if tdf["mf"].mean()>0:
    print(f"\n  Avg max float: ${tdf['mf'].mean():,.2f}")
    print(f"  Avg actual PnL: ${tdf['pnl'].mean():+,.2f}")
    print(f"  Retention: {tdf['pnl'].mean()/tdf['mf'].mean()*100:.0f}%")
    if len(wins)>0:
        print(f"\n  Wins: mf ${wins['mf'].mean():,.2f} -> pnl ${wins['pnl'].mean():+,.2f} ({wins['pnl'].mean()/wins['mf'].mean()*100:.0f}%)")
    if len(losses)>0:
        print(f"  Losses: mf ${losses['mf'].mean():,.2f} -> pnl ${losses['pnl'].mean():+,.2f}")
    if len(sn)>0:
        print(f"  SafeNet: mf ${sn['mf'].mean():,.2f} -> pnl ${sn['pnl'].mean():+,.2f}")

# ============================================================
print(f"\n{'='*100}")
print("7. Top Trades")
print(f"{'='*100}")

print(f"\n  Top 5 wins:")
for _,t in tdf.nlargest(5,"pnl").iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | RZ {t.get('ratio_zscore',0):+.2f} | {t['dt']}")

print(f"\n  Top 5 losses:")
for _,t in tdf.nsmallest(5,"pnl").iterrows():
    print(f"    ${t['pnl']:+,.0f} | {t['side']} | {t['bars']}h | mf ${t['mf']:,.0f} | RZ {t.get('ratio_zscore',0):+.2f} | {t['dt']}")

# ============================================================
print(f"\n{'='*100}")
print("8. Hour / Weekday (Active Hours Only)")
print(f"{'='*100}")

print(f"\n  {'Hour':<8s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'SN':>3s}")
print(f"  {'-'*34}")
for h in range(24):
    if h in WORST_HOURS:continue
    sub=tdf[tdf["entry_hour"]==h]
    if len(sub)==0:continue
    sn_h=sub[sub["type"]=="SafeNet"]
    flag=" <<<" if sub["pnl"].sum()<-50 else(" !!!" if sub["pnl"].sum()>100 else "")
    print(f"  {h:02d}:00   {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {(sub['pnl']>0).mean()*100:>5.0f}% {len(sn_h):>3d}{flag}")

print(f"\n  {'Day':<8s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'SN':>3s}")
print(f"  {'-'*34}")
for d in range(7):
    if d in WORST_DAYS:continue
    name=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d]
    sub=tdf[tdf["entry_weekday"]==d]
    if len(sub)==0:continue
    sn_d=sub[sub["type"]=="SafeNet"]
    flag=" <<<" if sub["pnl"].sum()<-80 else(" !!!" if sub["pnl"].sum()>100 else "")
    print(f"  {name:<8s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {(sub['pnl']>0).mean()*100:>5.0f}% {len(sn_d):>3d}{flag}")

# ============================================================
print(f"\n{'='*100}")
print("9. Walk-Forward (8m/4m) + Rolling (3m->1m)")
print(f"{'='*100}")

split=int(len(df)*8/12)
dev=df.iloc[:split].reset_index(drop=True);oos=df.iloc[split:].reset_index(drop=True)

td=run_champion(dev);to=run_champion(oos)
dp=td["pnl"].sum();op=to["pnl"].sum()
owr=(to["pnl"]>0).mean()*100 if len(to)>0 else 0
ow=to[to["pnl"]>0];ol=to[to["pnl"]<=0]
opf=ow["pnl"].sum()/abs(ol["pnl"].sum()) if len(ol)>0 and ol["pnl"].sum()!=0 else 999
osn=to[to["type"]=="SafeNet"]

# Base WF for comparison
td_b=run_base(dev);to_b=run_base(oos)
ob_pnl=to_b["pnl"].sum()
ow_b=to_b[to_b["pnl"]>0];ol_b=to_b[to_b["pnl"]<=0]
opf_b=ow_b["pnl"].sum()/abs(ol_b["pnl"].sum()) if len(ol_b)>0 and ol_b["pnl"].sum()!=0 else 999

print(f"""
  Champion:
    DEV(8m): {len(td)} trades, ${dp:+,.0f}
    OOS(4m): {len(to)} trades, ${op:+,.0f}, WR {owr:.1f}%, PF {opf:.2f}, SN {len(osn)}

  Base C4:
    OOS(4m): {len(to_b)} trades, ${ob_pnl:+,.0f}, PF {opf_b:.2f}

  OOS improvement: ${op-ob_pnl:+,.0f} ({(op/max(1,ob_pnl)-1)*100:+.0f}%)
""")

months=df["month"].unique()
print(f"  Rolling 3m->1m:")
print(f"  {'Month':<10s} {'Champion':>10s} {'Base C4':>10s} {'Diff':>10s}")
print(f"  {'-'*45}")
fold_champ=[];fold_base=[]
for fold in range(len(months)-3):
    test_m=months[fold+3];ft=df[df["month"]==test_m].reset_index(drop=True)
    if len(ft)<20:continue
    tc=run_champion(ft);tb=run_base(ft)
    pc=tc["pnl"].sum() if len(tc)>0 else 0
    pb=tb["pnl"].sum() if len(tb)>0 else 0
    fold_champ.append(pc);fold_base.append(pb)
    flag=" !!!" if pc>pb+50 else (" <<<" if pc<pb-50 else "")
    print(f"  {str(test_m):<10s} ${pc:>+9,.0f} ${pb:>+9,.0f} ${pc-pb:>+9,.0f}{flag}")

c_prof=sum(1 for x in fold_champ if x>0)
b_prof=sum(1 for x in fold_base if x>0)
print(f"\n  Champion: ${sum(fold_champ):+,.0f}, {c_prof}/{len(fold_champ)} folds")
print(f"  Base:     ${sum(fold_base):+,.0f}, {b_prof}/{len(fold_base)} folds")

# ============================================================
print(f"\n{'='*100}")
print("C2. Market Environment Analysis")
print(f"{'='*100}")

print(f"\n  Base C4 虧損月 vs Champion 表現：")
base_monthly=tdf_base.groupby("month")["pnl"].sum()
champ_monthly=tdf.groupby("month")["pnl"].sum()

loss_months=[m for m in base_monthly.index if base_monthly[m]<0]
print(f"\n  Base C4 虧損月數: {len(loss_months)}")
print(f"\n  {'Month':<10s} {'Base':>8s} {'Champion':>10s} {'Saved':>8s}")
print(f"  {'-'*40}")
total_base_loss=0;total_champ_in_loss=0
for m in loss_months:
    bl=base_monthly[m]
    cl=champ_monthly[m] if m in champ_monthly.index else 0
    saved=cl-bl
    total_base_loss+=bl;total_champ_in_loss+=cl
    flag=" !!!" if saved>100 else ""
    print(f"  {str(m):<10s} ${bl:>+7,.0f} ${cl:>+9,.0f} ${saved:>+7,.0f}{flag}")
print(f"  {'TOTAL':<10s} ${total_base_loss:>+7,.0f} ${total_champ_in_loss:>+9,.0f} ${total_champ_in_loss-total_base_loss:>+7,.0f}")

# Equity curve comparison
print(f"\n  Equity curve stats:")
c_cum=tdf["pnl"].cumsum()
b_cum=tdf_base["pnl"].cumsum()
c_dd=(c_cum-c_cum.cummax()).min()
b_dd=(b_cum-b_cum.cummax()).min()
print(f"    Champion: Max DD ${c_dd:,.0f}, Sharpe-like {tdf['pnl'].mean()/tdf['pnl'].std()*np.sqrt(len(tdf)):.2f}")
print(f"    Base C4:  Max DD ${b_dd:,.0f}, Sharpe-like {tdf_base['pnl'].mean()/tdf_base['pnl'].std()*np.sqrt(len(tdf_base)):.2f}")

# ============================================================
print(f"\n{'='*100}")
print("C3. Consecutive Loss / Drawdown Analysis")
print(f"{'='*100}")

# Max consecutive losses
consec=0;max_consec=0;consec_pnl=0;max_consec_pnl=0
for _,t in tdf.iterrows():
    if t["pnl"]<=0:
        consec+=1;consec_pnl+=t["pnl"]
        if consec>max_consec:max_consec=consec;max_consec_pnl=consec_pnl
    else:
        consec=0;consec_pnl=0

print(f"\n  Max consecutive losses: {max_consec} (${max_consec_pnl:+,.0f})")
print(f"  Max drawdown: ${c_dd:,.0f}")

# Recovery analysis
c_cum_vals=tdf["pnl"].cumsum().values
peak=0;dd_start=-1;max_recovery=0
for i,v in enumerate(c_cum_vals):
    if v>=peak:
        if dd_start>=0:
            recovery=i-dd_start
            max_recovery=max(max_recovery,recovery)
        peak=v;dd_start=-1
    else:
        if dd_start<0:dd_start=i
print(f"  Longest recovery: {max_recovery} trades")

# ============================================================
print(f"\n{'='*100}")
print("SUMMARY — Champion vs Base C4")
print(f"{'='*100}")

b_wins=tdf_base[tdf_base["pnl"]>0];b_losses=tdf_base[tdf_base["pnl"]<=0]
b_sn=tdf_base[tdf_base["type"]=="SafeNet"]
b_rr=abs(b_wins["pnl"].mean()/b_losses["pnl"].mean()) if len(b_losses)>0 and b_losses["pnl"].mean()!=0 else 999

print(f"""
  {'Metric':<25s} {'Champion':>12s} {'Base C4':>12s} {'Change':>10s}
  {'-'*65}
  {'PnL (1yr)':<25s} ${tdf['pnl'].sum():>+10,.0f} ${tdf_base['pnl'].sum():>+10,.0f} {(tdf['pnl'].sum()/tdf_base['pnl'].sum()-1)*100:>+9.0f}%
  {'Trades':<25s} {len(tdf):>12d} {len(tdf_base):>12d} {(len(tdf)/len(tdf_base)-1)*100:>+9.0f}%
  {'Win Rate':<25s} {(tdf['pnl']>0).mean()*100:>11.1f}% {(tdf_base['pnl']>0).mean()*100:>11.1f}%
  {'RR':<25s} {rr:>11.2f}:1 {b_rr:>11.2f}:1
  {'SafeNet':<25s} {len(sn):>12d} {len(b_sn):>12d} {(len(sn)/max(1,len(b_sn))-1)*100:>+9.0f}%
  {'SafeNet $':<25s} ${sn['pnl'].sum():>+10,.0f} ${b_sn['pnl'].sum():>+10,.0f}
  {'Max DD':<25s} ${c_dd:>+10,.0f} ${b_dd:>+10,.0f}
  {'Avg hold':<25s} {tdf['bars'].mean():>11.0f}h {tdf_base['bars'].mean():>11.0f}h
  {'OOS PnL':<25s} ${op:>+10,.0f} ${ob_pnl:>+10,.0f} {(op/max(1,ob_pnl)-1)*100:>+9.0f}%
  {'OOS PF':<25s} {opf:>12.2f} {opf_b:>12.2f}
  {'Rolling PnL':<25s} ${sum(fold_champ):>+10,.0f} ${sum(fold_base):>+10,.0f}
  {'Rolling folds':<25s} {c_prof}/{len(fold_champ):>11d} {b_prof}/{len(fold_base):>11d}
  {'Profitable months':<25s} {c_prof_m}/{len(all_months):>11d} {b_prof_m}/{len(all_months):>11d}
  {'Loss month recovery':<25s} ${total_champ_in_loss:>+10,.0f} ${total_base_loss:>+10,.0f}
""")

print("Done.")
