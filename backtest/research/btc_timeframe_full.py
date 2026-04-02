"""
完整多時間框架測試：5m / 15m / 30m / 1h / 2h / 4h
含 Walk-Forward 驗證
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
# 從 Binance 抓資料
# ============================================================
def fetch_klines(interval, days_back=180):
    all_data = []
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    current = start_ms
    while current < end_ms:
        try:
            resp = requests.get("https://api.binance.com/api/v3/klines",
                                params={"symbol": "BTCUSDT", "interval": interval,
                                        "startTime": current, "limit": 1000}, timeout=10)
            data = resp.json()
            if not data: break
            all_data.extend(data)
            current = data[-1][0] + 1
            time.sleep(0.1)
        except Exception as e:
            print(f"    Error: {e}"); break
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_vol","taker_buy_quote_vol","ignore"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    return df

def add_indicators(df):
    tr = pd.DataFrame({
        "hl": df["high"]-df["low"],
        "hc": abs(df["high"]-df["close"].shift(1)),
        "lc": abs(df["low"]-df["close"].shift(1)),
    }).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    w=5
    sh=pd.Series(np.nan,index=df.index); sl=pd.Series(np.nan,index=df.index)
    for i in range(w,len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max(): sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min(): sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill(); df["swing_low"]=sl.ffill()
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_upper"]=df["close"].rolling(20).mean()+2*df["close"].rolling(20).std()
    return df.dropna().reset_index(drop=True)

def run_v3(data, max_c=3):
    long_positions=[]; short_positions=[]; trades=[]
    for i in range(2,len(data)):
        row=data.iloc[i]; price=row["close"]; atr=row["atr"]; hi=row["high"]; lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

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
                m=bm*0.6 if rsi>65 else bm
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
                m=bm*0.6 if rsi<35 else bm
                sl=min(p["trail_lo"]+atr*m, p["entry"])
                if hi>=sl:
                    pnl=(p["entry"]-sl)*p["qty"]-sl*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]}); closed=True
            if not closed: ns.append(p)
        short_positions=ns

        if len(long_positions)<max_c and row["rsi"]<30:
            qty=(MARGIN*LEVERAGE)/price
            long_positions.append({"entry":price,"qty":qty,"oqty":qty,
                "sl":row["swing_low"]-atr*0.3,"tp1":price+atr,"atr":atr,"phase":1,"trail_hi":price})
        if len(short_positions)<max_c and price>row["bb_upper"]:
            qty=(MARGIN*LEVERAGE)/price
            short_positions.append({"entry":price,"qty":qty,"oqty":qty,
                "sl":row["swing_high"]+atr*0.3,"tp1":price-atr,"atr":atr,"phase":1,"trail_lo":price})
    return pd.DataFrame(trades)

def calc_stats(tdf):
    if len(tdf)<3: return None
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    lt=tdf[tdf["side"]=="long"]; st=tdf[tdf["side"]=="short"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"exp":round(pnl/len(tdf),2),
            "l_pnl":round(lt["pnl"].sum(),2),"s_pnl":round(st["pnl"].sum(),2),
            "l_n":len(lt),"s_n":len(st)}

# ============================================================
# 抓取所有週期
# ============================================================
timeframes = ["5m", "15m", "30m", "1h", "2h", "4h"]
tf_data = {}

for tf in timeframes:
    print(f"Fetching {tf}...", end=" ", flush=True)
    raw = fetch_klines(tf, days_back=180)
    if len(raw) > 0:
        data = add_indicators(raw)
        tf_data[tf] = data
        print(f"{len(data)} bars ({data['datetime'].iloc[0].date()} ~ {data['datetime'].iloc[-1].date()})")
    else:
        print("Failed")

# ============================================================
# 全樣本回測
# ============================================================
print(f"\n{'='*120}")
print("全樣本回測（6 個月）")
print(f"{'='*120}")

header = (f"  {'週期':>6s} {'K線數':>7s} {'交易數':>7s} {'總損益(PnL)':>14s} {'勝率(WR)':>10s} "
          f"{'獲利因子(PF)':>14s} {'最大回撤(DD)':>14s} {'多單損益':>12s} {'空單損益':>12s} "
          f"{'每筆期望值':>12s} {'每日交易':>10s}")
print(header)
print(f"  {'-'*135}")

results = {}
for tf in timeframes:
    if tf not in tf_data: continue
    data = tf_data[tf]
    if len(data) < 120:
        print(f"  {tf:>6s} {len(data):>7d}  (資料不足)"); continue

    tdf = run_v3(data)
    s = calc_stats(tdf)
    if not s:
        print(f"  {tf:>6s} {len(data):>7d}  (交易數不足)"); continue

    s["bars"] = len(data)
    s["tdf"] = tdf
    s["data"] = data
    s["trades_per_day"] = s["n"] / 180
    results[tf] = s

    mark = " <-- 目前使用" if tf == "1h" else ""
    print(f"  {tf:>6s} {len(data):>7d} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% "
          f"{s['pf']:>13.2f} ${s['dd']:>12,.2f} ${s['l_pnl']:>+10,.0f} ${s['s_pnl']:>+10,.0f} "
          f"${s['exp']:>+10.2f} {s['trades_per_day']:>9.1f}{mark}")

# ============================================================
# Walk-Forward 驗證
# ============================================================
print(f"\n{'='*120}")
print("Walk-Forward 驗證（前 4 個月訓練 / 後 2 個月測試）")
print(f"{'='*120}")

print(f"\n  {'週期':>6s} {'樣本內損益(IS)':>16s} {'樣本外損益(OOS)':>16s} {'樣本外勝率':>12s} "
      f"{'樣本外PF':>10s} {'保留率':>8s} {'樣本外交易':>10s}")
print(f"  {'-'*85}")

wf_results = {}
for tf in timeframes:
    if tf not in results: continue
    data = results[tf]["data"]
    split = int(len(data) * (4/6))
    train = data.iloc[:split].reset_index(drop=True)
    test = data.iloc[split:].reset_index(drop=True)

    tdf_is = run_v3(train); tdf_oos = run_v3(test)
    s_is = calc_stats(tdf_is); s_oos = calc_stats(tdf_oos)

    if not s_is or not s_oos: continue

    is_monthly = s_is["pnl"] / 4 if s_is["pnl"] != 0 else 0.01
    oos_monthly = s_oos["pnl"] / 2
    retention = oos_monthly / is_monthly * 100 if is_monthly != 0 else 0

    wf_results[tf] = {"is": s_is, "oos": s_oos, "retention": retention}

    mark = " <-- 目前使用" if tf == "1h" else ""
    print(f"  {tf:>6s} ${s_is['pnl']:>+14,.2f} ${s_oos['pnl']:>+14,.2f} {s_oos['wr']:>11.1f}% "
          f"{s_oos['pf']:>9.2f} {retention:>7.0f}% {s_oos['n']:>10d}{mark}")

# ============================================================
# 風險調整後排名
# ============================================================
print(f"\n{'='*120}")
print("綜合排名（考慮收益、風險、穩定性）")
print(f"{'='*120}")

print(f"\n  {'週期':>6s} {'總損益':>12s} {'樣本外損益':>12s} {'獲利因子':>10s} {'最大回撤':>10s} "
      f"{'收益/回撤比':>12s} {'勝率':>8s} {'每筆期望值':>12s} {'保留率':>8s}")
print(f"  {'-'*100}")

ranking_data = []
for tf in timeframes:
    if tf not in results or tf not in wf_results: continue
    r = results[tf]; w = wf_results[tf]
    roi_dd = abs(r["pnl"] / r["dd"]) if r["dd"] != 0 else 999
    ranking_data.append({
        "tf": tf, "pnl": r["pnl"], "oos_pnl": w["oos"]["pnl"],
        "pf": r["pf"], "dd": r["dd"], "roi_dd": round(roi_dd, 1),
        "wr": r["wr"], "exp": r["exp"], "retention": w["retention"],
    })
    mark = " <-- 目前使用" if tf == "1h" else ""
    print(f"  {tf:>6s} ${r['pnl']:>+10,.0f} ${w['oos']['pnl']:>+10,.0f} {r['pf']:>9.2f} "
          f"${r['dd']:>9,.0f} {roi_dd:>11.1f}x {r['wr']:>7.1f}% ${r['exp']:>+10.2f} "
          f"{w['retention']:>7.0f}%{mark}")

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(20, 14))
fig.suptitle("多時間框架完整比較（含 5 分鐘）", fontsize=14, fontweight="bold")

valid = [tf for tf in timeframes if tf in results]

# 圖 1: 總損益
ax = axes[0][0]
pnls = [results[tf]["pnl"] for tf in valid]
colors = ["#FF9800" if tf == "1h" else "#2196F3" for tf in valid]
bars = ax.bar(valid, pnls, color=colors, edgecolor="black", linewidth=0.5)
for bar, val in zip(bars, pnls):
    ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"${val:+,.0f}",
            ha="center", va="bottom", fontsize=9)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("總損益(PnL) - 各週期"); ax.set_ylabel("損益 ($)"); ax.grid(True, alpha=0.3)

# 圖 2: 樣本外損益 + 保留率
ax = axes[0][1]
oos_pnls = [wf_results[tf]["oos"]["pnl"] for tf in valid if tf in wf_results]
rets = [wf_results[tf]["retention"] for tf in valid if tf in wf_results]
valid_wf = [tf for tf in valid if tf in wf_results]
x = range(len(valid_wf))
ax2 = ax.twinx()
bars = ax.bar(x, oos_pnls, color=["#FF9800" if tf=="1h" else "#4CAF50" for tf in valid_wf],
              edgecolor="black", linewidth=0.5)
ax2.plot(list(x), rets, 'ro-', linewidth=2, markersize=8)
for bar, val in zip(bars, oos_pnls):
    ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"${val:+,.0f}",
            ha="center", va="bottom", fontsize=8)
for i, r in enumerate(rets):
    ax2.text(i, r+3, f"{r:.0f}%", ha="center", fontsize=8, color="red")
ax.set_xticks(list(x)); ax.set_xticklabels(valid_wf)
ax.set_ylabel("樣本外損益(OOS) ($)"); ax2.set_ylabel("保留率 (%)", color="red")
ax.set_title("樣本外損益 + 保留率"); ax.grid(True, alpha=0.3)

# 圖 3: 累計曲線
ax = axes[1][0]
for tf in valid:
    if "tdf" in results[tf]:
        cum = results[tf]["tdf"].sort_values("dt")["pnl"].cumsum()
        lw = 2.5 if tf == "1h" else 1.0
        alpha = 1.0 if tf == "1h" else 0.5
        ax.plot(range(len(cum)), cum.values, linewidth=lw, alpha=alpha,
                label=f"{tf} ${results[tf]['pnl']:+,.0f}")
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("累計損益曲線"); ax.set_ylabel("損益 ($)"); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]; ax.axis("off")
table_header = ["週期", "K線數", "交易數", "總損益\n(PnL)", "勝率\n(WR)", "獲利因子\n(PF)",
                "最大回撤\n(DD)", "每筆\n期望值", "收益/\n回撤比", "樣本外\n(OOS)", "保留率"]
table_rows = []
for tf in valid:
    r = results[tf]
    w = wf_results.get(tf, {})
    oos_pnl = w.get("oos", {}).get("pnl", 0)
    ret = w.get("retention", 0)
    roi_dd = abs(r["pnl"]/r["dd"]) if r["dd"]!=0 else 999
    table_rows.append([
        tf, f"{r['bars']:,}", str(r["n"]),
        f"${r['pnl']:+,.0f}", f"{r['wr']}%", f"{r['pf']}",
        f"${r['dd']:,.0f}", f"${r['exp']:+.1f}",
        f"{roi_dd:.0f}x", f"${oos_pnl:+,.0f}", f"{ret:.0f}%"
    ])

table = ax.table(cellText=table_rows, colLabels=table_header, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1.0, 1.7)
for j in range(11):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
row_1h = [i for i, tf in enumerate(valid) if tf == "1h"]
if row_1h:
    for j in range(11):
        table[row_1h[0]+1, j].set_facecolor("#FFF2CC")

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_timeframe_full.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 最終結論
# ============================================================
print(f"\n{'='*120}")
print("最終結論")
print(f"{'='*120}")

if ranking_data:
    best_pnl = max(ranking_data, key=lambda x: x["pnl"])
    best_oos = max(ranking_data, key=lambda x: x["oos_pnl"])
    best_pf = max(ranking_data, key=lambda x: x["pf"])
    best_roi_dd = max(ranking_data, key=lambda x: x["roi_dd"])

    print(f"\n  總損益最高：      {best_pnl['tf']}  ${best_pnl['pnl']:+,.0f}")
    print(f"  樣本外最高：      {best_oos['tf']}  ${best_oos['oos_pnl']:+,.0f}")
    print(f"  獲利因子最高：    {best_pf['tf']}  PF {best_pf['pf']}")
    print(f"  收益/回撤比最高： {best_roi_dd['tf']}  {best_roi_dd['roi_dd']}x")
