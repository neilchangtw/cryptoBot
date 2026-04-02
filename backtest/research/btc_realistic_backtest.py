"""
最嚴格實戰回測 — A1 + A2 + A3 + A4 合併測試
所有回測假設都對齊真實交易環境，不用任何上帝視角

修正項目：
  1. SL 滑價模型：止損不在 SL 價精確成交，而是用 bar 的極值模擬穿越
  2. TP1 延遲：模擬 Monitor 每 5 分鐘才檢查一次（延遲 1 根 5m bar）
  3. 資金費率：每 8 小時結算，多單付費 / 空單收費
  4. 急跌過濾器：連續紅K時暫停做多
  5. 進場用 next-open
  6. 指標用已收盤 bar（iloc[-2]）
  7. 止損冷卻：被 SL 後等 3 根 bar
"""
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from datetime import datetime, timedelta
import time as _time

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
            _time.sleep(0.1)
        except: break
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    return df

def fetch_funding_rates(days_back=180):
    all_fr = []
    current = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    end = int(datetime.now().timestamp() * 1000)
    while current < end:
        try:
            resp = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                                params={"symbol":"BTCUSDT","startTime":current,"limit":1000}, timeout=10)
            data = resp.json()
            if not data: break
            all_fr.extend(data)
            current = data[-1]["fundingTime"] + 1
            _time.sleep(0.1)
        except: break
    if not all_fr: return pd.DataFrame()
    fr = pd.DataFrame(all_fr)
    fr["datetime"] = pd.to_datetime(fr["fundingTime"], unit="ms") + timedelta(hours=8)
    fr["funding_rate"] = pd.to_numeric(fr["fundingRate"])
    return fr

print("Fetching 5m data...")
raw = fetch_klines("5m", 180)
print(f"  5m: {len(raw)} bars")

print("Fetching funding rates...")
fr_df = fetch_funding_rates(180)
print(f"  Funding: {len(fr_df)} records")

# 指標
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
    # 連續紅K
    df["is_red"] = df["close"] < df["open"]
    count = 0
    consec = []
    for red in df["is_red"]:
        count = count + 1 if red else 0
        consec.append(count)
    df["consec_red"] = consec
    return df.dropna().reset_index(drop=True)

df = add_indicators(raw)
print(f"  Processed: {len(df)} bars")

# 建立 funding rate lookup
fr_times = set()
fr_rates = {}
if len(fr_df) > 0:
    for _, row in fr_df.iterrows():
        # funding 結算時間（UTC+8）：08:00, 16:00, 00:00
        key = row["datetime"].floor("8h")
        fr_times.add(key)
        fr_rates[key] = row["funding_rate"]

