"""
v5 實際程式邏輯回測 — 完全對齊 cryptoBot v5

進場：RSI<30 + BB Lower + 3 過濾（ATR pctile ≤ 75, |偏離 EMA21| < 2%, 1h RSI 方向）
SL：安全網 ±3%（STOP_MARKET，25% 滑價）
TP1：1.5x ATR → 全平 100%（不是部分）
時間止損：8h（96 根 5m bar）未到 TP1 → 全平（用 close，限價 0 滑價）
沒有 Phase 2 Trail
Max 同方向：2 單
進場用 next-open
指標用 iloc[-2]
1h RSI 延遲一根
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN = 100; LEVERAGE = 20
SAFENET_PCT = 0.03
TP1_ATR_MULT = 1.5
TIME_STOP_BARS = 96  # 8h = 96 x 5m
MAX_SAME = 2

def fetch(interval, days=180):
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
    df["ema21"]=df["close"].ewm(span=21).mean()
    df["price_vs_ema21"]=(df["close"]-df["ema21"])/df["ema21"]*100
    return df.dropna().reset_index(drop=True)

print("Fetching 5m...", end=" ", flush=True)
raw5 = fetch("5m", 180); df5 = add_ind(raw5); print(f"{len(df5)} bars")

print("Fetching 1h...", end=" ", flush=True)
raw1h = fetch("1h", 180); df1h = add_ind(raw1h)
# 1h RSI 延遲一根
df1h_map = df1h[["datetime","rsi"]].copy()
df1h_map.rename(columns={"rsi":"h1_rsi"}, inplace=True)
df1h_map["h1_rsi_prev"] = df1h_map["h1_rsi"].shift(1)
df1h_map["hour_key"] = df1h_map["datetime"].dt.floor("h") + timedelta(hours=1)
df5["hour_key"] = df5["datetime"].dt.floor("h")
df5 = df5.merge(df1h_map[["hour_key","h1_rsi","h1_rsi_prev"]], on="hour_key", how="left")
df5 = df5.dropna(subset=["h1_rsi"]).reset_index(drop=True)
print(f"Aligned: {len(df5)} bars")

# ============================================================
# 回測引擎
# ============================================================
def run(data):
    lpos = []; spos = []; trades = []

    for i in range(105, len(data)-1):
        row = data.iloc[i]; nxt = data.iloc[i+1]
        atr = row["atr"]; hi = row["high"]; lo = row["low"]; close = row["close"]
        ap = row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi = row["rsi"]

        # --- 更新多單 ---
        nl = []
        for p in lpos:
            c = False
            bars_held = i - p["entry_i"]

            # 安全網 SL（STOP_MARKET，25% 滑價）
            safenet_sl = p["entry"] * (1 - SAFENET_PCT)
            if lo <= safenet_sl:
                gap = safenet_sl - lo
                ep = safenet_sl - gap * 0.25
                pnl = (ep - p["entry"]) * p["qty"] - ep * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"SafeNet", "side":"long",
                               "bars":bars_held, "entry":p["entry"], "exit":ep,
                               "dt":row["datetime"]}); c = True

            # TP1：close >= entry + 1.5x ATR → 全平 100%（限價，0 滑價）
            elif close >= p["tp1"]:
                pnl = (close - p["entry"]) * p["qty"] - close * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"TP1", "side":"long",
                               "bars":bars_held, "entry":p["entry"], "exit":close,
                               "dt":row["datetime"]}); c = True

            # 時間止損：8h（96 根）未到 TP1 → 全平（限價，0 滑價）
            elif bars_held >= TIME_STOP_BARS:
                pnl = (close - p["entry"]) * p["qty"] - close * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"TimeStop", "side":"long",
                               "bars":bars_held, "entry":p["entry"], "exit":close,
                               "dt":row["datetime"]}); c = True

            if not c: nl.append(p)
        lpos = nl

        # --- 更新空單 ---
        ns = []
        for p in spos:
            c = False
            bars_held = i - p["entry_i"]

            safenet_sl = p["entry"] * (1 + SAFENET_PCT)
            if hi >= safenet_sl:
                gap = hi - safenet_sl
                ep = safenet_sl + gap * 0.25
                pnl = (p["entry"] - ep) * p["qty"] - ep * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"SafeNet", "side":"short",
                               "bars":bars_held, "entry":p["entry"], "exit":ep,
                               "dt":row["datetime"]}); c = True

            elif close <= p["tp1"]:
                pnl = (p["entry"] - close) * p["qty"] - close * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"TP1", "side":"short",
                               "bars":bars_held, "entry":p["entry"], "exit":close,
                               "dt":row["datetime"]}); c = True

            elif bars_held >= TIME_STOP_BARS:
                pnl = (p["entry"] - close) * p["qty"] - close * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl, "type":"TimeStop", "side":"short",
                               "bars":bars_held, "entry":p["entry"], "exit":close,
                               "dt":row["datetime"]}); c = True

            if not c: ns.append(p)
        spos = ns

        # --- 進場 ---
        ep = nxt["open"]

        # 做多 5 個條件
        long_go = (row["rsi"] < 30
                   and row["close"] < row["bb_lower"]
                   and ap <= 75
                   and abs(row["price_vs_ema21"]) < 2
                   and row["h1_rsi"] >= row["h1_rsi_prev"]
                   and len(lpos) < MAX_SAME)

        # 做空 5 個條件
        short_go = (row["rsi"] > 70
                    and row["close"] > row["bb_upper"]
                    and ap <= 75
                    and abs(row["price_vs_ema21"]) < 2
                    and row["h1_rsi"] <= row["h1_rsi_prev"]
                    and len(spos) < MAX_SAME)

        if long_go:
            qty = (MARGIN * LEVERAGE) / ep
            lpos.append({"entry":ep, "qty":qty, "tp1":ep + TP1_ATR_MULT * atr, "entry_i":i})

        if short_go:
            qty = (MARGIN * LEVERAGE) / ep
            spos.append({"entry":ep, "qty":qty, "tp1":ep - TP1_ATR_MULT * atr, "entry_i":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","entry","exit","dt"])

# ============================================================
# 全樣本
# ============================================================
print("\nRunning full sample...", flush=True)
tdf = run(df5)

print(f"\n{'='*100}")
print("v5 程式邏輯回測結果（完全對齊實際程式）")
print(f"{'='*100}")

if len(tdf) > 0:
    pnl = tdf["pnl"].sum(); wr = (tdf["pnl"]>0).mean()*100
    w = tdf[tdf["pnl"]>0]; l = tdf[tdf["pnl"]<=0]
    pf = w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum = tdf["pnl"].cumsum(); dd = (cum-cum.cummax()).min()

    print(f"\n  全樣本績效：")
    print(f"    交易數：{len(tdf)}")
    print(f"    損益(PnL)：${pnl:+,.2f}")
    print(f"    勝率(WR)：{wr:.1f}%")
    print(f"    獲利因子(PF)：{pf:.2f}")
    print(f"    最大回撤(DD)：${dd:,.2f}")

    # 出場類型拆解
    print(f"\n  出場類型拆解：")
    print(f"    {'類型':<12s} {'次數':>6s} {'損益':>12s} {'平均/筆':>10s} {'勝率':>8s} {'平均持倉':>10s}")
    print(f"    {'-'*60}")
    for t in ["TP1","TimeStop","SafeNet"]:
        sub = tdf[tdf["type"]==t]
        if len(sub)==0: continue
        sub_pnl = sub["pnl"].sum(); avg = sub["pnl"].mean(); sub_wr = (sub["pnl"]>0).mean()*100
        avg_bars = sub["bars"].mean()
        print(f"    {t:<12s} {len(sub):>6d} ${sub_pnl:>+10,.2f} ${avg:>+8.2f} {sub_wr:>7.1f}% {avg_bars:>8.1f} bars")

    # 多空拆解
    lt = tdf[tdf["side"]=="long"]; st = tdf[tdf["side"]=="short"]
    print(f"\n  多空拆解：")
    print(f"    做多：{len(lt)} 筆, ${lt['pnl'].sum():+,.2f}")
    print(f"    做空：{len(st)} 筆, ${st['pnl'].sum():+,.2f}")

# ============================================================
# Walk-Forward（前 3 後 3）
# ============================================================
print(f"\n{'='*100}")
print("Walk-Forward（前 3 個月 / 後 3 個月）")
print(f"{'='*100}")

split = int(len(df5) * 0.5)
dev = df5.iloc[:split].reset_index(drop=True)
oos = df5.iloc[split:].reset_index(drop=True)

for label, subset in [("DEV (前3個月)", dev), ("OOS (後3個月)", oos)]:
    t = run(subset)
    if len(t) == 0:
        print(f"\n  {label}: 無交易"); continue
    p = t["pnl"].sum(); w_r = (t["pnl"]>0).mean()*100
    ww = t[t["pnl"]>0]; ll = t[t["pnl"]<=0]
    p_f = ww["pnl"].sum()/abs(ll["pnl"].sum()) if len(ll)>0 and ll["pnl"].sum()!=0 else 999
    c_m = t["pnl"].cumsum(); d_d = (c_m-c_m.cummax()).min()

    print(f"\n  {label}:")
    print(f"    交易數：{len(t)}")
    print(f"    損益(PnL)：${p:+,.2f}")
    print(f"    勝率(WR)：{w_r:.1f}%")
    print(f"    獲利因子(PF)：{p_f:.2f}")
    print(f"    回撤(DD)：${d_d:,.2f}")

    # 出場類型
    for tp in ["TP1","TimeStop","SafeNet"]:
        sub = t[t["type"]==tp]
        if len(sub) == 0: continue
        print(f"    {tp}: {len(sub)} 筆, ${sub['pnl'].sum():+,.2f}, 勝率 {(sub['pnl']>0).mean()*100:.0f}%")

# ============================================================
# 月度
# ============================================================
print(f"\n{'='*100}")
print("逐月損益")
print(f"{'='*100}")

if len(tdf) > 0 and "dt" in tdf.columns:
    tdf["month"] = pd.to_datetime(tdf["dt"]).dt.to_period("M")
    monthly = tdf.groupby("month")["pnl"].agg(["sum","count"])
    print(f"\n  {'月份':<10s} {'損益':>12s} {'交易數':>8s}")
    print(f"  {'-'*32}")
    for m, row in monthly.iterrows():
        print(f"  {str(m):<10s} ${row['sum']:>+10,.2f} {int(row['count']):>8d}")

print("\nDone.")
