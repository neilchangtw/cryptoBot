"""
V10 R1: ETH 1h 全面數據特性分析
目標：找出可利用的統計 edge，為策略設計提供依據
分析維度：
  1. 基本收益分佈
  2. 日內時段效應 (Hour-of-Day)
  3. 星期效應 (Day-of-Week)
  4. Taker Buy Ratio (TBR) 訂單流分析
  5. 成交量 / 成交筆數異常
  6. 波動率聚集與回歸
  7. 連續漲跌棒後的均值回歸
  8. BTC 領先 ETH 的 lead-lag 關係
  9. 價格區間突破後的動量持續性
"""
import pandas as pd
import numpy as np
from collections import defaultdict

# ===== 載入數據 =====
eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

eth["ret"] = eth["close"].pct_change()
eth["log_ret"] = np.log(eth["close"] / eth["close"].shift(1))
btc["ret"] = btc["close"].pct_change()

# Taker Buy Ratio
eth["tbr"] = eth["tbv"] / eth["volume"]
eth["avg_trade_size"] = eth["volume"] / eth["trades"]

# Hour and Day
eth["hour"] = eth["datetime"].dt.hour
eth["dow"] = eth["datetime"].dt.dayofweek  # 0=Mon
eth["date"] = eth["datetime"].dt.date
eth["month"] = eth["datetime"].dt.to_period("M")

print("=" * 70)
print("V10 R1: ETH 1h 全面數據特性分析")
print(f"數據範圍: {eth['datetime'].iloc[0]} ~ {eth['datetime'].iloc[-1]}")
print(f"總 bars: {len(eth)}")
print("=" * 70)

# ===== 1. 基本收益分佈 =====
print("\n" + "=" * 70)
print("1. 基本收益分佈")
print("=" * 70)
ret = eth["ret"].dropna()
print(f"均值(hourly): {ret.mean()*100:.4f}%")
print(f"標準差: {ret.std()*100:.4f}%")
print(f"偏度: {ret.skew():.4f}")
print(f"峰度: {ret.kurtosis():.4f}")
print(f"正報酬比例: {(ret > 0).mean()*100:.1f}%")
print(f"中位數: {ret.median()*100:.4f}%")

# 大幅波動分佈
for thresh in [1, 2, 3, 4, 5]:
    up = (ret > thresh/100).sum()
    dn = (ret < -thresh/100).sum()
    print(f"  |ret| > {thresh}%: 上漲 {up} 次, 下跌 {dn} 次")

# ===== 2. 日內時段效應 =====
print("\n" + "=" * 70)
print("2. 日內時段效應 (UTC+8)")
print("=" * 70)
print(f"{'Hour':>4} {'MeanRet%':>10} {'StdRet%':>10} {'WinRate%':>10} {'N':>6} {'Sharpe':>8}")
print("-" * 50)
hourly = eth.groupby("hour")["ret"].agg(["mean", "std", "count"])
hourly["wr"] = eth.groupby("hour")["ret"].apply(lambda x: (x > 0).mean())
for h in range(24):
    if h in hourly.index:
        r = hourly.loc[h]
        sharpe = r["mean"] / r["std"] if r["std"] > 0 else 0
        print(f"{h:4d} {r['mean']*100:10.4f} {r['std']*100:10.4f} {r['wr']*100:10.1f} {int(r['count']):6d} {sharpe:8.4f}")

# 時段分組
sessions = {
    "Asia(0-7)": list(range(0, 8)),
    "Europe(8-15)": list(range(8, 16)),
    "US(16-23)": list(range(16, 24)),
}
print("\n時段分組:")
for name, hours in sessions.items():
    mask = eth["hour"].isin(hours)
    r = eth.loc[mask, "ret"]
    wr = (r > 0).mean()
    print(f"  {name}: 均值 {r.mean()*100:.4f}%, WR {wr*100:.1f}%, std {r.std()*100:.4f}%")

# ===== 3. 星期效應 =====
print("\n" + "=" * 70)
print("3. 星期效應")
print("=" * 70)
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
daily_ret = eth.groupby("date")["ret"].sum().reset_index()
daily_ret["dow"] = pd.to_datetime(daily_ret["date"]).dt.dayofweek
print(f"{'Day':>4} {'MeanDailyRet%':>14} {'WinRate%':>10} {'N':>6}")
print("-" * 40)
for d in range(7):
    mask = daily_ret["dow"] == d
    r = daily_ret.loc[mask, "ret"]
    wr = (r > 0).mean()
    print(f"{days[d]:>4} {r.mean()*100:14.4f} {wr*100:10.1f} {len(r):6d}")

