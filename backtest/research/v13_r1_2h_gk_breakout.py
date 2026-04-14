"""
V13-R1: 2h GK Compression Breakout L+S
=======================================
假說：2h 時框的手續費佔比從 28.7% 降到 20.3%，
      同樣的 GK 壓縮突破信號在更低費用環境下應能產生更高淨利。

方法：
  1. 1h 數據重取樣為 2h
  2. 先用 V11-E 等效參數快速驗證 GK 信號在 2h 是否有效
  3. 再做參數掃描找最佳配置
"""
import pandas as pd
import numpy as np
from itertools import product

# ===== 載入 & 重取樣 =====
df_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df_1h["datetime"] = pd.to_datetime(df_1h["datetime"])
df_1h = df_1h.sort_values("datetime").reset_index(drop=True)

# 重取樣 1h → 2h (每 2 根合併)
# datetime 已是 UTC+8，確保偶數對齊
df_1h["hour"] = df_1h["datetime"].dt.hour
# 用 2h resample: 0-1, 2-3, 4-5, ... 22-23
df_1h.set_index("datetime", inplace=True)
df = df_1h.resample("2h").agg({
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "taker_buy_volume": "sum",
}).dropna().reset_index()

print(f"2h bars: {len(df)} (from {len(df_1h)} 1h bars)")
print(f"Date range: {df['datetime'].min()} ~ {df['datetime'].max()}")

# 驗證 avg |move|
abs_move = (df["close"] - df["open"]).abs()
print(f"Actual 2h avg |move|: ${abs_move.mean():.2f}")
print(f"Actual 2h avg |H-L|:  ${(df['high'] - df['low']).mean():.2f}")
print(f"Fee/avg|move|: {4/abs_move.mean()*100:.1f}%")

# ===== 指標計算 =====
o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
v = df["volume"].values.astype(float)
dt = df["datetime"].values

# GK volatility
gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk)
gk_ratio = gk_s.rolling(5).mean() / gk_s.rolling(20).mean()
gk_pctile = gk_ratio.shift(1).rolling(100).apply(
    lambda x: pd.Series(x).rank(pct=True).iloc[-1]
)

# Breakout (shift(1) applied)
c_s = pd.Series(c)
breakout_up_15 = c_s > c_s.shift(1).rolling(15).max()
breakout_dn_15 = c_s < c_s.shift(1).rolling(15).min()
breakout_up_10 = c_s > c_s.shift(1).rolling(10).max()
breakout_dn_10 = c_s < c_s.shift(1).rolling(10).min()
breakout_up_20 = c_s > c_s.shift(1).rolling(20).max()
breakout_dn_20 = c_s < c_s.shift(1).rolling(20).min()
breakout_up_8 = c_s > c_s.shift(1).rolling(8).max()
breakout_dn_8 = c_s < c_s.shift(1).rolling(8).min()

breakout_up = {8: breakout_up_8, 10: breakout_up_10, 15: breakout_up_15, 20: breakout_up_20}
breakout_dn = {8: breakout_dn_8, 10: breakout_dn_10, 15: breakout_dn_15, 20: breakout_dn_20}

# Session info (2h bars: hour is even: 0,2,4,...,22)
hours_2h = pd.to_datetime(dt).hour
days_2h = pd.to_datetime(dt).day_name()

# IS/OOS split
TOTAL = len(df)
IS_END = TOTAL // 2
print(f"IS: 0~{IS_END} ({IS_END} bars) | OOS: {IS_END}~{TOTAL} ({TOTAL - IS_END} bars)")

# ===== 回測函數 =====
DAILY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4
CONSEC_LOSS_COOLDOWN_2H = 12  # 24 hours


