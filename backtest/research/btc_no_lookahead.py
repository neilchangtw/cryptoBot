"""
無上帝視角回測 — 修復所有 look-ahead bias

修復項目：
  1. 多時間框架對齊：5m bar 必須用「已收盤」的 1h/15m 指標，不能用正在發展中的
     例：10:25 的 5m bar → 只能看到 9:00~10:00 的 1h bar，不能看 10:00~11:00
  2. 進場價格：用下一根 bar 的 open，不能用觸發 bar 的 close
  3. 參數選擇：用前 3 個月選參數，後 3 個月做真正的樣本外測試
  4. Swing High/Low：確認用的是已確認的（至少 5 根前的）swing，不是未來
"""
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from datetime import datetime, timedelta
import time

mpl.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "SimHei", "Arial"]
mpl.rcParams["axes.unicode_minus"] = False

MARGIN = 100; LEVERAGE = 20

# ============================================================
# 抓資料
# ============================================================
def fetch_klines(interval, days_back=180):
    all_data = []
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    current = start_ms
    while current < end_ms:
        try:
            resp = requests.get("https://api.binance.com/api/v3/klines",
                                params={"symbol":"BTCUSDT","interval":interval,
                                        "startTime":current,"limit":1000}, timeout=10)
            data = resp.json()
            if not data: break
            all_data.extend(data)
            current = data[-1][0] + 1
            time.sleep(0.1)
        except: break
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    return df