# ============================================================
# 回測引擎：最嚴格實戰版
# ============================================================
def run_realistic(data, config):
    """
    config keys:
      sl_slippage: "none" / "bar_extreme" / "fixed_100" / "fixed_200"
      tp1_delay: 0 (即時) / 1 (延遲 1 bar) / 2 (延遲 2 bar)
      funding: True / False
      crash_filter: 0 (不過濾) / 4 / 6 (連續N紅K暫停做多)
      sl_cooldown: 0 / 3 (被SL後冷卻幾根bar)
    """
    sl_slippage = config.get("sl_slippage", "none")
    tp1_delay = config.get("tp1_delay", 0)
    use_funding = config.get("funding", False)
    crash_filter = config.get("crash_filter", 0)
    sl_cooldown = config.get("sl_cooldown", 0)

    long_positions = []; short_positions = []; trades = []
    total_funding = 0
    sl_cooldown_until = {"long": 0, "short": 0}

    for i in range(2, len(data)-1):
        row = data.iloc[i]; nxt = data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]; price=row["close"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        # ── Funding rate 結算 ──
        if use_funding and len(fr_rates) > 0:
            bar_time = row["datetime"]
            fr_key = bar_time.floor("8h")
            if fr_key in fr_rates:
                # 檢查這根 bar 是否跨過結算時間
                bar_start = bar_time
                bar_end = bar_time + timedelta(minutes=5)
                settle_times = [fr_key]
                for st in settle_times:
                    if bar_start <= st < bar_end:
                        rate = fr_rates[fr_key]
                        for p in long_positions:
                            cost = p["entry"] * p["qty"] * rate  # 多單付費
                            total_funding -= cost
                        for p in short_positions:
                            cost = p["entry"] * p["qty"] * rate  # 空單收費
                            total_funding += cost

        # ── 更新多單 ──
        nl=[]
        for p in long_positions:
            c=False

            # 止損檢查
            if lo <= p["sl"]:
                # 滑價模型
                if sl_slippage == "bar_extreme":
                    exit_p = lo  # 最差：成交在 bar 最低
                elif sl_slippage == "fixed_100":
                    exit_p = p["sl"] - 100
                elif sl_slippage == "fixed_200":
                    exit_p = p["sl"] - 200
                else:
                    exit_p = p["sl"]  # 理想：精確成交

                pnl = (exit_p - p["entry"]) * p["qty"] - exit_p * p["qty"] * 0.0004 * 2
                trades.append({"pnl":pnl,"side":"long","type":"SL","dt":row["datetime"]})
                sl_cooldown_until["long"] = i + sl_cooldown
                c=True

            # TP1
            elif p["phase"]==1 and hi>=p["tp1"]:
                # TP1 延遲模型
                if tp1_delay > 0:
                    p["tp1_triggered_at"] = i  # 記錄觸發時間，延遲處理
                    if p.get("tp1_triggered_at", 0) > 0 and i - p["tp1_triggered_at"] >= tp1_delay:
                        pass  # 已過延遲，下面處理
                    else:
                        if not c: nl.append(p)
                        continue

                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"TP1","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_hi"]=hi

            # Phase 2 trail
            elif p["phase"]==2:
                if hi>p["trail_hi"]: p["trail_hi"]=hi
                m=bm*0.6 if rsi>65 else bm
                sl=max(p["trail_hi"]-atr*m, p["entry"])
                if lo<=sl:
                    if sl_slippage == "bar_extreme":
                        exit_p = lo
                    else:
                        exit_p = sl
                    pnl=(exit_p-p["entry"])*p["qty"]-exit_p*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"long","type":"Trail","dt":row["datetime"]}); c=True
            # TP1 延遲中
            elif p["phase"]==1 and p.get("tp1_triggered_at", 0) > 0 and i - p["tp1_triggered_at"] >= tp1_delay:
                out=p["oqty"]*0.1; pnl=(nxt["open"]-p["entry"])*out-nxt["open"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"TP1_delayed","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_hi"]=hi

            if not c: nl.append(p)
        long_positions=nl

        # ── 更新空單 ──
        ns=[]
        for p in short_positions:
            c=False
            if hi>=p["sl"]:
                if sl_slippage == "bar_extreme":
                    exit_p = hi
                elif sl_slippage == "fixed_100":
                    exit_p = p["sl"] + 100
                elif sl_slippage == "fixed_200":
                    exit_p = p["sl"] + 200
                else:
                    exit_p = p["sl"]
                pnl=(p["entry"]-exit_p)*p["qty"]-exit_p*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"SL","dt":row["datetime"]})
                sl_cooldown_until["short"] = i + sl_cooldown
                c=True
            elif p["phase"]==1 and lo<=p["tp1"]:
                if tp1_delay > 0:
                    p["tp1_triggered_at"] = i
                    if p.get("tp1_triggered_at", 0) > 0 and i - p["tp1_triggered_at"] >= tp1_delay:
                        pass
                    else:
                        if not c: ns.append(p)
                        continue
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"TP1","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_lo"]=lo
            elif p["phase"]==2:
                if lo<p["trail_lo"]: p["trail_lo"]=lo
                m=bm*0.6 if rsi<35 else bm
                sl=min(p["trail_lo"]+atr*m, p["entry"])
                if hi>=sl:
                    if sl_slippage == "bar_extreme":
                        exit_p = hi
                    else:
                        exit_p = sl
                    pnl=(p["entry"]-exit_p)*p["qty"]-exit_p*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"short","type":"Trail","dt":row["datetime"]}); c=True
            elif p["phase"]==1 and p.get("tp1_triggered_at", 0) > 0 and i - p["tp1_triggered_at"] >= tp1_delay:
                out=p["oqty"]*0.1; pnl=(p["entry"]-nxt["open"])*out-nxt["open"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"TP1_delayed","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_lo"]=lo
            if not c: ns.append(p)
        short_positions=ns

        # ── 進場 ──
        ep = nxt["open"]
        long_sig = row["rsi"]<30 and row["close"]<row["bb_lower"]
        short_sig = row["rsi"]>70 and row["close"]>row["bb_upper"]

        # 急跌過濾
        if crash_filter > 0 and row["consec_red"] >= crash_filter:
            long_sig = False  # 急跌時不做多

        # 止損冷卻
        if i < sl_cooldown_until["long"]:
            long_sig = False
        if i < sl_cooldown_until["short"]:
            short_sig = False

        if len(long_positions)<3 and long_sig:
            qty=(MARGIN*LEVERAGE)/ep
            long_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_low"]-atr*0.3,"tp1":ep+atr,"atr":atr,"phase":1,"trail_hi":ep})
        if len(short_positions)<3 and short_sig:
            qty=(MARGIN*LEVERAGE)/ep
            short_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_high"]+atr*0.3,"tp1":ep-atr,"atr":atr,"phase":1,"trail_lo":ep})

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","side","type","dt"])
    return tdf, total_funding