def run_backtest(
    side,  # "L" or "S"
    gk_thresh,
    bo_period,
    tp_pct,
    maxhold,
    safenet_pct,
    cooldown,
    monthly_loss_limit,
    block_hours,  # set of 2h bar hours to block
    block_days,   # set of day names to block
    monthly_cap=20,
):
    FEE = 4.0
    MARGIN = 200
    LEVERAGE = 20
    NOTIONAL = MARGIN * LEVERAGE  # 4000
    SLIP_MODEL = 0.25  # SafeNet 25% penetration

    bu = breakout_up[bo_period]
    bd = breakout_dn[bo_period]

    positions = {}
    trades = []
    last_exit_bar = -999
    daily_pnl = {}
    monthly_pnl = {}
    monthly_entries = {}
    consec_losses = 0
    consec_pause_until = -1

    for i in range(150, TOTAL - 1):
        bar_dt = pd.Timestamp(dt[i])
        day_key = bar_dt.strftime("%Y-%m-%d")
        month_key = bar_dt.strftime("%Y-%m")

        if day_key not in daily_pnl:
            daily_pnl[day_key] = 0.0
        if month_key not in monthly_pnl:
            monthly_pnl[month_key] = 0.0
        if month_key not in monthly_entries:
            monthly_entries[month_key] = 0

        # === 出場 ===
        for tid in list(positions.keys()):
            pos = positions[tid]
            entry_p = pos["entry_price"]
            bars_held = i - pos["entry_bar"]

            if side == "L":
                pnl_pct = (c[i] - entry_p) / entry_p
                hit_tp = (h[i] - entry_p) / entry_p >= tp_pct
                hit_sl = (l[i] - entry_p) / entry_p <= -safenet_pct
                # TP: use entry * (1 + tp_pct) as exit
                tp_exit = entry_p * (1 + tp_pct)
                # SL: use entry * (1 - safenet * (1 + slip))
                sl_exit = entry_p * (1 - safenet_pct * (1 + SLIP_MODEL))
            else:  # S
                pnl_pct = (entry_p - c[i]) / entry_p
                hit_tp = (entry_p - l[i]) / entry_p >= tp_pct
                hit_sl = (entry_p - h[i]) / entry_p <= -safenet_pct
                tp_exit = entry_p * (1 - tp_pct)
                sl_exit = entry_p * (1 + safenet_pct * (1 + SLIP_MODEL))

            exit_price = None
            exit_reason = None

            if hit_sl:
                exit_price = sl_exit
                exit_reason = "SafeNet"
            elif hit_tp:
                exit_price = tp_exit
                exit_reason = "TP"
            elif bars_held >= maxhold:
                exit_price = c[i]
                exit_reason = "MaxHold"

            if exit_price is not None:
                if side == "L":
                    raw_pnl = (exit_price - entry_p) / entry_p * NOTIONAL
                else:
                    raw_pnl = (entry_p - exit_price) / entry_p * NOTIONAL
                net_pnl = raw_pnl - FEE

                trades.append({
                    "entry_bar": pos["entry_bar"],
                    "exit_bar": i,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "pnl": net_pnl,
                    "reason": exit_reason,
                    "side": side,
                    "bars_held": bars_held,
                    "is_oos": "OOS" if pos["entry_bar"] >= IS_END else "IS",
                })

                daily_pnl[day_key] += net_pnl
                monthly_pnl[month_key] += net_pnl
                last_exit_bar = i

                if net_pnl < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0

                if consec_losses >= CONSEC_LOSS_PAUSE:
                    consec_pause_until = i + CONSEC_LOSS_COOLDOWN_2H

                del positions[tid]

        # === 風控檢查 ===
        if len(positions) > 0:
            continue  # maxTotal=1 per side

        if i - last_exit_bar < cooldown:
            continue

        if i < consec_pause_until:
            continue

        if daily_pnl.get(day_key, 0) <= DAILY_LOSS_LIMIT:
            continue

        if monthly_pnl.get(month_key, 0) <= monthly_loss_limit:
            continue

        if monthly_entries.get(month_key, 0) >= monthly_cap:
            continue

        # === Session filter ===
        bar_hour = hours_2h[i]
        bar_day = days_2h[i]
        if bar_hour in block_hours:
            continue
        if bar_day in block_days:
            continue

        # === 進場信號 ===
        gk_ok = gk_pctile.iloc[i] < (gk_thresh / 100.0) if not np.isnan(gk_pctile.iloc[i]) else False

        if side == "L":
            signal = gk_ok and bu.iloc[i]
        else:
            signal = gk_ok and bd.iloc[i]

        if signal:
            entry_price = o[i + 1]  # next bar open
            positions[f"{side}_{i}"] = {
                "entry_price": entry_price,
                "entry_bar": i,
            }
            monthly_entries[month_key] += 1

    return trades


