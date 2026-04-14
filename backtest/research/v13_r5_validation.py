"""
V13-R5: Walk-Forward Validation + Decomposition
=================================================
驗證 R4 最佳結果：
  L: GK(5/20)pw100 <25 TP3.5% MH6 + ext=2+BE
  S: GK(10/30)pw100 <35 TP2.0% MH8 + ext=2+BE

1. 6-fold Walk-Forward
2. 8-fold Walk-Forward
3. 效果分解：GK 窗口 vs extension 各自貢獻多少
4. Robustness: 鄰近參數穩定性
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values

TOTAL = len(df)

# GK base
gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk_raw)

# Pre-compute GK pctile arrays
gk_ma = {}
for w in [3, 5, 7, 10, 15, 20, 30]:
    gk_ma[w] = gk_s.rolling(w).mean()

def compute_gk_pctile(fast, slow, pw):
    ratio = gk_ma[fast] / gk_ma[slow]
    pctile = ratio.shift(1).rolling(pw).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1]
    )
    return pctile.values

print("Computing GK pctile arrays...")
gk_p_5_20_100 = compute_gk_pctile(5, 20, 100)
gk_p_10_30_100 = compute_gk_pctile(10, 30, 100)
print("Done.")

# Breakout
c_s = pd.Series(c)
bo_up_15 = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}


def backtest_range(side, gk_pctile_arr, gk_thresh, tp_pct, maxhold, safenet_pct,
                   cooldown, monthly_loss_limit, start_bar, end_bar,
                   ext=0, be_trail=False):
    """Run backtest on a specific bar range."""
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25
    DAILY_LOSS_LIMIT = -200
    CONSEC_LOSS_COOLDOWN = 24

    pos = None
    trades = []
    last_exit_bar = -999
    daily_pnl = {}
    monthly_pnl = {}
    monthly_entries = {}
    consec_losses = 0
    consec_pause_until = -1

    for i in range(max(200, start_bar), min(end_bar, TOTAL - 1)):
        bar_dt = pd.Timestamp(dt[i])
        day_key = bar_dt.strftime("%Y-%m-%d")
        month_key = bar_dt.strftime("%Y-%m")
        daily_pnl.setdefault(day_key, 0.0)
        monthly_pnl.setdefault(month_key, 0.0)
        monthly_entries.setdefault(month_key, 0)

        if pos is not None:
            ep = pos["ep"]
            held = i - pos["bar"]
            in_ext = pos.get("in_ext", False)
            be_p = pos.get("be_p", None)

            if side == "L":
                cur_pnl_pct = (c[i] - ep) / ep
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * (1 + SLIP))
                hit_be = be_p is not None and l[i] <= be_p
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * (1 + SLIP))
                hit_be = be_p is not None and h[i] >= be_p

            ex_p = ex_r = None
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif in_ext:
                ext_held = i - pos["ext_start"]
                if hit_be:
                    ex_p, ex_r = be_p, "BE"
                elif ext_held >= ext:
                    ex_p, ex_r = c[i], "MH-ext"
            elif held >= maxhold:
                if ext > 0 and cur_pnl_pct > 0:
                    pos["in_ext"] = True
                    pos["ext_start"] = i
                    if be_trail:
                        pos["be_p"] = ep
                else:
                    ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                trades.append({"bar": pos["bar"], "pnl": net, "reason": ex_r})
                daily_pnl[day_key] += net
                monthly_pnl[month_key] += net
                last_exit_bar = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
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
        if monthly_entries.get(month_key, 0) >= 20:
            continue
        if hours[i] in BH or days[i] in BD:
            continue

        gk_val = gk_pctile_arr[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh / 100.0:
            continue

        if side == "L" and bo_up_15[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1
        elif side == "S" and bo_dn_15[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1

    return trades


def walk_forward(side, gk_p, gk_thresh, tp, mh, sn, cd, mloss, folds, ext=0, be_trail=False):
    """K-fold walk-forward: train on (K-1) folds, test on 1 fold."""
    fold_size = TOTAL // folds
    results = []

    for test_fold in range(folds):
        test_start = test_fold * fold_size
        test_end = (test_fold + 1) * fold_size if test_fold < folds - 1 else TOTAL

        # Train: all folds except test
        train_trades = []
        for train_fold in range(folds):
            if train_fold == test_fold:
                continue
            ts = train_fold * fold_size
            te = (train_fold + 1) * fold_size if train_fold < folds - 1 else TOTAL
            train_trades.extend(backtest_range(side, gk_p, gk_thresh, tp, mh, sn, cd, mloss,
                                               ts, te, ext, be_trail))

        # Test
        test_trades = backtest_range(side, gk_p, gk_thresh, tp, mh, sn, cd, mloss,
                                     test_start, test_end, ext, be_trail)

        train_pnl = sum(t["pnl"] for t in train_trades) if train_trades else 0
        test_pnl = sum(t["pnl"] for t in test_trades) if test_trades else 0
        test_n = len(test_trades)

        results.append({
            "fold": test_fold + 1,
            "train_pnl": train_pnl,
            "test_pnl": test_pnl,
            "test_n": test_n,
            "pass": train_pnl > 0 and test_pnl > 0,
        })

    return results


# ===== 1. DECOMPOSITION =====
print("=" * 60)
print("1. Effect Decomposition")
print("=" * 60)

IS_END = TOTAL // 2

# S: V11-E params (5/20/100, <30, TP2.0%, MH7, no ext)
t_s_v11e = backtest_range("S", gk_p_5_20_100, 30, 0.02, 7, 0.04, 8, -150, IS_END, TOTAL)
s_v11e_oos = sum(t["pnl"] for t in t_s_v11e)

# S: New GK window only (10/30/100, <35, TP2.0%, MH8, no ext)
t_s_newgk = backtest_range("S", gk_p_10_30_100, 35, 0.02, 8, 0.04, 8, -150, IS_END, TOTAL)
s_newgk_oos = sum(t["pnl"] for t in t_s_newgk)

# S: V11-E + extension only (5/20/100, <30, TP2.0%, MH7, ext=2+BE)
t_s_ext = backtest_range("S", gk_p_5_20_100, 30, 0.02, 7, 0.04, 8, -150, IS_END, TOTAL,
                          ext=2, be_trail=True)
s_ext_oos = sum(t["pnl"] for t in t_s_ext)

# S: Both (10/30/100, <35, TP2.0%, MH8, ext=2+BE)
t_s_both = backtest_range("S", gk_p_10_30_100, 35, 0.02, 8, 0.04, 8, -150, IS_END, TOTAL,
                           ext=2, be_trail=True)
s_both_oos = sum(t["pnl"] for t in t_s_both)

# L decomposition
t_l_v11e = backtest_range("L", gk_p_5_20_100, 25, 0.035, 6, 0.035, 6, -75, IS_END, TOTAL)
l_v11e_oos = sum(t["pnl"] for t in t_l_v11e)

t_l_ext = backtest_range("L", gk_p_5_20_100, 25, 0.035, 6, 0.035, 6, -75, IS_END, TOTAL,
                          ext=2, be_trail=True)
l_ext_oos = sum(t["pnl"] for t in t_l_ext)

print(f"\n--- S Decomposition (OOS) ---")
print(f"  V11-E baseline (5/20,<30,MH7):        ${s_v11e_oos:>7.0f} ({len(t_s_v11e)}t)")
print(f"  + Extension only:                      ${s_ext_oos:>7.0f} ({len(t_s_ext)}t) Δ${s_ext_oos-s_v11e_oos:+.0f}")
print(f"  + New GK window only (10/30,<35,MH8):  ${s_newgk_oos:>7.0f} ({len(t_s_newgk)}t) Δ${s_newgk_oos-s_v11e_oos:+.0f}")
print(f"  + Both (GK+ext):                       ${s_both_oos:>7.0f} ({len(t_s_both)}t) Δ${s_both_oos-s_v11e_oos:+.0f}")
print(f"  GK window contribution:                ${s_newgk_oos - s_v11e_oos:+.0f}")
print(f"  Extension contribution:                ${s_ext_oos - s_v11e_oos:+.0f}")

print(f"\n--- L Decomposition (OOS) ---")
print(f"  V11-E baseline:                        ${l_v11e_oos:>7.0f} ({len(t_l_v11e)}t)")
print(f"  + Extension:                           ${l_ext_oos:>7.0f} ({len(t_l_ext)}t) Δ${l_ext_oos-l_v11e_oos:+.0f}")

print(f"\n--- Total L+S ---")
print(f"  V11-E baseline:          ${l_v11e_oos + s_v11e_oos:.0f}")
print(f"  V13 (both enhancements): ${l_ext_oos + s_both_oos:.0f}")
print(f"  Delta:                   ${(l_ext_oos + s_both_oos) - (l_v11e_oos + s_v11e_oos):+.0f}")

# ===== 2. WALK-FORWARD =====
print("\n" + "=" * 60)
print("2. Walk-Forward Validation")
print("=" * 60)

# V13-L: GK(5/20)pw100 <25 TP3.5% MH6 + ext=2+BE
# V13-S: GK(10/30)pw100 <35 TP2.0% MH8 + ext=2+BE

for folds in [6, 8]:
    print(f"\n--- {folds}-fold WF ---")

    # L
    wf_l = walk_forward("L", gk_p_5_20_100, 25, 0.035, 6, 0.035, 6, -75,
                         folds, ext=2, be_trail=True)
    print(f"\n  L ({folds}-fold):")
    l_pass = 0
    for r in wf_l:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"    Fold {r['fold']}: train ${r['train_pnl']:>7.0f} | test ${r['test_pnl']:>7.0f} ({r['test_n']}t) {status}")
        if r["pass"]:
            l_pass += 1
    print(f"  L WF: {l_pass}/{folds}")

    # S
    wf_s = walk_forward("S", gk_p_10_30_100, 35, 0.02, 8, 0.04, 8, -150,
                         folds, ext=2, be_trail=True)
    print(f"\n  S ({folds}-fold):")
    s_pass = 0
    for r in wf_s:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"    Fold {r['fold']}: train ${r['train_pnl']:>7.0f} | test ${r['test_pnl']:>7.0f} ({r['test_n']}t) {status}")
        if r["pass"]:
            s_pass += 1
    print(f"  S WF: {s_pass}/{folds}")

# Also run WF on V11-E baseline for comparison
print("\n--- V11-E Baseline WF (6-fold) ---")
wf_l_base = walk_forward("L", gk_p_5_20_100, 25, 0.035, 6, 0.035, 6, -75, 6)
l_base_pass = sum(1 for r in wf_l_base if r["pass"])
print(f"  L baseline WF: {l_base_pass}/6")
for r in wf_l_base:
    status = "PASS" if r["pass"] else "FAIL"
    print(f"    Fold {r['fold']}: train ${r['train_pnl']:>7.0f} | test ${r['test_pnl']:>7.0f} ({r['test_n']}t) {status}")

wf_s_base = walk_forward("S", gk_p_5_20_100, 30, 0.02, 7, 0.04, 8, -150, 6)
s_base_pass = sum(1 for r in wf_s_base if r["pass"])
print(f"\n  S baseline WF: {s_base_pass}/6")
for r in wf_s_base:
    status = "PASS" if r["pass"] else "FAIL"
    print(f"    Fold {r['fold']}: train ${r['train_pnl']:>7.0f} | test ${r['test_pnl']:>7.0f} ({r['test_n']}t) {status}")

# ===== 3. NEIGHBOR ROBUSTNESS =====
print("\n" + "=" * 60)
print("3. Neighbor Robustness (S strategy)")
print("=" * 60)

# Check nearby parameter combinations for S
print("\n--- S: Nearby GK thresholds ---")
for gk_t in [30, 33, 35, 37, 40]:
    t = backtest_range("S", gk_p_10_30_100, gk_t, 0.02, 8, 0.04, 8, -150, 0, TOTAL,
                       ext=2, be_trail=True)
    if not t:
        print(f"  GK<{gk_t}: 0 trades")
        continue
    tdf = pd.DataFrame(t)
    is_end = TOTAL // 2
    is_t = [x for x in t if x["bar"] < is_end]
    oos_t = [x for x in t if x["bar"] >= is_end]
    is_pnl = sum(x["pnl"] for x in is_t)
    oos_pnl = sum(x["pnl"] for x in oos_t)
    print(f"  GK<{gk_t}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f}")

print("\n--- S: Nearby TP ---")
for tp in [0.015, 0.02, 0.025, 0.03, 0.035]:
    t = backtest_range("S", gk_p_10_30_100, 35, tp, 8, 0.04, 8, -150, 0, TOTAL,
                       ext=2, be_trail=True)
    if not t:
        continue
    is_t = [x for x in t if x["bar"] < TOTAL // 2]
    oos_t = [x for x in t if x["bar"] >= TOTAL // 2]
    is_pnl = sum(x["pnl"] for x in is_t)
    oos_pnl = sum(x["pnl"] for x in oos_t)
    print(f"  TP{tp:.1%}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f}")

print("\n--- S: Nearby MH ---")
for mh in [5, 6, 7, 8, 9, 10]:
    t = backtest_range("S", gk_p_10_30_100, 35, 0.02, mh, 0.04, 8, -150, 0, TOTAL,
                       ext=2, be_trail=True)
    if not t:
        continue
    is_t = [x for x in t if x["bar"] < TOTAL // 2]
    oos_t = [x for x in t if x["bar"] >= TOTAL // 2]
    is_pnl = sum(x["pnl"] for x in is_t)
    oos_pnl = sum(x["pnl"] for x in oos_t)
    print(f"  MH{mh}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f}")

print("\n--- S: Nearby SafeNet ---")
for sn in [0.03, 0.035, 0.04, 0.045, 0.05]:
    t = backtest_range("S", gk_p_10_30_100, 35, 0.02, 8, sn, 8, -150, 0, TOTAL,
                       ext=2, be_trail=True)
    if not t:
        continue
    is_t = [x for x in t if x["bar"] < TOTAL // 2]
    oos_t = [x for x in t if x["bar"] >= TOTAL // 2]
    is_pnl = sum(x["pnl"] for x in is_t)
    oos_pnl = sum(x["pnl"] for x in oos_t)
    print(f"  SN{sn:.1%}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f}")

# ===== 4. FINAL SUMMARY =====
print("\n" + "=" * 60)
print("4. FINAL SUMMARY")
print("=" * 60)

# Full run with best params
t_l_full = backtest_range("L", gk_p_5_20_100, 25, 0.035, 6, 0.035, 6, -75, 0, TOTAL,
                           ext=2, be_trail=True)
t_s_full = backtest_range("S", gk_p_10_30_100, 35, 0.02, 8, 0.04, 8, -150, 0, TOTAL,
                           ext=2, be_trail=True)

all_trades = t_l_full + t_s_full
is_trades = [t for t in all_trades if t["bar"] < TOTAL // 2]
oos_trades = [t for t in all_trades if t["bar"] >= TOTAL // 2]

is_pnl = sum(t["pnl"] for t in is_trades)
oos_pnl = sum(t["pnl"] for t in oos_trades)

# Monthly OOS
adf = pd.DataFrame(oos_trades)
adf["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in adf["bar"]]
monthly = adf.groupby("month")["pnl"].sum()
pm = (monthly > 0).sum()
tm = len(monthly)
worst_m = monthly.min()

# MDD
adf_sorted = adf.sort_values("bar")
cum = adf_sorted["pnl"].cumsum()
mdd = (cum - cum.cummax()).min()

# Worst day
adf["day"] = [pd.Timestamp(dt[b]).strftime("%Y-%m-%d") for b in adf["bar"]]
daily = adf.groupby("day")["pnl"].sum()
worst_day = daily.min()

# WR
oos_wr = (adf["pnl"] > 0).mean() * 100

print(f"\n  V13 Candidate: L GK(5/20)<25 TP3.5% MH6 + S GK(10/30)<35 TP2.0% MH8")
print(f"  Both with ext=2 + BE trail")
print(f"\n  IS:  {len(is_trades)}t ${is_pnl:.0f}")
print(f"  OOS: {len(oos_trades)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")
print(f"  PM:  {pm}/{tm}")
print(f"  Worst month: ${worst_m:.0f}")
print(f"  MDD: ${mdd:.0f}")
print(f"  Worst day: ${worst_day:.0f}")

print(f"\n  === vs V11-E ===")
print(f"  {'Metric':>15} | {'V11-E':>8} | {'V13':>8} | {'Target':>8} | {'Pass':>5}")
print(f"  {'-'*55}")
print(f"  {'OOS PnL':>15} | ${'2,801':>7} | ${oos_pnl:>7.0f} | ${'2,801':>7} | {'Y' if oos_pnl > 2801 else 'N':>5}")
print(f"  {'IS PnL':>15} | ${'1,034':>7} | ${is_pnl:>7.0f} | ${'500':>7} | {'Y' if is_pnl > 500 else 'N':>5}")
print(f"  {'PM':>15} | {'12/13':>8} | {pm}/{tm:>5} | {'11/13':>8} | {'Y' if pm >= 11 else 'N':>5}")
print(f"  {'Worst Month':>15} | ${'-8':>7} | ${worst_m:>7.0f} | ${'-150':>7} | {'Y' if worst_m >= -150 else 'N':>5}")
print(f"  {'MDD':>15} | ${'186':>7} | ${abs(mdd):>7.0f} | ${'500':>7} | {'Y' if mdd >= -500 else 'N':>5}")
print(f"  {'Worst Day':>15} | ${'-179':>7} | ${worst_day:>7.0f} | ${'-250':>7} | {'Y' if worst_day >= -250 else 'N':>5}")

print("\n防前瞻自檢：")
print("  [x] 1. 所有指標 .shift(1)")
print("  [x] 2. 百分位 .shift(1).rolling(N)")
print("  [x] 3. 進場價 = O[i+1]")
print("  [x] 4. IS/OOS 固定分割（前 50% / 後 50%）")
print("  [x] 5. 無更高時框 resample 前瞻")
print("  [x] 6. 出場用當前 bar OHLC")

print("\nDone.")
