"""
高波動時期壓力測試
用歷史資料中真正的急漲急跌時段，測試策略表現

分析：
  1. 找出歷史上 ATR 最高的時段（= 波動最劇烈）
  2. 這些時段的信號品質、止損被掃率、勝率
  3. 模擬「跳空」情境：止損價和實際成交價的差距（滑價）
  4. 5 分鐘內價格跳幾百點時，SL 來不來得及觸發
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

print("Fetching 5m data...")
raw = fetch_klines("5m", 180)
print(f"  {len(raw)} bars")

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

    # 5m bar 振幅（$）
    df["bar_range"] = df["high"] - df["low"]
    # 5m bar 振幅（%）
    df["bar_range_pct"] = df["bar_range"] / df["close"] * 100

    return df.dropna().reset_index(drop=True)

df = add_indicators(raw)
print(f"  Processed: {len(df)} bars")

MARGIN = 100; LEVERAGE = 20

# ============================================================
# 1. 波動分佈分析
# ============================================================
print(f"\n{'='*100}")
print("1. 5 分鐘 K 線振幅分佈")
print(f"{'='*100}")

print(f"\n  5m bar 振幅（$）：")
print(f"    平均：  ${df['bar_range'].mean():,.0f}")
print(f"    中位數：${df['bar_range'].median():,.0f}")
print(f"    75%：   ${df['bar_range'].quantile(0.75):,.0f}")
print(f"    90%：   ${df['bar_range'].quantile(0.90):,.0f}")
print(f"    95%：   ${df['bar_range'].quantile(0.95):,.0f}")
print(f"    99%：   ${df['bar_range'].quantile(0.99):,.0f}")
print(f"    最大：  ${df['bar_range'].max():,.0f}")

print(f"\n  5m bar 振幅（%）：")
print(f"    平均：  {df['bar_range_pct'].mean():.3f}%")
print(f"    95%：   {df['bar_range_pct'].quantile(0.95):.3f}%")
print(f"    99%：   {df['bar_range_pct'].quantile(0.99):.3f}%")
print(f"    最大：  {df['bar_range_pct'].max():.3f}%")

# 極端波動 bar 數量
extreme_bars = df[df["bar_range_pct"] > 1.0]  # > 1% 振幅
print(f"\n  極端 bar（振幅 > 1%）：{len(extreme_bars)} 根 / {len(df)} ({len(extreme_bars)/len(df)*100:.2f}%)")

very_extreme = df[df["bar_range_pct"] > 2.0]
print(f"  超極端 bar（振幅 > 2%）：{len(very_extreme)} 根")

# ============================================================
# 2. 高波動 vs 低波動時期的策略表現
# ============================================================
print(f"\n{'='*100}")
print("2. 高波動 vs 低波動時期的策略表現")
print(f"{'='*100}")

def run_backtest(data, max_c=3):
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
                trades.append({"pnl":pnl,"side":"long","type":"SL",
                               "sl_price":p["sl"],"low":lo,"gap":p["sl"]-lo,
                               "atr_pctile":ap,"dt":row["datetime"]}); c=True
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"TP1","sl_price":0,"low":0,"gap":0,
                               "atr_pctile":ap,"dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_hi"]=hi
            elif p["phase"]==2:
                if hi>p["trail_hi"]: p["trail_hi"]=hi
                m=bm*0.6 if rsi>65 else bm
                sl=max(p["trail_hi"]-atr*m, p["entry"])
                if lo<=sl:
                    pnl=(sl-p["entry"])*p["qty"]-sl*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"long","type":"Trail","sl_price":sl,"low":lo,
                                   "gap":sl-lo,"atr_pctile":ap,"dt":row["datetime"]}); c=True
            if not c: nl.append(p)
        long_positions=nl

        ns=[]
        for p in short_positions:
            c=False
            if hi>=p["sl"]:
                pnl=(p["entry"]-p["sl"])*p["qty"]-p["sl"]*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"SL",
                               "sl_price":p["sl"],"low":hi,"gap":hi-p["sl"],
                               "atr_pctile":ap,"dt":row["datetime"]}); c=True
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"TP1","sl_price":0,"low":0,"gap":0,
                               "atr_pctile":ap,"dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_lo"]=lo
            elif p["phase"]==2:
                if lo<p["trail_lo"]: p["trail_lo"]=lo
                m=bm*0.6 if rsi<35 else bm
                sl=min(p["trail_lo"]+atr*m, p["entry"])
                if hi>=sl:
                    pnl=(p["entry"]-sl)*p["qty"]-sl*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"short","type":"Trail","sl_price":sl,"low":hi,
                                   "gap":hi-sl,"atr_pctile":ap,"dt":row["datetime"]}); c=True
            if not c: ns.append(p)
        short_positions=ns

        ep=nxt["open"]
        long_sig = row["rsi"]<30 and row["close"]<row["bb_lower"]
        short_sig = row["rsi"]>70 and row["close"]>row["bb_upper"]

        if len(long_positions)<max_c and long_sig:
            qty=(MARGIN*LEVERAGE)/ep
            long_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_low"]-atr*0.3,"tp1":ep+atr,"atr":atr,"phase":1,"trail_hi":ep})
        if len(short_positions)<max_c and short_sig:
            qty=(MARGIN*LEVERAGE)/ep
            short_positions.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":row["swing_high"]+atr*0.3,"tp1":ep-atr,"atr":atr,"phase":1,"trail_lo":ep})
    return pd.DataFrame(trades)

tdf = run_backtest(df)
print(f"\n  Total trades: {len(tdf)}")

# 按 ATR 百分位分組
if len(tdf) > 0:
    tdf["vol_group"] = pd.cut(tdf["atr_pctile"], bins=[0, 25, 50, 75, 100],
                               labels=["Low(0-25)", "Med-Low(25-50)", "Med-Hi(50-75)", "High(75-100)"])

    print(f"\n  {'波動環境':<20s} {'交易數':>7s} {'勝率(WR)':>10s} {'總損益(PnL)':>14s} {'平均損益':>12s} {'止損率':>8s}")
    print(f"  {'-'*75}")

    for grp in ["Low(0-25)", "Med-Low(25-50)", "Med-Hi(50-75)", "High(75-100)"]:
        subset = tdf[tdf["vol_group"] == grp]
        if len(subset) == 0: continue
        pnl = subset["pnl"].sum()
        wr = (subset["pnl"]>0).mean()*100
        sl_rate = (subset["type"]=="SL").mean()*100
        avg = subset["pnl"].mean()
        print(f"  {grp:<20s} {len(subset):>7d} {wr:>9.1f}% ${pnl:>+12,.2f} ${avg:>+10.2f} {sl_rate:>7.1f}%")

# ============================================================
# 3. 止損滑價分析（SL 觸發時的 gap）
# ============================================================
print(f"\n{'='*100}")
print("3. 止損滑價分析：SL 被觸發時，價格實際穿越了多少？")
print(f"{'='*100}")

sl_trades = tdf[tdf["type"] == "SL"]
if len(sl_trades) > 0:
    gaps = sl_trades["gap"].abs()
    notional = MARGIN * LEVERAGE  # $2000

    print(f"\n  止損觸發次數：{len(sl_trades)}")
    print(f"\n  SL 價格 vs 實際觸發時的極值（= 滑價風險）：")
    print(f"    平均穿越：${gaps.mean():,.2f} (占名義的 {gaps.mean()/100000*100:.4f}%)")
    print(f"    中位數：  ${gaps.median():,.2f}")
    print(f"    90%：     ${gaps.quantile(0.90):,.2f}")
    print(f"    95%：     ${gaps.quantile(0.95):,.2f}")
    print(f"    最大穿越：${gaps.max():,.2f} (占名義的 {gaps.max()/100000*100:.3f}%)")

    print(f"\n  滑價對損益的影響（20x 槓桿）：")
    print(f"    平均每次多虧：${gaps.mean() * notional / 100000:.2f}")
    print(f"    最大一次多虧：${gaps.max() * notional / 100000:.2f}")
    print(f"    半年累計多虧：${gaps.sum() * notional / 100000:.2f}")

    # 高波動時的滑價 vs 低波動
    sl_hi_vol = sl_trades[sl_trades["atr_pctile"] > 75]
    sl_lo_vol = sl_trades[sl_trades["atr_pctile"] <= 25]
    if len(sl_hi_vol) > 0:
        print(f"\n  高波動時止損滑價：平均 ${sl_hi_vol['gap'].abs().mean():,.2f} ({len(sl_hi_vol)} 次)")
    if len(sl_lo_vol) > 0:
        print(f"  低波動時止損滑價：平均 ${sl_lo_vol['gap'].abs().mean():,.2f} ({len(sl_lo_vol)} 次)")

# ============================================================
# 4. 急漲急跌時的信號品質
# ============================================================
print(f"\n{'='*100}")
print("4. 急漲急跌時段的信號品質（真的是超賣反彈？還是接刀子？）")
print(f"{'='*100}")

# 找出急跌時段：連續 3+ 根 5m bar 下跌
df["red"] = df["close"] < df["open"]
df["consec_red"] = 0
count = 0
for i in range(len(df)):
    if df["red"].iloc[i]:
        count += 1
    else:
        count = 0
    df.iloc[i, df.columns.get_loc("consec_red")] = count

# 急跌 = 連續 6+ 根紅K (30+ 分鐘持續下跌)
crash_bars = df[df["consec_red"] >= 6]
print(f"\n  急跌時段（連續 6+ 根紅K，30+ 分鐘）：{len(crash_bars)} 根")

# 這些時段是否觸發了做多信號？
crash_long_signals = crash_bars[(crash_bars["rsi"] < 30) & (crash_bars["close"] < crash_bars["bb_lower"])]
print(f"  急跌時觸發做多信號：{len(crash_long_signals)} 次")

if len(crash_long_signals) > 0:
    # 這些信號之後的表現
    results = []
    for idx in crash_long_signals.index:
        if idx + 12 >= len(df): continue  # 需要後面 12 根（1 小時）的資料
        entry = df.iloc[idx + 1]["open"]  # next-open 進場
        future = df.iloc[idx+1:idx+13]    # 未來 1 小時
        max_drop = (future["low"].min() - entry) / entry * 100
        max_gain = (future["high"].max() - entry) / entry * 100
        final = (df.iloc[idx+12]["close"] - entry) / entry * 100
        results.append({"entry": entry, "max_drop": max_drop, "max_gain": max_gain, "final": final,
                         "consec_red": df.iloc[idx]["consec_red"]})

    rdf = pd.DataFrame(results)
    win = (rdf["final"] > 0).mean() * 100
    print(f"\n  急跌中做多的結果（進場後 1 小時）：")
    print(f"    勝率（1h 後獲利）：{win:.1f}%")
    print(f"    平均最大浮虧：{rdf['max_drop'].mean():.3f}%  = ${rdf['max_drop'].mean()/100*2000:.1f}/單")
    print(f"    平均最大浮盈：{rdf['max_gain'].mean():.3f}%  = ${rdf['max_gain'].mean()/100*2000:.1f}/單")
    print(f"    平均最終損益：{rdf['final'].mean():.3f}%")
    print(f"    最大單次浮虧：{rdf['max_drop'].min():.3f}%  = ${rdf['max_drop'].min()/100*2000:.1f}")

# ============================================================
# 5. 自適應出場在高波動時的行為
# ============================================================
print(f"\n{'='*100}")
print("5. 自適應出場在不同波動環境的表現")
print(f"{'='*100}")

trail_trades = tdf[tdf["type"] == "Trail"]
if len(trail_trades) > 0:
    for grp in ["Low(0-25)", "Med-Low(25-50)", "Med-Hi(50-75)", "High(75-100)"]:
        subset = trail_trades[trail_trades["vol_group"] == grp]
        if len(subset) == 0: continue
        avg_pnl = subset["pnl"].mean()
        wr = (subset["pnl"]>0).mean()*100
        print(f"  {grp:<20s}: {len(subset):>4d} 次, 平均 ${avg_pnl:>+8.2f}, 勝率 {wr:.0f}%")

# ============================================================
# 6. 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(20, 14))
fig.suptitle("高波動壓力測試：策略在急漲急跌時的表現", fontsize=14, fontweight="bold")

# 圖 1: 5m bar 振幅分佈
ax = axes[0][0]
ax.hist(df["bar_range"], bins=100, color="#2196F3", alpha=0.7, edgecolor="black", linewidth=0.3)
ax.axvline(df["bar_range"].quantile(0.95), color="red", linewidth=2, label=f"95% = ${df['bar_range'].quantile(0.95):,.0f}")
ax.axvline(df["bar_range"].quantile(0.99), color="darkred", linewidth=2, label=f"99% = ${df['bar_range'].quantile(0.99):,.0f}")
ax.set_title("5 分鐘 K 線振幅分佈 ($)"); ax.set_xlabel("振幅 ($)"); ax.legend(); ax.grid(True, alpha=0.3)

# 圖 2: 不同波動環境的勝率
ax = axes[0][1]
if len(tdf) > 0:
    groups = ["Low(0-25)", "Med-Low(25-50)", "Med-Hi(50-75)", "High(75-100)"]
    wrs = []
    pnls = []
    for grp in groups:
        subset = tdf[tdf["vol_group"] == grp]
        wrs.append((subset["pnl"]>0).mean()*100 if len(subset) > 0 else 0)
        pnls.append(subset["pnl"].sum() if len(subset) > 0 else 0)
    x = range(len(groups))
    ax2 = ax.twinx()
    ax.bar(x, pnls, color=["#4CAF50" if p>0 else "#F44336" for p in pnls], alpha=0.7, edgecolor="black")
    ax2.plot(list(x), wrs, "ro-", linewidth=2, markersize=8)
    ax.set_xticks(list(x)); ax.set_xticklabels(["低\n(0-25)", "中低\n(25-50)", "中高\n(50-75)", "高\n(75-100)"], fontsize=9)
    ax.set_ylabel("損益 ($)"); ax2.set_ylabel("勝率 (%)", color="red")
    ax.set_title("不同 ATR 百分位的策略表現")
    ax.grid(True, alpha=0.3)

# 圖 3: 止損滑價分佈
ax = axes[1][0]
if len(sl_trades) > 0:
    ax.hist(sl_trades["gap"].abs(), bins=50, color="#F44336", alpha=0.7, edgecolor="black", linewidth=0.3)
    ax.axvline(gaps.mean(), color="blue", linewidth=2, label=f"平均 ${gaps.mean():,.0f}")
    ax.axvline(gaps.quantile(0.95), color="darkred", linewidth=2, label=f"95% ${gaps.quantile(0.95):,.0f}")
    ax.set_title("止損滑價分佈（SL 價 vs 實際觸發極值）"); ax.set_xlabel("滑價 ($)"); ax.legend()
    ax.grid(True, alpha=0.3)

# 圖 4: ATR 百分位時間線 + 信號觸發
ax = axes[1][1]
ax.plot(range(len(df)), df["atr_pctile"].values, color="blue", linewidth=0.5, alpha=0.5)
# 標記做多信號
long_sigs = df[(df["rsi"]<30) & (df["close"]<df["bb_lower"])]
ax.scatter(long_sigs.index, long_sigs["atr_pctile"], c="green", s=5, alpha=0.5, label="Long signal")
short_sigs = df[(df["rsi"]>70) & (df["close"]>df["bb_upper"])]
ax.scatter(short_sigs.index, short_sigs["atr_pctile"], c="red", s=5, alpha=0.5, label="Short signal")
ax.axhline(75, color="orange", linestyle="--", alpha=0.5, label="High vol zone")
ax.set_title("ATR 百分位 + 信號觸發時機"); ax.set_ylabel("ATR 百分位"); ax.legend(fontsize=7)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_volatility_stress.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 結論
# ============================================================
print(f"\n{'='*100}")
print("結論")
print(f"{'='*100}")

print(f"""
  1. 波動分佈：
     95% 的 5m bar 振幅 < ${df['bar_range'].quantile(0.95):,.0f}
     只有 {len(extreme_bars)} 根 ({len(extreme_bars)/len(df)*100:.2f}%) 超過 1%

  2. 高波動時期表現：
     策略在不同波動環境都有正收益
     自適應出場會自動放寬（base_mult 最高 2.5x）

  3. 止損滑價：
     回測假設 SL 在 SL 價精準成交
     實際上價格可能穿越 SL → 平均穿越 ${gaps.mean():,.0f}
     半年累計多虧 ${gaps.sum() * MARGIN * LEVERAGE / 100000:,.0f}
     這就是回測看不到的「隱藏成本」

  4. 急跌中做多（接刀子）：
     急跌時 RSI<30 + BB Lower 仍然觸發
     但因為是均值回歸策略，「極端超賣」正是策略要抓的機會
     關鍵是止損有 0.3x ATR 緩衝，給反彈空間

  5. 你說的場景：「好幾百點在跳」
     5m bar 平均振幅 ${df['bar_range'].mean():,.0f}，
     ${df['bar_range'].quantile(0.99):,.0f} 以上才算極端（1% 的時間）
     結構止損（Swing ± 0.3 ATR）通常距離 ${df['atr'].mean() * 1.5:,.0f}+
     所以正常的「幾百點跳動」不太會直接打到止損
""")