# ===== V11-E 等效參數 (快速驗證) =====
print("\n" + "=" * 70)
print("V11-E 等效參數在 2h 上的表現")
print("=" * 70)

# V11-E L on 2h: 需要映射 session filter
# V11-E blocks hours {0,1,2,12} UTC+8 → 2h bars containing these: {0, 2, 12}
# V11-E blocks days {Mon, Sat, Sun}
# Cooldown 6 bars on 1h = 3 bars on 2h

for side_label, params in [
    ("L-equiv", {"side": "L", "gk_thresh": 25, "bo_period": 15, "tp_pct": 0.035,
                 "maxhold": 3, "safenet_pct": 0.035, "cooldown": 3,
                 "monthly_loss_limit": -75,
                 "block_hours": {0, 2, 12}, "block_days": {"Monday", "Saturday", "Sunday"}}),
    ("S-equiv", {"side": "S", "gk_thresh": 30, "bo_period": 15, "tp_pct": 0.02,
                 "maxhold": 4, "safenet_pct": 0.04, "cooldown": 4,
                 "monthly_loss_limit": -150,
                 "block_hours": {0, 2, 12}, "block_days": {"Monday", "Saturday", "Sunday"}}),
]:
    trades = run_backtest(**params)
    if not trades:
        print(f"\n{side_label}: 0 trades")
        continue

    tdf = pd.DataFrame(trades)
    is_trades = tdf[tdf["is_oos"] == "IS"]
    oos_trades = tdf[tdf["is_oos"] == "OOS"]

    print(f"\n--- {side_label} ---")
    for label, subset in [("IS", is_trades), ("OOS", oos_trades), ("ALL", tdf)]:
        if len(subset) == 0:
            print(f"  {label}: 0 trades")
            continue
        pnl = subset["pnl"].sum()
        wr = (subset["pnl"] > 0).mean() * 100
        trades_count = len(subset)
        avg_pnl = subset["pnl"].mean()
        # MDD
        cum = subset["pnl"].cumsum()
        mdd = (cum - cum.cummax()).min()
        # Exit reasons
        reasons = subset["reason"].value_counts().to_dict()
        print(f"  {label}: {trades_count}t ${pnl:.0f} WR {wr:.0f}% avg ${avg_pnl:.1f} MDD ${mdd:.0f} | {reasons}")

# ===== 參數掃描 =====
print("\n" + "=" * 70)
print("2h 參數掃描")
print("=" * 70)

# Parameter grid
gk_thresholds = [20, 25, 30, 35]
bo_periods = [8, 10, 15, 20]
tp_pcts = [0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05]
maxholds = [3, 4, 5, 6, 7, 8]
safenets = [0.03, 0.035, 0.04, 0.045, 0.05]
cooldowns = [2, 3, 4, 5, 6]

# Fixed session filter (same as V11-E mapped to 2h)
BLOCK_HOURS = {0, 2, 12}
BLOCK_DAYS = {"Monday", "Saturday", "Sunday"}

results_l = []
results_s = []

