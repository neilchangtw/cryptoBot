"""
v5 深度分析 — 站在資料分析師角度拆解每一個環節
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; SAFENET_PCT=0.03; TP1_ATR_MULT=1.5; TIME_STOP_BARS=96; MAX_SAME=2

def fetch(interval,days=180):
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
    df["ema9"]=df["close"].ewm(span=9).mean()
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    df["vol_ma20"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/df["vol_ma20"]
    count=0;consec=[]
    for red in (df["close"]<df["open"]):count=count+1 if red else 0;consec.append(count)
    df["consec_red"]=consec
    return df.dropna().reset_index(drop=True)

print("Fetching...", end=" ", flush=True)
raw5=fetch("5m",180);df5=add_ind(raw5)
raw1h=fetch("1h",180);df1h=add_ind(raw1h)
m=df1h[["datetime","rsi"]].copy();m.rename(columns={"rsi":"h1_rsi"},inplace=True)
m["h1_rsi_prev"]=m["h1_rsi"].shift(1);m["hour_key"]=m["datetime"].dt.floor("h")+timedelta(hours=1)
df5["hour_key"]=df5["datetime"].dt.floor("h")
df5=df5.merge(m[["hour_key","h1_rsi","h1_rsi_prev"]],on="hour_key",how="left")
df5=df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
print(f"{len(df5)} bars")

def run_detail(data):
    lpos=[];spos=[];trades=[]
    for i in range(105,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        atr=row["atr"];hi=row["high"];lo=row["low"];close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]

        nl=[]
        for p in lpos:
            c=False; bars=i-p["ei"]
            sn_sl=p["entry"]*(1-SAFENET_PCT)
            # 追蹤最大浮盈
            max_fav=max(p.get("mf",0),(hi-p["entry"])*p["qty"])
            p["mf"]=max_fav
            if lo<=sn_sl:
                gap=sn_sl-lo;ep=sn_sl-gap*0.25
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":p["mf"],"exit":ep}); c=True
            elif close>=p["tp1"]:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"long","bars":bars,"mf":p["mf"],"exit":close}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"long","bars":bars,"mf":p["mf"],"exit":close}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False; bars=i-p["ei"]
            sn_sl=p["entry"]*(1+SAFENET_PCT)
            max_fav=max(p.get("mf",0),(p["entry"]-lo)*p["qty"])
            p["mf"]=max_fav
            if hi>=sn_sl:
                gap=hi-sn_sl;ep=sn_sl+gap*0.25
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":p["mf"],"exit":ep}); c=True
            elif close<=p["tp1"]:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TP1","side":"short","bars":bars,"mf":p["mf"],"exit":close}); c=True
            elif bars>=TIME_STOP_BARS:
                pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                trades.append({**p["info"],"pnl":pnl,"type":"TimeStop","side":"short","bars":bars,"mf":p["mf"],"exit":close}); c=True
            if not c:ns.append(p)
        spos=ns

        ep=nxt["open"]
        info={"entry_rsi":rsi,"entry_atr":atr,"entry_ap":ap,"entry_ema21_dev":row["price_vs_ema21"],
              "entry_vol":row["vol_ratio"],"entry_consec_red":row["consec_red"],
              "entry_hour":row["datetime"].hour,"entry_weekday":row["datetime"].weekday(),
              "h1_rsi":row["h1_rsi"],"h1_rsi_prev":row["h1_rsi_prev"],
              "entry_price":ep,"dt":row["datetime"]}

        l_go=(rsi<30 and close<row["bb_lower"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]>=row["h1_rsi_prev"] and len(lpos)<MAX_SAME)
        s_go=(rsi>70 and close>row["bb_upper"] and ap<=75 and abs(row["price_vs_ema21"])<2
              and row["h1_rsi"]<=row["h1_rsi_prev"] and len(spos)<MAX_SAME)

        if l_go:
            qty=(MARGIN*LEVERAGE)/ep
            lpos.append({"entry":ep,"qty":qty,"tp1":ep+TP1_ATR_MULT*atr,"ei":i,"mf":0,"info":info})
        if s_go:
            qty=(MARGIN*LEVERAGE)/ep
            spos.append({"entry":ep,"qty":qty,"tp1":ep-TP1_ATR_MULT*atr,"ei":i,"mf":0,"info":info})

    return pd.DataFrame(trades)

print("Running...", flush=True)
tdf = run_detail(df5)
print(f"Trades: {len(tdf)}")

# ============================================================
# 分析
# ============================================================
total_pnl = tdf["pnl"].sum()
total_fee = sum(2 * 0.0004 * t["entry_price"] * (MARGIN*LEVERAGE/t["entry_price"]) for _,t in tdf.iterrows())

print(f"\n{'='*100}")
print("1. 總收益拆解")
print(f"{'='*100}")
print(f"\n  總損益：${total_pnl:+,.2f}")
print(f"  總手續費：${total_fee:,.2f}")
print(f"  不含手續費損益：${total_pnl+total_fee:+,.2f}")
print(f"  手續費佔比：{total_fee/(total_pnl+total_fee)*100:.1f}%")

for t in ["TP1","TimeStop","SafeNet"]:
    sub=tdf[tdf["type"]==t]
    if len(sub)==0:continue
    print(f"\n  {t}：")
    print(f"    筆數：{len(sub)}（{len(sub)/len(tdf)*100:.1f}%）")
    print(f"    損益：${sub['pnl'].sum():+,.2f}")
    print(f"    平均：${sub['pnl'].mean():+,.2f}")
    print(f"    勝率：{(sub['pnl']>0).mean()*100:.1f}%")
    print(f"    平均持倉：{sub['bars'].mean():.1f} bars（{sub['bars'].mean()*5:.0f} 分鐘）")

print(f"\n{'='*100}")
print("2. TP1 深度分析（主要收入來源）")
print(f"{'='*100}")

tp1 = tdf[tdf["type"]=="TP1"]
if len(tp1)>0:
    tp1_pnl_dist = tp1["pnl"]
    print(f"\n  TP1 損益分佈：")
    print(f"    平均：${tp1_pnl_dist.mean():+,.2f}")
    print(f"    中位數：${tp1_pnl_dist.median():+,.2f}")
    print(f"    最小：${tp1_pnl_dist.min():+,.2f}")
    print(f"    最大：${tp1_pnl_dist.max():+,.2f}")
    print(f"    標準差：${tp1_pnl_dist.std():,.2f}")

    # TP1 到達速度
    print(f"\n  TP1 到達速度：")
    print(f"    平均：{tp1['bars'].mean():.1f} bars（{tp1['bars'].mean()*5:.0f} 分鐘）")
    print(f"    中位數：{tp1['bars'].median():.0f} bars（{tp1['bars'].median()*5:.0f} 分鐘）")
    print(f"    最快：{tp1['bars'].min()} bars（{tp1['bars'].min()*5} 分鐘）")
    print(f"    最慢：{tp1['bars'].max()} bars（{tp1['bars'].max()*5} 分鐘）")

    # TP1 按持倉時間分組
    print(f"\n  TP1 按速度分組：")
    for lo,hi,label in [(0,6,"<30min"),(6,12,"30-60min"),(12,24,"1-2h"),(24,48,"2-4h"),(48,96,"4-8h")]:
        sub = tp1[(tp1["bars"]>=lo)&(tp1["bars"]<hi)]
        if len(sub)>0:
            print(f"    {label:<10s}: {len(sub):>4d} 筆, 平均 ${sub['pnl'].mean():+,.2f}")

    # 最大浮盈 vs 實際（TP1 應該沒什麼回吐因為全平）
    if "mf" in tp1.columns:
        print(f"\n  TP1 最大浮盈 vs 實際：")
        print(f"    平均最大浮盈：${tp1['mf'].mean():,.2f}")
        print(f"    平均實際獲利：${tp1['pnl'].mean():+,.2f}")
        print(f"    = TP1 全平時已經鎖住了大部分利潤")

print(f"\n{'='*100}")
print("3. TimeStop 深度分析（主要虧損來源）")
print(f"{'='*100}")

ts = tdf[tdf["type"]=="TimeStop"]
if len(ts)>0:
    print(f"\n  TimeStop 損益分佈：")
    print(f"    平均虧損：${ts['pnl'].mean():+,.2f}")
    print(f"    最大虧損：${ts['pnl'].min():+,.2f}")
    print(f"    最小虧損：${ts['pnl'].max():+,.2f}（最接近打平的）")

    # TimeStop 的最大浮盈 — 這些單子曾經有多接近 TP1？
    if "mf" in ts.columns:
        print(f"\n  TimeStop 曾經最接近獲利多少？")
        print(f"    平均最大浮盈：${ts['mf'].mean():,.2f}")
        print(f"    最大浮盈最高：${ts['mf'].max():,.2f}")
        print(f"    浮盈 = 0 的筆數：{(ts['mf']<=0.1).sum()}（完全沒往對的方向走）")
        approached_tp1 = ts[ts["mf"]>3]  # 曾經浮盈 > $3（接近 TP1）
        print(f"    曾經接近 TP1（浮盈>$3）：{len(approached_tp1)} 筆")

    # TimeStop 的進場特徵
    print(f"\n  TimeStop vs TP1 進場特徵差異：")
    for col, label in [("entry_rsi","RSI"),("entry_ap","ATR pctile"),
                        ("entry_ema21_dev","EMA21偏離%"),("entry_vol","成交量比"),
                        ("entry_hour","小時"),("h1_rsi","1h RSI")]:
        if col in ts.columns and col in tp1.columns:
            ts_mean = ts[col].mean()
            tp1_mean = tp1[col].mean()
            print(f"    {label:<12s}: TimeStop {ts_mean:>8.2f} vs TP1 {tp1_mean:>8.2f} (diff {ts_mean-tp1_mean:+.2f})")

    # 多空比例
    ts_long = ts[ts["side"]=="long"]; ts_short = ts[ts["side"]=="short"]
    print(f"\n  TimeStop 多空：")
    print(f"    做多：{len(ts_long)} 筆 (${ts_long['pnl'].sum():+,.2f})")
    print(f"    做空：{len(ts_short)} 筆 (${ts_short['pnl'].sum():+,.2f})")

print(f"\n{'='*100}")
print("4. SafeNet 分析")
print(f"{'='*100}")

sn = tdf[tdf["type"]=="SafeNet"]
if len(sn)>0:
    print(f"\n  只有 {len(sn)} 筆觸發：")
    for _,t in sn.iterrows():
        print(f"    {t['side']} @ {t.get('entry_price',0):.0f}, 虧 ${t['pnl']:+,.2f}, "
              f"持 {t['bars']} bars, RSI {t.get('entry_rsi',0):.1f}")
else:
    print(f"\n  沒有 SafeNet 觸發")

print(f"\n{'='*100}")
print("5. 時段分析")
print(f"{'='*100}")

print(f"\n  {'小時(UTC+8)':<12s} {'TP1':>5s} {'TS':>5s} {'SN':>5s} {'淨損益':>10s} {'TP1率':>8s}")
print(f"  {'-'*50}")
for h in range(24):
    sub = tdf[tdf["entry_hour"]==h]
    if len(sub)==0:continue
    t1=len(sub[sub["type"]=="TP1"]); ts_n=len(sub[sub["type"]=="TimeStop"])
    sn_n=len(sub[sub["type"]=="SafeNet"])
    net=sub["pnl"].sum()
    tp1_rate=t1/len(sub)*100
    flag = " <<<" if net < -20 else (" !!!" if net > 20 else "")
    print(f"  {h:02d}:00       {t1:>5d} {ts_n:>5d} {sn_n:>5d} ${net:>+8,.2f} {tp1_rate:>7.0f}%{flag}")

print(f"\n{'='*100}")
print("6. TP1 距離分析（1.5x ATR 是否最佳？）")
print(f"{'='*100}")

# 模擬不同 TP1 距離
print(f"\n  如果 TP1 距離不同，結果會怎樣？")
for tp_mult in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]:
    # 簡單估算：看 TP1 的最大浮盈是否夠到不同 TP1 距離
    # 用全部交易的 mf 欄位
    tp1_count = 0; ts_count = 0; sn_count = 0
    total = 0
    for _, t in tdf.iterrows():
        atr = t.get("entry_atr", 150)
        tp_dist = tp_mult * atr * (MARGIN*LEVERAGE) / t.get("entry_price", 80000)
        if t["mf"] >= tp_dist:
            tp1_count += 1
            total += tp_dist - (MARGIN*LEVERAGE) * 0.0004 * 2
        elif t["type"] == "SafeNet":
            sn_count += 1
            total += t["pnl"]
        else:
            ts_count += 1
            total += t["pnl"]  # TimeStop 虧損不變
    mark = " <-- 目前" if tp_mult == 1.5 else ""
    print(f"    TP1 {tp_mult:.1f}x ATR: TP1 {tp1_count:>4d} / TS {ts_count:>3d} / SN {sn_count:>2d} | "
          f"估算淨損益 ${total:>+8,.0f}{mark}")

print(f"\n{'='*100}")
print("7. 時間止損距離分析（8h 是否最佳？）")
print(f"{'='*100}")

# 看 TimeStop 的持倉期間盈虧走勢
if len(ts)>0:
    print(f"\n  TimeStop 的持倉中間狀態：")
    print(f"    平均最大浮盈：${ts['mf'].mean():,.2f}")
    print(f"    有 {(ts['mf']>0.5).sum()}/{len(ts)} 筆曾經有浮盈但沒到 TP1")
    print(f"    有 {(ts['mf']<0.1).sum()}/{len(ts)} 筆完全沒浮盈（一直在虧）")

    # 如果時間止損更短/更長
    print(f"\n  不同時間止損的影響（估算）：")
    for hours in [2, 4, 6, 8, 12, 16, 24]:
        bars_limit = hours * 12  # 5m bars per hour
        # 看 TP1 在 bars_limit 內到達的比例
        tp1_in_time = len(tp1[tp1["bars"] <= bars_limit])
        tp1_miss = len(tp1[tp1["bars"] > bars_limit])
        mark = " <-- 目前" if hours == 8 else ""
        print(f"    {hours:>2d}h: TP1 在時限內 {tp1_in_time:>4d} / 超時 {tp1_miss:>3d}{mark}")

print(f"\n{'='*100}")
print("8. 過濾器效果分析")
print(f"{'='*100}")

# 每個過濾器各擋了多少信號
print(f"\n  在沒有過濾的情況下有多少信號？")
base_long = ((df5["rsi"]<30) & (df5["close"]<df5["bb_lower"])).sum()
base_short = ((df5["rsi"]>70) & (df5["close"]>df5["bb_upper"])).sum()
print(f"    RSI+BB 做多信號：{base_long}")
print(f"    RSI+BB 做空信號：{base_short}")
print(f"    合計：{base_long+base_short}")
print(f"    過濾後實際進場：{len(tdf)}")
print(f"    過濾掉：{base_long+base_short-len(tdf)} ({(1-len(tdf)/(base_long+base_short))*100:.0f}%)")

# 各過濾器的擋掉比例
f1 = ((df5["rsi"]<30)&(df5["close"]<df5["bb_lower"])&(df5["atr_pctile"]<=75)).sum()
f2 = ((df5["rsi"]<30)&(df5["close"]<df5["bb_lower"])&(abs(df5["price_vs_ema21"])<2)).sum()
f3_possible = ((df5["rsi"]<30)&(df5["close"]<df5["bb_lower"])&(df5["h1_rsi"]>=df5["h1_rsi_prev"])).sum()
print(f"\n  各過濾器通過率（做多）：")
print(f"    ATR pctile ≤ 75：{f1}/{base_long} ({f1/base_long*100:.0f}%)")
print(f"    |EMA21偏離| < 2%：{f2}/{base_long} ({f2/base_long*100:.0f}%)")
print(f"    1h RSI 方向確認：{f3_possible}/{base_long} ({f3_possible/base_long*100:.0f}%)")

print(f"\n{'='*100}")
print("SUMMARY: 優化建議")
print(f"{'='*100}")

print(f"""
  === 收益結構 ===
  TP1 貢獻：+${tp1['pnl'].sum():,.0f}（{len(tp1)} 筆 × ${tp1['pnl'].mean():+,.1f}/筆）
  TimeStop 消耗：-${abs(ts['pnl'].sum()):,.0f}（{len(ts)} 筆 × ${ts['pnl'].mean():+,.1f}/筆）
  SafeNet 消耗：-${abs(sn['pnl'].sum()):,.0f}（{len(sn)} 筆）
  手續費消耗：-${total_fee:,.0f}
  淨利：${total_pnl:+,.0f}

  TP1 / TimeStop 比 = {len(tp1)}/{len(ts)} = {len(tp1)/max(len(ts),1):.1f}:1
  每做對 {len(tp1)/max(len(ts),1):.1f} 筆才虧 1 筆
""")

print("Done.")