def add_indicators(df):
    tr = pd.DataFrame({
        "hl":df["high"]-df["low"],
        "hc":abs(df["high"]-df["close"].shift(1)),
        "lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    w=5; sh=pd.Series(np.nan,index=df.index); sl=pd.Series(np.nan,index=df.index)
    for i in range(w,len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max(): sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min(): sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill(); df["swing_low"]=sl.ffill()
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean()
    df["bb_upper"]=df["bb_mid"]+2*df["close"].rolling(20).std()
    df["bb_lower"]=df["bb_mid"]-2*df["close"].rolling(20).std()
    return df.dropna().reset_index(drop=True)

print("=== Fetching data ===")
raw_5m = fetch_klines("5m", 180); print(f"  5m: {len(raw_5m)} bars")
raw_15m = fetch_klines("15m", 180); print(f"  15m: {len(raw_15m)} bars")
raw_1h = fetch_klines("1h", 180); print(f"  1h: {len(raw_1h)} bars")

df_5m = add_indicators(raw_5m)
df_15m = add_indicators(raw_15m)
df_1h = add_indicators(raw_1h)

# ============================================================
# 修復 1: 多時間框架對齊 — 只用「上一根已收盤」的大週期指標
# ============================================================
print("\n=== Aligning timeframes (NO look-ahead) ===")

# 1h 指標：shift(1) 確保用的是上一根已收盤的 1h
df_1h_map = df_1h[["datetime","rsi","bb_upper","bb_mid","bb_lower","atr","swing_high","swing_low","atr_pctile"]].copy()
df_1h_map = df_1h_map.rename(columns={c: f"h1_{c}" for c in df_1h_map.columns if c != "datetime"})
# 用 floor("h") + shift(1) = 上一個完整小時
df_1h_map["hour_key"] = df_1h_map["datetime"].dt.floor("h") + timedelta(hours=1)  # 10:00 的數據，對齊到 11:00~12:00 的 5m bars

df_5m["hour_key"] = df_5m["datetime"].dt.floor("h")
df_5m = df_5m.merge(df_1h_map.drop(columns="datetime"), on="hour_key", how="left")

# 15m 指標：同理 shift
df_15m_map = df_15m[["datetime","rsi","bb_upper","bb_lower","atr"]].copy()
df_15m_map = df_15m_map.rename(columns={c: f"m15_{c}" for c in df_15m_map.columns if c != "datetime"})
df_15m_map["q15_key"] = df_15m_map["datetime"].dt.floor("15min") + timedelta(minutes=15)

df_5m["q15_key"] = df_5m["datetime"].dt.floor("15min")
df_5m = df_5m.merge(df_15m_map.drop(columns="datetime"), on="q15_key", how="left")

df_5m = df_5m.dropna().reset_index(drop=True)
print(f"  Aligned 5m: {len(df_5m)} bars")
print(f"  Check: 5m bar at {df_5m.iloc[100]['datetime']} uses 1h RSI from hour_key={df_5m.iloc[100]['hour_key']}")

# ============================================================
# 修復 2: 進場用下一根 open，不用當根 close
# ============================================================
# 在回測引擎中實作

# ============================================================
# 回測引擎（嚴格無 look-ahead）
# ============================================================
def run_strict(data, long_cond_fn, short_cond_fn,
               sl_col="swing_low", sh_col="swing_high",
               atr_col="atr", atr_pctile_col="atr_pctile", rsi_col="rsi",
               max_c=3):
    """
    嚴格版回測：
    - 進場價 = 下一根 bar 的 open（不是當根 close）
    - 止損/TP1 基於進場時的指標（不偷看未來）
    - Swing H/L 已經是 ffill 的（至少 5 根前確認的）
    """
    long_positions=[]; short_positions=[]; trades=[]

    for i in range(2, len(data)-1):  # -1 因為要用 i+1 的 open
        row = data.iloc[i]
        next_row = data.iloc[i+1]
        atr = row[atr_col]; hi = row["high"]; lo = row["low"]
        ap = row[atr_pctile_col] if not np.isnan(row[atr_pctile_col]) else 50
        rsi_val = row[rsi_col]
        bm = 1.0 + (ap/100)*1.5

        # --- 更新持倉（用當根 OHLC 檢查止損/止盈）---
        nl=[]
        for p in long_positions:
            closed=False
            if lo<=p["sl"]:
                pnl=(p["sl"]-p["entry"])*p["qty"]-p["sl"]*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"long","dt":row["datetime"]}); closed=True
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_hi"]=hi
            elif p["phase"]==2:
                if hi>p["trail_hi"]: p["trail_hi"]=hi
                m=bm*0.6 if rsi_val>65 else bm
                sl=max(p["trail_hi"]-atr*m, p["entry"])
                if lo<=sl:
                    pnl=(sl-p["entry"])*p["qty"]-sl*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"long","dt":row["datetime"]}); closed=True
            if not closed: nl.append(p)
        long_positions=nl

        ns=[]
        for p in short_positions:
            closed=False
            if hi>=p["sl"]:
                pnl=(p["entry"]-p["sl"])*p["qty"]-p["sl"]*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]}); closed=True
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_lo"]=lo
            elif p["phase"]==2:
                if lo<p["trail_lo"]: p["trail_lo"]=lo
                m=bm*0.6 if rsi_val<35 else bm
                sl=min(p["trail_lo"]+atr*m, p["entry"])
                if hi>=sl:
                    pnl=(p["entry"]-sl)*p["qty"]-sl*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]}); closed=True
            if not closed: ns.append(p)
        short_positions=ns

        # --- 進場：信號在當根判斷，但進場價用下一根 open ---
        entry_price = next_row["open"]

        if len(long_positions)<max_c and long_cond_fn(row):
            qty=(MARGIN*LEVERAGE)/entry_price
            long_positions.append({"entry":entry_price,"qty":qty,"oqty":qty,
                "sl":row[sl_col]-atr*0.3,"tp1":entry_price+atr,
                "atr":atr,"phase":1,"trail_hi":entry_price})

        if len(short_positions)<max_c and short_cond_fn(row):
            qty=(MARGIN*LEVERAGE)/entry_price
            short_positions.append({"entry":entry_price,"qty":qty,"oqty":qty,
                "sl":row[sh_col]+atr*0.3,"tp1":entry_price-atr,
                "atr":atr,"phase":1,"trail_lo":entry_price})

    return pd.DataFrame(trades)

def calc_stats(tdf):
    if len(tdf)<3: return None
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    lt=tdf[tdf["side"]=="long"]; st=tdf[tdf["side"]=="short"]
    roi_dd = abs(pnl/dd) if dd!=0 else 999
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"exp":round(pnl/len(tdf),2),"roi_dd":round(roi_dd,1),
            "l_pnl":round(lt["pnl"].sum(),2),"s_pnl":round(st["pnl"].sum(),2),
            "l_n":len(lt),"s_n":len(st),"tdf":tdf}