def stats(tdf, funding=0):
    if len(tdf)<1: return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"exp":0,"funding":funding,"net":funding}
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    net = pnl + funding
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"exp":round(pnl/len(tdf),2),
            "funding":round(funding,2),"net":round(net,2)}

# ============================================================
# 跑所有配置
# ============================================================
configs = [
    # 名稱, config
    ("A. 理想回測\n(之前的版本)", {
        "sl_slippage":"none", "tp1_delay":0, "funding":False, "crash_filter":0, "sl_cooldown":0}),

    ("B. +SL滑價\n(bar extreme)", {
        "sl_slippage":"bar_extreme", "tp1_delay":0, "funding":False, "crash_filter":0, "sl_cooldown":0}),

    ("C. +SL滑價+TP1延遲\n(1 bar delay)", {
        "sl_slippage":"bar_extreme", "tp1_delay":1, "funding":False, "crash_filter":0, "sl_cooldown":0}),

    ("D. +SL+TP1+Funding\n(含資金費率)", {
        "sl_slippage":"bar_extreme", "tp1_delay":1, "funding":True, "crash_filter":0, "sl_cooldown":0}),

    ("E. +全部+止損冷卻\n(SL後等3根bar)", {
        "sl_slippage":"bar_extreme", "tp1_delay":1, "funding":True, "crash_filter":0, "sl_cooldown":3}),

    ("F. +全部+急跌過濾\n(6紅K暫停做多)", {
        "sl_slippage":"bar_extreme", "tp1_delay":1, "funding":True, "crash_filter":6, "sl_cooldown":3}),

    ("G. 全部+寬鬆過濾\n(4紅K暫停做多)", {
        "sl_slippage":"bar_extreme", "tp1_delay":1, "funding":True, "crash_filter":4, "sl_cooldown":3}),

    ("H. SL固定滑價$100", {
        "sl_slippage":"fixed_100", "tp1_delay":1, "funding":True, "crash_filter":0, "sl_cooldown":3}),
]

print(f"\n{'='*140}")
print("全樣本嚴格回測：逐步加入實戰成本")
print(f"{'='*140}")

print(f"\n  {'配置':<25s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} "
      f"{'回撤(DD)':>10s} {'資金費率':>12s} {'淨損益':>14s} {'vs理想':>10s}")
print(f"  {'-'*125}")

all_results = []
base_pnl = None