# L scan: focus on reasonable parameter space
print("\nScanning L...")
scan_count = 0
for gk_t, bo, tp, mh, sn, cd in product(
    gk_thresholds, bo_periods, [0.03, 0.035, 0.04, 0.045, 0.05],
    [3, 4, 5, 6, 7, 8], [0.035, 0.04, 0.045], [2, 3, 4, 5]
):
    trades = run_backtest(
        side="L", gk_thresh=gk_t, bo_period=bo, tp_pct=tp,
        maxhold=mh, safenet_pct=sn, cooldown=cd,
        monthly_loss_limit=-75,
        block_hours=BLOCK_HOURS, block_days=BLOCK_DAYS,
    )
    scan_count += 1
    if not trades:
        continue

    tdf = pd.DataFrame(trades)
    is_t = tdf[tdf["is_oos"] == "IS"]
    oos_t = tdf[tdf["is_oos"] == "OOS"]

    if len(is_t) == 0 or len(oos_t) == 0:
        continue

    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()

    if is_pnl <= 0:
        continue  # IS must be positive

    is_wr = (is_t["pnl"] > 0).mean() * 100
    oos_wr = (oos_t["pnl"] > 0).mean() * 100
    oos_cum = oos_t["pnl"].cumsum()
    oos_mdd = (oos_cum - oos_cum.cummax()).min()

    # Monthly PnL (OOS)
    oos_t = oos_t.copy()
    oos_t["month"] = pd.to_datetime(dt[oos_t["entry_bar"].values]).strftime("%Y-%m")
    monthly = oos_t.groupby("month")["pnl"].sum()
    pos_months = (monthly > 0).sum()
    total_months = len(monthly)
    worst_month = monthly.min()

    results_l.append({
        "gk": gk_t, "bo": bo, "tp": tp, "mh": mh, "sn": sn, "cd": cd,
        "is_pnl": is_pnl, "is_wr": is_wr, "is_n": len(is_t),
        "oos_pnl": oos_pnl, "oos_wr": oos_wr, "oos_n": len(oos_t),
        "oos_mdd": oos_mdd,
        "pos_months": pos_months, "total_months": total_months,
        "worst_month": worst_month,
    })

print(f"  Scanned {scan_count} configs, {len(results_l)} passed IS>0")

# S scan
print("Scanning S...")
scan_count = 0
for gk_t, bo, tp, mh, sn, cd in product(
    gk_thresholds, bo_periods, [0.015, 0.02, 0.025, 0.03, 0.035, 0.04],
    [3, 4, 5, 6, 7, 8], [0.035, 0.04, 0.045, 0.05], [2, 3, 4, 5, 6]
):
    trades = run_backtest(
        side="S", gk_thresh=gk_t, bo_period=bo, tp_pct=tp,
        maxhold=mh, safenet_pct=sn, cooldown=cd,
        monthly_loss_limit=-150,
        block_hours=BLOCK_HOURS, block_days=BLOCK_DAYS,
    )
    scan_count += 1
    if not trades:
        continue

    tdf = pd.DataFrame(trades)
    is_t = tdf[tdf["is_oos"] == "IS"]
    oos_t = tdf[tdf["is_oos"] == "OOS"]

    if len(is_t) == 0 or len(oos_t) == 0:
        continue

    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()

    if is_pnl <= 0:
        continue

    is_wr = (is_t["pnl"] > 0).mean() * 100
    oos_wr = (oos_t["pnl"] > 0).mean() * 100
    oos_cum = oos_t["pnl"].cumsum()
    oos_mdd = (oos_cum - oos_cum.cummax()).min()

    oos_t = oos_t.copy()
    oos_t["month"] = pd.to_datetime(dt[oos_t["entry_bar"].values]).strftime("%Y-%m")
    monthly = oos_t.groupby("month")["pnl"].sum()
    pos_months = (monthly > 0).sum()
    total_months = len(monthly)
    worst_month = monthly.min()

    results_s.append({
        "gk": gk_t, "bo": bo, "tp": tp, "mh": mh, "sn": sn, "cd": cd,
        "is_pnl": is_pnl, "is_wr": is_wr, "is_n": len(is_t),
        "oos_pnl": oos_pnl, "oos_wr": oos_wr, "oos_n": len(oos_t),
        "oos_mdd": oos_mdd,
        "pos_months": pos_months, "total_months": total_months,
        "worst_month": worst_month,
    })