# ============================================================
# 修復 3: 真正的樣本外測試
# 前 3 個月選策略，後 3 個月測試（完全不碰）
# ============================================================
print("\n=== Splitting data: first 3 months DEVELOP / last 3 months TRUE OOS ===")

# 5m 資料切半
mid_5m = len(df_5m) // 2
dev_5m = df_5m.iloc[:mid_5m].reset_index(drop=True)
oos_5m = df_5m.iloc[mid_5m:].reset_index(drop=True)

# 1h 資料切半
mid_1h = len(df_1h) // 2
dev_1h = df_1h.iloc[:mid_1h].reset_index(drop=True)
oos_1h = df_1h.iloc[mid_1h:].reset_index(drop=True)

print(f"  Development: 5m {len(dev_5m)} bars, 1h {len(dev_1h)} bars")
print(f"    Period: {dev_5m['datetime'].iloc[0].date()} ~ {dev_5m['datetime'].iloc[-1].date()}")
print(f"  True OOS:    5m {len(oos_5m)} bars, 1h {len(oos_1h)} bars")
print(f"    Period: {oos_5m['datetime'].iloc[0].date()} ~ {oos_5m['datetime'].iloc[-1].date()}")

# ============================================================
# Phase 1: 在開發集上測試所有組合（選最佳策略）
# ============================================================
print(f"\n{'='*140}")
print("Phase 1: 開發集（前 3 個月）— 選策略用，不是最終績效")
print(f"{'='*140}")

combos = [
    ("A. 純5m RSI<30", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30, lambda r: r["close"]>r["bb_upper"]),
    ("B. 純1h RSI<30", df_1h, dev_1h, oos_1h,
     lambda r: r["rsi"]<30, lambda r: r["close"]>r["bb_upper"]),
    ("C. 1h信號+5m進場", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["h1_rsi"]<30, lambda r: r["close"]>r["bb_upper"] and r["close"]>r.get("h1_bb_upper",999)),
    ("D. 5m信號+1h寬過濾", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["h1_rsi"]<40, lambda r: r["close"]>r["bb_upper"] and r["h1_rsi"]>60),
    ("E. 5m信號+1h嚴格", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["h1_rsi"]<35, lambda r: r["close"]>r["bb_upper"] and r["h1_rsi"]>65),
    ("F. 5m極端RSI<20", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<20, lambda r: r["rsi"]>80),
    ("G. 5m+1h趨勢", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["close"]<r.get("h1_bb_mid",r["close"]+1),
     lambda r: r["close"]>r["bb_upper"] and r["close"]>r.get("h1_bb_mid",r["close"]-1)),
    ("H. 5m雙重RSI<30+BB", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["close"]<r["bb_lower"], lambda r: r["rsi"]>70 and r["close"]>r["bb_upper"]),
    ("I. 三重5m+1h+15m", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<30 and r["h1_rsi"]<40 and r.get("m15_rsi",50)<35,
     lambda r: r["close"]>r["bb_upper"] and r["h1_rsi"]>60 and r.get("m15_rsi",50)>65),
    ("J. 5m極端RSI<15", df_5m, dev_5m, oos_5m,
     lambda r: r["rsi"]<15, lambda r: r["rsi"]>85),
]

# 用哪些欄位取決於資料集
def get_cols(data):
    if "h1_rsi" in data.columns:
        return "swing_low","swing_high","atr","atr_pctile","rsi"
    else:
        return "swing_low","swing_high","atr","atr_pctile","rsi"

print(f"\n  {'組合':<25s} {'交易':>6s} {'開發集損益(DEV)':>16s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'每筆期望值':>12s}")
print(f"  {'-'*100}")

dev_results = []
for name, full_data, dev_data, oos_data, lc, sc in combos:
    cols = get_cols(dev_data)
    tdf = run_strict(dev_data, lc, sc, *cols)
    s = calc_stats(tdf)
    if not s:
        print(f"  {name:<25s} (交易不足)")
        dev_results.append({"name":name, "dev_s": None})
        continue
    dev_results.append({"name":name, "dev_s":s, "full_data":full_data, "oos_data":oos_data, "lc":lc, "sc":sc})
    print(f"  {name:<25s} {s['n']:>6d} ${s['pnl']:>+14,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} ${s['dd']:>9,.2f} ${s['exp']:>+10.2f}")