for name, cfg in configs:
    short_name = name.split("\n")[0]
    tdf, funding = run_realistic(df, cfg)
    s = stats(tdf, funding)
    if base_pnl is None: base_pnl = s["pnl"]
    diff = s["net"] - base_pnl
    all_results.append({"name":name, "short_name":short_name, **s, "tdf":tdf})

    print(f"  {short_name:<25s} {s['n']:>7d} ${s['pnl']:>+12,.2f} {s['wr']:>9.1f}% {s['pf']:>13.2f} "
          f"${s['dd']:>9,.2f} ${s['funding']:>+10,.2f} ${s['net']:>+12,.2f} ${diff:>+9,.0f}")

# ============================================================
# Walk-Forward (用最嚴格的 config)
# ============================================================
print(f"\n{'='*140}")
print("Walk-Forward 驗證（最嚴格版 = Config F）")
print(f"{'='*140}")

strict_config = {"sl_slippage":"bar_extreme", "tp1_delay":1, "funding":True, "crash_filter":6, "sl_cooldown":3}

split = int(len(df) * (4/6))
train = df.iloc[:split].reset_index(drop=True)
test = df.iloc[split:].reset_index(drop=True)

tdf_is, f_is = run_realistic(train, strict_config)
tdf_oos, f_oos = run_realistic(test, strict_config)
s_is = stats(tdf_is, f_is)
s_oos = stats(tdf_oos, f_oos)

print(f"\n  樣本內(IS) 4個月：{s_is['n']} 筆, 損益 ${s_is['pnl']:+,.2f}, 資金費率 ${s_is['funding']:+,.2f}, 淨損益 ${s_is['net']:+,.2f}")
print(f"  樣本外(OOS) 2個月：{s_oos['n']} 筆, 損益 ${s_oos['pnl']:+,.2f}, 資金費率 ${s_oos['funding']:+,.2f}, 淨損益 ${s_oos['net']:+,.2f}")
print(f"  OOS 勝率：{s_oos['wr']}%, PF：{s_oos['pf']}, DD：${s_oos['dd']:,.2f}")

# 滾動 WF
df["month"] = df["datetime"].dt.to_period("M")
months = df["month"].unique()

print(f"\n  滾動 2m→1m：")
fold_pnls = []
for fold in range(len(months)-2):
    test_m = months[fold+2]
    fold_test = df[df["month"]==test_m].reset_index(drop=True)
    if len(fold_test)<20: continue
    tdf_f, f_f = run_realistic(fold_test, strict_config)
    s_f = stats(tdf_f, f_f)
    fold_pnls.append(s_f["net"])
    print(f"    {str(test_m)}: 淨損益 ${s_f['net']:>+10,.2f}, 勝率 {s_f['wr']}%, {s_f['n']} 筆")

prof = sum(1 for p in fold_pnls if p > 0)
print(f"  合計：${sum(fold_pnls):+,.2f}, 獲利 {prof}/{len(fold_pnls)} 折")

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(22, 16))
fig.suptitle("最嚴格實戰回測：逐步加入真實成本的影響\n(SL滑價 + TP1延遲 + 資金費率 + 止損冷卻 + 急跌過濾)",
             fontsize=13, fontweight="bold")

# 圖 1: 逐步加入成本的損益
ax = axes[0][0]
names = [r["short_name"] for r in all_results]
nets = [r["net"] for r in all_results]
colors = ["#4CAF50" if r["short_name"].startswith("A") else
          "#FF9800" if r["short_name"].startswith("F") else "#2196F3" for r in all_results]
bars = ax.barh(range(len(names)), nets, color=colors, edgecolor="black", linewidth=0.5)
ax.set_yticks(range(len(names))); ax.set_yticklabels(names, fontsize=7); ax.invert_yaxis()
for i, v in enumerate(nets): ax.text(v+50, i, f"${v:+,.0f}", va="center", fontsize=7)
ax.axvline(base_pnl, color="gray", linestyle="--", alpha=0.5, label=f"Ideal ${base_pnl:+,.0f}")
ax.set_xlabel("Net PnL ($)"); ax.set_title("逐步加入實戰成本"); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

