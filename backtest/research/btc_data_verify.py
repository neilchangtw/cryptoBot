"""
資料驗證 + 多時間框架測試
1. 比對 CSV 與 Binance API 即時抓取的資料
2. 從 Binance 抓 15m / 30m / 1h / 2h / 4h 資料
3. 用相同策略邏輯在不同週期回測
4. 找出最佳週期
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

# ============================================================
# Part 1: 驗證 CSV 資料 vs Binance API
# ============================================================
print(f"{'='*100}")
print("PART 1: CSV vs Binance API 資料比對")
print(f"{'='*100}")

# 載入現有 CSV
csv_df = pd.read_csv(r"C:\Users\neil.chang\workspace\btc-strategy-research\data\btc_1h_6months.csv")
csv_df["datetime"] = pd.to_datetime(csv_df["datetime"])
for col in ["open", "high", "low", "close", "volume"]:
    csv_df[col] = pd.to_numeric(csv_df[col])

print(f"\n  CSV 資料：")
print(f"    筆數：{len(csv_df)}")
print(f"    期間：{csv_df['datetime'].iloc[0]} ~ {csv_df['datetime'].iloc[-1]}")
print(f"    價格範圍：${csv_df['close'].min():,.0f} ~ ${csv_df['close'].max():,.0f}")

# 從 Binance 即時抓取同期資料的一小段來比對
print(f"\n  從 Binance API 抓取資料比對...")

# 抓 CSV 中間段 100 根來比對
mid_idx = len(csv_df) // 2
sample_start = csv_df.iloc[mid_idx]["open_time"] if "open_time" in csv_df.columns else None

# 用 CSV 的 datetime 反推 timestamp
sample_dt = csv_df.iloc[mid_idx]["datetime"]
start_ms = int(sample_dt.timestamp() * 1000)

try:
    resp = requests.get("https://api.binance.com/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "1h",
                                "startTime": start_ms, "limit": 100},
                        timeout=10)
    api_data = resp.json()

    api_df = pd.DataFrame(api_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        api_df[col] = pd.to_numeric(api_df[col])
    api_df["datetime"] = pd.to_datetime(api_df["open_time"], unit="ms") + timedelta(hours=8)

    # 比對
    merged = csv_df.merge(api_df[["datetime", "open", "high", "low", "close"]],
                          on="datetime", suffixes=("_csv", "_api"))

    if len(merged) > 0:
        for col in ["open", "high", "low", "close"]:
            diff = (merged[f"{col}_csv"] - merged[f"{col}_api"]).abs()
            max_diff = diff.max()
            mean_diff = diff.mean()
            match_pct = (diff < 0.01).mean() * 100
            print(f"    {col:>5s}: 平均差 ${mean_diff:.4f}, 最大差 ${max_diff:.4f}, 完全一致 {match_pct:.1f}%")

        print(f"    比對筆數：{len(merged)}")
        if merged[["open_csv", "high_csv", "low_csv", "close_csv"]].equals(
           merged[["open_api", "high_api", "low_api", "close_api"]].rename(
               columns=lambda x: x.replace("_api", "_csv"))):
            print(f"    >>> 資料完全一致 <<<")
        else:
            all_close = ((merged["close_csv"] - merged["close_api"]).abs() < 0.01).all()
            print(f"    >>> 收盤價一致：{all_close} <<<")
    else:
        print(f"    無法比對（時間不匹配）")

except Exception as e:
    print(f"    API 請求失敗：{e}")
    print(f"    跳過比對，繼續後續測試")

# ============================================================
# Part 2: 從 Binance 抓取不同週期資料
# ============================================================
print(f"\n{'='*100}")
print("PART 2: 從 Binance 抓取多時間框架資料")
print(f"{'='*100}")

def fetch_binance_klines(symbol, interval, days_back=180):
    """從 Binance 抓取歷史 K 線"""
    all_data = []
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    current = start_ms

    while current < end_ms:
        try:
            resp = requests.get("https://api.binance.com/api/v3/klines",
                                params={"symbol": symbol, "interval": interval,
                                        "startTime": current, "limit": 1000},
                                timeout=10)
            data = resp.json()
            if not data:
                break
            all_data.extend(data)
            current = data[-1][0] + 1
            time.sleep(0.1)  # rate limit
        except Exception as e:
            print(f"    Error fetching {interval}: {e}")
            break

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    return df

timeframes = {
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
}

tf_data = {}
for label, interval in timeframes.items():
    print(f"\n  抓取 {label} 資料...", end=" ", flush=True)
    tf_df = fetch_binance_klines("BTCUSDT", interval, days_back=180)
    if len(tf_df) > 0:
        tf_data[label] = tf_df
        print(f"OK, {len(tf_df)} 根 ({tf_df['datetime'].iloc[0].date()} ~ {tf_df['datetime'].iloc[-1].date()})")
    else:
        print(f"Failed")

# ============================================================
# Part 3: 計算指標 + 回測（每個週期）
# ============================================================
print(f"\n{'='*100}")
print("PART 3: 多時間框架回測")
print(f"{'='*100}")

MARGIN = 100; LEVERAGE = 20

def add_indicators(df):
    """計算所有指標"""
    tr = pd.DataFrame({
        "hl": df["high"] - df["low"],
        "hc": abs(df["high"] - df["close"].shift(1)),
        "lc": abs(df["low"] - df["close"].shift(1)),
    }).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100 if x.max() != x.min() else 50)

    # Swing
    w = 5
    sh = pd.Series(np.nan, index=df.index)
    sl = pd.Series(np.nan, index=df.index)
    for i in range(w, len(df) - w):
        if df["high"].iloc[i] == df["high"].iloc[i-w:i+w+1].max():
            sh.iloc[i] = df["high"].iloc[i]
        if df["low"].iloc[i] == df["low"].iloc[i-w:i+w+1].min():
            sl.iloc[i] = df["low"].iloc[i]
    df["swing_high"] = sh.ffill()
    df["swing_low"] = sl.ffill()

    # RSI
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + g / l)

    # BB
    df["bb_upper"] = df["close"].rolling(20).mean() + 2 * df["close"].rolling(20).std()

    df = df.dropna().reset_index(drop=True)
    return df


def run_v3(data, max_c=3):
    """v3 自適應回測"""
    long_positions = []; short_positions = []; trades = []

    for i in range(2, len(data)):
        row = data.iloc[i]
        price = row["close"]; atr = row["atr"]; hi = row["high"]; lo = row["low"]
        atr_pctile = row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi = row["rsi"]
        base_mult = 1.0 + (atr_pctile / 100) * 1.5

        new_longs = []
        for p in long_positions:
            closed = False
            if lo <= p["sl"]:
                pnl = (p["sl"] - p["entry"]) * p["qty"]
                pnl -= p["sl"] * p["qty"] * 0.0004 * 2
                trades.append({"pnl": pnl, "side": "long", "dt": row["datetime"]}); closed = True
            elif p["phase"] == 1 and hi >= p["tp1"]:
                out = p["oqty"] * 0.1
                pnl = (p["tp1"] - p["entry"]) * out
                pnl -= p["tp1"] * out * 0.0004 * 2
                trades.append({"pnl": pnl, "side": "long", "dt": row["datetime"]})
                p["qty"] = p["oqty"] * 0.9; p["phase"] = 2; p["sl"] = p["entry"]; p["trail_hi"] = hi
            elif p["phase"] == 2:
                if hi > p["trail_hi"]: p["trail_hi"] = hi
                mult = base_mult * 0.6 if rsi > 65 else base_mult
                sl = max(p["trail_hi"] - atr * mult, p["entry"])
                if lo <= sl:
                    pnl = (sl - p["entry"]) * p["qty"]
                    pnl -= sl * p["qty"] * 0.0004 * 2
                    trades.append({"pnl": pnl, "side": "long", "dt": row["datetime"]}); closed = True
            if not closed: new_longs.append(p)
        long_positions = new_longs

        new_shorts = []
        for p in short_positions:
            closed = False
            if hi >= p["sl"]:
                pnl = (p["entry"] - p["sl"]) * p["qty"]
                pnl -= p["sl"] * p["qty"] * 0.0004 * 2
                trades.append({"pnl": pnl, "side": "short", "dt": row["datetime"]}); closed = True
            elif p["phase"] == 1 and lo <= p["tp1"]:
                out = p["oqty"] * 0.1
                pnl = (p["entry"] - p["tp1"]) * out
                pnl -= p["tp1"] * out * 0.0004 * 2
                trades.append({"pnl": pnl, "side": "short", "dt": row["datetime"]})
                p["qty"] = p["oqty"] * 0.9; p["phase"] = 2; p["sl"] = p["entry"]; p["trail_lo"] = lo
            elif p["phase"] == 2:
                if lo < p["trail_lo"]: p["trail_lo"] = lo
                mult = base_mult * 0.6 if rsi < 35 else base_mult
                sl = min(p["trail_lo"] + atr * mult, p["entry"])
                if hi >= sl:
                    pnl = (p["entry"] - sl) * p["qty"]
                    pnl -= sl * p["qty"] * 0.0004 * 2
                    trades.append({"pnl": pnl, "side": "short", "dt": row["datetime"]}); closed = True
            if not closed: new_shorts.append(p)
        short_positions = new_shorts

        if len(long_positions) < max_c and row["rsi"] < 30:
            qty = (MARGIN * LEVERAGE) / price
            long_positions.append({"entry": price, "qty": qty, "oqty": qty,
                "sl": row["swing_low"] - atr * 0.3, "tp1": price + atr,
                "atr": atr, "phase": 1, "trail_hi": price})

        if len(short_positions) < max_c and price > row["bb_upper"]:
            qty = (MARGIN * LEVERAGE) / price
            short_positions.append({"entry": price, "qty": qty, "oqty": qty,
                "sl": row["swing_high"] + atr * 0.3, "tp1": price - atr,
                "atr": atr, "phase": 1, "trail_lo": price})

    return pd.DataFrame(trades)


# 回測每個週期
results = {}
print(f"\n  {'TF':>5s} {'Bars':>6s} {'Signals':>10s} {'Trades':>7s} {'PnL':>12s} {'WR':>7s} {'PF':>7s} {'DD':>10s} {'L PnL':>10s} {'S PnL':>10s} {'Exp/trade':>10s}")
print(f"  {'-'*105}")

for label in ["15m", "30m", "1h", "2h", "4h"]:
    if label not in tf_data:
        continue

    raw = tf_data[label].copy()
    data = add_indicators(raw)

    if len(data) < 120:
        print(f"  {label:>5s} {len(data):>6d}  (too few bars, skip)")
        continue

    # 統計信號數量
    long_sigs = (data["rsi"] < 30).sum()
    short_sigs = (data["close"] > data["bb_upper"]).sum()

    tdf = run_v3(data)

    if len(tdf) < 3:
        print(f"  {label:>5s} {len(data):>6d} {long_sigs+short_sigs:>10d} {len(tdf):>7d}  (too few trades)")
        results[label] = {"n": len(tdf), "pnl": 0, "bars": len(data)}
        continue

    pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    wins = tdf[tdf["pnl"] > 0]; losses = tdf[tdf["pnl"] <= 0]
    pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else 999
    cum = tdf["pnl"].cumsum(); dd = (cum - cum.cummax()).min()
    lt = tdf[tdf["side"] == "long"]; st = tdf[tdf["side"] == "short"]
    exp = pnl / len(tdf)

    results[label] = {
        "n": len(tdf), "pnl": round(pnl, 2), "wr": round(wr, 1),
        "pf": round(pf, 2), "dd": round(dd, 2), "bars": len(data),
        "l_pnl": round(lt["pnl"].sum(), 2), "s_pnl": round(st["pnl"].sum(), 2),
        "l_n": len(lt), "s_n": len(st), "exp": round(exp, 2),
        "tdf": tdf, "data": data,
    }

    mark = " <-- current" if label == "1h" else ""
    print(f"  {label:>5s} {len(data):>6d} {long_sigs+short_sigs:>10d} {len(tdf):>7d} ${pnl:>+11,.2f} {wr:>6.1f}% {pf:>6.2f} ${dd:>9,.2f} ${lt['pnl'].sum():>+9,.0f} ${st['pnl'].sum():>+9,.0f} ${exp:>+9.2f}{mark}")

# ============================================================
# Part 4: Walk-Forward 驗證各週期
# ============================================================
print(f"\n{'='*100}")
print("PART 4: Walk-Forward 驗證各週期（4m/2m 切分）")
print(f"{'='*100}")

print(f"\n  {'TF':>5s} {'IS PnL':>12s} {'OOS PnL':>12s} {'OOS WR':>7s} {'OOS PF':>7s} {'Retention':>10s}")
print(f"  {'-'*60}")

for label in ["15m", "30m", "1h", "2h", "4h"]:
    if label not in results or "data" not in results[label]:
        continue

    data = results[label]["data"]
    split = int(len(data) * (4/6))
    train = data.iloc[:split].reset_index(drop=True)
    test = data.iloc[split:].reset_index(drop=True)

    tdf_is = run_v3(train)
    tdf_oos = run_v3(test)

    is_pnl = tdf_is["pnl"].sum() if len(tdf_is) > 0 else 0
    oos_pnl = tdf_oos["pnl"].sum() if len(tdf_oos) > 0 else 0
    oos_wr = (tdf_oos["pnl"] > 0).mean() * 100 if len(tdf_oos) > 0 else 0
    w = tdf_oos[tdf_oos["pnl"] > 0]; l = tdf_oos[tdf_oos["pnl"] <= 0]
    oos_pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999

    # Retention
    is_monthly = is_pnl / 4 if is_pnl != 0 else 0.01
    oos_monthly = oos_pnl / 2
    retention = oos_monthly / is_monthly * 100 if is_monthly != 0 else 0

    mark = " <-- current" if label == "1h" else ""
    print(f"  {label:>5s} ${is_pnl:>+11,.2f} ${oos_pnl:>+11,.2f} {oos_wr:>6.1f}% {oos_pf:>6.2f} {retention:>9.0f}%{mark}")

# ============================================================
# Part 5: 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(20, 14))
fig.suptitle("Data Verification & Multi-Timeframe Comparison", fontsize=14, fontweight="bold")

# 圖 1: 各週期 PnL
ax = axes[0][0]
valid_tfs = [tf for tf in ["15m", "30m", "1h", "2h", "4h"] if tf in results and results[tf]["pnl"] != 0]
pnls = [results[tf]["pnl"] for tf in valid_tfs]
colors = ["#FF9800" if tf == "1h" else "#2196F3" for tf in valid_tfs]
bars = ax.bar(valid_tfs, pnls, color=colors, edgecolor="black", linewidth=0.5)
for bar, val in zip(bars, pnls):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
            f"${val:+,.0f}", ha="center", va="bottom", fontsize=9)
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("PnL by Timeframe (6 months)")
ax.set_ylabel("PnL ($)"); ax.grid(True, alpha=0.3)

# 圖 2: 各週期交易數 & 勝率
ax = axes[0][1]
trades_n = [results[tf]["n"] for tf in valid_tfs]
wrs = [results[tf]["wr"] for tf in valid_tfs]
x = range(len(valid_tfs))
ax2 = ax.twinx()
ax.bar(x, trades_n, color="#90CAF9", edgecolor="black", linewidth=0.5, label="Trades")
ax2.plot(list(x), wrs, 'ro-', linewidth=2, markersize=8, label="Win Rate")
ax.set_xticks(list(x)); ax.set_xticklabels(valid_tfs)
ax.set_ylabel("Trade Count"); ax2.set_ylabel("Win Rate (%)")
ax.set_title("Trades & Win Rate by Timeframe")
ax.legend(loc="upper left"); ax2.legend(loc="upper right")

# 圖 3: 累計曲線
ax = axes[1][0]
for tf in valid_tfs:
    if "tdf" in results[tf]:
        cum = results[tf]["tdf"].sort_values("dt")["pnl"].cumsum()
        lw = 2.5 if tf == "1h" else 1.2
        alpha = 1.0 if tf == "1h" else 0.6
        ax.plot(range(len(cum)), cum.values, linewidth=lw, alpha=alpha,
                label=f"{tf} ${results[tf]['pnl']:+,.0f}")
ax.axhline(0, color="black", linewidth=0.5)
ax.set_title("Equity Curves by Timeframe")
ax.set_ylabel("PnL ($)"); ax.legend(); ax.grid(True, alpha=0.3)

# 圖 4: 總結表
ax = axes[1][1]
ax.axis("off")
table_data = [["TF", "Bars", "Trades", "PnL", "WR", "PF", "DD", "Exp/trade"]]
for tf in valid_tfs:
    r = results[tf]
    table_data.append([
        tf, str(r["bars"]), str(r["n"]),
        f"${r['pnl']:+,.0f}", f"{r['wr']}%", f"{r['pf']}",
        f"${r['dd']:,.0f}", f"${r['exp']:+,.2f}"
    ])
table = ax.table(cellText=table_data[1:], colLabels=table_data[0], cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(9); table.scale(1.0, 1.8)
for j in range(8):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
# 高亮 1h
for j in range(8):
    row_1h = valid_tfs.index("1h") + 1 if "1h" in valid_tfs else -1
    if row_1h > 0:
        table[row_1h, j].set_facecolor("#FFF2CC")
ax.set_title("Multi-Timeframe Comparison", fontweight="bold", pad=20)

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_data_verify.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 結論
# ============================================================
print(f"\n{'='*100}")
print("CONCLUSION")
print(f"{'='*100}")

if valid_tfs:
    best_tf = max(valid_tfs, key=lambda x: results[x]["pnl"])
    best_pf_tf = max(valid_tfs, key=lambda x: results[x].get("pf", 0))

    print(f"\n  收益最高：{best_tf} (${results[best_tf]['pnl']:+,.0f})")
    print(f"  PF 最高：{best_pf_tf} (PF {results[best_pf_tf]['pf']})")

    for tf in valid_tfs:
        r = results[tf]
        trades_per_day = r["n"] / 180  # 半年
        fee_total = r["n"] * 1.6  # 每筆約 $1.6 手續費
        print(f"\n  {tf}:")
        print(f"    每日交易：{trades_per_day:.1f} 筆")
        print(f"    半年手續費：${fee_total:,.0f}")
        print(f"    扣手續費後淨利：${r['pnl']:+,.0f}")
        if r["n"] > 0:
            print(f"    每筆期望值：${r['exp']:+,.2f}")
