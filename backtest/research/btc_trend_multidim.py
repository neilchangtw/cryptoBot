"""
1h 趨勢策略 + 多維度確認（資金費率、OI、多空比）
目標：少做、做對、吃大段

策略類型：
  T1. BB Squeeze Breakout + Volume
  T2. EMA20/50 Cross + ADX>25
  T3. Donchian 20 Breakout + Volume
  T4. Supertrend(3) Flip
  T5. Swing High/Low Breakout

出場：全部用 trailing（不用固定 TP）
  - ATR trailing（2x / 3x）
  - EMA20 trailing
  - Supertrend trailing

輔助過濾：
  F0. 無過濾（基線）
  F1. 資金費率（不做過度擁擠的方向）
  F2. 資金費率 + 成交量

1 年資料，嚴格規則
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

def fetch(interval, days=365):
    all_d=[];end=int(datetime.now().timestamp()*1000);cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":"BTCUSDT","interval":interval,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_d.extend(d);cur=d[-1][0]+1;_time.sleep(0.1)
        except:break
    if not all_d:return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

def fetch_funding(days=365):
    all_fr=[];cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000);end=int(datetime.now().timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://fapi.binance.com/fapi/v1/fundingRate",params={"symbol":"BTCUSDT","startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_fr.extend(d);cur=d[-1]["fundingTime"]+1;_time.sleep(0.1)
        except:break
    if not all_fr:return pd.Series(dtype=float)
    fr=pd.DataFrame(all_fr)
    fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    # 8h 填充到 1h
    s=fr.set_index("dt")["rate"].resample("1h").ffill()
    return s

print("Fetching 1h (1 year)...", end=" ", flush=True)
raw=fetch("1h",365);
df=raw.copy()
print(f"{len(df)} bars")

print("Fetching funding rates...", end=" ", flush=True)
fr_series=fetch_funding(365)
print(f"{len(fr_series)} records")

# 指標
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
df["rsi"]=100-100/(1+g/l)
df["ema9"]=df["close"].ewm(span=9).mean()
df["ema20"]=df["close"].ewm(span=20).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df["vol_ma20"]=df["volume"].rolling(20).mean()
df["vol_ratio"]=df["volume"]/df["vol_ma20"]
# Donchian
df["don_hi"]=df["high"].rolling(20).max()
df["don_lo"]=df["low"].rolling(20).min()
# ADX
up_m=df["high"]-df["high"].shift(1);dn_m=df["low"].shift(1)-df["low"]
pdm=pd.Series(np.where((up_m>dn_m)&(up_m>0),up_m,0.0),index=df.index)
mdm=pd.Series(np.where((dn_m>up_m)&(dn_m>0),dn_m,0.0),index=df.index)
s_atr=tr.ewm(alpha=1/14,min_periods=14).mean()
df["plus_di"]=100*pdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
df["minus_di"]=100*mdm.ewm(alpha=1/14,min_periods=14).mean()/s_atr.replace(0,1)
dx=100*(df["plus_di"]-df["minus_di"]).abs()/(df["plus_di"]+df["minus_di"]).replace(0,1)
df["adx"]=dx.ewm(alpha=1/14,min_periods=14).mean()
# Supertrend
hl2=(df["high"]+df["low"])/2
st_up=(hl2+3*df["atr"]).values.copy();st_dn=(hl2-3*df["atr"]).values.copy()
c=df["close"].values;n=len(c);st_dir=np.ones(n)
for i in range(1,n):
    if not np.isnan(st_up[i-1]):
        if st_up[i]>st_up[i-1] and c[i-1]<=st_up[i-1]:st_up[i]=st_up[i-1]
    if not np.isnan(st_dn[i-1]):
        if st_dn[i]<st_dn[i-1] and c[i-1]>=st_dn[i-1]:st_dn[i]=st_dn[i-1]
    if st_dir[i-1]==1: st_dir[i]=-1 if c[i]<st_dn[i] else 1
    else: st_dir[i]=1 if c[i]>st_up[i] else -1
df["st_dir"]=st_dir
# Swing
w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
for i in range(w,len(df)-w):
    if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
    if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
# 資金費率合併
df=df.set_index("datetime")
df["fr"]=fr_series.reindex(df.index,method="ffill")
df["fr_pctile"]=df["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df=df.reset_index()
df=df.dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"Processed: {len(df)} bars")

# ============================================================
# 回測引擎（趨勢型：trailing 出場，不用固定 TP）
# ============================================================
def run(data, entry_fn, trail_mode="atr2", funding_filter=False):
    """
    trail_mode: "atr2" / "atr3" / "ema20" / "supertrend"
    """
    lpos=[];spos=[];trades=[]
    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]

        # 更新多單
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            # 安全網
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            else:
                # trailing
                if hi>p["trhi"]:p["trhi"]=hi
                if trail_mode=="atr2": tsl=p["trhi"]-2*atr
                elif trail_mode=="atr3": tsl=p["trhi"]-3*atr
                elif trail_mode=="ema20": tsl=row["ema20"]
                elif trail_mode=="supertrend":
                    tsl=p["trhi"]-3*atr if row["st_dir"]==1 else close+1  # ST翻空就出
                # 至少等 2 根才開始 trail（避免剛進場就被踢）
                if bars>=2 and close<=tsl:
                    pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        # 更新空單
        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            else:
                if lo<p["trlo"]:p["trlo"]=lo
                if trail_mode=="atr2": tsl=p["trlo"]+2*atr
                elif trail_mode=="atr3": tsl=p["trlo"]+3*atr
                elif trail_mode=="ema20": tsl=row["ema20"]
                elif trail_mode=="supertrend":
                    tsl=p["trlo"]+3*atr if row["st_dir"]==-1 else close-1
                if bars>=2 and close>=tsl:
                    pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        # 進場
        ep=nxt["open"]
        l_sig, s_sig = entry_fn(row, data, i)

        # 資金費率過濾
        if funding_filter:
            fr_p = row.get("fr_pctile", 50)
            if not np.isnan(fr_p):
                if fr_p > 80: l_sig = False  # 費率太高不做多（過度擁擠）
                if fr_p < 20: s_sig = False  # 費率太低不做空

        if l_sig and len(lpos)<MAX_SAME:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"ei":i,"trhi":ep,"trlo":ep})
        if s_sig and len(spos)<MAX_SAME:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"ei":i,"trhi":ep,"trlo":ep})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","dt"])

# ============================================================
# 進場信號
# ============================================================
def _xup(s1,s2,i): return s1.iloc[i]>s2.iloc[i] and s1.iloc[i-1]<=s2.iloc[i-1]
def _first_above(s,val,i): return s.iloc[i]>val and s.iloc[i-1]<=val

def entry_T1(row, data, i):
    """BB Squeeze Breakout + Volume"""
    squeeze = row["bb_width_pctile"] < 20
    was_squeeze = data.iloc[i-1]["bb_width_pctile"] < 20 if i>0 else False
    vol_ok = row["vol_ratio"] > 1.2
    l = was_squeeze and row["close"] > row["bb_upper"] and vol_ok
    s = was_squeeze and row["close"] < row["bb_lower"] and vol_ok
    return l, s

def entry_T2(row, data, i):
    """EMA20/50 Cross + ADX>25"""
    if i < 1: return False, False
    adx_ok = row["adx"] > 25
    l = _xup(data["ema20"], data["ema50"], i) and adx_ok
    s = data["ema20"].iloc[i]<data["ema50"].iloc[i] and data["ema20"].iloc[i-1]>=data["ema50"].iloc[i-1] and adx_ok
    return l, s

def entry_T3(row, data, i):
    """Donchian 20 Breakout + Volume"""
    if i < 1: return False, False
    vol_ok = row["vol_ratio"] > 1.0
    l = row["close"] > data["don_hi"].iloc[i-1] and vol_ok
    s = row["close"] < data["don_lo"].iloc[i-1] and vol_ok
    # first time only
    if l and data["close"].iloc[i-1] > data["don_hi"].iloc[i-2] if i>1 else False: l = False
    if s and data["close"].iloc[i-1] < data["don_lo"].iloc[i-2] if i>1 else False: s = False
    return l, s

def entry_T4(row, data, i):
    """Supertrend(3) Flip"""
    if i < 1: return False, False
    l = row["st_dir"]==1 and data.iloc[i-1]["st_dir"]==-1
    s = row["st_dir"]==-1 and data.iloc[i-1]["st_dir"]==1
    return l, s

def entry_T5(row, data, i):
    """Swing High/Low Breakout"""
    if i < 1: return False, False
    prev_shi = data["swing_hi"].iloc[i-1]
    prev_slo = data["swing_lo"].iloc[i-1]
    l = row["close"] > prev_shi and data["close"].iloc[i-1] <= prev_shi
    s = row["close"] < prev_slo and data["close"].iloc[i-1] >= prev_slo
    return l, s

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"avg_bars":0,"win_avg":0,"loss_avg":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    wa=w["pnl"].mean() if len(w)>0 else 0
    la=l["pnl"].mean() if len(l)>0 else 0
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "avg_bars":round(tdf["bars"].mean(),1),"win_avg":round(wa,2),"loss_avg":round(la,2)}

# ============================================================
# 測試
# ============================================================
entries = [
    ("T1.BB_Squeeze", entry_T1),
    ("T2.EMA_ADX", entry_T2),
    ("T3.Donchian", entry_T3),
    ("T4.Supertrend", entry_T4),
    ("T5.Swing_Break", entry_T5),
]
trails = ["atr2", "atr3", "ema20", "supertrend"]

print(f"\n{'='*130}")
print("1h 趨勢策略：5 進場 x 4 出場 x 2 過濾 = 40 組合")
print(f"{'='*130}")

print(f"\n  {'組合':<35s} {'交易':>5s} {'損益':>8s} {'勝率':>6s} {'PF':>5s} {'回撤':>8s} {'持倉':>6s} "
      f"{'W均':>7s} {'L均':>7s} {'賺賠比':>6s}")
print(f"  {'-'*100}")

all_results = []
for ename, efn in entries:
    for trail in trails:
        for ff, flabel in [(False,""), (True,"+FR")]:
            name = f"{ename}/{trail}{flabel}"
            tdf = run(df, efn, trail_mode=trail, funding_filter=ff)
            s = calc(tdf)
            if s["n"] < 3: continue
            rr = abs(s["win_avg"]/s["loss_avg"]) if s["loss_avg"]!=0 else 999
            all_results.append({"name":name,"entry":ename,"trail":trail,"ff":ff,**s,"rr":round(rr,2)})
            print(f"  {name:<35s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} "
                  f"{s['avg_bars']:>5.0f}h ${s['win_avg']:>+5.0f} ${s['loss_avg']:>+5.0f} {rr:>5.1f}:1")

# WF
print(f"\n{'='*130}")
print("Walk-Forward（前 8 月 / 後 4 月）— Top 10")
print(f"{'='*130}")

all_results.sort(key=lambda x: x["pnl"], reverse=True)
split=int(len(df)*8/12)
dev_df=df.iloc[:split].reset_index(drop=True);oos_df=df.iloc[split:].reset_index(drop=True)

print(f"\n  {'組合':<35s} {'Full':>8s} {'DEV':>8s} {'OOS':>8s} {'OOS WR':>7s} {'OOS PF':>7s} {'OOS N':>6s} {'賺賠比':>6s}")
print(f"  {'-'*85}")

for r in all_results[:10]:
    efn = [e[1] for e in entries if e[0]==r["entry"]][0]
    td=run(dev_df,efn,r["trail"],r["ff"]);to=run(oos_df,efn,r["trail"],r["ff"])
    sd=calc(td);so=calc(to)
    rr_oos = abs(so["win_avg"]/so["loss_avg"]) if so["loss_avg"]!=0 else 999
    print(f"  {r['name']:<35s} ${r['pnl']:>+6,.0f} ${sd['pnl']:>+6,.0f} ${so['pnl']:>+6,.0f} {so['wr']:>6.1f}% "
          f"{so['pf']:>6.2f} {so['n']:>6d} {rr_oos:>5.1f}:1")

# 滾動 WF Top 3
print(f"\n{'='*130}")
print("滾動 WF（3m -> 1m）Top 3")
print(f"{'='*130}")

months=df["month"].unique()
for rank, r in enumerate(all_results[:3]):
    efn = [e[1] for e in entries if e[0]==r["entry"]][0]
    fold_pnls=[]
    for fold in range(len(months)-3):
        test_m=months[fold+3]
        ft=df[df["month"]==test_m].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,efn,r["trail"],r["ff"])
        if len(t)>0:fold_pnls.append(t["pnl"].sum())
        else:fold_pnls.append(0)
    prof=sum(1 for x in fold_pnls if x>0)
    print(f"\n  #{rank+1} {r['name']}: 滾動 ${sum(fold_pnls):+,.0f}, 獲利 {prof}/{len(fold_pnls)} 折")

# 分類摘要
print(f"\n{'='*130}")
print("分類摘要")
print(f"{'='*130}")

for ename, _ in entries:
    sub = [r for r in all_results if r["entry"]==ename]
    if not sub:continue
    best = max(sub, key=lambda x:x["pnl"])
    avg_pnl = np.mean([r["pnl"] for r in sub])
    print(f"\n  {ename}: avg PnL ${avg_pnl:+,.0f}, best = {best['name']} ${best['pnl']:+,.0f} (PF {best['pf']}, RR {best['rr']}:1)")

print("\nDone.")
