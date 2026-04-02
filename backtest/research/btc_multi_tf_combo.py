"""
多時間框架組合測試
用數據找出最佳組合方式

組合方式：
  A. 純 5m（基線：收益最高）
  B. 純 1h（基線：風險最低）
  C. 1h 信號 + 5m 進場（1h RSI<30 時，等 5m 也 RSI<30 才進）
  D. 5m 信號 + 1h 過濾（5m RSI<30，且 1h RSI<40 才進）
  E. 5m 信號 + 1h 嚴格過濾（5m RSI<30，且 1h RSI<35 才進）
  F. 15m 信號 + 1h 過濾
  G. 5m 極端才進（5m RSI<20，不看 1h）
  H. 5m 信號 + 1h 趨勢（1h 收盤 < BB mid = 偏空環境才做多）
  I. 雙重確認（5m RSI<30 + 5m close < 5m BB lower）
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

print("Fetching data...")
raw_5m = fetch_klines("5m", 180); print(f"  5m: {len(raw_5m)} bars")
raw_15m = fetch_klines("15m", 180); print(f"  15m: {len(raw_15m)} bars")
raw_1h = fetch_klines("1h", 180); print(f"  1h: {len(raw_1h)} bars")

df_5m = add_indicators(raw_5m)
df_15m = add_indicators(raw_15m)
df_1h = add_indicators(raw_1h)

# ============================================================
# 對齊多時間框架：把 1h 指標映射到 5m/15m
# ============================================================
print("Aligning timeframes...")

# 1h 指標映射到 5m：每根 5m 找到它所屬的 1h 時段
df_1h_map = df_1h[["datetime","rsi","bb_upper","bb_mid","bb_lower","atr","swing_high","swing_low","atr_pctile"]].copy()
df_1h_map = df_1h_map.rename(columns={c: f"h1_{c}" for c in df_1h_map.columns if c != "datetime"})
df_1h_map["hour_key"] = df_1h_map["datetime"].dt.floor("h")

df_5m["hour_key"] = df_5m["datetime"].dt.floor("h")
df_5m = df_5m.merge(df_1h_map.drop(columns="datetime"), on="hour_key", how="left")

# 15m 指標映射到 5m
df_15m_map = df_15m[["datetime","rsi","bb_upper","bb_lower","atr"]].copy()
df_15m_map = df_15m_map.rename(columns={c: f"m15_{c}" for c in df_15m_map.columns if c != "datetime"})
df_15m_map["q15_key"] = df_15m_map["datetime"].dt.floor("15min")
df_5m["q15_key"] = df_5m["datetime"].dt.floor("15min")
df_5m = df_5m.merge(df_15m_map.drop(columns="datetime"), on="q15_key", how="left")

df_5m = df_5m.dropna().reset_index(drop=True)
print(f"  Aligned 5m data: {len(df_5m)} bars")

# ============================================================
# 回測引擎（通用，接受 entry condition 函數）
# ============================================================
def run_backtest(data, long_cond_fn, short_cond_fn, sl_col="swing_low", sh_col="swing_high",
                 atr_col="atr", atr_pctile_col="atr_pctile", rsi_col="rsi", max_c=3, label=""):
    long_positions=[]; short_positions=[]; trades=[]

    for i in range(2, len(data)):
        row = data.iloc[i]
        price=row["close"]; atr=row[atr_col]; hi=row["high"]; lo=row["low"]
        ap = row[atr_pctile_col] if not np.isnan(row[atr_pctile_col]) else 50
        rsi_val = row[rsi_col]
        bm = 1.0 + (ap/100)*1.5

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

        # 進場
        if len(long_positions)<max_c and long_cond_fn(row):
            qty=(MARGIN*LEVERAGE)/price
            long_positions.append({"entry":price,"qty":qty,"oqty":qty,
                "sl":row[sl_col]-atr*0.3,"tp1":price+atr,"atr":atr,"phase":1,"trail_hi":price})
        if len(short_positions)<max_c and short_cond_fn(row):
            qty=(MARGIN*LEVERAGE)/price
            short_positions.append({"entry":price,"qty":qty,"oqty":qty,
                "sl":row[sh_col]+atr*0.3,"tp1":price-atr,"atr":atr,"phase":1,"trail_lo":price})

    return pd.DataFrame(trades)

def stats(tdf):
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
# 定義所有組合
# ============================================================
combos = [
    # (名稱, data, long_cond, short_cond, sl_col, sh_col, atr_col, atr_pctile_col, rsi_col)
    ("A. 純 5m",
     df_5m,
     lambda r: r["rsi"] < 30,
     lambda r: r["close"] > r["bb_upper"],
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("B. 純 1h",
     df_1h,
     lambda r: r["rsi"] < 30,
     lambda r: r["close"] > r["bb_upper"],
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("C. 1h信號+5m進場\n(1h RSI<30時 等5m也RSI<30)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["h1_rsi"] < 30,
     lambda r: r["close"] > r["bb_upper"] and r["close"] > r["h1_bb_upper"],
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("D. 5m信號+1h寬過濾\n(5m RSI<30 且 1h RSI<40)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["h1_rsi"] < 40,
     lambda r: r["close"] > r["bb_upper"] and r["h1_rsi"] > 60,
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("E. 5m信號+1h嚴格過濾\n(5m RSI<30 且 1h RSI<35)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["h1_rsi"] < 35,
     lambda r: r["close"] > r["bb_upper"] and r["h1_rsi"] > 65,
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("F. 15m信號+1h過濾\n(15m RSI<30 且 1h RSI<40)",
     df_5m,
     lambda r: r["m15_rsi"] < 30 and r["h1_rsi"] < 40,
     lambda r: r["close"] > r["m15_bb_upper"] and r["h1_rsi"] > 60,
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("G. 5m極端\n(5m RSI<20)",
     df_5m,
     lambda r: r["rsi"] < 20,
     lambda r: r["rsi"] > 80,
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("H. 5m信號+1h趨勢\n(做多:1h<BB mid 做空:1h>BB mid)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["close"] < r["h1_bb_mid"],
     lambda r: r["close"] > r["bb_upper"] and r["close"] > r["h1_bb_mid"],
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("I. 5m雙重確認\n(5m RSI<30 + close<BB lower)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["close"] < r["bb_lower"],
     lambda r: r["rsi"] > 70 and r["close"] > r["bb_upper"],
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),

    ("J. 5m信號+1h方向+15m確認\n(三重過濾)",
     df_5m,
     lambda r: r["rsi"] < 30 and r["h1_rsi"] < 40 and r["m15_rsi"] < 35,
     lambda r: r["close"] > r["bb_upper"] and r["h1_rsi"] > 60 and r["m15_rsi"] > 65,
     "swing_low", "swing_high", "atr", "atr_pctile", "rsi"),
]

# ============================================================
# 回測所有組合
# ============================================================
print(f"\n{'='*140}")
print("全樣本回測")
print(f"{'='*140}")

header = (f"  {'組合':<35s} {'交易數':>7s} {'總損益(PnL)':>14s} {'勝率(WR)':>10s} "
          f"{'獲利因子(PF)':>14s} {'最大回撤(DD)':>14s} {'收益/回撤':>10s} {'每筆期望值':>12s} "
          f"{'多單損益':>10s} {'空單損益':>10s}")
print(header)
print(f"  {'-'*140}")

all_results = []
for name, data, lc, sc, sl_c, sh_c, a_c, ap_c, r_c in combos:
    short_name = name.split("\n")[0]
    tdf = run_backtest(data, lc, sc, sl_c, sh_c, a_c, ap_c, r_c)
    s = stats(tdf)
    if not s:
        print(f"  {short_name:<35s} (交易數不足)")
        continue
    all_results.append({"name": name, "short_name": short_name, **s})
    print(f"  {short_name:<35s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% "
          f"{s['pf']:>13.2f} ${s['dd']:>12,.2f} {s['roi_dd']:>9.1f}x ${s['exp']:>+10.2f} "
          f"${s['l_pnl']:>+9,.0f} ${s['s_pnl']:>+9,.0f}")

# ============================================================
# Walk-Forward
# ============================================================
print(f"\n{'='*140}")
print("Walk-Forward 驗證（前 4 個月 / 後 2 個月）")
print(f"{'='*140}")

print(f"\n  {'組合':<35s} {'樣本內(IS)':>14s} {'樣本外(OOS)':>14s} {'OOS勝率':>10s} "
      f"{'OOS PF':>10s} {'保留率':>8s} {'OOS交易':>8s}")
print(f"  {'-'*100}")

for r in all_results:
    name = r["short_name"]
    tdf = r["tdf"]
    # 按時間切分
    tdf_sorted = tdf.sort_values("dt").reset_index(drop=True)
    n = len(tdf_sorted)
    split = int(n * (4/6))
    is_tdf = tdf_sorted.iloc[:split]
    oos_tdf = tdf_sorted.iloc[split:]

    is_s = stats(is_tdf)
    oos_s = stats(oos_tdf)

    if not is_s or not oos_s:
        print(f"  {name:<35s} (資料不足)")
        continue

    is_monthly = is_s["pnl"]/4 if is_s["pnl"]!=0 else 0.01
    oos_monthly = oos_s["pnl"]/2
    retention = oos_monthly/is_monthly*100 if is_monthly!=0 else 0

    r["oos_pnl"] = oos_s["pnl"]
    r["oos_wr"] = oos_s["wr"]
    r["oos_pf"] = oos_s["pf"]
    r["retention"] = round(retention, 0)
    r["oos_n"] = oos_s["n"]

    print(f"  {name:<35s} ${is_s['pnl']:>+12,.2f} ${oos_s['pnl']:>+12,.2f} {oos_s['wr']:>9.1f}% "
          f"{oos_s['pf']:>9.2f} {retention:>7.0f}% {oos_s['n']:>8d}")

# ============================================================
# 綜合排名
# ============================================================
print(f"\n{'='*140}")
print("綜合排名")
print(f"{'='*140}")

# 計算綜合分數：OOS PnL (40%) + PF (20%) + 回撤倒數 (20%) + 保留率 (20%)
valid = [r for r in all_results if "oos_pnl" in r]

if valid:
    max_oos = max(r["oos_pnl"] for r in valid)
    max_pf = max(r["pf"] for r in valid)
    min_dd = min(abs(r["dd"]) for r in valid)
    max_ret = max(r["retention"] for r in valid)

    for r in valid:
        r["score"] = (
            (r["oos_pnl"] / max_oos * 40 if max_oos > 0 else 0) +
            (r["pf"] / max_pf * 20 if max_pf > 0 else 0) +
            (min_dd / abs(r["dd"]) * 20 if r["dd"] != 0 else 20) +
            (r["retention"] / max_ret * 20 if max_ret > 0 else 0)
        )

    valid.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n  {'排名':>4s} {'組合':<35s} {'綜合分':>8s} {'總損益':>12s} {'樣本外':>12s} "
          f"{'獲利因子':>10s} {'回撤':>10s} {'勝率':>8s} {'保留率':>8s}")
    print(f"  {'-'*110}")

    for i, r in enumerate(valid):
        mark = " ***" if i == 0 else ""
        print(f"  {i+1:>4d} {r['short_name']:<35s} {r['score']:>7.1f} ${r['pnl']:>+10,.0f} "
              f"${r['oos_pnl']:>+10,.0f} {r['pf']:>9.2f} ${r['dd']:>9,.0f} "
              f"{r['wr']:>7.1f}% {r['retention']:>7.0f}%{mark}")

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(22, 16))
fig.suptitle("多時間框架組合測試", fontsize=14, fontweight="bold")

# 圖 1: 樣本外損益排名
ax = axes[0][0]
names = [r["short_name"][:30] for r in valid]
oos_pnls = [r["oos_pnl"] for r in valid]
colors = ["#FF9800" if "1h" in r["short_name"] and "5m" not in r["short_name"]
          else "#4CAF50" if r == valid[0] else "#2196F3" for r in valid]
bars = ax.barh(range(len(names)), oos_pnls, color=colors, edgecolor="black", linewidth=0.5)
ax.set_yticks(range(len(names))); ax.set_yticklabels(names, fontsize=7); ax.invert_yaxis()
for i, v in enumerate(oos_pnls):
    ax.text(v+50, i, f"${v:+,.0f}", va="center", fontsize=7)
ax.set_xlabel("損益 ($)"); ax.set_title("樣本外損益(OOS) 排名"); ax.grid(True, alpha=0.3, axis="x")

# 圖 2: 收益 vs 回撤 散佈圖
ax = axes[0][1]
for r in valid:
    color = "#FF9800" if "1h" in r["short_name"] and "5m" not in r["short_name"] \
            else "#4CAF50" if r == valid[0] else "#2196F3"
    ax.scatter(abs(r["dd"]), r["oos_pnl"], s=100, color=color, edgecolors="black", zorder=5)
    ax.annotate(r["short_name"][:15], (abs(r["dd"]), r["oos_pnl"]),
                textcoords="offset points", xytext=(5, 5), fontsize=6)
ax.set_xlabel("最大回撤(DD) ($, 絕對值)"); ax.set_ylabel("樣本外損益(OOS) ($)")
ax.set_title("收益 vs 風險（右上角最佳）"); ax.grid(True, alpha=0.3)

# 圖 3: 累計曲線（Top 5）
ax = axes[1][0]
for i, r in enumerate(valid[:5]):
    cum = r["tdf"].sort_values("dt")["pnl"].cumsum()
    lw = 2.5 if i == 0 else 1.0
    ax.plot(range(len(cum)), cum.values, linewidth=lw,
            label=f"{r['short_name'][:25]} ${r['pnl']:+,.0f}", alpha=0.8 if i==0 else 0.5)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("Top 5 累計損益"); ax.set_ylabel("損益 ($)"); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]; ax.axis("off")
table_header = ["排名","組合","交易數","總損益\n(PnL)","樣本外\n(OOS)","勝率\n(WR)",
                "獲利因子\n(PF)","回撤\n(DD)","收益/\n回撤","保留率","綜合分"]
table_rows = []
for i, r in enumerate(valid[:8]):
    table_rows.append([
        str(i+1), r["short_name"][:28], str(r["n"]),
        f"${r['pnl']:+,.0f}", f"${r['oos_pnl']:+,.0f}", f"{r['wr']}%",
        f"{r['pf']}", f"${r['dd']:,.0f}", f"{r['roi_dd']}x",
        f"{r['retention']:.0f}%", f"{r['score']:.1f}"
    ])
table = ax.table(cellText=table_rows, colLabels=table_header, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(7); table.scale(1.0, 1.6)
for j in range(11):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
for j in range(11):
    table[1, j].set_facecolor("#FFF2CC")

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_multi_tf_combo.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 最終建議
# ============================================================
print(f"\n{'='*140}")
print("最終建議")
print(f"{'='*140}")

if valid:
    best = valid[0]
    print(f"\n  綜合最佳：{best['name']}")
    print(f"    總損益(PnL)：    ${best['pnl']:+,.2f}")
    print(f"    樣本外(OOS)：    ${best['oos_pnl']:+,.2f}")
    print(f"    勝率(WR)：       {best['wr']}%")
    print(f"    獲利因子(PF)：   {best['pf']}")
    print(f"    最大回撤(DD)：   ${best['dd']:,.2f}")
    print(f"    收益/回撤比：    {best['roi_dd']}x")
    print(f"    保留率：         {best['retention']:.0f}%")
    print(f"    交易數：         {best['n']}")
    print(f"    每筆期望值：     ${best['exp']:+,.2f}")