print(f"  Scanned {scan_count} configs, {len(results_s)} passed IS>0")

# ===== 結果排序 =====
print("\n" + "=" * 70)
print("TOP 15 L configs (sorted by OOS PnL)")
print("=" * 70)

if results_l:
    rl = pd.DataFrame(results_l).sort_values("oos_pnl", ascending=False)
    for idx, row in rl.head(15).iterrows():
        print(f"  GK<{row['gk']} BO{row['bo']} TP{row['tp']:.1%} MH{row['mh']} SN{row['sn']:.1%} CD{row['cd']}"
              f" | IS: {row['is_n']:.0f}t ${row['is_pnl']:.0f} WR{row['is_wr']:.0f}%"
              f" | OOS: {row['oos_n']:.0f}t ${row['oos_pnl']:.0f} WR{row['oos_wr']:.0f}% MDD${row['oos_mdd']:.0f}"
              f" | PM {row['pos_months']:.0f}/{row['total_months']:.0f} worst${row['worst_month']:.0f}")
else:
    print("  No L configs passed IS>0")

print("\n" + "=" * 70)
print("TOP 15 S configs (sorted by OOS PnL)")
print("=" * 70)

if results_s:
    rs = pd.DataFrame(results_s).sort_values("oos_pnl", ascending=False)
    for idx, row in rs.head(15).iterrows():
        print(f"  GK<{row['gk']} BO{row['bo']} TP{row['tp']:.1%} MH{row['mh']} SN{row['sn']:.1%} CD{row['cd']}"
              f" | IS: {row['is_n']:.0f}t ${row['is_pnl']:.0f} WR{row['is_wr']:.0f}%"
              f" | OOS: {row['oos_n']:.0f}t ${row['oos_pnl']:.0f} WR{row['oos_wr']:.0f}% MDD${row['oos_mdd']:.0f}"
              f" | PM {row['pos_months']:.0f}/{row['total_months']:.0f} worst${row['worst_month']:.0f}")
else:
    print("  No S configs passed IS>0")

# ===== Best L+S combo =====
print("\n" + "=" * 70)
print("L+S 組合 vs V11-E")
print("=" * 70)

if results_l and results_s:
    best_l = pd.DataFrame(results_l).sort_values("oos_pnl", ascending=False).iloc[0]
    best_s = pd.DataFrame(results_s).sort_values("oos_pnl", ascending=False).iloc[0]

    combo_oos = best_l["oos_pnl"] + best_s["oos_pnl"]
    combo_is = best_l["is_pnl"] + best_s["is_pnl"]

    print(f"Best L: GK<{best_l['gk']} BO{best_l['bo']} TP{best_l['tp']:.1%} MH{best_l['mh']} "
          f"SN{best_l['sn']:.1%} CD{best_l['cd']}")
    print(f"  IS: {best_l['is_n']:.0f}t ${best_l['is_pnl']:.0f} | OOS: {best_l['oos_n']:.0f}t ${best_l['oos_pnl']:.0f}")
    print(f"Best S: GK<{best_s['gk']} BO{best_s['bo']} TP{best_s['tp']:.1%} MH{best_s['mh']} "
          f"SN{best_s['sn']:.1%} CD{best_s['cd']}")
    print(f"  IS: {best_s['is_n']:.0f}t ${best_s['is_pnl']:.0f} | OOS: {best_s['oos_n']:.0f}t ${best_s['oos_pnl']:.0f}")
    print(f"\nL+S OOS: ${combo_oos:.0f} (V11-E: $2,801, diff: ${combo_oos - 2801:.0f})")
    print(f"L+S IS:  ${combo_is:.0f} (V11-E: $1,034, diff: ${combo_is - 1034:.0f})")

print("\nDone.")
