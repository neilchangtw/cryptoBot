"""
拆解四大因素的影響力：進場、止損、出場、TP1
用 432 組合的數據，isolate 每個因素對績效的貢獻
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time as _time
import warnings
warnings.filterwarnings("ignore")

MARGIN = 100

# === 資料 + 指標（跟 trader_backtest 一樣）===
def fetch(interval, days=180):
    all_d = []
    end = int(datetime.now().timestamp()*1000)
    cur = int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur < end:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol":"BTCUSDT","interval":interval,"startTime":cur,"limit":1000}, timeout=10)
            d = r.json()
            if not d: break
            all_d.extend(d); cur = d[-1][0]+1; _time.sleep(0.1)
        except: break
    if not all_d: return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

def add_ind(df):
    tr = pd.DataFrame({"hl":df["high"]-df["low"],
        "hc":abs(df["high"]-df["close"].shift(1)),
        "lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    w=5; sh=pd.Series(np.nan,index=df.index); sl=pd.Series(np.nan,index=df.index)
    for i in range(w, len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max(): sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min(): sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill(); df["swing_low"]=sl.ffill()
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean()
    bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs; df["bb_lower"]=df["bb_mid"]-2*bbs
    df["bb_upper_1.5"]=df["bb_mid"]+1.5*bbs; df["bb_lower_1.5"]=df["bb_mid"]-1.5*bbs
    tp=(df["high"]+df["low"]+df["close"])/3
    ma=tp.rolling(20).mean(); md=tp.rolling(20).apply(lambda x: np.abs(x-x.mean()).mean())
    df["cci"]=(tp-ma)/(0.015*md)
    rsi_min=df["rsi"].rolling(14).min(); rsi_max=df["rsi"].rolling(14).max()
    df["stochrsi"]=(df["rsi"]-rsi_min)/(rsi_max-rsi_min)*100
    df["vol_ma20"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    df["ema9"]=df["close"].ewm(span=9).mean()
    count=0; consec=[]
    for red in (df["close"]<df["open"]): count=count+1 if red else 0; consec.append(count)
    df["consec_red"]=consec
    return df.dropna().reset_index(drop=True)

print("Fetching...", end=" ", flush=True)
raw = fetch("5m", 180)
df = add_ind(raw)
print(f"{len(df)} bars")

# === 信號 + 回測引擎（跟 trader_backtest 完全一樣）===
def get_long_signals(row):
    s = {}
    rsi=row["rsi"]; close=row["close"]; bbl=row["bb_lower"]; bbl15=row["bb_lower_1.5"]
    cci=row.get("cci",0); sr=row.get("stochrsi",50); vr=row.get("vol_ratio",1)
    ap=row.get("atr_pctile",50); cr=row.get("consec_red",0)
    s["1.RSI30+BB"]=rsi<30 and close<bbl
    s["2.RSI30"]=rsi<30
    s["3.RSI25"]=rsi<25
    s["4.RSI20"]=rsi<20
    s["5.BB_lower"]=close<bbl
    s["6.RSI30+BB+Vol"]=rsi<30 and close<bbl and vr>1.5
    s["7.RSI35+BB1.5"]=rsi<35 and close<bbl15
    s["8.CCI+BB"]=cci<-100 and close<bbl
    s["9.3red+RSI40"]=cr>=3 and rsi<40
    s["10.RSI30+loVol"]=rsi<30 and ap<50
    s["11.RSI30+hiVol"]=rsi<30 and ap>50
    s["12.StochRSI20"]=sr<20
    return s

def get_short_signals(row):
    s = {}
    rsi=row["rsi"]; close=row["close"]; bbu=row["bb_upper"]; bbu15=row["bb_upper_1.5"]
    cci=row.get("cci",0); sr=row.get("stochrsi",50); vr=row.get("vol_ratio",1); ap=row.get("atr_pctile",50)
    s["1.RSI70+BB"]=rsi>70 and close>bbu
    s["2.RSI70"]=rsi>70
    s["3.RSI75"]=rsi>75
    s["4.RSI80"]=rsi>80
    s["5.BB_upper"]=close>bbu
    s["6.RSI70+BB+Vol"]=rsi>70 and close>bbu and vr>1.5
    s["7.RSI65+BB1.5"]=rsi>65 and close>bbu15
    s["8.CCI+BB"]=cci>100 and close>bbu
    s["9.RSI70+loVol"]=rsi>70 and ap<50
    s["10.RSI70+hiVol"]=rsi>70 and ap>50
    s["11.StochRSI80"]=sr>80
    s["12.RSI70+BB+CCI"]=rsi>70 and close>bbu and cci>100
    return s

def run(data, long_key, short_key, sl_mode, exit_mode, tp1_mult,
        leverage=20, sl_slip=0.25, max_c=3, sl_cooldown=3):
    lpos=[]; spos=[]; trades=[]
    sl_cool={"long":0,"short":0}
    WARMUP=105
    for i in range(WARMUP, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            if lo<=p["sl"]:
                gap=p["sl"]-lo; ep=p["sl"]-gap*sl_slip
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SL"}); c=True; sl_cool["long"]=i+sl_cooldown
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trhi"]=hi
            elif p["phase"]==2:
                if hi>p["trhi"]: p["trhi"]=hi
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi>65 else bm; sl=max(p["trhi"]-atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if row["close"]<row["ema9"]:
                        ep=row["close"]; pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=p["sl"]
                elif exit_mode=="rsi_revert":
                    if rsi>55:
                        ep=row["close"]; pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=max(p["trhi"]-atr*2.0, p["entry"])
                if not c and lo<=sl:
                    gap=sl-lo; ep=sl-gap*sl_slip; ep=max(ep,p["entry"])
                    pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c: nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            if hi>=p["sl"]:
                gap=hi-p["sl"]; ep=p["sl"]+gap*sl_slip
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SL"}); c=True; sl_cool["short"]=i+sl_cooldown
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trlo"]=lo
            elif p["phase"]==2:
                if lo<p["trlo"]: p["trlo"]=lo
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi<35 else bm; sl=min(p["trlo"]+atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if row["close"]>row["ema9"]:
                        ep=row["close"]; pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=p["sl"]
                elif exit_mode=="rsi_revert":
                    if rsi<45:
                        ep=row["close"]; pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=min(p["trlo"]+atr*2.0, p["entry"])
                if not c and hi>=sl:
                    gap=hi-sl; ep=sl+gap*sl_slip; ep=min(ep,p["entry"])
                    pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c: ns.append(p)
        spos=ns

        ep=nxt["open"]; notional=MARGIN*leverage
        lsigs=get_long_signals(row); ssigs=get_short_signals(row)
        lk_idx=list(lsigs.keys()).index(long_key) if long_key in lsigs else 0
        sk = list(ssigs.keys())[lk_idx] if lk_idx<len(ssigs) else list(ssigs.keys())[0]
        long_go=lsigs.get(long_key,False) and len(lpos)<max_c and i>=sl_cool["long"]
        short_go=ssigs.get(sk,False) and len(spos)<max_c and i>=sl_cool["short"]

        if long_go:
            qty=notional/ep
            if sl_mode=="struct_0.3": slp=row["swing_low"]-atr*0.3
            elif sl_mode=="struct_0.5": slp=row["swing_low"]-atr*0.5
            elif sl_mode=="struct_0": slp=row["swing_low"]
            elif sl_mode=="fixed_2x": slp=ep-atr*2.0
            if slp>=ep: slp=ep-atr*1.5
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,
                "tp1":ep+tp1_mult*atr,"atr":atr,"phase":1,"trhi":ep})
        if short_go:
            qty=notional/ep
            if sl_mode=="struct_0.3": slp=row["swing_high"]+atr*0.3
            elif sl_mode=="struct_0.5": slp=row["swing_high"]+atr*0.5
            elif sl_mode=="struct_0": slp=row["swing_high"]
            elif sl_mode=="fixed_2x": slp=ep+atr*2.0
            if slp<=ep: slp=ep+atr*1.5
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,
                "tp1":ep-tp1_mult*atr,"atr":atr,"phase":1,"trlo":ep})

    tdf=pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type"])
    pnl=tdf["pnl"].sum() if len(tdf)>0 else 0
    n=len(tdf)
    sl_n=len(tdf[tdf["type"]=="SL"]) if len(tdf)>0 else 0
    sl_pnl=tdf[tdf["type"]=="SL"]["pnl"].sum() if sl_n>0 else 0
    tp1_pnl=tdf[tdf["type"]=="TP1"]["pnl"].sum() if len(tdf)>0 else 0
    trail_pnl=tdf[tdf["type"]=="Trail"]["pnl"].sum() if len(tdf)>0 else 0
    return {"pnl":pnl, "n":n, "sl_n":sl_n, "sl_pnl":sl_pnl, "tp1_pnl":tp1_pnl, "trail_pnl":trail_pnl}

# === 因素分析 ===
long_keys = list(get_long_signals(df.iloc[200]).keys())
sl_modes = ["struct_0.3", "struct_0.5", "struct_0", "fixed_2x"]
exit_modes = ["adaptive", "ema9", "rsi_revert"]
tp1_mults = [0.5, 1.0, 1.5]

# 跑所有組合，收集結果
print("Running 432 combos...", flush=True)
all_results = []
count = 0
for lk in long_keys:
    for slm in sl_modes:
        for ex in exit_modes:
            for tp1 in tp1_mults:
                count += 1
                if count % 50 == 0: print(f"  {count}/432...", flush=True)
                r = run(df, lk, lk, slm, ex, tp1)
                all_results.append({"entry":lk, "sl":slm, "exit":ex, "tp1":tp1, **r})

rdf = pd.DataFrame(all_results)

# === 因素影響力分析 ===
print(f"\n{'='*100}")
print("因素影響力分析：哪個因素對績效影響最大？")
print(f"{'='*100}")

# 方法：固定其他 3 個因素，只改 1 個，看 PnL 的變動範圍
print(f"\n--- 方法：每個因素的平均 PnL（跨所有其他因素的組合）---\n")

# 1. 進場信號
print(f"{'='*80}")
print("1. 進場信號影響")
print(f"{'='*80}")
entry_impact = rdf.groupby("entry")["pnl"].agg(["mean","std","min","max","count"])
entry_impact = entry_impact.sort_values("mean", ascending=False)
print(f"\n  {'進場信號':<18s} {'平均PnL':>10s} {'標準差':>10s} {'最差':>10s} {'最好':>10s} {'組合數':>6s}")
print(f"  {'-'*65}")
for idx, row in entry_impact.iterrows():
    print(f"  {idx:<18s} ${row['mean']:>+8,.0f} ${row['std']:>8,.0f} ${row['min']:>+8,.0f} ${row['max']:>+8,.0f} {int(row['count']):>6d}")
entry_range = entry_impact["mean"].max() - entry_impact["mean"].min()

# 2. 止損模式
print(f"\n{'='*80}")
print("2. 止損模式影響")
print(f"{'='*80}")
sl_impact = rdf.groupby("sl")["pnl"].agg(["mean","std","min","max","count"])
sl_impact = sl_impact.sort_values("mean", ascending=False)
print(f"\n  {'止損模式':<18s} {'平均PnL':>10s} {'標準差':>10s} {'最差':>10s} {'最好':>10s}")
print(f"  {'-'*55}")
for idx, row in sl_impact.iterrows():
    print(f"  {idx:<18s} ${row['mean']:>+8,.0f} ${row['std']:>8,.0f} ${row['min']:>+8,.0f} ${row['max']:>+8,.0f}")
sl_range = sl_impact["mean"].max() - sl_impact["mean"].min()

# 3. 出場方式
print(f"\n{'='*80}")
print("3. 出場方式影響")
print(f"{'='*80}")
exit_impact = rdf.groupby("exit")["pnl"].agg(["mean","std","min","max","count"])
exit_impact = exit_impact.sort_values("mean", ascending=False)
print(f"\n  {'出場方式':<18s} {'平均PnL':>10s} {'標準差':>10s} {'最差':>10s} {'最好':>10s}")
print(f"  {'-'*55}")
for idx, row in exit_impact.iterrows():
    print(f"  {idx:<18s} ${row['mean']:>+8,.0f} ${row['std']:>8,.0f} ${row['min']:>+8,.0f} ${row['max']:>+8,.0f}")
exit_range = exit_impact["mean"].max() - exit_impact["mean"].min()

# 4. TP1 距離
print(f"\n{'='*80}")
print("4. TP1 距離影響")
print(f"{'='*80}")
tp1_impact = rdf.groupby("tp1")["pnl"].agg(["mean","std","min","max","count"])
tp1_impact = tp1_impact.sort_values("mean", ascending=False)
print(f"\n  {'TP1距離':<18s} {'平均PnL':>10s} {'標準差':>10s} {'最差':>10s} {'最好':>10s}")
print(f"  {'-'*55}")
for idx, row in tp1_impact.iterrows():
    print(f"  {str(idx)+'x':<18s} ${row['mean']:>+8,.0f} ${row['std']:>8,.0f} ${row['min']:>+8,.0f} ${row['max']:>+8,.0f}")
tp1_range = tp1_impact["mean"].max() - tp1_impact["mean"].min()

# === 總結：影響力排名 ===
print(f"\n{'='*80}")
print("影響力排名（平均 PnL 變動範圍）")
print(f"{'='*80}")

factors = [
    ("進場信號", entry_range),
    ("止損模式", sl_range),
    ("出場方式", exit_range),
    ("TP1距離", tp1_range),
]
factors.sort(key=lambda x: x[1], reverse=True)

print(f"\n  {'排名':>4s} {'因素':<12s} {'影響範圍':>12s} {'佔比':>8s}")
print(f"  {'-'*40}")
total_range = sum(f[1] for f in factors)
for i, (name, rng) in enumerate(factors):
    pct = rng / total_range * 100
    print(f"  {i+1:>4d} {name:<12s} ${rng:>10,.0f} {pct:>7.1f}%")

# === 額外分析：SL 出場的損益拆解 ===
print(f"\n{'='*80}")
print("額外：出場類型損益拆解（所有 432 組合平均）")
print(f"{'='*80}")

avg_sl = rdf["sl_pnl"].mean()
avg_tp1 = rdf["tp1_pnl"].mean()
avg_trail = rdf["trail_pnl"].mean()
avg_total = rdf["pnl"].mean()

print(f"\n  止損(SL)出場平均損益：  ${avg_sl:>+10,.0f}")
print(f"  TP1出場平均損益：       ${avg_tp1:>+10,.0f}")
print(f"  Trail出場平均損益：     ${avg_trail:>+10,.0f}")
print(f"  總平均損益：            ${avg_total:>+10,.0f}")
print(f"\n  止損佔總虧損比例：      {abs(avg_sl)/abs(avg_total)*100:.0f}%")

# === SL 次數 vs 績效 ===
print(f"\n{'='*80}")
print("SL 次數 vs 績效（止損越多越虧？）")
print(f"{'='*80}")

rdf["sl_rate"] = rdf["sl_n"] / rdf["n"] * 100
rdf["sl_rate_bin"] = pd.cut(rdf["sl_rate"], bins=[0, 40, 50, 60, 70, 80, 100])

print(f"\n  {'止損率':<15s} {'組合數':>6s} {'平均PnL':>10s} {'平均止損虧':>12s}")
print(f"  {'-'*45}")
for grp, sub in rdf.groupby("sl_rate_bin", observed=True):
    if len(sub) == 0: continue
    print(f"  {str(grp):<15s} {len(sub):>6d} ${sub['pnl'].mean():>+8,.0f} ${sub['sl_pnl'].mean():>+10,.0f}")

print("\nDone.")
