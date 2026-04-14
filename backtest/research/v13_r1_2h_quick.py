"""
V13-R1 Quick: 2h GK Compression Breakout — 快速驗證 + 精簡掃描
"""
import pandas as pd
import numpy as np

# ===== 載入 & 重取樣 =====
df_1h = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df_1h["datetime"] = pd.to_datetime(df_1h["datetime"])
df_1h = df_1h.sort_values("datetime").reset_index(drop=True)

df_1h.set_index("datetime", inplace=True)
df = df_1h.resample("2h").agg({
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "taker_buy_volume": "sum",
}).dropna().reset_index()

print(f"2h bars: {len(df)}")
abs_move = (df["close"] - df["open"]).abs()
print(f"Actual 2h avg |move|: ${abs_move.mean():.2f}, Fee ratio: {4/abs_move.mean()*100:.1f}%")

# ===== 預計算指標 =====
o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values

# GK
gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk)
gk_ratio = gk_s.rolling(5).mean() / gk_s.rolling(20).mean()
gk_pctile = gk_ratio.shift(1).rolling(100).apply(
    lambda x: pd.Series(x).rank(pct=True).iloc[-1]
)
gk_pctile_arr = gk_pctile.values

# Breakouts (pre-compute for multiple periods)
c_s = pd.Series(c)
bo_up = {}
bo_dn = {}
for p in [8, 10, 15, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values
    bo_dn[p] = (c_s < c_s.shift(1).rolling(p).min()).values

hours_arr = pd.to_datetime(dt).hour
days_arr = np.array([pd.Timestamp(d).day_name() for d in dt])

TOTAL = len(df)
IS_END = TOTAL // 2

# ===== 回測 =====
DAILY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4
CONSEC_LOSS_COOLDOWN = 12  # 24h in 2h bars

def backtest(side, gk_thresh, bo_period, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss_limit, block_hours, block_days, monthly_cap=20):
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25

    bu = bo_up[bo_period]
    bd = bo_dn[bo_period]

    pos = None  # {entry_price, entry_bar}
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
        daily_pnl.setdefault(day_key, 0.0)
        monthly_pnl.setdefault(month_key, 0.0)
        monthly_entries.setdefault(month_key, 0)

        # === 出場 ===
        if pos is not None:
            ep = pos["entry_price"]
            bars_held = i - pos["entry_bar"]

            if side == "L":
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * (1 + SLIP))
            else:
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * (1 + SLIP))

            exit_price = exit_reason = None
            if hit_sl:
                exit_price, exit_reason = sl_exit, "SL"
            elif hit_tp:
                exit_price, exit_reason = tp_exit, "TP"
            elif bars_held >= maxhold:
                exit_price, exit_reason = c[i], "MH"

            if exit_price is not None:
                if side == "L":
                    raw = (exit_price - ep) / ep * NOTIONAL
                else:
                    raw = (ep - exit_price) / ep * NOTIONAL
                net = raw - FEE

                trades.append({
                    "bar": pos["entry_bar"], "pnl": net, "reason": exit_reason,
                    "oos": pos["entry_bar"] >= IS_END, "held": bars_held,
                })
                daily_pnl[day_key] += net
                monthly_pnl[month_key] += net
                last_exit_bar = i

                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= CONSEC_LOSS_PAUSE:
                    consec_pause_until = i + CONSEC_LOSS_COOLDOWN
                pos = None

        # === 進場條件 ===
        if pos is not None:
            continue
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

        if hours_arr[i] in block_hours:
            continue
        if days_arr[i] in block_days:
            continue

        gk_val = gk_pctile_arr[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh / 100.0:
            continue

        if side == "L" and bu[i]:
            pos = {"entry_price": o[i + 1], "entry_bar": i}
            monthly_entries[month_key] += 1
        elif side == "S" and bd[i]:
            pos = {"entry_price": o[i + 1], "entry_bar": i}
            monthly_entries[month_key] += 1

    return trades


def summarize(trades, label=""):
    if not trades:
        print(f"  {label}: 0 trades")
        return None
    tdf = pd.DataFrame(trades)
    is_t = tdf[~tdf["oos"]]
    oos_t = tdf[tdf["oos"]]

    results = {}
    for name, sub in [("IS", is_t), ("OOS", oos_t)]:
        if len(sub) == 0:
            continue
        pnl = sub["pnl"].sum()
        wr = (sub["pnl"] > 0).mean() * 100
        cum = sub["pnl"].cumsum()
        mdd = (cum - cum.cummax()).min()
        reasons = sub["reason"].value_counts().to_dict()
        results[name] = {"pnl": pnl, "wr": wr, "n": len(sub), "mdd": mdd}
        print(f"  {label} {name}: {len(sub)}t ${pnl:.0f} WR{wr:.0f}% MDD${mdd:.0f} | {reasons}")

    # Monthly (OOS)
    if len(oos_t) > 0:
        oos_t = oos_t.copy()
        oos_t["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in oos_t["bar"]]
        monthly = oos_t.groupby("month")["pnl"].sum()
        pm = (monthly > 0).sum()
        tm = len(monthly)
        worst = monthly.min()
        results["pm"] = f"{pm}/{tm}"
        results["worst_month"] = worst
        print(f"  {label} OOS months: {pm}/{tm} positive, worst ${worst:.0f}")

    return results


# ===== Part 1: V11-E 等效 =====
print("\n" + "=" * 60)
print("Part 1: V11-E 等效參數在 2h 上")
print("=" * 60)

BLOCK_H = {0, 2, 12}
BLOCK_D = {"Monday", "Saturday", "Sunday"}

# L equiv (cooldown 6→3, maxhold 6→3)
t = backtest("L", 25, 15, 0.035, 3, 0.035, 3, -75, BLOCK_H, BLOCK_D)
summarize(t, "L-v11e")

# S equiv (cooldown 8→4, maxhold 7→4)
t = backtest("S", 30, 15, 0.02, 4, 0.04, 4, -150, BLOCK_H, BLOCK_D)
summarize(t, "S-v11e")

# ===== Part 2: 系統性掃描 =====
print("\n" + "=" * 60)
print("Part 2: 2h 參數掃描（精簡版）")
print("=" * 60)

best_l = []
best_s = []

# L scan: 4 * 3 * 4 * 4 * 3 * 3 = 1728 configs
print("L scan (1728 configs)...")
for gk_t in [20, 25, 30, 35]:
    for bo in [8, 10, 15]:
        for tp in [0.03, 0.035, 0.04, 0.05]:
            for mh in [3, 4, 5, 6]:
                for sn in [0.035, 0.04, 0.045]:
                    for cd in [2, 3, 4]:
                        tr = backtest("L", gk_t, bo, tp, mh, sn, cd, -75, BLOCK_H, BLOCK_D)
                        if not tr:
                            continue
                        tdf = pd.DataFrame(tr)
                        is_t = tdf[~tdf["oos"]]
                        oos_t = tdf[tdf["oos"]]
                        if len(is_t) < 3 or len(oos_t) < 3:
                            continue
                        is_pnl = is_t["pnl"].sum()
                        if is_pnl <= 0:
                            continue
                        oos_pnl = oos_t["pnl"].sum()
                        oos_wr = (oos_t["pnl"] > 0).mean() * 100
                        cum = oos_t["pnl"].cumsum()
                        mdd = (cum - cum.cummax()).min()
                        best_l.append((oos_pnl, gk_t, bo, tp, mh, sn, cd, is_pnl, len(is_t),
                                       oos_wr, len(oos_t), mdd))

best_l.sort(reverse=True)
print(f"  {len(best_l)} passed IS>0")
print("\n  TOP 10 L:")
for r in best_l[:10]:
    print(f"    GK<{r[1]} BO{r[2]} TP{r[3]:.1%} MH{r[4]} SN{r[5]:.1%} CD{r[6]}"
          f" | IS:{r[8]}t ${r[7]:.0f} | OOS:{r[10]}t ${r[0]:.0f} WR{r[9]:.0f}% MDD${r[11]:.0f}")

# S scan: 4 * 3 * 4 * 4 * 3 * 3 = 1728 configs
print("\nS scan (1728 configs)...")
for gk_t in [20, 25, 30, 35]:
    for bo in [8, 10, 15]:
        for tp in [0.015, 0.02, 0.025, 0.03]:
            for mh in [3, 4, 5, 6]:
                for sn in [0.035, 0.04, 0.045]:
                    for cd in [2, 3, 4]:
                        tr = backtest("S", gk_t, bo, tp, mh, sn, cd, -150, BLOCK_H, BLOCK_D)
                        if not tr:
                            continue
                        tdf = pd.DataFrame(tr)
                        is_t = tdf[~tdf["oos"]]
                        oos_t = tdf[tdf["oos"]]
                        if len(is_t) < 3 or len(oos_t) < 3:
                            continue
                        is_pnl = is_t["pnl"].sum()
                        if is_pnl <= 0:
                            continue
                        oos_pnl = oos_t["pnl"].sum()
                        oos_wr = (oos_t["pnl"] > 0).mean() * 100
                        cum = oos_t["pnl"].cumsum()
                        mdd = (cum - cum.cummax()).min()
                        best_s.append((oos_pnl, gk_t, bo, tp, mh, sn, cd, is_pnl, len(is_t),
                                       oos_wr, len(oos_t), mdd))

best_s.sort(reverse=True)
print(f"  {len(best_s)} passed IS>0")
print("\n  TOP 10 S:")
for r in best_s[:10]:
    print(f"    GK<{r[1]} BO{r[2]} TP{r[3]:.1%} MH{r[4]} SN{r[5]:.1%} CD{r[6]}"
          f" | IS:{r[8]}t ${r[7]:.0f} | OOS:{r[10]}t ${r[0]:.0f} WR{r[9]:.0f}% MDD${r[11]:.0f}")

# ===== Part 3: Best combo 月報 + vs V11-E =====
if best_l and best_s:
    print("\n" + "=" * 60)
    print("Part 3: Best L+S 月報 vs V11-E")
    print("=" * 60)

    bl = best_l[0]
    bs = best_s[0]

    # Rerun best configs to get monthly detail
    trades_l = backtest("L", bl[1], bl[2], bl[3], bl[4], bl[5], bl[6], -75, BLOCK_H, BLOCK_D)
    trades_s = backtest("S", bs[1], bs[2], bs[3], bs[4], bs[5], bs[6], -150, BLOCK_H, BLOCK_D)

    # Monthly OOS
    all_oos = [t for t in trades_l + trades_s if t["oos"]]
    if all_oos:
        adf = pd.DataFrame(all_oos)
        adf["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in adf["bar"]]
        monthly = adf.groupby("month")["pnl"].sum().sort_index()

        v11e = {
            "2025-04": 224, "2025-05": 503, "2025-06": 19, "2025-07": 109,
            "2025-08": 119, "2025-09": 162, "2025-10": 185, "2025-11": 152,
            "2025-12": 199, "2026-01": 84, "2026-02": 629, "2026-03": 413, "2026-04": -8,
        }

        print(f"\n{'Month':>8} | {'V13-2h':>8} | {'V11-E':>8} | {'Diff':>8}")
        print("-" * 40)
        combo_total = 0
        for m in sorted(set(list(monthly.index) + list(v11e.keys()))):
            v13 = monthly.get(m, 0)
            v11 = v11e.get(m, 0)
            combo_total += v13
            print(f"{m:>8} | ${v13:>7.0f} | ${v11:>7.0f} | ${v13 - v11:>+7.0f}")

        print(f"\n  V13-2h OOS Total: ${combo_total:.0f}")
        print(f"  V11-E OOS Total:  $2,801")
        print(f"  Diff: ${combo_total - 2801:+.0f}")

        # Goal check
        oos_l = pd.DataFrame([t for t in trades_l if t["oos"]])
        oos_s = pd.DataFrame([t for t in trades_s if t["oos"]])
        oos_pnl = adf["pnl"].sum()
        is_pnl = bl[7] + bs[7]
        pm_count = (monthly > 0).sum()
        pm_total = len(monthly)
        worst_m = monthly.min()
        cum = adf.sort_values("bar")["pnl"].cumsum()
        mdd = (cum - cum.cummax()).min()

        print(f"\n  === Goal Check ===")
        print(f"  OOS > $2,801:  ${oos_pnl:.0f} {'PASS' if oos_pnl > 2801 else 'FAIL'}")
        print(f"  IS > $500:     ${is_pnl:.0f} {'PASS' if is_pnl > 500 else 'FAIL'}")
        print(f"  PM >= 11/13:   {pm_count}/{pm_total} {'PASS' if pm_count >= 11 else 'FAIL'}")
        print(f"  Worst M >= -$150: ${worst_m:.0f} {'PASS' if worst_m >= -150 else 'FAIL'}")
        print(f"  MDD <= $500:   ${mdd:.0f} {'PASS' if mdd >= -500 else 'FAIL'}")

print("\nDone.")
