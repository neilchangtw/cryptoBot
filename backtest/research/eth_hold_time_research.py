"""
12-24h 虧損帶深入研究
Optimized Champion 在 12-24h 有 45 筆 16% WR (-$977)
但 24-48h 是 90% WR (+$2,309), 48-96h 是 100% WR (+$3,102)

問題：能否在進場時或持倉中途識別哪些交易會變成 24h+ 大贏家？

分析維度：
  1. 進場特徵對比：24h+ winners vs 12-24h losers
  2. 持倉 6h/12h 時的中途狀態（浮盈/浮虧、趨勢方向）
  3. 改善方案：中途條件決定是否繼續持有或提前出場
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;MAX_SAME=2;FEE=1.6
WORST_HOURS={0,1,2,12}
WORST_DAYS={0,5,6}  # Mon, Sat, Sun

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

print("="*100)
print("  12-24h Loss Zone Deep Research")
print("="*100)

print("\nFetching data...", flush=True)
df_1h=fetch("ETHUSDT","1h",365)
btc=fetch("BTCUSDT","1h",365)
df_4h=fetch("ETHUSDT","4h",365)

btc_map=btc.set_index("ot")["close"].to_dict()
df=df_1h.copy()
df["btc_close"]=df["ot"].map(btc_map)

tr_4h=pd.DataFrame({"hl":df_4h["high"]-df_4h["low"],"hc":abs(df_4h["high"]-df_4h["close"].shift(1)),"lc":abs(df_4h["low"]-df_4h["close"].shift(1))}).max(axis=1)
df_4h["atr_4h"]=tr_4h.rolling(14).mean()
mapped=pd.merge_asof(df[["ot"]].sort_values("ot"),df_4h[["ot","atr_4h"]].dropna().sort_values("ot"),on="ot",direction="backward")
df["atr_4h"]=mapped["atr_4h"].values

tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema10"]=df["close"].ewm(span=10).mean()
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

df["ratio"]=df["close"]/df["btc_close"]
ratio_mean=df["ratio"].rolling(50).mean();ratio_std=df["ratio"].rolling(50).std()
df["ratio_zscore"]=(df["ratio"]-ratio_mean)/ratio_std
df["ratio_ema20"]=df["ratio"].ewm(span=20).mean()
df["ratio_ema50"]=df["ratio"].ewm(span=50).mean()
df["ratio_trend_up"]=df["ratio_ema20"]>df["ratio_ema50"]
df["ratio_mom10"]=df["ratio"].pct_change(10)*100

df["mtf_ratio"]=df["atr"]/df["atr_4h"]
df["mtf_ratio_pctile"]=df["mtf_ratio"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# Body ratio
hl_range=(df["high"]-df["low"]).replace(0,np.nan)
df["body_ratio"]=abs(df["close"]-df["open"])/hl_range
df["body_ratio"]=df["body_ratio"].fillna(0)
df["bullish_body"]=df["close"]>df["open"]

# OBV
df["obv"]=(np.sign(df["close"].diff())*df["volume"]).cumsum()
df["obv_ema20"]=df["obv"].ewm(span=20).mean()
df["obv_trend_up"]=df["obv"]>df["obv_ema20"]

# Taker
df["taker_ratio"]=df["tbv"]/df["volume"]

df["hour"]=df["datetime"].dt.hour
df["weekday"]=df["datetime"].dt.weekday
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Ready: {len(df)} bars")

# ============================================================
# Run detailed backtest tracking mid-trade state
# ============================================================
def run_detailed(data):
    """Run champion with detailed per-bar tracking of each trade"""
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            # Track mid-trade state at key checkpoints
            if bars==6:
                p["info"]["pnl_6h"]=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]
                p["info"]["trend_6h"]=close>data.iloc[i]["ema50"]
                p["info"]["ratio_z_6h"]=data.iloc[i]["ratio_zscore"]
                p["info"]["mf_6h"]=mf
            if bars==12:
                p["info"]["pnl_12h"]=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]
                p["info"]["trend_12h"]=close>data.iloc[i]["ema50"]
                p["info"]["ratio_z_12h"]=data.iloc[i]["ratio_zscore"]
                p["info"]["mf_12h"]=mf

            if lo<=p["entry"]*(1-0.035):
                ep=p["entry"]*(1-0.035)-(p["entry"]*(1-0.035)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            elif bars>=12 and close<=row["ema20"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if bars==6:
                p["info"]["pnl_6h"]=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]
                p["info"]["trend_6h"]=close<data.iloc[i]["ema50"]
                p["info"]["ratio_z_6h"]=data.iloc[i]["ratio_zscore"]
                p["info"]["mf_6h"]=mf
            if bars==12:
                p["info"]["pnl_12h"]=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]
                p["info"]["trend_12h"]=close<data.iloc[i]["ema50"]
                p["info"]["ratio_z_12h"]=data.iloc[i]["ratio_zscore"]
                p["info"]["mf_12h"]=mf

            if hi>=p["entry"]*(1+0.035):
                ep=p["entry"]*(1+0.035)+(hi-p["entry"]*(1+0.035))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=12 and close>=row["ema20"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p["info"],"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        h=row["hour"];wd=row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:continue
        if data.iloc[i-1]["mtf_ratio_pctile"]>=40:continue

        bb_long=close>row["bb_upper"];bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue
        if row["vol_ratio"]<=1.0:continue

        rzv=row["ratio_zscore"]
        l_sig=bb_long and rzv>1.0
        s_sig=bb_short and rzv<-1.0

        info={"entry_hour":h,"entry_weekday":wd,"entry_atr":row["atr"],
              "entry_vol":row["vol_ratio"],"entry_rsi":row["rsi"],
              "entry_bb_pctile":row["bb_width_pctile"],"entry_price":ep,
              "dt":row["datetime"],"ratio_zscore":rzv,"abs_rz":abs(rzv),
              "mtf_pctile":row["mtf_ratio_pctile"],"trend_up":row["trend_up"],
              "body_ratio":row["body_ratio"],"bullish":row["bullish_body"],
              "obv_trend":row["obv_trend_up"],"taker_ratio":row["taker_ratio"],
              "ratio_trend_up":row["ratio_trend_up"],"ratio_mom10":row["ratio_mom10"],
              "pnl_6h":np.nan,"pnl_12h":np.nan,"trend_6h":np.nan,"trend_12h":np.nan,
              "ratio_z_6h":np.nan,"ratio_z_12h":np.nan,"mf_6h":np.nan,"mf_12h":np.nan}

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

print("\nRunning detailed backtest...", flush=True)
tdf=run_detailed(df)
print(f"Total: {len(tdf)} trades")

# Categorize trades
tdf["category"]="other"
tdf.loc[(tdf["bars"]<12)&(tdf["type"]=="SafeNet"),"category"]="early_sn"
tdf.loc[(tdf["bars"]>=12)&(tdf["bars"]<24)&(tdf["pnl"]<=0),"category"]="12_24h_loser"
tdf.loc[(tdf["bars"]>=24)&(tdf["pnl"]>0),"category"]="24h_winner"
tdf.loc[(tdf["bars"]>=12)&(tdf["bars"]<24)&(tdf["pnl"]>0),"category"]="12_24h_winner"
tdf.loc[(tdf["bars"]>=24)&(tdf["pnl"]<=0),"category"]="24h_loser"

for cat in tdf["category"].unique():
    sub=tdf[tdf["category"]==cat]
    print(f"  {cat}: {len(sub)} trades, ${sub['pnl'].sum():+,.0f}")

# ============================================================
print(f"\n{'='*100}")
print("1. Entry Characteristics: 24h+ Winners vs 12-24h Losers")
print(f"{'='*100}")

w24=tdf[tdf["category"]=="24h_winner"]
l12=tdf[tdf["category"]=="12_24h_loser"]
w12=tdf[tdf["category"]=="12_24h_winner"]
sn_early=tdf[tdf["category"]=="early_sn"]

print(f"\n  Counts: 24h+ winners={len(w24)}, 12-24h losers={len(l12)}, 12-24h winners={len(w12)}, early SN={len(sn_early)}")

print(f"\n  {'Feature':<22s} {'24h+ Win':>10s} {'12-24h Loss':>12s} {'12-24h Win':>11s} {'Early SN':>10s} {'Diff(W-L)':>10s}")
print(f"  {'-'*80}")

features=[
    ("ratio_zscore","Ratio Z-score"),("abs_rz","|Ratio Z|"),
    ("mtf_pctile","MTF pctile"),("entry_vol","Volume"),
    ("entry_atr","ATR"),("entry_rsi","RSI"),
    ("entry_bb_pctile","BB width pctile"),("body_ratio","Body ratio"),
    ("taker_ratio","Taker ratio"),("entry_hour","Hour"),
    ("ratio_mom10","Ratio Mom10%"),
]

for col,label in features:
    if col not in tdf.columns:continue
    wv=w24[col].mean() if len(w24)>0 else 0
    lv=l12[col].mean() if len(l12)>0 else 0
    w12v=w12[col].mean() if len(w12)>0 else 0
    sv=sn_early[col].mean() if len(sn_early)>0 else 0
    print(f"  {label:<22s} {wv:>10.3f} {lv:>12.3f} {w12v:>11.3f} {sv:>10.3f} {wv-lv:>+10.3f}")

# Boolean features
for col,label in [("trend_up","Trend Up%"),("obv_trend","OBV Up%"),
                   ("ratio_trend_up","Ratio Trend Up%"),("bullish","Bullish Body%")]:
    if col not in tdf.columns:continue
    wv=w24[col].mean()*100 if len(w24)>0 else 0
    lv=l12[col].mean()*100 if len(l12)>0 else 0
    w12v=w12[col].mean()*100 if len(w12)>0 else 0
    sv=sn_early[col].mean()*100 if len(sn_early)>0 else 0
    print(f"  {label:<22s} {wv:>10.1f} {lv:>12.1f} {w12v:>11.1f} {sv:>10.1f} {wv-lv:>+10.1f}")

# ============================================================
print(f"\n{'='*100}")
print("2. Mid-Trade State: 6h and 12h Checkpoints")
print(f"{'='*100}")

print(f"\n  At 6h checkpoint:")
print(f"  {'Feature':<22s} {'24h+ Win':>10s} {'12-24h Loss':>12s} {'Diff':>10s}")
print(f"  {'-'*58}")

for col,label in [("pnl_6h","PnL at 6h"),("mf_6h","Max Float at 6h")]:
    if col not in tdf.columns:continue
    wv=w24[col].dropna().mean() if len(w24[col].dropna())>0 else 0
    lv=l12[col].dropna().mean() if len(l12[col].dropna())>0 else 0
    print(f"  {label:<22s} ${wv:>+9.1f} ${lv:>+11.1f} ${wv-lv:>+9.1f}")

for col,label in [("trend_6h","Trend aligned at 6h%")]:
    if col not in tdf.columns:continue
    w_valid=w24[col].dropna()
    l_valid=l12[col].dropna()
    wv=w_valid.mean()*100 if len(w_valid)>0 else 0
    lv=l_valid.mean()*100 if len(l_valid)>0 else 0
    print(f"  {label:<22s} {wv:>10.1f}% {lv:>11.1f}% {wv-lv:>+9.1f}")

print(f"\n  At 12h checkpoint:")
print(f"  {'Feature':<22s} {'24h+ Win':>10s} {'12-24h Loss':>12s} {'Diff':>10s}")
print(f"  {'-'*58}")

for col,label in [("pnl_12h","PnL at 12h"),("mf_12h","Max Float at 12h")]:
    if col not in tdf.columns:continue
    wv=w24[col].dropna().mean() if len(w24[col].dropna())>0 else 0
    lv=l12[col].dropna().mean() if len(l12[col].dropna())>0 else 0
    print(f"  {label:<22s} ${wv:>+9.1f} ${lv:>+11.1f} ${wv-lv:>+9.1f}")

for col,label in [("trend_12h","Trend aligned at 12h%"),("ratio_z_12h","Ratio Z at 12h")]:
    if col not in tdf.columns:continue
    w_valid=w24[col].dropna()
    l_valid=l12[col].dropna()
    if col.endswith("%") or col.startswith("trend"):
        wv=w_valid.mean()*100 if len(w_valid)>0 else 0
        lv=l_valid.mean()*100 if len(l_valid)>0 else 0
        print(f"  {label:<22s} {wv:>10.1f}% {lv:>11.1f}% {wv-lv:>+9.1f}")
    else:
        wv=w_valid.mean() if len(w_valid)>0 else 0
        lv=l_valid.mean() if len(l_valid)>0 else 0
        print(f"  {label:<22s} {wv:>+10.3f} {lv:>+11.3f} {wv-lv:>+9.3f}")

# ============================================================
print(f"\n{'='*100}")
print("3. 12h Floating PnL Distribution")
print(f"{'='*100}")

# For trades that reach 12h, what does the floating PnL look like?
has_12h=tdf[tdf["pnl_12h"].notna()].copy()
if len(has_12h)>0:
    print(f"\n  All trades reaching 12h: {len(has_12h)}")
    for lo_p,hi_p,label in [(-999,-20,"Deep loss <-$20"),(-20,-5,"Small loss -$20~-$5"),
                              (-5,5,"Flat -$5~+$5"),(5,20,"Small win +$5~+$20"),
                              (20,50,"Good win +$20~+$50"),(50,999,"Big win >+$50")]:
        sub=has_12h[(has_12h["pnl_12h"]>=lo_p)&(has_12h["pnl_12h"]<hi_p)]
        if len(sub)==0:continue
        final_w=(sub["pnl"]>0).mean()*100
        avg_final=sub["pnl"].mean()
        w24_pct=(sub["bars"]>=24).mean()*100
        print(f"  {label:<22s} N={len(sub):>3d} | Final WR={final_w:>5.0f}% | Avg PnL=${avg_final:>+6.0f} | Reach 24h={w24_pct:>5.0f}%")

# ============================================================
print(f"\n{'='*100}")
print("4. Conditional Exit Tests at 12h")
print(f"{'='*100}")

def run_conditional(data, exit_rule="none"):
    """
    exit_rule:
      none = standard (mh12 + ema20 trail)
      profit_only = at 12h, if floating loss > -$10, force exit
      deep_loss_exit = at 12h, if floating loss > -$20, force exit
      trend_check = at 12h, if trend not aligned, force exit
      mf_check = at 12h, if max float < $10, force exit (never showed promise)
      pnl_positive = at 12h, if pnl < 0, force exit
      breakout_check = at 12h, if price back inside BB, force exit
    """
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            cur_pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]

            if lo<=p["entry"]*(1-0.035):
                ep=p["entry"]*(1-0.035)-(p["entry"]*(1-0.035)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            # Conditional exit at 12h
            elif bars==12 and exit_rule!="none":
                force_exit=False
                if exit_rule=="deep_loss_exit" and cur_pnl<-20:force_exit=True
                elif exit_rule=="profit_only" and cur_pnl<-10:force_exit=True
                elif exit_rule=="trend_check" and not row["trend_up"]:force_exit=True
                elif exit_rule=="pnl_positive" and cur_pnl<0:force_exit=True
                elif exit_rule=="breakout_check" and close<row["bb_upper"]:force_exit=True
                elif exit_rule=="ratio_check" and row["ratio_zscore"]<0:force_exit=True
                if force_exit:
                    pnl=cur_pnl-FEE
                    trades.append({"pnl":pnl,"type":"CondExit","side":"long","bars":bars,"mf":mf});c=True
            elif bars>=12 and close<=row["ema20"]:
                pnl=cur_pnl-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf});c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            cur_pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]

            if hi>=p["entry"]*(1+0.035):
                ep=p["entry"]*(1+0.035)+(hi-p["entry"]*(1+0.035))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf});c=True
            elif bars==12 and exit_rule!="none":
                force_exit=False
                if exit_rule=="deep_loss_exit" and cur_pnl<-20:force_exit=True
                elif exit_rule=="profit_only" and cur_pnl<-10:force_exit=True
                elif exit_rule=="trend_check" and row["trend_up"]:force_exit=True
                elif exit_rule=="pnl_positive" and cur_pnl<0:force_exit=True
                elif exit_rule=="breakout_check" and close>row["bb_lower"]:force_exit=True
                elif exit_rule=="ratio_check" and row["ratio_zscore"]>0:force_exit=True
                if force_exit:
                    pnl=cur_pnl-FEE
                    trades.append({"pnl":pnl,"type":"CondExit","side":"short","bars":bars,"mf":mf});c=True
            elif bars>=12 and close>=row["ema20"]:
                pnl=cur_pnl-FEE
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf});c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        if i<1:continue
        h=row["hour"];wd=row["weekday"]
        if h in WORST_HOURS or wd in WORST_DAYS:continue
        if data.iloc[i-1]["mtf_ratio_pctile"]>=40:continue
        bb_long=close>row["bb_upper"];bb_short=close<row["bb_lower"]
        if not bb_long and not bb_short:continue
        if row["vol_ratio"]<=1.0:continue
        rzv=row["ratio_zscore"]
        l_sig=bb_long and rzv>1.0;s_sig=bb_short and rzv<-1.0
        if l_sig and len(lpos)<MAX_SAME:lpos.append({"entry":ep,"ei":i,"mf":0,"info":{}})
        if s_sig and len(spos)<MAX_SAME:spos.append({"entry":ep,"ei":i,"mf":0,"info":{}})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    dd=(tdf["pnl"].cumsum()-(tdf["pnl"].cumsum()).cummax()).min()
    sn=len(tdf[tdf["type"]=="SafeNet"])
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),"sn":sn}

print(f"\n  {'Rule':<30s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'CondEx':>7s}")
print(f"  {'-'*72}")

rules=["none","deep_loss_exit","profit_only","trend_check","pnl_positive","breakout_check","ratio_check"]
for rule in rules:
    tdf_r=run_conditional(df,rule)
    s=calc(tdf_r)
    ce=len(tdf_r[tdf_r["type"]=="CondExit"]) if "type" in tdf_r.columns else 0
    flag=" <<<" if s["pnl"]<3000 else (" !!!" if s["pnl"]>3800 else "")
    print(f"  {rule:<30s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} {ce:>7d}{flag}")

# WF for top rules
print(f"\n  Walk-Forward + Rolling:")
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"  {'Rule':<30s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*65}")

for rule in rules:
    tdf_r=run_conditional(df,rule);s=calc(tdf_r)
    to=run_conditional(oos,rule);so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run_conditional(ft,rule)
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {rule:<30s} ${s['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

# ============================================================
print(f"\n{'='*100}")
print("5. Ratio Z-score Magnitude Analysis")
print(f"{'='*100}")

# Does higher |Z| at entry predict better outcomes?
print(f"\n  {'|Z| Range':<15s} {'N':>4s} {'PnL':>8s} {'WR':>6s} {'24h+ %':>8s} {'Avg$':>7s}")
print(f"  {'-'*52}")

for lo_z,hi_z,label in [(1.0,1.5,"1.0-1.5"),(1.5,2.0,"1.5-2.0"),(2.0,3.0,"2.0-3.0"),(3.0,99,"3.0+")]:
    sub=tdf[(tdf["abs_rz"]>=lo_z)&(tdf["abs_rz"]<hi_z)]
    if len(sub)==0:continue
    wr=(sub["pnl"]>0).mean()*100
    pct24=(sub["bars"]>=24).mean()*100
    print(f"  {label:<15s} {len(sub):>4d} ${sub['pnl'].sum():>+6,.0f} {wr:>5.0f}% {pct24:>7.0f}% ${sub['pnl'].mean():>+5.0f}")

print("\nDone.")