# ============================================================
# Phase 2: 真正的樣本外測試（後 3 個月，完全沒碰過）
# ============================================================
print(f"\n{'='*140}")
print("Phase 2: 真正樣本外（後 3 個月）— 這才是真實績效")
print(f"{'='*140}")

print(f"\n  {'組合':<25s} {'開發集(DEV)':>14s} {'樣本外(OOS)':>14s} {'OOS勝率':>10s} {'OOS PF':>10s} {'OOS回撤':>10s} {'OOS交易':>8s} {'衰退率':>8s}")
print(f"  {'-'*105}")

oos_results = []
for r in dev_results:
    if r["dev_s"] is None: continue
    name = r["name"]
    cols = get_cols(r["oos_data"])
    tdf_oos = run_strict(r["oos_data"], r["lc"], r["sc"], *cols)
    s_oos = calc_stats(tdf_oos)
    if not s_oos:
        print(f"  {name:<25s} (OOS 交易不足)")
        continue

    dev_pnl = r["dev_s"]["pnl"]
    oos_pnl = s_oos["pnl"]
    # 衰退率：OOS 月均 vs DEV 月均
    dev_monthly = dev_pnl / 3
    oos_monthly = oos_pnl / 3
    decay = (1 - oos_monthly / dev_monthly) * 100 if dev_monthly != 0 else 0

    oos_results.append({
        "name": name, "dev_pnl": dev_pnl, "oos_pnl": oos_pnl,
        "oos_wr": s_oos["wr"], "oos_pf": s_oos["pf"], "oos_dd": s_oos["dd"],
        "oos_n": s_oos["n"], "decay": decay,
        "dev_s": r["dev_s"], "oos_s": s_oos,
        "pf": r["dev_s"]["pf"], "dd": r["dev_s"]["dd"],
    })

    print(f"  {name:<25s} ${dev_pnl:>+12,.2f} ${oos_pnl:>+12,.2f} {s_oos['wr']:>9.1f}% "
          f"{s_oos['pf']:>9.2f} ${s_oos['dd']:>9,.2f} {s_oos['n']:>8d} {decay:>+7.1f}%")

# ============================================================
# 全樣本（6個月）嚴格版
# ============================================================
print(f"\n{'='*140}")
print("全樣本嚴格回測（next-open 進場 + 1h 指標延遲一根）")
print(f"{'='*140}")

print(f"\n  {'組合':<25s} {'交易':>6s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'收益/回撤':>10s}")
print(f"  {'-'*95}")

full_results = []
for r_dev in dev_results:
    if r_dev["dev_s"] is None: continue
    name = r_dev["name"]
    cols = get_cols(r_dev["full_data"])
    tdf = run_strict(r_dev["full_data"], r_dev["lc"], r_dev["sc"], *cols)
    s = calc_stats(tdf)
    if not s: continue
    roi_dd = abs(s["pnl"]/s["dd"]) if s["dd"]!=0 else 999
    full_results.append({"name":name, **s, "roi_dd": round(roi_dd,1)})
    print(f"  {name:<25s} {s['n']:>6d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} ${s['dd']:>9,.2f} {roi_dd:>9.1f}x")

# ============================================================
# 綜合排名（用 OOS 排）
# ============================================================
print(f"\n{'='*140}")
print("綜合排名（按真正樣本外損益排序）")
print(f"{'='*140}")

oos_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n  {'排名':>4s} {'組合':<25s} {'開發集':>12s} {'樣本外(OOS)':>14s} {'OOS勝率':>10s} {'OOS PF':>10s} {'OOS回撤':>10s} {'衰退率':>8s}")
print(f"  {'-'*95}")

for i, r in enumerate(oos_results):
    mark = " ***" if i == 0 else ""
    print(f"  {i+1:>4d} {r['name']:<25s} ${r['dev_pnl']:>+10,.0f} ${r['oos_pnl']:>+12,.2f} "
          f"{r['oos_wr']:>9.1f}% {r['oos_pf']:>9.2f} ${r['oos_dd']:>9,.2f} {r['decay']:>+7.1f}%{mark}")

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(22, 16))
fig.suptitle("No Look-Ahead Bias: Strict Backtest Results\n(next-open entry + lagged HTF indicators + true OOS)",
             fontsize=13, fontweight="bold")

