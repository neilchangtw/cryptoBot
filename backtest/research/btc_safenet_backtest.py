"""
安全網架構回測：
  交易所 SL = 進場價 ± 3%（只防爆倉，幾乎不觸發）
  程式監控 = 每根 5m bar 用指標判斷出場（模擬每 1 分鐘，但用 5m 近似）
  出場用限價單 = 滑價為 0

測試前 3 名進場信號（從 432 組合結果挑出）：
  1. RSI<20
  2. RSI<25
  3. RSI<30 + BB Lower（目前方案 H）

對比：
  A. 舊架構（SL 在結構止損，STOP_MARKET 25% 滑價）
  B. 新架構（安全網 + 程式監控限價出場，0 滑價）
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time as _time
import warnings
warnings.filterwarnings("ignore")

MARGIN = 100; LEVERAGE = 20

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
    df["ema9"]=df["close"].ewm(span=9).mean()
    return df.dropna().reset_index(drop=True)

print("Fetching...", end=" ", flush=True)
raw = fetch("5m", 180)
df = add_ind(raw)
print(f"{len(df)} bars")

# ============================================================
# 舊架構：交易所 STOP_MARKET（含 25% 滑價）
# ============================================================
def run_old(data, entry_fn, exit_mode="adaptive", sl_mode="struct_0.3",
            tp1_mult=1.0, sl_slip=0.25):
    lpos=[]; spos=[]; trades=[]
    sl_cool={"long":0,"short":0}
    for i in range(105, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]; close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            if lo<=p["sl"]:
                gap=p["sl"]-lo; ep=p["sl"]-gap*sl_slip
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"type":"SL"}); c=True; sl_cool["long"]=i+3
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trhi"]=hi
            elif p["phase"]==2:
                if hi>p["trhi"]: p["trhi"]=hi
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi>65 else bm; sl=max(p["trhi"]-atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if close<row["ema9"]:
                        pnl=(close-p["entry"])*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=p["sl"]
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
                trades.append({"pnl":pnl,"type":"SL"}); c=True; sl_cool["short"]=i+3
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trlo"]=lo
            elif p["phase"]==2:
                if lo<p["trlo"]: p["trlo"]=lo
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi<35 else bm; sl=min(p["trlo"]+atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if close>row["ema9"]:
                        pnl=(p["entry"]-close)*p["qty"]-close*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"type":"Trail"}); c=True
                    sl=p["sl"]
                if not c and hi>=sl:
                    gap=hi-sl; ep=sl+gap*sl_slip; ep=min(ep,p["entry"])
                    pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True
            if not c: ns.append(p)
        spos=ns

        ep=nxt["open"]; notional=MARGIN*LEVERAGE
        l_go, s_go = entry_fn(row)
        if l_go and len(lpos)<3 and i>=sl_cool["long"]:
            qty=notional/ep
            if sl_mode=="struct_0.3": slp=row["swing_low"]-atr*0.3
            else: slp=row["swing_low"]-atr*0.5
            if slp>=ep: slp=ep-atr*1.5
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,
                "tp1":ep+tp1_mult*atr,"phase":1,"trhi":ep,"atr":atr})
        if s_go and len(spos)<3 and i>=sl_cool["short"]:
            qty=notional/ep
            if sl_mode=="struct_0.3": slp=row["swing_high"]+atr*0.3
            else: slp=row["swing_high"]+atr*0.5
            if slp<=ep: slp=ep+atr*1.5
            spos.append({"entry":ep,"qty":qty,"oqty":qty,"sl":slp,
                "tp1":ep-tp1_mult*atr,"phase":1,"trlo":ep,"atr":atr})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type"])

# ============================================================
# 新架構：安全網 SL (±3%) + 程式監控限價出場 (0 滑價)
# ============================================================
def run_new(data, entry_fn, exit_mode="adaptive", tp1_mult=1.0,
            safenet_pct=0.03):
    """
    安全網 SL = 進場價 ± 3%（只防爆倉）
    策略出場 = 程式每根 bar 用 close 判斷 → 限價單（0 滑價，用 close 成交）
    """
    lpos=[]; spos=[]; trades=[]
    sl_cool={"long":0,"short":0}

    for i in range(105, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]; close=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        nl=[]
        for p in lpos:
            c=False
            # 安全網 SL：只有極端情況才觸發（進場價 -3%）
            safenet_sl = p["entry"] * (1 - safenet_pct)
            if lo <= safenet_sl:
                # 安全網觸發 = STOP_MARKET，有滑價
                gap = safenet_sl - lo
                ep = safenet_sl - gap * 0.25  # 安全網也有 25% 滑價
                pnl = (ep - p["entry"]) * p["qty"] - ep * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl,"type":"SafeNet"}); c=True; sl_cool["long"]=i+3

            # TP1：程式監控（用 close 判斷，限價出場 = 0 滑價）
            elif p["phase"]==1 and close >= p["tp1"]:
                out=p["oqty"]*0.1
                # 限價單成交在 close（0 滑價）
                pnl=(close-p["entry"])*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["trhi"]=close

            # 策略出場：程式用指標判斷，限價單（0 滑價，用 close 成交）
            elif p["phase"]==2:
                if close > p["trhi"]: p["trhi"] = close
                should_exit = False

                if exit_mode == "adaptive":
                    m = bm*0.6 if rsi>65 else bm
                    trail_sl = p["trhi"] - atr * m
                    if close <= trail_sl and close <= p["entry"]:
                        # 已經虧到保本以下 → 用 close 出場
                        should_exit = True
                    elif close <= trail_sl and close > p["entry"]:
                        # 還在賺 → 用 close 鎖利出場
                        should_exit = True

                elif exit_mode == "ema9":
                    if close < row["ema9"]:
                        should_exit = True

                if should_exit:
                    pnl = (close - p["entry"]) * p["qty"] - close * p["qty"] * 0.0004 * 2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True

            if not c: nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False
            safenet_sl = p["entry"] * (1 + safenet_pct)
            if hi >= safenet_sl:
                gap = hi - safenet_sl
                ep = safenet_sl + gap * 0.25
                pnl = (p["entry"] - ep) * p["qty"] - ep * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl,"type":"SafeNet"}); c=True; sl_cool["short"]=i+3

            elif p["phase"]==1 and close <= p["tp1"]:
                out=p["oqty"]*0.1
                pnl=(p["entry"]-close)*out-close*out*0.0004*2
                trades.append({"pnl":pnl,"type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["trlo"]=close

            elif p["phase"]==2:
                if close < p["trlo"]: p["trlo"] = close
                should_exit = False

                if exit_mode == "adaptive":
                    m = bm*0.6 if rsi<35 else bm
                    trail_sl = p["trlo"] + atr * m
                    if close >= trail_sl:
                        should_exit = True

                elif exit_mode == "ema9":
                    if close > row["ema9"]:
                        should_exit = True

                if should_exit:
                    pnl = (p["entry"] - close) * p["qty"] - close * p["qty"] * 0.0004 * 2
                    trades.append({"pnl":pnl,"type":"Trail"}); c=True

            if not c: ns.append(p)
        spos=ns

        ep=nxt["open"]; notional=MARGIN*LEVERAGE
        l_go, s_go = entry_fn(row)
        if l_go and len(lpos)<3 and i>=sl_cool["long"]:
            qty=notional/ep
            lpos.append({"entry":ep,"qty":qty,"oqty":qty,
                "tp1":ep+tp1_mult*atr,"phase":1,"trhi":ep,"atr":atr})
        if s_go and len(spos)<3 and i>=sl_cool["short"]:
            qty=notional/ep
            spos.append({"entry":ep,"qty":qty,"oqty":qty,
                "tp1":ep-tp1_mult*atr,"phase":1,"trlo":ep,"atr":atr})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type"])

def stats(tdf):
    if len(tdf)<1: return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0}
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    # 出場類型統計
    safenet_n = len(tdf[tdf["type"]=="SafeNet"]) if "SafeNet" in tdf["type"].values else 0
    sl_n = len(tdf[tdf["type"]=="SL"]) if "SL" in tdf["type"].values else 0
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"safenet_n":safenet_n,"sl_n":sl_n}

# ============================================================
# 進場函數
# ============================================================
entries = {
    "RSI<20": lambda r: (r["rsi"]<20, r["rsi"]>80),
    "RSI<25": lambda r: (r["rsi"]<25, r["rsi"]>75),
    "RSI30+BB": lambda r: (r["rsi"]<30 and r["close"]<r["bb_lower"],
                           r["rsi"]>70 and r["close"]>r["bb_upper"]),
}

# ============================================================
# 對比測試
# ============================================================
print(f"\n{'='*120}")
print("舊架構 vs 新架構（安全網 SL ±3% + 程式限價出場）")
print(f"{'='*120}")

print(f"\n  {'進場':<12s} {'架構':<15s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'安全網觸發':>10s} {'SL觸發':>8s}")
print(f"  {'-'*110}")

for ename, efn in entries.items():
    # 舊架構 x 2 種出場
    for ex in ["adaptive", "ema9"]:
        tdf_old = run_old(df, efn, exit_mode=ex)
        s = stats(tdf_old)
        print(f"  {ename:<12s} {'OLD '+ex:<15s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% "
              f"{s['pf']:>13.2f} ${s['dd']:>9,.2f} {'-':>10s} {s['sl_n']:>8d}")

    # 新架構 x 2 種出場
    for ex in ["adaptive", "ema9"]:
        tdf_new = run_new(df, efn, exit_mode=ex)
        s = stats(tdf_new)
        print(f"  {ename:<12s} {'NEW '+ex:<15s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% "
              f"{s['pf']:>13.2f} ${s['dd']:>9,.2f} {s['safenet_n']:>10d} {'-':>8s}")

    print()

# ============================================================
# Walk-Forward (前3後3)
# ============================================================
print(f"\n{'='*120}")
print("Walk-Forward（前 3 個月 / 後 3 個月）")
print(f"{'='*120}")

split = int(len(df) * 0.5)
dev = df.iloc[:split].reset_index(drop=True)
oos = df.iloc[split:].reset_index(drop=True)

print(f"\n  {'進場':<12s} {'架構':<15s} {'DEV PnL':>12s} {'OOS PnL':>12s} {'OOS WR':>8s} {'OOS PF':>8s}")
print(f"  {'-'*65}")

for ename, efn in entries.items():
    for ex in ["adaptive", "ema9"]:
        # 新架構 WF
        tdf_dev = run_new(dev, efn, exit_mode=ex)
        tdf_oos = run_new(oos, efn, exit_mode=ex)
        s_dev = stats(tdf_dev); s_oos = stats(tdf_oos)
        print(f"  {ename:<12s} {'NEW '+ex:<15s} ${s_dev['pnl']:>+10,.0f} ${s_oos['pnl']:>+10,.0f} "
              f"{s_oos['wr']:>7.1f}% {s_oos['pf']:>7.2f}")

print("\nDone.")
