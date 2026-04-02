"""
v5.1 微調：在 ATR<ATR_MA50 基礎上，找最佳參數組合
1. ATR MA 週期（30/40/50/60/80）
2. + BB 寬度組合
3. TP1 距離（0.75/1.0/1.25/1.5/1.75）
4. TimeStop（4h/6h/8h/10h/12h）
全排列太多，分階段測試
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; MAX_SAME=2

def fetch(interval,days=365):
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

def add_ind(df):
    tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"]=tr.rolling(14).mean()
    df["atr_pctile"]=df["atr"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    d=df["close"].diff();g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean();l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs;df["bb_lower"]=df["bb_mid"]-2*bbs
    df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]*100
    df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    # 多種 ATR MA
    for p in [30,40,50,60,80]:
        df[f"atr_ma{p}"]=df["atr"].rolling(p).mean()
    return df.dropna().reset_index(drop=True)

print("Fetching 1 year...", end=" ", flush=True)
raw5=fetch("5m",365);df5=add_ind(raw5)
raw1h=fetch("1h",365);df1h=add_ind(raw1h)
m=df1h[["datetime","rsi"]].copy();m.rename(columns={"rsi":"h1_rsi"},inplace=True)
m["h1_rsi_prev"]=m["h1_rsi"].shift(1);m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
df5["month"]=df5["datetime"].dt.to_period("M")
print(f"{len(df5)} bars")

def run(data, tp1_mult=1.25, ts_bars=96, atr_ma_period=50, bb_filter=None):
    lpos=[];spos=[];trades=[]
    atr_ma_col = f"atr_ma{atr_ma_period}"
    for i in range(120,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            elif bars>=ts_bars:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            elif bars>=ts_bars:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        # ATR 趨勢過濾
        atr_ma_val = row.get(atr_ma_col, row["atr"])
        if pd.isna(atr_ma_val): atr_ma_val = row["atr"]
        atr_ok = atr <= atr_ma_val

        # BB 寬度過濾
        bb_ok = True
        if bb_filter is not None:
            bb_ok = row["bb_width_pctile"] < bb_filter

        base_long=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
                   and row["h1_rsi"]>=row["h1_rsi_prev"])
        base_short=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
                    and row["h1_rsi"]<=row["h1_rsi_prev"])

        l_go = base_long and atr_ok and bb_ok and len(lpos)<MAX_SAME
        s_go = base_short and atr_ok and bb_ok and len(spos)<MAX_SAME

        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+tp1_mult*atr,"ei":i})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-tp1_mult*atr,"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","dt"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"tp1":0,"ts":0,"sn":0,"ts_loss":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "tp1":len(tdf[tdf["type"]=="TP1"]),"ts":len(tdf[tdf["type"]=="TimeStop"]),
            "sn":len(tdf[tdf["type"]=="SafeNet"]),
            "ts_loss":round(tdf[tdf["type"]=="TimeStop"]["pnl"].sum(),2)}

# WF 分割
split=int(len(df5)*(8/12))
dev=df5.iloc[:split].reset_index(drop=True)
oos=df5.iloc[split:].reset_index(drop=True)
months=df5["month"].unique()

def test_config(name, tp1m, tsb, atr_ma, bb_f):
    """跑全樣本 + OOS + 滾動 WF"""
    tdf = run(df5, tp1_mult=tp1m, ts_bars=tsb, atr_ma_period=atr_ma, bb_filter=bb_f)
    full = calc(tdf)
    td=run(dev, tp1m, tsb, atr_ma, bb_f); to=run(oos, tp1m, tsb, atr_ma, bb_f)
    s_oos = calc(to)
    # 滾動
    fold_pnls=[]
    for fold in range(len(months)-3):
        test_m=months[fold+3]
        ft=df5[df5["month"]==test_m].reset_index(drop=True)
        if len(ft)<50:continue
        t=run(ft,tp1m,tsb,atr_ma,bb_f)
        if len(t)>0:fold_pnls.append(t["pnl"].sum())
    roll_sum=sum(fold_pnls);roll_prof=sum(1 for x in fold_pnls if x>0);roll_n=len(fold_pnls)
    return {"name":name,"full":full,"oos":s_oos,"roll_sum":roll_sum,"roll_prof":roll_prof,"roll_n":roll_n}

# ============================================================
# 1. ATR MA 週期
# ============================================================
print(f"\n{'='*110}")
print("1. ATR MA 週期微調（TP1=1.25x, TimeStop=8h）")
print(f"{'='*110}")

print(f"\n  {'MA週期':<8s} {'交易':>5s} {'全樣本':>8s} {'PF':>5s} {'OOS':>8s} {'OOS PF':>7s} {'滾動WF':>8s} {'折':>5s} {'TS':>4s} {'TS虧':>8s}")
print(f"  {'-'*75}")

for ma in [30, 40, 50, 60, 80]:
    r = test_config(f"MA{ma}", 1.25, 96, ma, None)
    f=r["full"];o=r["oos"]
    print(f"  MA{ma:<5d} {f['n']:>5d} ${f['pnl']:>+6,.0f} {f['pf']:>4.2f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']} {f['ts']:>4d} ${f['ts_loss']:>+7,.0f}")

# ============================================================
# 2. + BB 寬度組合
# ============================================================
print(f"\n{'='*110}")
print("2. ATR MA50 + BB 寬度過濾組合")
print(f"{'='*110}")

print(f"\n  {'BB過濾':<12s} {'交易':>5s} {'全樣本':>8s} {'PF':>5s} {'OOS':>8s} {'OOS PF':>7s} {'滾動WF':>8s} {'折':>5s}")
print(f"  {'-'*60}")

for bb in [None, 80, 70, 60, 50]:
    label = f"BB<{bb}" if bb else "None"
    r = test_config(f"BB{bb or 'None'}", 1.25, 96, 50, bb)
    f=r["full"];o=r["oos"]
    print(f"  {label:<12s} {f['n']:>5d} ${f['pnl']:>+6,.0f} {f['pf']:>4.2f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']}")

# ============================================================
# 3. TP1 距離微調
# ============================================================
print(f"\n{'='*110}")
print("3. TP1 距離微調（ATR MA50, TimeStop=8h）")
print(f"{'='*110}")

print(f"\n  {'TP1':<8s} {'交易':>5s} {'全樣本':>8s} {'PF':>5s} {'OOS':>8s} {'OOS PF':>7s} {'滾動WF':>8s} {'折':>5s} {'TP1筆':>5s} {'TS筆':>4s}")
print(f"  {'-'*70}")

for tp1 in [0.75, 1.0, 1.1, 1.25, 1.5, 1.75]:
    r = test_config(f"TP{tp1}", tp1, 96, 50, None)
    f=r["full"];o=r["oos"]
    print(f"  {tp1:.2f}x   {f['n']:>5d} ${f['pnl']:>+6,.0f} {f['pf']:>4.2f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']} {f['tp1']:>5d} {f['ts']:>4d}")

# ============================================================
# 4. TimeStop 微調
# ============================================================
print(f"\n{'='*110}")
print("4. TimeStop 時間微調（ATR MA50, TP1=1.25x）")
print(f"{'='*110}")

print(f"\n  {'TimeStop':<10s} {'交易':>5s} {'全樣本':>8s} {'PF':>5s} {'OOS':>8s} {'OOS PF':>7s} {'滾動WF':>8s} {'折':>5s} {'TS筆':>4s} {'TS虧':>8s}")
print(f"  {'-'*75}")

for hours in [4, 6, 8, 10, 12]:
    bars = hours * 12
    r = test_config(f"TS{hours}h", 1.25, bars, 50, None)
    f=r["full"];o=r["oos"]
    print(f"  {hours}h        {f['n']:>5d} ${f['pnl']:>+6,.0f} {f['pf']:>4.2f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']} {f['ts']:>4d} ${f['ts_loss']:>+7,.0f}")

# ============================================================
# 5. 最佳組合搜索（從上面各維度的 Top 2 組合）
# ============================================================
print(f"\n{'='*110}")
print("5. 最佳組合搜索")
print(f"{'='*110}")

best_combos = []
for ma in [40, 50, 60]:
    for tp1 in [1.0, 1.25, 1.5]:
        for ts_h in [6, 8, 10]:
            for bb in [None, 70]:
                ts_b = ts_h * 12
                r = test_config(f"MA{ma}/TP{tp1}/TS{ts_h}h/BB{bb or '-'}", tp1, ts_b, ma, bb)
                best_combos.append(r)

best_combos.sort(key=lambda x: x["roll_sum"], reverse=True)

print(f"\n  Top 15 by 滾動 WF：")
print(f"  {'#':>2s} {'組合':<30s} {'全樣本':>8s} {'OOS':>8s} {'OOS PF':>7s} {'滾動WF':>8s} {'折':>5s} {'TS':>4s}")
print(f"  {'-'*80}")

for i, r in enumerate(best_combos[:15]):
    f=r["full"];o=r["oos"]
    print(f"  {i+1:>2d} {r['name']:<30s} ${f['pnl']:>+6,.0f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']} {f['ts']:>4d}")

print(f"\n  Top 5 by OOS PnL：")
best_by_oos = sorted(best_combos, key=lambda x: x["oos"]["pnl"], reverse=True)
for i, r in enumerate(best_by_oos[:5]):
    f=r["full"];o=r["oos"]
    print(f"  {i+1:>2d} {r['name']:<30s} ${f['pnl']:>+6,.0f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']}")

print(f"\n  Top 5 by 全樣本 PnL：")
best_by_full = sorted(best_combos, key=lambda x: x["full"]["pnl"], reverse=True)
for i, r in enumerate(best_by_full[:5]):
    f=r["full"];o=r["oos"]
    print(f"  {i+1:>2d} {r['name']:<30s} ${f['pnl']:>+6,.0f} ${o['pnl']:>+6,.0f} {o['pf']:>6.2f} "
          f"${r['roll_sum']:>+6,.0f} {r['roll_prof']}/{r['roll_n']}")

print("\nDone.")