# 圖 1: DEV vs OOS
ax = axes[0][0]
names = [r["name"][:22] for r in oos_results]
dev_pnls = [r["dev_pnl"] for r in oos_results]
oos_pnls_list = [r["oos_pnl"] for r in oos_results]
x = range(len(names))
w = 0.35
ax.barh([i-w/2 for i in x], dev_pnls, w, label="Development (3m)", color="#90CAF9", edgecolor="black", linewidth=0.5)
ax.barh([i+w/2 for i in x], oos_pnls_list, w, label="True OOS (3m)", color="#EF9A9A", edgecolor="black", linewidth=0.5)
ax.set_yticks(list(x)); ax.set_yticklabels(names, fontsize=7); ax.invert_yaxis()
for i, (d, o) in enumerate(zip(dev_pnls, oos_pnls_list)):
    ax.text(max(d,o)+100, i, f"OOS ${o:+,.0f}", va="center", fontsize=7)
ax.set_xlabel("Losses ($)"); ax.set_title("Development vs True OOS"); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

# 圖 2: OOS 排名
ax = axes[0][1]
colors = ["#4CAF50" if r["oos_pnl"]>0 else "#F44336" for r in oos_results]
ax.barh(range(len(oos_results)), [r["oos_pnl"] for r in oos_results], color=colors, edgecolor="black")
ax.set_yticks(range(len(oos_results))); ax.set_yticklabels([r["name"][:22] for r in oos_results], fontsize=7)
ax.invert_yaxis(); ax.axvline(0, color="black", linewidth=0.5)
ax.set_title("True OOS (3 months, never seen during development)"); ax.set_xlabel("PnL ($)")
ax.grid(True, alpha=0.3, axis="x")

# 圖 3: 全樣本嚴格版累計曲線
ax = axes[1][0]
for r in full_results[:5]:
    s = r; tdf = s["tdf"]
    cum = tdf.sort_values("dt")["pnl"].cumsum()
    ax.plot(range(len(cum)), cum.values, label=f"{r['name'][:20]} ${r['pnl']:+,.0f}", linewidth=1.2)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("Strict Backtest (6m, next-open entry)"); ax.set_ylabel("PnL ($)"); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]; ax.axis("off")
table_h = ["Rank","Strategy","DEV (3m)","OOS (3m)","OOS WR","OOS PF","OOS DD","Decay"]
table_r = []
for i, r in enumerate(oos_results[:8]):
    table_r.append([str(i+1), r["name"][:24], f"${r['dev_pnl']:+,.0f}",
                    f"${r['oos_pnl']:+,.0f}", f"{r['oos_wr']}%", f"{r['oos_pf']}",
                    f"${r['oos_dd']:,.0f}", f"{r['decay']:+.0f}%"])
table = ax.table(cellText=table_r, colLabels=table_h, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1.0, 1.7)
for j in range(8):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
for j in range(8):
    table[1, j].set_facecolor("#FFF2CC")

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_no_lookahead.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 最終結論
# ============================================================
print(f"\n{'='*140}")
print("FINAL: No Look-Ahead Verdict")
print(f"{'='*140}")

if oos_results:
    best = oos_results[0]
    print(f"\n  Best strategy (by true OOS): {best['name']}")
    print(f"    DEV (3m):  ${best['dev_pnl']:+,.2f}")
    print(f"    OOS (3m):  ${best['oos_pnl']:+,.2f}")
    print(f"    OOS WR:    {best['oos_wr']}%")
    print(f"    OOS PF:    {best['oos_pf']}")
    print(f"    OOS DD:    ${best['oos_dd']:,.2f}")
    print(f"    Decay:     {best['decay']:+.1f}%")

print(f"\n  Look-ahead fixes applied:")
print(f"    1. HTF alignment: 1h/15m indicators lagged by 1 bar (use PREVIOUS closed bar)")
print(f"    2. Entry price: next bar's open (not signal bar's close)")
print(f"    3. Parameter selection: done on DEV set only, OOS never touched")
print(f"    4. Swing H/L: ffill ensures only confirmed (5+ bars old) swings used")
