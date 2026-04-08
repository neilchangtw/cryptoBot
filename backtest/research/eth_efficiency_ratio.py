"""
A4. ETH Kaufman Efficiency Ratio (ER) - 價格效率
ER = |close[0]-close[n]| / sum(|close[i]-close[i-1]|)
0 = 純雜訊, 1 = 完美趨勢

跟 ATR/ADX 的差異：
  ATR = 波動大小  ADX = 有沒有趨勢  ER = 趨勢乾不乾淨
  高 ATR + 低 ER = 大波動但震盪（假突破）
  低 ATR + 高 ER = 小但一致的趨勢（乾淨）

測試：
  S1. ER 作為 Squeeze breakout 品質過濾（ER > threshold）
  S2. ER 作為 regime filter（只在趨勢環境做）
  S3. ER 下降作為新型出場（趨勢死亡偵測）
  S4. ER 獨立進場（ER 突破 + 趨勢方向）
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
print("  A4. ETH Kaufman Efficiency Ratio")
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

# Novel: Kaufman Efficiency Ratio (multiple periods)
for period in [10, 20, 30]:
    direction = abs(df["close"] - df["close"].shift(period))
    volatility = df["close"].diff().abs().rolling(period).sum()
    df[f"er_{period}"] = direction / volatility
    df[f"er_{period}_ma5"] = df[f"er_{period}"].rolling(5).mean()
    df[f"er_{period}_pctile"] = df[f"er_{period}"].rolling(100).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

# ER direction (is price moving up or down efficiently?)
df["er_direction"] = np.sign(df["close"] - df["close"].shift(10))  # +1 = up, -1 = down

df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")

print(f"\nER(10) stats: mean={df['er_10'].mean():.3f} std={df['er_10'].std():.3f} "
      f"median={df['er_10'].median():.3f}")

# ============================================================
# Backtest engine
# ============================================================
def run(data, mode="base", er_period=10, er_thresh=0.3, er_exit=False, min_hold=6):
    """
    mode:
      base       = Squeeze<20 + vol>1.0 + trend + min6h (baseline C4)
      er_filter  = base + ER > threshold (breakout quality)
      er_regime  = base but only when ER percentile > 40 (trending regime)
      er_only    = ER crossing above threshold + trend direction (no squeeze)
      er_replace = Squeeze<20 + ER>thresh 替換 vol>1.0 + trend
    er_exit:
      if True, also exit when ER drops below 0.15 (trend dying)
    """
    er_col=f"er_{er_period}"
    er_pctile_col=f"er_{er_period}_pctile"

    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]
        er_val=row[er_col] if er_col in row.index else 0.5

        # Exits
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf});c=True
            elif er_exit and bars>=min_hold and er_val<0.15:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"ER_Exit","side":"long","bars":bars,"mf":mf});c=True
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
            elif er_exit and bars>=min_hold and er_val<0.15:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-FEE
                trades.append({**p.get("info",{}),"pnl":pnl,"type":"ER_Exit","side":"short","bars":bars,"mf":mf});c=True
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

        elif mode=="er_filter":
            # Base + ER quality filter
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and er_val>er_thresh
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and er_val>er_thresh

        elif mode=="er_regime":
            # Base but only in trending regime
            er_p=row[er_pctile_col] if er_pctile_col in row.index else 50
            l_sig=was_squeeze and close>row["bb_upper"] and row["vol_ratio"]>1.0 and row["trend_up"] and er_p>40
            s_sig=was_squeeze and close<row["bb_lower"] and row["vol_ratio"]>1.0 and not row["trend_up"] and er_p>40

        elif mode=="er_only":
            # ER crossing above threshold + trend (no squeeze)
            prev_er=data.iloc[i-1][er_col] if i>0 else 0
            l_sig=er_val>er_thresh and prev_er<=er_thresh and row["trend_up"]
            s_sig=er_val>er_thresh and prev_er<=er_thresh and not row["trend_up"]

        elif mode=="er_replace":
            # Squeeze + ER replaces vol>1.0
            l_sig=was_squeeze and close>row["bb_upper"] and er_val>er_thresh and row["trend_up"]
            s_sig=was_squeeze and close<row["bb_lower"] and er_val>er_thresh and not row["trend_up"]

        info={"entry_hour":row["datetime"].hour,"er":er_val,
              "er_pctile":row[er_pctile_col] if er_pctile_col in row.index else 50,
              "vol_ratio":row["vol_ratio"],"trend_up":row["trend_up"],
              "dt":row["datetime"],"entry_price":ep}

        if l_sig and len(lpos)<MAX_SAME:
            lpos.append({"entry":ep,"ei":i,"mf":0,"info":info})
        if s_sig and len(spos)<MAX_SAME:
            spos.append({"entry":ep,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"sn":0,"sn_pnl":0,"trail_pnl":0,"er_exit_n":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    sn=tdf[tdf["type"]=="SafeNet"];tr=tdf[tdf["type"]=="Trail"]
    er_exit=tdf[tdf["type"]=="ER_Exit"] if "type" in tdf.columns else pd.DataFrame()
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "sn":len(sn),"sn_pnl":round(sn["pnl"].sum(),2),"trail_pnl":round(tr["pnl"].sum(),2),
            "er_exit_n":len(er_exit)}

# ============================================================
# 1. Full Sample Results
# ============================================================
print(f"\n{'='*100}")
print("1. Full Sample Results")
print(f"{'='*100}")

configs=[
    ("0. Base C4", "base", 10, 0.3, False, 6),
    # ER as quality filter on squeeze
    ("1. ER(10)>0.2 filter", "er_filter", 10, 0.2, False, 6),
    ("2. ER(10)>0.3 filter", "er_filter", 10, 0.3, False, 6),
    ("3. ER(10)>0.4 filter", "er_filter", 10, 0.4, False, 6),
    ("4. ER(20)>0.2 filter", "er_filter", 20, 0.2, False, 6),
    ("5. ER(20)>0.3 filter", "er_filter", 20, 0.3, False, 6),
    # ER regime filter
    ("6. ER regime pctile>40", "er_regime", 10, 0.3, False, 6),
    # ER replaces vol>1.0
    ("7. ER(10)>0.2 替換 vol", "er_replace", 10, 0.2, False, 6),
    ("8. ER(10)>0.3 替換 vol", "er_replace", 10, 0.3, False, 6),
    # ER standalone
    ("9. ER only crossing 0.3", "er_only", 10, 0.3, False, 6),
    ("10. ER only crossing 0.4", "er_only", 10, 0.4, False, 6),
    # ER exit (trend dying)
    ("11. Base + ER exit<0.15", "base", 10, 0.3, True, 6),
    ("12. ER filter + ER exit", "er_filter", 10, 0.3, True, 6),
    # Different periods
    ("13. ER(30)>0.2 filter", "er_filter", 30, 0.2, False, 6),
    ("14. ER(30)>0.3 filter", "er_filter", 30, 0.3, False, 6),
]

print(f"\n  {'Config':<30s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>6s} {'DD':>8s} {'SN':>4s} {'SN$':>8s} {'Trail$':>8s} {'ER_X':>5s}")
print(f"  {'-'*95}")

all_r=[]
for name,mode,period,thresh,er_exit,mh in configs:
    tdf=run(df,mode=mode,er_period=period,er_thresh=thresh,er_exit=er_exit,min_hold=mh)
    s=calc(tdf)
    all_r.append({"name":name,"mode":mode,"period":period,"thresh":thresh,"er_exit":er_exit,"mh":mh,**s})
    flag=" <<<" if s["pnl"]<0 else (" !!!" if s["pnl"]>2000 else "")
    print(f"  {name:<30s} {s['n']:>5d} ${s['pnl']:>+7,.0f} {s['wr']:>5.1f}% {s['pf']:>5.2f} ${s['dd']:>7,.0f} "
          f"{s['sn']:>4d} ${s['sn_pnl']:>+7,.0f} ${s['trail_pnl']:>+7,.0f} {s['er_exit_n']:>5d}{flag}")

# ============================================================
# 2. Win vs Loss: ER at Entry
# ============================================================
print(f"\n{'='*100}")
print("2. ER at Entry: Winners vs Losers vs SafeNet")
print(f"{'='*100}")

tdf_base=run(df,mode="base")
if len(tdf_base)>0 and "er" in tdf_base.columns:
    wins=tdf_base[tdf_base["pnl"]>0]
    losses=tdf_base[tdf_base["pnl"]<=0]
    sn=tdf_base[tdf_base["type"]=="SafeNet"]

    print(f"\n  {'Feature':<20s} {'Winners':>10s} {'Losers':>10s} {'SafeNet':>10s} {'Diff(W-L)':>10s}")
    print(f"  {'-'*65}")
    for col,label in [("er","ER(10)"),("er_pctile","ER pctile"),("vol_ratio","Vol Ratio")]:
        if col in tdf_base.columns:
            wv=wins[col].mean() if len(wins)>0 else 0
            lv=losses[col].mean() if len(losses)>0 else 0
            sv=sn[col].mean() if len(sn)>0 else 0
            print(f"  {label:<20s} {wv:>10.3f} {lv:>10.3f} {sv:>10.3f} {wv-lv:>+10.3f}")

# ============================================================
# 3. Walk-Forward + Rolling
# ============================================================
print(f"\n{'='*100}")
print("3. Walk-Forward + Rolling")
print(f"{'='*100}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'Config':<30s} {'Full':>7s} {'OOS':>7s} {'OOS PF':>7s} {'OOS SN':>6s} {'Roll':>8s} {'Folds':>6s}")
print(f"  {'-'*78}")

for r in all_r[:10]:
    to=run(oos,r["mode"],r["period"],r["thresh"],r["er_exit"],r["mh"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["mode"],r["period"],r["thresh"],r["er_exit"],r["mh"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<30s} ${r['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['pf']:>6.2f} {so['sn']:>6d} "
          f"${sum(fp):>+6,.0f} {prof}/{len(fp)}")

# ============================================================
# 4. ER Distribution
# ============================================================
print(f"\n{'='*100}")
print("4. ER Distribution Analysis")
print(f"{'='*100}")

for p in [10,20,30]:
    col=f"er_{p}"
    print(f"\n  ER({p}): mean={df[col].mean():.3f} P25={df[col].quantile(0.25):.3f} "
          f"P50={df[col].quantile(0.50):.3f} P75={df[col].quantile(0.75):.3f}")

print("\nDone.")