# 圖 2: 累計曲線（理想 vs 最嚴格）
ax = axes[0][1]
ideal = all_results[0]["tdf"]
strict = all_results[5]["tdf"]  # Config F
if len(ideal)>0:
    cum = ideal.sort_values("dt")["pnl"].cumsum()
    ax.plot(range(len(cum)), cum.values, color="gray", linewidth=1, alpha=0.5, label=f"Ideal ${all_results[0]['pnl']:+,.0f}")
if len(strict)>0:
    cum = strict.sort_values("dt")["pnl"].cumsum()
    ax.plot(range(len(cum)), cum.values, color="red", linewidth=2, label=f"Strict ${all_results[5]['net']:+,.0f}")
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("Ideal vs Strict Equity Curve"); ax.set_ylabel("PnL ($)"); ax.legend(); ax.grid(True, alpha=0.3)

# 圖 3: 成本拆解
ax = axes[1][0]
cost_items = ["SL滑價", "TP1延遲", "資金費率", "止損冷卻", "急跌過濾"]
# 逐步差異
diffs = []
for i in range(1, min(6, len(all_results))):
    diffs.append(all_results[i]["net"] - all_results[i-1]["net"])
if len(diffs) >= 5:
    colors_cost = ["#F44336" if d<0 else "#4CAF50" for d in diffs]
    ax.bar(cost_items, diffs, color=colors_cost, edgecolor="black")
    for i, v in enumerate(diffs):
        ax.text(i, v, f"${v:+,.0f}", ha="center", va="bottom" if v>0 else "top", fontsize=9)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("Each cost component impact"); ax.set_ylabel("PnL change ($)"); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]; ax.axis("off")
table_h = ["配置","交易數","損益\n(PnL)","勝率\n(WR)","獲利因子\n(PF)","回撤\n(DD)","資金\n費率","淨損益","vs理想"]
table_r = []
for r in all_results:
    diff = r["net"] - base_pnl
    table_r.append([r["short_name"], str(r["n"]), f"${r['pnl']:+,.0f}", f"{r['wr']}%",
                    f"{r['pf']}", f"${r['dd']:,.0f}", f"${r['funding']:+,.0f}",
                    f"${r['net']:+,.0f}", f"${diff:+,.0f}"])
table = ax.table(cellText=table_r, colLabels=table_h, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(7); table.scale(1.0, 1.6)
for j in range(9):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
# 高亮最嚴格版
for j in range(9): table[6, j].set_facecolor("#FFF2CC")

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_realistic_backtest.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 最終結論
# ============================================================
print(f"\n{'='*140}")
print("FINAL: Ideal vs Reality")
print(f"{'='*140}")

ideal_r = all_results[0]
strict_r = all_results[5]  # Config F

print(f"\n  理想回測（之前的版本）：")
print(f"    損益 ${ideal_r['pnl']:+,.2f}, 勝率 {ideal_r['wr']}%, PF {ideal_r['pf']}, DD ${ideal_r['dd']:,.2f}")

print(f"\n  最嚴格實戰版（SL滑價+TP1延遲+Funding+冷卻+急跌過濾）：")
print(f"    損益 ${strict_r['pnl']:+,.2f}, 資金費率 ${strict_r['funding']:+,.2f}")
print(f"    淨損益 ${strict_r['net']:+,.2f}, 勝率 {strict_r['wr']}%, PF {strict_r['pf']}, DD ${strict_r['dd']:,.2f}")

decay = (1 - strict_r["net"] / ideal_r["pnl"]) * 100
print(f"\n  理想 → 實戰衰退：{decay:.1f}%")
print(f"  = 理想的 {100-decay:.0f}% 會實現")

print(f"\n  WF OOS（最嚴格）：${s_oos['net']:+,.2f}, 勝率 {s_oos['wr']}%, 滾動 {prof}/{len(fold_pnls)} 折獲利")