# ===== 4. Taker Buy Ratio (TBR) 分析 =====
print("\n" + "=" * 70)
print("4. Taker Buy Ratio (TBR) 訂單流分析")
print("=" * 70)
tbr = eth["tbr"].dropna()
print(f"TBR 均值: {tbr.mean():.4f}")
print(f"TBR 中位數: {tbr.median():.4f}")
print(f"TBR std: {tbr.std():.4f}")
print(f"TBR Q10/Q25/Q75/Q90: {tbr.quantile(0.1):.4f} / {tbr.quantile(0.25):.4f} / {tbr.quantile(0.75):.4f} / {tbr.quantile(0.9):.4f}")

# TBR 分位與下一根 bar 收益
eth["next_ret"] = eth["ret"].shift(-1)
for q_lo, q_hi, label in [(0, 20, "TBR<20%ile"), (20, 40, "TBR 20-40%ile"),
                           (40, 60, "TBR 40-60%ile"), (60, 80, "TBR 60-80%ile"),
                           (80, 100, "TBR>80%ile")]:
    lo = tbr.quantile(q_lo / 100)
    hi = tbr.quantile(q_hi / 100)
    if q_hi == 100:
        hi = tbr.max() + 1
    mask = (eth["tbr"] >= lo) & (eth["tbr"] < hi)
    nr = eth.loc[mask, "next_ret"].dropna()
    wr = (nr > 0).mean()
    print(f"  {label:>16}: next_bar 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# TBR 5-bar 滾動均值與預測
eth["tbr_ma5"] = eth["tbr"].rolling(5).mean().shift(1)
eth["tbr_ma5_pct"] = eth["tbr_ma5"].rolling(100).rank(pct=True).shift(0)  # already shifted tbr_ma5

# TBR 極端值 → 反轉?
print("\n  TBR 極端值後的反轉效應:")
for lookback in [1, 3, 5]:
    eth[f"fwd_ret_{lookback}"] = eth["close"].shift(-lookback) / eth["close"] - 1

    # TBR > 90th percentile (strong buying)
    hi_mask = eth["tbr"] > tbr.quantile(0.9)
    lo_mask = eth["tbr"] < tbr.quantile(0.1)

    hi_fwd = eth.loc[hi_mask, f"fwd_ret_{lookback}"].dropna()
    lo_fwd = eth.loc[lo_mask, f"fwd_ret_{lookback}"].dropna()

    print(f"  TBR>90pct → {lookback}bar後: 均值 {hi_fwd.mean()*100:.4f}%, WR {(hi_fwd>0).mean()*100:.1f}%, N={len(hi_fwd)}")
    print(f"  TBR<10pct → {lookback}bar後: 均值 {lo_fwd.mean()*100:.4f}%, WR {(lo_fwd>0).mean()*100:.1f}%, N={len(lo_fwd)}")

# ===== 5. 成交量 / 成交筆數異常 =====
print("\n" + "=" * 70)
print("5. 成交量異常分析")
print("=" * 70)
eth["vol_ma20"] = eth["volume"].rolling(20).mean().shift(1)
eth["vol_ratio"] = eth["volume"] / eth["vol_ma20"]
eth["trades_ma20"] = eth["trades"].rolling(20).mean().shift(1)
eth["trades_ratio"] = eth["trades"] / eth["trades_ma20"]

for ratio_name, col in [("Volume Ratio", "vol_ratio"), ("Trades Ratio", "trades_ratio")]:
    print(f"\n  {ratio_name} 與下一根 bar 收益:")
    for lo, hi, label in [(0, 0.5, "<0.5x"), (0.5, 0.8, "0.5-0.8x"), (0.8, 1.2, "0.8-1.2x"),
                          (1.2, 2.0, "1.2-2.0x"), (2.0, 100, ">2.0x")]:
        mask = (eth[col] >= lo) & (eth[col] < hi)
        nr = eth.loc[mask, "next_ret"].dropna()
        if len(nr) > 10:
            wr = (nr > 0).mean()
            print(f"    {label:>8}: next_bar 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# Volume + Direction interaction
print("\n  成交量 + 方向交互:")
for vol_label, vol_lo, vol_hi in [("Low Vol", 0, 0.7), ("Normal Vol", 0.7, 1.5), ("High Vol", 1.5, 100)]:
    for dir_label, dir_fn in [("Up Bar", lambda x: x > 0), ("Down Bar", lambda x: x < 0)]:
        mask = (eth["vol_ratio"] >= vol_lo) & (eth["vol_ratio"] < vol_hi) & dir_fn(eth["ret"])
        nr = eth.loc[mask, "next_ret"].dropna()
        if len(nr) > 10:
            wr = (nr > 0).mean()
            print(f"    {vol_label} + {dir_label}: next 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# ===== 6. 波動率聚集與回歸 =====
print("\n" + "=" * 70)
print("6. 波動率聚集分析")
print("=" * 70)
eth["abs_ret"] = eth["ret"].abs()
eth["vol20"] = eth["abs_ret"].rolling(20).mean().shift(1)
eth["vol5"] = eth["abs_ret"].rolling(5).mean().shift(1)
eth["vol_ratio_5_20"] = eth["vol5"] / eth["vol20"]

print("  波動率壓縮/擴張 vs 下 5 bar 絕對收益:")
for lo, hi, label in [(0, 0.5, "極度壓縮 <0.5"), (0.5, 0.75, "壓縮 0.5-0.75"),
                       (0.75, 1.25, "正常 0.75-1.25"), (1.25, 2.0, "擴張 1.25-2.0"),
                       (2.0, 100, "極度擴張 >2.0")]:
    mask = (eth["vol_ratio_5_20"] >= lo) & (eth["vol_ratio_5_20"] < hi)
    fwd = eth.loc[mask, "fwd_ret_5"].dropna().abs()
    cur = eth.loc[mask, "next_ret"].dropna()
    if len(fwd) > 10:
        print(f"    {label:>20}: 5bar_abs_ret {fwd.mean()*100:.4f}%, next_bar_WR_up {(cur>0).mean()*100:.1f}%, N={len(fwd)}")

# ===== 7. 連續漲跌棒 =====
print("\n" + "=" * 70)
print("7. 連續漲跌棒均值回歸分析")
print("=" * 70)

# Count consecutive up/down bars
eth["up"] = (eth["ret"] > 0).astype(int)
consec = []
count = 0
prev_up = None
for i, row in eth.iterrows():
    if pd.isna(row["ret"]):
        consec.append(0)
        continue
    is_up = row["ret"] > 0
    if is_up == prev_up:
        count += 1
    else:
        count = 1
    prev_up = is_up
    consec.append(count if is_up else -count)
eth["consec"] = consec

print(f"{'Streak':>8} {'NextBarMean%':>14} {'NextBarWR%':>12} {'N':>6}")
print("-" * 45)
for streak in [-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6]:
    if streak > 0:
        mask = eth["consec"] == streak
    else:
        mask = eth["consec"] == streak
    nr = eth.loc[mask, "next_ret"].dropna()
    if len(nr) > 5:
        wr = (nr > 0).mean()
        label = f"+{streak}" if streak > 0 else str(streak)
        print(f"{label:>8} {nr.mean()*100:14.4f} {wr*100:12.1f} {len(nr):6d}")

# ===== 8. BTC 領先 ETH 分析 =====
print("\n" + "=" * 70)
print("8. BTC 領先 ETH (Lead-Lag) 分析")
print("=" * 70)

# Merge BTC returns
merged = eth[["datetime", "ret", "next_ret", "close"]].copy()
merged = merged.merge(btc[["datetime", "ret"]].rename(columns={"ret": "btc_ret"}), on="datetime", how="inner")

# BTC return → ETH next bar return
for btc_lag in [1, 2, 3]:
    merged[f"btc_ret_lag{btc_lag}"] = merged["btc_ret"].shift(btc_lag)

# BTC 本 bar 大幅波動 → ETH 下一 bar
print("\n  BTC 本 bar 大幅波動 → ETH 下一 bar:")
for thresh in [1, 2, 3]:
    btc_up = merged["btc_ret"] > thresh / 100
    btc_dn = merged["btc_ret"] < -thresh / 100

    eth_after_btc_up = merged.loc[btc_up, "next_ret"].dropna()
    eth_after_btc_dn = merged.loc[btc_dn, "next_ret"].dropna()

    if len(eth_after_btc_up) > 5:
        print(f"  BTC > +{thresh}%: ETH next 均值 {eth_after_btc_up.mean()*100:.4f}%, WR {(eth_after_btc_up>0).mean()*100:.1f}%, N={len(eth_after_btc_up)}")
    if len(eth_after_btc_dn) > 5:
        print(f"  BTC < -{thresh}%: ETH next 均值 {eth_after_btc_dn.mean()*100:.4f}%, WR {(eth_after_btc_dn>0).mean()*100:.1f}%, N={len(eth_after_btc_dn)}")

# BTC-ETH 同 bar 背離
merged["divergence"] = merged["btc_ret"] - merged["ret"]
print("\n  BTC-ETH 同 bar 背離 → ETH 下一 bar:")
for q_lo, q_hi, label in [(0, 10, "ETH 大幅落後 BTC"), (10, 25, "ETH 落後 BTC"),
                           (75, 90, "ETH 領先 BTC"), (90, 100, "ETH 大幅領先 BTC")]:
    lo = merged["divergence"].quantile(q_lo / 100)
    hi = merged["divergence"].quantile(q_hi / 100)
    mask = (merged["divergence"] >= lo) & (merged["divergence"] < hi)
    nr = merged.loc[mask, "next_ret"].dropna()
    if len(nr) > 10:
        wr = (nr > 0).mean()
        print(f"    {label:>24}: next 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# Cumulative lead-lag: BTC 過去3 bar 累積收益
merged["btc_cum3"] = merged["btc_ret"].rolling(3).sum().shift(1)
merged["eth_cum3"] = merged["ret"].rolling(3).sum().shift(1)
merged["rel_cum3"] = merged["btc_cum3"] - merged["eth_cum3"]

print("\n  BTC-ETH 3bar累積背離 → ETH 下一 bar:")
for q_lo, q_hi, label in [(0, 10, "ETH 大幅落後"), (10, 25, "ETH 落後"),
                           (75, 90, "ETH 領先"), (90, 100, "ETH 大幅領先")]:
    lo = merged["rel_cum3"].quantile(q_lo / 100)
    hi = merged["rel_cum3"].quantile(q_hi / 100)
    mask = (merged["rel_cum3"] >= lo) & (merged["rel_cum3"] < hi)
    nr = merged.loc[mask, "next_ret"].dropna()
    if len(nr) > 10:
        wr = (nr > 0).mean()
        print(f"    {label:>24}: next 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# ===== 9. Breakout 持續性分析 =====
print("\n" + "=" * 70)
print("9. Breakout 後動量持續性")
print("=" * 70)
for lookback in [5, 10, 15, 20]:
    eth[f"hi_{lookback}"] = eth["high"].rolling(lookback).max().shift(1)
    eth[f"lo_{lookback}"] = eth["low"].rolling(lookback).min().shift(1)

    breakout_up = eth["close"] > eth[f"hi_{lookback}"]
    breakout_dn = eth["close"] < eth[f"lo_{lookback}"]

    for bars_fwd in [1, 3, 5]:
        fwd_col = f"fwd_ret_{bars_fwd}" if f"fwd_ret_{bars_fwd}" in eth.columns else None
        if fwd_col is None:
            eth[f"fwd_ret_{bars_fwd}"] = eth["close"].shift(-bars_fwd) / eth["close"] - 1
            fwd_col = f"fwd_ret_{bars_fwd}"

        up_fwd = eth.loc[breakout_up, fwd_col].dropna()
        dn_fwd = eth.loc[breakout_dn, fwd_col].dropna()

        if len(up_fwd) > 10 and bars_fwd == 3:
            print(f"  突破{lookback}bar高: {bars_fwd}bar後 均值 {up_fwd.mean()*100:.4f}%, WR {(up_fwd>0).mean()*100:.1f}%, N={len(up_fwd)}")
            print(f"  跌破{lookback}bar低: {bars_fwd}bar後 均值 {dn_fwd.mean()*100:.4f}%, WR {(dn_fwd>0).mean()*100:.1f}%, N={len(dn_fwd)}")

# ===== 10. 綜合 Edge 評分 =====
print("\n" + "=" * 70)
print("10. TBR + Direction + Volume 多因子交互")
print("=" * 70)

eth["tbr_pct"] = eth["tbr"].rolling(100).rank(pct=True).shift(1)
# Combine: TBR extreme + consecutive bars + volume
print("\n  TBR 極端 + 連續棒 → 下一 bar:")
for tbr_cond, tbr_label in [("high", "TBR>80pct"), ("low", "TBR<20pct")]:
    for streak_cond in [-3, -2, 2, 3]:
        if tbr_cond == "high":
            tbr_mask = eth["tbr_pct"] > 0.8
        else:
            tbr_mask = eth["tbr_pct"] < 0.2
        streak_mask = eth["consec"] == streak_cond
        combined = tbr_mask & streak_mask
        nr = eth.loc[combined, "next_ret"].dropna()
        if len(nr) >= 10:
            wr = (nr > 0).mean()
            streak_label = f"+{streak_cond}" if streak_cond > 0 else str(streak_cond)
            print(f"    {tbr_label} + streak{streak_label}: 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# ===== 11. 回報自相關分析 =====
print("\n" + "=" * 70)
print("11. 回報自相關 (Autocorrelation)")
print("=" * 70)
for lag in [1, 2, 3, 5, 10, 20]:
    ac = eth["ret"].autocorr(lag=lag)
    print(f"  Lag {lag:2d}: {ac:.4f}")

# Rolling autocorrelation
eth["ac1_20"] = eth["ret"].rolling(20).apply(lambda x: x.autocorr(lag=1), raw=False).shift(1)
print("\n  20-bar 滾動 AC(1) 分位 vs 下一 bar:")
for q_lo, q_hi, label in [(0, 20, "強負自相關"), (20, 40, "弱負自相關"),
                           (40, 60, "中性"), (60, 80, "弱正自相關"),
                           (80, 100, "強正自相關")]:
    lo = eth["ac1_20"].quantile(q_lo / 100)
    hi = eth["ac1_20"].quantile(q_hi / 100)
    mask = (eth["ac1_20"] >= lo) & (eth["ac1_20"] < hi)
    nr = eth.loc[mask, "next_ret"].dropna()
    if len(nr) > 10:
        wr = (nr > 0).mean()
        print(f"    {label:>12}: next 均值 {nr.mean()*100:.4f}%, WR {wr*100:.1f}%, N={len(nr)}")

# ===== 12. 大幅波動後反轉 =====
print("\n" + "=" * 70)
print("12. 大幅波動後短期反轉")
print("=" * 70)
for thresh in [2, 3, 4, 5]:
    big_up = eth["ret"] > thresh / 100
    big_dn = eth["ret"] < -thresh / 100

    for fwd in [1, 3, 5]:
        fwd_col = f"fwd_ret_{fwd}"
        up_fwd = eth.loc[big_up, fwd_col].dropna()
        dn_fwd = eth.loc[big_dn, fwd_col].dropna()

        if len(up_fwd) >= 5 and fwd == 3:
            print(f"  大漲>{thresh}%: {fwd}bar後 均值 {up_fwd.mean()*100:.4f}%, WR_down {(up_fwd<0).mean()*100:.1f}%, N={len(up_fwd)}")
        if len(dn_fwd) >= 5 and fwd == 3:
            print(f"  大跌<-{thresh}%: {fwd}bar後 均值 {dn_fwd.mean()*100:.4f}%, WR_up {(dn_fwd>0).mean()*100:.1f}%, N={len(dn_fwd)}")

# ===== 13. 波動率壓縮→突破方向性 =====
print("\n" + "=" * 70)
print("13. 波動率壓縮後突破方向性")
print("=" * 70)
# GK volatility (consistent with v6)
gk = 0.5 * np.log(eth["high"] / eth["low"])**2 - (2*np.log(2) - 1) * np.log(eth["close"] / eth["open"])**2
eth["gk"] = gk
eth["gk_ratio"] = eth["gk"].rolling(5).mean() / eth["gk"].rolling(20).mean()
eth["gk_pct"] = eth["gk_ratio"].shift(1).rolling(100).rank(pct=True) * 100

# During compression, which direction wins?
compressed = eth["gk_pct"] < 30
normal = (eth["gk_pct"] >= 30) & (eth["gk_pct"] <= 70)
expanded = eth["gk_pct"] > 70

for label, mask in [("壓縮 GK<30", compressed), ("正常 30-70", normal), ("擴張 GK>70", expanded)]:
    nr = eth.loc[mask, "next_ret"].dropna()
    if len(nr) > 10:
        wr_up = (nr > 0).mean()
        abs_mean = nr.abs().mean()
        print(f"  {label}: WR_up {wr_up*100:.1f}%, abs_mean {abs_mean*100:.4f}%, N={len(nr)}")

# ===== Summary =====
print("\n" + "=" * 70)
print("=== 分析完成 ===")
print("=" * 70)
print("\n請根據以上數據，評估哪些 edge 最有潛力用於 $1K 帳戶穩定獲利策略。")
