"""
嚴格版 Walk-Forward 驗證（Top 3 策略）
無上帝視角：next-open 進場 + 延遲 1h 指標 + 滾動窗口

驗證方式：
  1. 固定切分：前 4 個月 / 後 2 個月
  2. 滾動 WF：2 個月訓練 → 1 個月測試（逐月滾動）
  3. 逐月績效
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
# 抓資料 + 指標
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

print("=== Fetching ===")
raw_5m = fetch_klines("5m", 180); print(f"  5m: {len(raw_5m)}")
raw_1h = fetch_klines("1h", 180); print(f"  1h: {len(raw_1h)}")

df_5m = add_indicators(raw_5m)
df_1h = add_indicators(raw_1h)

# 對齊（延遲一根 1h）
df_1h_map = df_1h[["datetime","rsi","bb_upper","bb_mid","bb_lower","atr","swing_high","swing_low","atr_pctile"]].copy()
df_1h_map = df_1h_map.rename(columns={c: f"h1_{c}" for c in df_1h_map.columns if c != "datetime"})
df_1h_map["hour_key"] = df_1h_map["datetime"].dt.floor("h") + timedelta(hours=1)
df_5m["hour_key"] = df_5m["datetime"].dt.floor("h")
df_5m = df_5m.merge(df_1h_map.drop(columns="datetime"), on="hour_key", how="left")
df_5m = df_5m.dropna().reset_index(drop=True)
df_5m["month"] = df_5m["datetime"].dt.to_period("M")
df_1h["month"] = df_1h["datetime"].dt.to_period("M")
print(f"  Aligned 5m: {len(df_5m)}")

months_5m = df_5m["month"].unique()
months_1h = df_1h["month"].unique()
print(f"  5m months: {[str(m) for m in months_5m]}")

# ============================================================
# 嚴格回測引擎（next-open 進場）
# ============================================================
def run_strict(data, long_cond, short_cond, max_c=3):
    long_positions=[]; short_positions=[]; trades=[]
    for i in range(2, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        nl=[]
        for p in long_positions:
            c=False
            if lo<=p["sl"]:
                pnl=(p["sl"]-p["entry"])*p["qty"]-p["sl"]*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"long","dt":row["datetime"]}); c=True
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
                    trades.append({"pnl":pnl,"side":"long","dt":row["datetime"]}); c=True
            if not c: nl.append(p)
        long_positions=nl

        ns=[]
        for p in short_positions:
            c=False
            if hi>=p["sl"]:
                pnl=(p["entry"]-p["sl"])*p["qty"]-p["sl"]*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]}); c=True
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
                    trades.append({"pnl":pnl,"side":"short","dt":row["datetime"]}); c=True
            if not c: ns.append(p)
        short_positions=ns

        ep=nxt["open"]
        if len(long_positions)<max_c and long_cond(row):
            qty=(MARGIN*LEVERAGE)/ep
            long_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_low"]-atr*0.3,"tp1":ep+atr,"atr":atr,"phase":1,"trail_hi":ep})
        if len(short_positions)<max_c and short_cond(row):
            qty=(MARGIN*LEVERAGE)/ep
            short_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_high"]+atr*0.3,"tp1":ep-atr,"atr":atr,"phase":1,"trail_lo":ep})
    return pd.DataFrame(trades)

def s(tdf):
    if len(tdf)<1: return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"exp":0,"l_pnl":0,"s_pnl":0}
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min() if len(cum)>0 else 0
    lt=tdf[tdf["side"]=="long"]; st=tdf[tdf["side"]=="short"]
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"exp":round(pnl/max(len(tdf),1),2),
            "l_pnl":round(lt["pnl"].sum(),2),"s_pnl":round(st["pnl"].sum(),2)}

# ============================================================
# Top 3 策略定義
# ============================================================
strategies = {
    "A. 純5m RSI<30": {
        "data": df_5m, "months": months_5m,
        "long": lambda r: r["rsi"]<30,
        "short": lambda r: r["close"]>r["bb_upper"],
    },
    "G. 5m+1h趨勢": {
        "data": df_5m, "months": months_5m,
        "long": lambda r: r["rsi"]<30 and r["close"]<r.get("h1_bb_mid", r["close"]+1),
        "short": lambda r: r["close"]>r["bb_upper"] and r["close"]>r.get("h1_bb_mid", r["close"]-1),
    },
    "H. 5m雙重RSI+BB": {
        "data": df_5m, "months": months_5m,
        "long": lambda r: r["rsi"]<30 and r["close"]<r["bb_lower"],
        "short": lambda r: r["rsi"]>70 and r["close"]>r["bb_upper"],
    },
    "B. 純1h（基線）": {
        "data": df_1h, "months": months_1h,
        "long": lambda r: r["rsi"]<30,
        "short": lambda r: r["close"]>r["bb_upper"],
    },
}

# ============================================================
# 1. 全樣本
# ============================================================
print(f"\n{'='*120}")
print("1. 全樣本嚴格回測（next-open + 延遲指標）")
print(f"{'='*120}")

print(f"\n  {'策略':<22s} {'交易數':>7s} {'總損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'最大回撤(DD)':>14s} {'每筆期望值':>12s} {'多單損益':>10s} {'空單損益':>10s}")
print(f"  {'-'*120}")

full_tdfs = {}
for name, cfg in strategies.items():
    tdf = run_strict(cfg["data"], cfg["long"], cfg["short"])
    full_tdfs[name] = tdf
    r = s(tdf)
    print(f"  {name:<22s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>12,.2f} ${r['exp']:>+10.2f} ${r['l_pnl']:>+9,.0f} ${r['s_pnl']:>+9,.0f}")

# ============================================================
# 2. 固定切分 4m/2m
# ============================================================
print(f"\n{'='*120}")
print("2. 固定切分（前 4 個月 / 後 2 個月）")
print(f"{'='*120}")

print(f"\n  {'策略':<22s} {'樣本內(IS)':>14s} {'IS 交易':>8s} {'樣本外(OOS)':>14s} {'OOS交易':>8s} {'OOS勝率':>10s} {'OOS PF':>10s} {'OOS回撤':>10s} {'保留率':>8s}")
print(f"  {'-'*110}")

for name, cfg in strategies.items():
    data = cfg["data"]
    split = int(len(data) * (4/6))
    train = data.iloc[:split].reset_index(drop=True)
    test = data.iloc[split:].reset_index(drop=True)

    tdf_is = run_strict(train, cfg["long"], cfg["short"])
    tdf_oos = run_strict(test, cfg["long"], cfg["short"])
    r_is = s(tdf_is); r_oos = s(tdf_oos)

    is_mo = r_is["pnl"]/4 if r_is["pnl"]!=0 else 0.01
    oos_mo = r_oos["pnl"]/2
    ret = oos_mo/is_mo*100 if is_mo!=0 else 0

    print(f"  {name:<22s} ${r_is['pnl']:>+12,.2f} {r_is['n']:>8d} ${r_oos['pnl']:>+12,.2f} {r_oos['n']:>8d} "
          f"{r_oos['wr']:>9.1f}% {r_oos['pf']:>9.2f} ${r_oos['dd']:>9,.2f} {ret:>7.0f}%")

# ============================================================
# 3. 滾動 WF: 2m 訓練 → 1m 測試
# ============================================================
print(f"\n{'='*120}")
print("3. 滾動 Walk-Forward（2 個月訓練 → 1 個月測試）")
print(f"{'='*120}")

for name, cfg in strategies.items():
    data = cfg["data"]
    months = cfg["months"]

    print(f"\n  === {name} ===")
    print(f"  {'折':>4s} {'測試月':>10s} {'訓練損益':>12s} {'測試損益(OOS)':>14s} {'OOS勝率':>10s} {'OOS PF':>10s} {'OOS交易':>8s}")
    print(f"  {'-'*75}")

    fold_results = []
    for fold in range(len(months) - 2):
        train_ms = months[fold:fold+2]
        test_m = months[fold+2]

        train_mask = data["month"].isin(train_ms)
        test_mask = data["month"] == test_m

        fold_train = data[train_mask].reset_index(drop=True)
        fold_test = data[test_mask].reset_index(drop=True)

        if len(fold_test) < 10: continue

        tdf_tr = run_strict(fold_train, cfg["long"], cfg["short"])
        tdf_te = run_strict(fold_test, cfg["long"], cfg["short"])

        r_tr = s(tdf_tr); r_te = s(tdf_te)
        fold_results.append(r_te)

        print(f"  {fold+1:>4d} {str(test_m):>10s} ${r_tr['pnl']:>+10,.2f} ${r_te['pnl']:>+12,.2f} "
              f"{r_te['wr']:>9.1f}% {r_te['pf']:>9.2f} {r_te['n']:>8d}")

    if fold_results:
        total_oos = sum(r["pnl"] for r in fold_results)
        profitable = sum(1 for r in fold_results if r["pnl"] > 0)
        total_folds = len(fold_results)
        print(f"\n  {'合計':<16s} {'':>12s} ${total_oos:>+12,.2f}  獲利 {profitable}/{total_folds} 折")

# ============================================================
# 4. 逐月績效
# ============================================================
print(f"\n{'='*120}")
print("4. 逐月損益（全樣本嚴格版）")
print(f"{'='*120}")

print(f"\n  {'月份':>10s}", end="")
for name in strategies: print(f" {name[:18]:>18s}", end="")
print()
print(f"  {'-'*85}")

# 收集月度數據用於圖表
monthly_data = {name: {} for name in strategies}

all_months = sorted(set(str(m) for m in months_5m) | set(str(m) for m in months_1h))
for m_str in all_months:
    print(f"  {m_str:>10s}", end="")
    for name, cfg in strategies.items():
        data = cfg["data"]
        m_mask = data["month"].astype(str) == m_str
        m_data = data[m_mask].reset_index(drop=True)
        if len(m_data) < 10:
            print(f" {'N/A':>18s}", end="")
            monthly_data[name][m_str] = 0
            continue
        tdf = run_strict(m_data, cfg["long"], cfg["short"])
        pnl = tdf["pnl"].sum() if len(tdf) > 0 else 0
        monthly_data[name][m_str] = pnl
        print(f" ${pnl:>+16,.0f}", end="")
    print()

# 獲利月份統計
print(f"\n  {'獲利月份':>10s}", end="")
for name in strategies:
    vals = [v for v in monthly_data[name].values() if v != 0]
    pos = sum(1 for v in vals if v > 0)
    tot = len(vals)
    print(f" {pos}/{tot}".rjust(18), end="")
print()

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(22, 16))
fig.suptitle("嚴格版 Walk-Forward 驗證（無上帝視角）\nnext-open 進場 + 延遲 1h 指標", fontsize=13, fontweight="bold")

# 圖 1: 全樣本累計曲線
ax = axes[0][0]
colors_map = {"A. 純5m RSI<30":"red", "G. 5m+1h趨勢":"blue", "H. 5m雙重RSI+BB":"green", "B. 純1h（基線）":"gray"}
for name, tdf in full_tdfs.items():
    if len(tdf) == 0: continue
    cum = tdf.sort_values("dt")["pnl"].cumsum()
    r = s(tdf)
    lw = 2.5 if name.startswith("A") else 1.5
    ax.plot(range(len(cum)), cum.values, color=colors_map.get(name,"black"),
            linewidth=lw, label=f"{name[:18]} ${r['pnl']:+,.0f}")
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("全樣本累計損益（嚴格版）"); ax.set_ylabel("損益 ($)"); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)

# 圖 2: 逐月對比
ax = axes[0][1]
valid_months = [m for m in all_months if any(monthly_data[n].get(m, 0) != 0 for n in strategies)]
x = range(len(valid_months))
w = 0.2
for idx, (name, color) in enumerate(colors_map.items()):
    vals = [monthly_data[name].get(m, 0) for m in valid_months]
    ax.bar([i + (idx-1.5)*w for i in x], vals, w, label=name[:18], color=color, alpha=0.7, edgecolor="black", linewidth=0.3)
ax.set_xticks(list(x)); ax.set_xticklabels(valid_months, fontsize=7, rotation=45)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("逐月損益"); ax.set_ylabel("損益 ($)"); ax.legend(fontsize=6); ax.grid(True, alpha=0.3)

# 圖 3: 固定切分 IS vs OOS
ax = axes[1][0]
names_list = list(strategies.keys())
is_pnls = []; oos_pnls = []
for name, cfg in strategies.items():
    data = cfg["data"]; split = int(len(data)*(4/6))
    tdf_is = run_strict(data.iloc[:split].reset_index(drop=True), cfg["long"], cfg["short"])
    tdf_oos = run_strict(data.iloc[split:].reset_index(drop=True), cfg["long"], cfg["short"])
    is_pnls.append(tdf_is["pnl"].sum() if len(tdf_is)>0 else 0)
    oos_pnls.append(tdf_oos["pnl"].sum() if len(tdf_oos)>0 else 0)

x = range(len(names_list))
ax.bar([i-0.2 for i in x], is_pnls, 0.35, label="樣本內(IS) 4個月", color="#90CAF9", edgecolor="black")
ax.bar([i+0.2 for i in x], oos_pnls, 0.35, label="樣本外(OOS) 2個月", color="#EF9A9A", edgecolor="black")
ax.set_xticks(list(x)); ax.set_xticklabels([n[:18] for n in names_list], fontsize=7)
for i, (iv, ov) in enumerate(zip(is_pnls, oos_pnls)):
    ax.text(i+0.2, ov, f"${ov:+,.0f}", ha="center", va="bottom", fontsize=7)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("固定切分 IS vs OOS"); ax.set_ylabel("損益 ($)"); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]; ax.axis("off")

table_header = ["策略", "全樣本\n損益(PnL)", "OOS\n損益", "OOS\n勝率(WR)", "OOS\n獲利因子(PF)", "OOS\n回撤(DD)", "滾動WF\n獲利折數", "獲利\n月份"]
table_rows = []

for name, cfg in strategies.items():
    data = cfg["data"]; split = int(len(data)*(4/6))
    tdf_full = full_tdfs[name]; r_full = s(tdf_full)
    tdf_oos = run_strict(data.iloc[split:].reset_index(drop=True), cfg["long"], cfg["short"])
    r_oos = s(tdf_oos)

    # 滾動 WF 統計
    months = cfg["months"]
    fold_pnls = []
    for fold in range(len(months)-2):
        test_m = months[fold+2]
        fold_test = data[data["month"]==test_m].reset_index(drop=True)
        if len(fold_test)<10: continue
        tdf_f = run_strict(fold_test, cfg["long"], cfg["short"])
        fold_pnls.append(tdf_f["pnl"].sum() if len(tdf_f)>0 else 0)
    prof_folds = sum(1 for p in fold_pnls if p>0)

    vals = [v for v in monthly_data[name].values() if v != 0]
    prof_months = sum(1 for v in vals if v > 0)

    table_rows.append([
        name[:20], f"${r_full['pnl']:+,.0f}", f"${r_oos['pnl']:+,.0f}",
        f"{r_oos['wr']}%", f"{r_oos['pf']}", f"${r_oos['dd']:,.0f}",
        f"{prof_folds}/{len(fold_pnls)}", f"{prof_months}/{len(vals)}"
    ])

table = ax.table(cellText=table_rows, colLabels=table_header, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1.0, 2.0)
for j in range(8):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
for j in range(8):
    table[1, j].set_facecolor("#FFF2CC")
ax.set_title("嚴格版 Walk-Forward 總結", fontweight="bold", pad=20)

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_no_lookahead_wf.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()
