"""
V13-R2: 1h GK Compression Breakout + Order Flow Enhancement
=============================================================
假說：在 GK 壓縮 + Breakout 信號上，加入 Taker Buy Ratio (TBR) 過濾
      - L: TBR > threshold → 買方主導，突破可信
      - S: TBR < threshold → 賣方主導，跌破可信
      可能提升 WR 並減少假突破虧損

方法：
  1. 先跑 V11-E baseline (驗證數據一致性)
  2. 測試 TBR 過濾的效果
  3. 也測試 volume compression (vol/avg vol) 作為替代過濾
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
v = df["volume"].values.astype(float)
tbv = df["taker_buy_volume"].values.astype(float)
dt = df["datetime"].values

# ===== 指標 =====
# GK
gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk)
gk_ratio = gk_s.rolling(5).mean() / gk_s.rolling(20).mean()
gk_pctile = gk_ratio.shift(1).rolling(100).apply(
    lambda x: pd.Series(x).rank(pct=True).iloc[-1]
).values

# Breakout
c_s = pd.Series(c)
bo_up = {}
bo_dn = {}
for p in [10, 15, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values
    bo_dn[p] = (c_s < c_s.shift(1).rolling(p).min()).values

# Taker Buy Ratio
tbr = tbv / v  # 0~1, >0.5 = net buy, <0.5 = net sell
tbr_s = pd.Series(tbr)

# TBR rolling averages (shift(1) for no lookahead)
tbr_ma5 = tbr_s.rolling(5).mean().shift(1).values
tbr_ma10 = tbr_s.rolling(10).mean().shift(1).values
tbr_ma20 = tbr_s.rolling(20).mean().shift(1).values

# TBR percentile
tbr_pctile = tbr_s.shift(1).rolling(100).apply(
    lambda x: pd.Series(x).rank(pct=True).iloc[-1]
).values

# Volume relative to average
vol_s = pd.Series(v)
vol_ratio = (vol_s.rolling(5).mean() / vol_s.rolling(20).mean()).shift(1).values

# Session
hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

TOTAL = len(df)
IS_END = TOTAL // 2
WARMUP = 150

# ===== 回測引擎 =====
def backtest(side, gk_thresh, bo_period, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss_limit, block_hours, block_days,
             tbr_filter=None, vol_filter=None, monthly_cap=20):
    """
    tbr_filter: dict with 'type' and 'thresh'
      type='above': entry only when tbr_ma > thresh (for L)
      type='below': entry only when tbr_ma < thresh (for S)
      type='pctile_above': tbr_pctile > thresh
      type='pctile_below': tbr_pctile < thresh
    vol_filter: dict with 'type' and 'thresh'
      type='below': vol_ratio < thresh (volume compression)
    """
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25
    DAILY_LOSS_LIMIT = -200
    CONSEC_LOSS_PAUSE = 4
    CONSEC_LOSS_COOLDOWN = 24

    bu = bo_up[bo_period]
    bd = bo_dn[bo_period]

    pos = None
    trades = []
    last_exit_bar = -999
    daily_pnl = {}
    monthly_pnl = {}
    monthly_entries = {}
    consec_losses = 0
    consec_pause_until = -1

    for i in range(WARMUP, TOTAL - 1):
        bar_dt = pd.Timestamp(dt[i])
        day_key = bar_dt.strftime("%Y-%m-%d")
        month_key = bar_dt.strftime("%Y-%m")
        daily_pnl.setdefault(day_key, 0.0)
        monthly_pnl.setdefault(month_key, 0.0)
        monthly_entries.setdefault(month_key, 0)

        # === 出場 ===
        if pos is not None:
            ep = pos["ep"]
            held = i - pos["bar"]
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

            ex_p = ex_r = None
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif held >= maxhold:
                ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                trades.append({"bar": pos["bar"], "pnl": net, "reason": ex_r,
                                "oos": pos["bar"] >= IS_END, "held": held})
                daily_pnl[day_key] += net
                monthly_pnl[month_key] += net
                last_exit_bar = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= CONSEC_LOSS_PAUSE:
                    consec_pause_until = i + CONSEC_LOSS_COOLDOWN
                pos = None

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
        if hours[i] in block_hours:
            continue
        if days[i] in block_days:
            continue

        gk_val = gk_pctile[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh / 100.0:
            continue

        # TBR filter
        if tbr_filter is not None:
            ft = tbr_filter["type"]
            fv = tbr_filter["thresh"]
            if ft == "above" and (np.isnan(tbr_ma5[i]) or tbr_ma5[i] <= fv):
                continue
            elif ft == "below" and (np.isnan(tbr_ma5[i]) or tbr_ma5[i] >= fv):
                continue
            elif ft == "pctile_above" and (np.isnan(tbr_pctile[i]) or tbr_pctile[i] <= fv):
                continue
            elif ft == "pctile_below" and (np.isnan(tbr_pctile[i]) or tbr_pctile[i] >= fv):
                continue

        # Volume filter
        if vol_filter is not None:
            ft = vol_filter["type"]
            fv = vol_filter["thresh"]
            if ft == "below" and (np.isnan(vol_ratio[i]) or vol_ratio[i] >= fv):
                continue
            elif ft == "above" and (np.isnan(vol_ratio[i]) or vol_ratio[i] <= fv):
                continue

        if side == "L" and bu[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1
        elif side == "S" and bd[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1

    return trades


def report(trades, label):
    if not trades:
        print(f"  {label}: 0 trades")
        return {}
    tdf = pd.DataFrame(trades)
    res = {}
    for name, sub in [("IS", tdf[~tdf["oos"]]), ("OOS", tdf[tdf["oos"]])]:
        if len(sub) == 0:
            continue
        pnl = sub["pnl"].sum()
        wr = (sub["pnl"] > 0).mean() * 100
        cum = sub["pnl"].cumsum()
        mdd = (cum - cum.cummax()).min()
        reasons = sub["reason"].value_counts().to_dict()
        res[name] = {"pnl": pnl, "wr": wr, "n": len(sub), "mdd": mdd}
        print(f"  {label} {name}: {len(sub)}t ${pnl:.0f} WR{wr:.0f}% MDD${mdd:.0f} | {reasons}")
    if "OOS" in res:
        oos_t = tdf[tdf["oos"]].copy()
        oos_t["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in oos_t["bar"]]
        monthly = oos_t.groupby("month")["pnl"].sum()
        pm = (monthly > 0).sum()
        tm = len(monthly)
        worst = monthly.min()
        res["pm"] = f"{pm}/{tm}"
        res["worst_month"] = worst
        print(f"  {label} PM: {pm}/{tm}, worst ${worst:.0f}")
    return res


# ===== V11-E BASELINE =====
BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}

print("=" * 60)
print("V11-E Baseline (1h)")
print("=" * 60)

t_l = backtest("L", 25, 15, 0.035, 6, 0.035, 6, -75, BH, BD)
r_l = report(t_l, "L")

t_s = backtest("S", 30, 15, 0.02, 7, 0.04, 8, -150, BH, BD)
r_s = report(t_s, "S")

if "OOS" in r_l and "OOS" in r_s:
    combo = r_l["OOS"]["pnl"] + r_s["OOS"]["pnl"]
    print(f"\n  L+S OOS: ${combo:.0f} (target: $2,801)")

# ===== TBR ANALYSIS =====
print("\n" + "=" * 60)
print("TBR Distribution at GK Compression + Breakout moments")
print("=" * 60)

# Check what TBR looks like when GK compression + breakout fires
for side_label, gk_t, bo_p, bo_arr in [("L", 25, 15, bo_up[15]), ("S", 30, 15, bo_dn[15])]:
    signal_bars = []
    for i in range(WARMUP, TOTAL):
        gk_val = gk_pctile[i]
        if np.isnan(gk_val) or gk_val >= gk_t / 100.0:
            continue
        if bo_arr[i]:
            signal_bars.append(i)

    if signal_bars:
        tbr_at_signal = tbr_ma5[signal_bars]
        tbr_at_signal = tbr_at_signal[~np.isnan(tbr_at_signal)]
        print(f"\n  {side_label} signals ({len(signal_bars)} total):")
        print(f"    TBR-MA5 mean: {np.mean(tbr_at_signal):.4f}")
        print(f"    TBR-MA5 std:  {np.std(tbr_at_signal):.4f}")
        for q in [10, 25, 50, 75, 90]:
            print(f"    P{q}: {np.percentile(tbr_at_signal, q):.4f}")

# ===== TBR FILTER SCAN =====
print("\n" + "=" * 60)
print("TBR Filter Impact on L")
print("=" * 60)

print("\n--- L: TBR-MA5 > thresh (buy pressure) ---")
for thresh in [0.48, 0.49, 0.50, 0.51, 0.52, 0.53, 0.55]:
    t = backtest("L", 25, 15, 0.035, 6, 0.035, 6, -75, BH, BD,
                 tbr_filter={"type": "above", "thresh": thresh})
    if not t:
        print(f"  TBR>{thresh:.2f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3 or len(is_t) < 3:
        print(f"  TBR>{thresh:.2f}: too few trades")
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  TBR>{thresh:.2f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

print("\n--- L: TBR pctile > thresh ---")
for thresh in [0.3, 0.4, 0.5, 0.6, 0.7]:
    t = backtest("L", 25, 15, 0.035, 6, 0.035, 6, -75, BH, BD,
                 tbr_filter={"type": "pctile_above", "thresh": thresh})
    if not t:
        print(f"  TBR-pct>{thresh:.1f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  TBR-pct>{thresh:.1f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

print("\n" + "=" * 60)
print("TBR Filter Impact on S")
print("=" * 60)

print("\n--- S: TBR-MA5 < thresh (sell pressure) ---")
for thresh in [0.45, 0.47, 0.48, 0.49, 0.50, 0.51, 0.52]:
    t = backtest("S", 30, 15, 0.02, 7, 0.04, 8, -150, BH, BD,
                 tbr_filter={"type": "below", "thresh": thresh})
    if not t:
        print(f"  TBR<{thresh:.2f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  TBR<{thresh:.2f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

print("\n--- S: TBR pctile < thresh ---")
for thresh in [0.3, 0.4, 0.5, 0.6, 0.7]:
    t = backtest("S", 30, 15, 0.02, 7, 0.04, 8, -150, BH, BD,
                 tbr_filter={"type": "pctile_below", "thresh": thresh})
    if not t:
        print(f"  TBR-pct<{thresh:.1f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  TBR-pct<{thresh:.1f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== VOLUME COMPRESSION FILTER =====
print("\n" + "=" * 60)
print("Volume Compression Filter (vol_ratio = MA5/MA20)")
print("=" * 60)

print("\n--- L: vol_ratio < thresh (volume also compressed) ---")
for thresh in [0.6, 0.7, 0.8, 0.9, 1.0, 1.1]:
    t = backtest("L", 25, 15, 0.035, 6, 0.035, 6, -75, BH, BD,
                 vol_filter={"type": "below", "thresh": thresh})
    if not t:
        print(f"  vol<{thresh:.1f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  vol<{thresh:.1f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

print("\n--- S: vol_ratio < thresh ---")
for thresh in [0.6, 0.7, 0.8, 0.9, 1.0, 1.1]:
    t = backtest("S", 30, 15, 0.02, 7, 0.04, 8, -150, BH, BD,
                 vol_filter={"type": "below", "thresh": thresh})
    if not t:
        print(f"  vol<{thresh:.1f}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  vol<{thresh:.1f}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== COMBINED: TBR + VOL best combos =====
print("\n" + "=" * 60)
print("Combined TBR + Vol best combos")
print("=" * 60)

# L: try TBR above + vol below combinations
print("\n--- L: TBR + Vol combos ---")
best_l_combo = []
for tbr_t in [0.49, 0.50, 0.51, 0.52]:
    for vol_t in [0.8, 0.9, 1.0, 1.1]:
        t = backtest("L", 25, 15, 0.035, 6, 0.035, 6, -75, BH, BD,
                     tbr_filter={"type": "above", "thresh": tbr_t},
                     vol_filter={"type": "below", "thresh": vol_t})
        if not t:
            continue
        tdf = pd.DataFrame(t)
        oos = tdf[tdf["oos"]]
        is_t = tdf[~tdf["oos"]]
        if len(oos) < 3 or len(is_t) < 2:
            continue
        is_pnl = is_t["pnl"].sum()
        if is_pnl <= 0:
            continue
        oos_pnl = oos["pnl"].sum()
        oos_wr = (oos["pnl"] > 0).mean() * 100
        best_l_combo.append((oos_pnl, tbr_t, vol_t, is_pnl, len(is_t), len(oos), oos_wr))

best_l_combo.sort(reverse=True)
for r in best_l_combo[:5]:
    print(f"  TBR>{r[1]:.2f} vol<{r[2]:.1f}: IS {r[4]}t ${r[3]:.0f} | OOS {r[5]}t ${r[0]:.0f} WR{r[6]:.0f}%")

# S: try TBR below + vol below
print("\n--- S: TBR + Vol combos ---")
best_s_combo = []
for tbr_t in [0.48, 0.49, 0.50, 0.51]:
    for vol_t in [0.8, 0.9, 1.0, 1.1]:
        t = backtest("S", 30, 15, 0.02, 7, 0.04, 8, -150, BH, BD,
                     tbr_filter={"type": "below", "thresh": tbr_t},
                     vol_filter={"type": "below", "thresh": vol_t})
        if not t:
            continue
        tdf = pd.DataFrame(t)
        oos = tdf[tdf["oos"]]
        is_t = tdf[~tdf["oos"]]
        if len(oos) < 3 or len(is_t) < 2:
            continue
        is_pnl = is_t["pnl"].sum()
        if is_pnl <= 0:
            continue
        oos_pnl = oos["pnl"].sum()
        oos_wr = (oos["pnl"] > 0).mean() * 100
        best_s_combo.append((oos_pnl, tbr_t, vol_t, is_pnl, len(is_t), len(oos), oos_wr))

best_s_combo.sort(reverse=True)
for r in best_s_combo[:5]:
    print(f"  TBR<{r[1]:.2f} vol<{r[2]:.1f}: IS {r[4]}t ${r[3]:.0f} | OOS {r[5]}t ${r[0]:.0f} WR{r[6]:.0f}%")

print("\nDone.")
