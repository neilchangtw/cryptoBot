"""
高級交易員視角回測 — 432 組合全搜索
絕對禁止上帝視角，每一步都模擬真實交易流程

嚴格規則：
  1. 信號用 iloc[-2]（已收盤 bar），進場用 iloc[-1] 的 open（next-open）
  2. Swing H/L 只用已確認的（ffill，最後 w 根不計算）
  3. 前 100 根 warmup 不交易（ATR pctile 需要 100 根基準）
  4. SL 滑價 = SL + 25% × (bar_extreme - SL)
  5. 資金費率每 8h 結算
  6. 止損冷卻 3 根 bar
  7. 前 3 個月選策略，後 3 個月 OOS
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time as _time
import warnings
warnings.filterwarnings("ignore")

MARGIN = 100

# ============================================================
# 抓資料
# ============================================================
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

def fetch_funding(days=180):
    all_fr = []
    cur = int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    end = int(datetime.now().timestamp()*1000)
    while cur < end:
        try:
            r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol":"BTCUSDT","startTime":cur,"limit":1000}, timeout=10)
            d = r.json()
            if not d: break
            all_fr.extend(d); cur = d[-1]["fundingTime"]+1; _time.sleep(0.1)
        except: break
    if not all_fr: return {}
    fr = pd.DataFrame(all_fr)
    fr["dt"] = pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"] = pd.to_numeric(fr["fundingRate"])
    return {row["dt"].floor("8h"): row["rate"] for _, row in fr.iterrows()}

print("Fetching 5m data...", end=" ", flush=True)
raw = fetch("5m", 180)
print(f"{len(raw)} bars")

print("Fetching funding rates...", end=" ", flush=True)
fr_map = fetch_funding(180)
print(f"{len(fr_map)} records")

# ============================================================
# 指標計算
# ============================================================
def add_ind(df):
    tr = pd.DataFrame({"hl":df["high"]-df["low"],
        "hc":abs(df["high"]-df["close"].shift(1)),
        "lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)

    # Swing — 嚴格版：最後 w 根不算（因為需要未來 w 根確認）
    w=5
    sh=pd.Series(np.nan,index=df.index); sl=pd.Series(np.nan,index=df.index)
    for i in range(w, len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max(): sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min(): sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill(); df["swing_low"]=sl.ffill()

    # RSI
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)

    # BB
    df["bb_mid"]=df["close"].rolling(20).mean()
    bbs=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bbs; df["bb_lower"]=df["bb_mid"]-2*bbs
    df["bb_upper_1.5"]=df["bb_mid"]+1.5*bbs; df["bb_lower_1.5"]=df["bb_mid"]-1.5*bbs

    # CCI
    tp=(df["high"]+df["low"]+df["close"])/3
    ma=tp.rolling(20).mean(); md=tp.rolling(20).apply(lambda x: np.abs(x-x.mean()).mean())
    df["cci"]=(tp-ma)/(0.015*md)

    # StochRSI
    rsi_min=df["rsi"].rolling(14).min(); rsi_max=df["rsi"].rolling(14).max()
    df["stochrsi"]=(df["rsi"]-rsi_min)/(rsi_max-rsi_min)*100

    # Volume
    df["vol_ma20"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/df["vol_ma20"]

    # EMA9
    df["ema9"]=df["close"].ewm(span=9).mean()

    # 連續紅K
    count=0; consec=[]
    for red in (df["close"]<df["open"]):
        count=count+1 if red else 0; consec.append(count)
    df["consec_red"]=consec

    return df.dropna().reset_index(drop=True)

df = add_ind(raw)
print(f"Processed: {len(df)} bars")
df["month"]=df["datetime"].dt.to_period("M")

# ============================================================
# 進場信號定義
# ============================================================
def get_long_signals(row):
    s = {}
    rsi=row["rsi"]; close=row["close"]; bbl=row["bb_lower"]; bbl15=row["bb_lower_1.5"]
    cci=row.get("cci",0); sr=row.get("stochrsi",50); vr=row.get("vol_ratio",1)
    ap=row.get("atr_pctile",50); cr=row.get("consec_red",0)

    s["1.RSI30+BB"]      = rsi<30 and close<bbl
    s["2.RSI30"]          = rsi<30
    s["3.RSI25"]          = rsi<25
    s["4.RSI20"]          = rsi<20
    s["5.BB_lower"]       = close<bbl
    s["6.RSI30+BB+Vol"]   = rsi<30 and close<bbl and vr>1.5
    s["7.RSI35+BB1.5"]    = rsi<35 and close<bbl15
    s["8.CCI+BB"]         = cci<-100 and close<bbl
    s["9.3red+RSI40"]     = cr>=3 and rsi<40
    s["10.RSI30+loVol"]   = rsi<30 and ap<50
    s["11.RSI30+hiVol"]   = rsi<30 and ap>50
    s["12.StochRSI20"]    = sr<20
    return s

def get_short_signals(row):
    s = {}
    rsi=row["rsi"]; close=row["close"]; bbu=row["bb_upper"]; bbu15=row["bb_upper_1.5"]
    cci=row.get("cci",0); sr=row.get("stochrsi",50); vr=row.get("vol_ratio",1)
    ap=row.get("atr_pctile",50)
    cg=0  # consec green
    s["1.RSI70+BB"]      = rsi>70 and close>bbu
    s["2.RSI70"]          = rsi>70
    s["3.RSI75"]          = rsi>75
    s["4.RSI80"]          = rsi>80
    s["5.BB_upper"]       = close>bbu
    s["6.RSI70+BB+Vol"]   = rsi>70 and close>bbu and vr>1.5
    s["7.RSI65+BB1.5"]    = rsi>65 and close>bbu15
    s["8.CCI+BB"]         = cci>100 and close>bbu
    s["9.RSI70+loVol"]    = rsi>70 and ap<50
    s["10.RSI70+hiVol"]   = rsi>70 and ap>50
    s["11.StochRSI80"]    = sr>80
    s["12.RSI70+BB+CCI"]  = rsi>70 and close>bbu and cci>100
    return s

# ============================================================
# 回測引擎
# ============================================================
def run(data, long_key, short_key, sl_mode, exit_mode, tp1_mult,
        leverage=20, sl_slip=0.25, max_c=3, sl_cooldown=3, fr=None):

    lpos=[]; spos=[]; trades=[]; total_fr=0
    sl_cool={"long":0,"short":0}
    WARMUP=105  # 100 for ATR pctile + 5 buffer

    for i in range(WARMUP, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        # Funding rate
        if fr:
            bt=row["datetime"]; fk=bt.floor("8h")
            if fk in fr:
                be=bt+timedelta(minutes=5)
                if bt<=fk<be:
                    rate=fr[fk]; n=MARGIN*leverage
                    for p in lpos: total_fr-=p["entry"]*p["qty"]*rate
                    for p in spos: total_fr+=p["entry"]*p["qty"]*rate

        # --- Update longs ---
        nl=[]
        for p in lpos:
            c=False
            if lo<=p["sl"]:
                gap=p["sl"]-lo; ep=p["sl"]-gap*sl_slip
                pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"SL"}); c=True
                sl_cool["long"]=i+sl_cooldown
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trhi"]=hi
            elif p["phase"]==2:
                if hi>p["trhi"]: p["trhi"]=hi
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi>65 else bm
                    sl=max(p["trhi"]-atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if row["close"]<row["ema9"]:
                        ep=row["close"]
                        pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"long","type":"Trail"}); c=True
                    sl=p["sl"]  # keep breakeven if ema not triggered
                elif exit_mode=="rsi_revert":
                    if rsi>55:
                        ep=row["close"]
                        pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"long","type":"Trail"}); c=True
                    sl=max(p["trhi"]-atr*2.0, p["entry"])

                if not c and exit_mode!="ema9" or (exit_mode=="ema9" and not c):
                    if not c and lo<=sl:
                        gap=sl-lo; ep=sl-gap*sl_slip; ep=max(ep,p["entry"])
                        pnl=(ep-p["entry"])*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"long","type":"Trail"}); c=True
            if not c: nl.append(p)
        lpos=nl

        # --- Update shorts ---
        ns=[]
        for p in spos:
            c=False
            if hi>=p["sl"]:
                gap=hi-p["sl"]; ep=p["sl"]+gap*sl_slip
                pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"SL"}); c=True
                sl_cool["short"]=i+sl_cooldown
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"TP1"})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trlo"]=lo
            elif p["phase"]==2:
                if lo<p["trlo"]: p["trlo"]=lo
                if exit_mode=="adaptive":
                    m=bm*0.6 if rsi<35 else bm
                    sl=min(p["trlo"]+atr*m, p["entry"])
                elif exit_mode=="ema9":
                    if row["close"]>row["ema9"]:
                        ep=row["close"]
                        pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"short","type":"Trail"}); c=True
                    sl=p["sl"]
                elif exit_mode=="rsi_revert":
                    if rsi<45:
                        ep=row["close"]
                        pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"short","type":"Trail"}); c=True
                    sl=min(p["trlo"]+atr*2.0, p["entry"])

                if not c:
                    if hi>=sl:
                        gap=hi-sl; ep=sl+gap*sl_slip; ep=min(ep,p["entry"])
                        pnl=(p["entry"]-ep)*p["qty"]-ep*p["qty"]*0.0004*2
                        trades.append({"pnl":pnl,"side":"short","type":"Trail"}); c=True
            if not c: ns.append(p)
        spos=ns

        # --- Entry ---
        ep=nxt["open"]; notional=MARGIN*leverage

        lsigs = get_long_signals(row)
        ssigs = get_short_signals(row)

        long_go = lsigs.get(long_key, False) and len(lpos)<max_c and i>=sl_cool["long"]
        short_go = ssigs.get(short_key, False) and len(spos)<max_c and i>=sl_cool["short"]

        if long_go:
            qty=notional/ep
            if sl_mode=="struct_0.3": slp=row["swing_low"]-atr*0.3
            elif sl_mode=="struct_0.5": slp=row["swing_low"]-atr*0.5
            elif sl_mode=="struct_0": slp=row["swing_low"]
            elif sl_mode=="fixed_2x": slp=ep-atr*2.0
            # SL 方向驗證
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

    tdf=pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","side","type"])
    return tdf, total_fr

def calc(tdf, fr=0):
    if len(tdf)<3: return None
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    net=pnl+fr
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"net":round(net,2),"fr":round(fr,2)}

# ============================================================
# Phase 1: 全組合搜索（25% 滑價，全樣本）
# ============================================================
print(f"\n{'='*120}")
print("Phase 1: 432 combo search (25% slippage, full sample, with funding rate)")
print(f"{'='*120}")

long_keys = list(get_long_signals(df.iloc[200]).keys())
short_keys = list(get_short_signals(df.iloc[200]).keys())
sl_modes = ["struct_0.3", "struct_0.5", "struct_0", "fixed_2x"]
exit_modes = ["adaptive", "ema9", "rsi_revert"]
tp1_mults = [0.5, 1.0, 1.5]

results = []
total = len(long_keys)*len(sl_modes)*len(exit_modes)*len(tp1_mults)
count = 0

for lk in long_keys:
    # 用對稱的空單信號
    sk_idx = long_keys.index(lk)
    sk = short_keys[sk_idx] if sk_idx < len(short_keys) else short_keys[0]

    for slm in sl_modes:
        for ex in exit_modes:
            for tp1 in tp1_mults:
                count += 1
                if count % 50 == 0:
                    print(f"  Progress: {count}/{total}...", flush=True)

                tdf, fr_cost = run(df, lk, sk, slm, ex, tp1, sl_slip=0.25, fr=fr_map)
                s = calc(tdf, fr_cost)
                if s and s["n"] >= 20:
                    results.append({
                        "long":lk, "short":sk, "sl":slm, "exit":ex, "tp1":tp1, **s
                    })

results.sort(key=lambda x: x["net"], reverse=True)

print(f"\nValid combos: {len(results)}")
print(f"\n{'='*120}")
print(f"Top 20 by Net PnL (25% slippage + funding rate)")
print(f"{'='*120}")

print(f"\n{'Rank':>4s} {'Long Signal':<18s} {'SL':<12s} {'Exit':<10s} {'TP1':>4s} "
      f"{'Trades':>6s} {'Net PnL':>12s} {'WR':>7s} {'PF':>7s} {'DD':>10s} {'FR':>8s}")
print(f"{'-'*105}")

for i, r in enumerate(results[:20]):
    print(f"{i+1:>4d} {r['long']:<18s} {r['sl']:<12s} {r['exit']:<10s} {r['tp1']:>3.1f}x "
          f"{r['n']:>6d} ${r['net']:>+10,.2f} {r['wr']:>6.1f}% {r['pf']:>6.2f} ${r['dd']:>9,.2f} ${r['fr']:>+7,.0f}")

# ============================================================
# Phase 2: Top 20 滑價敏感度（10% / 25% / 50%）
# ============================================================
print(f"\n{'='*120}")
print("Phase 2: Top 20 slippage sensitivity (10% / 25% / 50%)")
print(f"{'='*120}")

print(f"\n{'Rank':>4s} {'Long Signal':<18s} {'SL':<12s} {'Exit':<10s} {'TP1':>4s} "
      f"{'Slip10%':>12s} {'Slip25%':>12s} {'Slip50%':>12s} {'Robust?':>8s}")
print(f"{'-'*100}")

top20_robust = []
for i, r in enumerate(results[:20]):
    pnls = {}
    for slip in [0.10, 0.25, 0.50]:
        tdf, fr_c = run(df, r["long"], r["short"], r["sl"], r["exit"], r["tp1"],
                        sl_slip=slip, fr=fr_map)
        s = calc(tdf, fr_c)
        pnls[slip] = s["net"] if s else 0

    robust = "YES" if all(v > 0 for v in pnls.values()) else "NO"
    top20_robust.append({**r, "s10":pnls[0.10], "s25":pnls[0.25], "s50":pnls[0.50], "robust":robust})

    print(f"{i+1:>4d} {r['long']:<18s} {r['sl']:<12s} {r['exit']:<10s} {r['tp1']:>3.1f}x "
          f"${pnls[0.10]:>+10,.0f} ${pnls[0.25]:>+10,.0f} ${pnls[0.50]:>+10,.0f} {robust:>8s}")

# ============================================================
# Phase 3: Robust 組合 Walk-Forward（前3後3 + 滾動2m→1m）
# ============================================================
print(f"\n{'='*120}")
print("Phase 3: Walk-Forward for robust combos (25% slippage)")
print(f"{'='*120}")

robust_combos = [r for r in top20_robust if r["robust"] == "YES"][:10]

split = int(len(df) * 0.5)  # 前3後3
dev = df.iloc[:split].reset_index(drop=True)
oos = df.iloc[split:].reset_index(drop=True)
months = df["month"].unique()

print(f"\n  DEV: {dev['datetime'].iloc[0].date()} ~ {dev['datetime'].iloc[-1].date()} ({len(dev)} bars)")
print(f"  OOS: {oos['datetime'].iloc[0].date()} ~ {oos['datetime'].iloc[-1].date()} ({len(oos)} bars)")

print(f"\n{'Rank':>4s} {'Long Signal':<18s} {'SL':<12s} {'Exit':<10s} {'TP1':>4s} "
      f"{'DEV Net':>12s} {'OOS Net':>12s} {'OOS WR':>8s} {'OOS PF':>8s} {'Roll 2m1m':>12s} {'Folds':>6s}")
print(f"{'-'*115}")

final_results = []
for i, r in enumerate(robust_combos):
    # Fixed split
    tdf_dev, fr_dev = run(dev, r["long"], r["short"], r["sl"], r["exit"], r["tp1"],
                          sl_slip=0.25, fr=fr_map)
    tdf_oos, fr_oos = run(oos, r["long"], r["short"], r["sl"], r["exit"], r["tp1"],
                          sl_slip=0.25, fr=fr_map)
    s_dev = calc(tdf_dev, fr_dev)
    s_oos = calc(tdf_oos, fr_oos)

    # Rolling WF 2m→1m
    fold_nets = []
    for fold in range(len(months)-2):
        test_m = months[fold+2]
        fold_test = df[df["month"]==test_m].reset_index(drop=True)
        if len(fold_test) < 50: continue
        tdf_f, fr_f = run(fold_test, r["long"], r["short"], r["sl"], r["exit"], r["tp1"],
                          sl_slip=0.25, fr=fr_map)
        s_f = calc(tdf_f, fr_f)
        if s_f: fold_nets.append(s_f["net"])

    roll_total = sum(fold_nets)
    roll_prof = sum(1 for x in fold_nets if x > 0)
    roll_n = len(fold_nets)

    dev_net = s_dev["net"] if s_dev else 0
    oos_net = s_oos["net"] if s_oos else 0
    oos_wr = s_oos["wr"] if s_oos else 0
    oos_pf = s_oos["pf"] if s_oos else 0

    final_results.append({
        **r, "dev_net":dev_net, "oos_net":oos_net, "oos_wr":oos_wr, "oos_pf":oos_pf,
        "roll_total":roll_total, "roll_prof":roll_prof, "roll_n":roll_n
    })

    print(f"{i+1:>4d} {r['long']:<18s} {r['sl']:<12s} {r['exit']:<10s} {r['tp1']:>3.1f}x "
          f"${dev_net:>+10,.0f} ${oos_net:>+10,.0f} {oos_wr:>7.1f}% {oos_pf:>7.2f} ${roll_total:>+10,.0f} {roll_prof}/{roll_n}")

# ============================================================
# 最終排名（用 OOS 排）
# ============================================================
print(f"\n{'='*120}")
print("FINAL RANKING (by OOS Net PnL)")
print(f"{'='*120}")

final_results.sort(key=lambda x: x["oos_net"], reverse=True)

print(f"\n{'Rank':>4s} {'Long':<18s} {'SL':<12s} {'Exit':<10s} {'TP1':>4s} "
      f"{'Full Net':>10s} {'OOS Net':>10s} {'OOS WR':>8s} {'OOS PF':>8s} "
      f"{'Slip10%':>10s} {'Slip50%':>10s} {'Roll':>10s} {'Folds':>6s}")
print(f"{'-'*130}")

for i, r in enumerate(final_results):
    print(f"{i+1:>4d} {r['long']:<18s} {r['sl']:<12s} {r['exit']:<10s} {r['tp1']:>3.1f}x "
          f"${r['net']:>+8,.0f} ${r['oos_net']:>+8,.0f} {r['oos_wr']:>7.1f}% {r['oos_pf']:>7.2f} "
          f"${r['s10']:>+8,.0f} ${r['s50']:>+8,.0f} ${r['roll_total']:>+8,.0f} {r['roll_prof']}/{r['roll_n']}")

# ============================================================
# 結論
# ============================================================
print(f"\n{'='*120}")
print("CONCLUSION")
print(f"{'='*120}")

if final_results:
    best = final_results[0]
    print(f"\n  Best by OOS (25% slippage + funding rate):")
    print(f"    Entry: Long={best['long']} / Short={best['short']}")
    print(f"    SL: {best['sl']}")
    print(f"    Exit: {best['exit']}")
    print(f"    TP1: {best['tp1']}x ATR")
    print(f"    Full Net: ${best['net']:+,.2f} ({best['n']} trades, WR {best['wr']}%)")
    print(f"    OOS Net:  ${best['oos_net']:+,.2f} (WR {best['oos_wr']}%, PF {best['oos_pf']})")
    print(f"    Slippage: 10%=${best['s10']:+,.0f} / 25%=${best['s25']:+,.0f} / 50%=${best['s50']:+,.0f}")
    print(f"    Rolling WF: ${best['roll_total']:+,.0f} ({best['roll_prof']}/{best['roll_n']} profitable)")

    # 跟目前策略比
    curr = [r for r in final_results if r["long"]=="1.RSI30+BB" and r["sl"]=="struct_0.3"
            and r["exit"]=="adaptive" and r["tp1"]==1.0]
    if curr:
        c = curr[0]
        print(f"\n  Current strategy (1.RSI30+BB / struct_0.3 / adaptive / 1.0x):")
        print(f"    Full Net: ${c['net']:+,.2f}")
        print(f"    OOS Net:  ${c['oos_net']:+,.2f}")
        print(f"    Slippage: 10%=${c['s10']:+,.0f} / 25%=${c['s25']:+,.0f} / 50%=${c['s50']:+,.0f}")

print("\nDone.")
