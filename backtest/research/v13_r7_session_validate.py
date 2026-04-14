"""
V13-R7: Validate "Remove Monday" for L + Full Combined Report
==============================================================
R6 發現：L 策略去掉 Monday block → IS +$439, OOS +$157
本輪：WF 驗證 + 月報 + 完整目標檢查
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
IS_END = TOTAL // 2

day_keys = np.array([str(pd.Timestamp(d).strftime("%Y-%m-%d")) for d in dt])
month_keys = np.array([str(pd.Timestamp(d).strftime("%Y-%m")) for d in dt])
hours_arr = pd.to_datetime(dt).hour.values
days_arr = np.array([pd.Timestamp(d).day_name() for d in dt])

gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2

def rolling_mean(arr, w):
    cs = np.cumsum(np.insert(arr, 0, 0))
    rm = (cs[w:] - cs[:-w]) / w
    result = np.full(len(arr), np.nan)
    result[w-1:] = rm
    return result

def fast_rolling_pctile(arr, window):
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        w = arr[i - window + 1:i + 1]
        valid_mask = ~np.isnan(w)
        if valid_mask.sum() < window // 2:
            continue
        valid = w[valid_mask]
        result[i] = np.sum(valid <= valid[-1]) / len(valid)
    return result

def compute_gk_pctile(fast_w, slow_w, pctile_w):
    gk_ma_fast = rolling_mean(gk_raw, fast_w)
    gk_ma_slow = rolling_mean(gk_raw, slow_w)
    ratio = gk_ma_fast / gk_ma_slow
    ratio_shifted = np.full(len(ratio), np.nan)
    ratio_shifted[1:] = ratio[:-1]
    return fast_rolling_pctile(ratio_shifted, pctile_w)

print("Computing GK pctiles...", flush=True)
gk_p_5_20 = compute_gk_pctile(5, 20, 100)
gk_p_10_30 = compute_gk_pctile(10, 30, 100)
print("Done.", flush=True)

c_s = pd.Series(c)
bo_up_15 = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values


def backtest(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss, block_hours, block_days,
             start_bar=200, end_bar=None, ext=2):
    if end_bar is None:
        end_bar = TOTAL
    NOTIONAL = 4000; SLIP = 1.25
    bo_arr = bo_up_15 if side == "L" else bo_dn_15

    in_pos = False; ep = 0.0; entry_bar = 0
    in_ext = False; ext_start = 0; be_p = 0.0
    trades = []
    last_exit = -999
    cur_day = ""; cur_month = ""
    day_pnl = 0.0; month_pnl = 0.0; month_entries = 0
    consec_losses = 0; consec_pause = -1

    for i in range(max(200, start_bar), min(end_bar, TOTAL - 1)):
        dk = day_keys[i]; mk = month_keys[i]
        if dk != cur_day:
            cur_day = dk; day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk; month_pnl = 0.0; month_entries = 0

        if in_pos:
            held = i - entry_bar
            if side == "L":
                pnl_pct = (c[i] - ep) / ep
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * SLIP)
                hit_be = in_ext and l[i] <= be_p
            else:
                pnl_pct = (ep - c[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * SLIP)
                hit_be = in_ext and h[i] >= be_p

            ex_p = None; ex_r = None
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif in_ext:
                if hit_be:
                    ex_p, ex_r = be_p, "BE"
                elif i - ext_start >= ext:
                    ex_p, ex_r = c[i], "MH-ext"
            elif held >= maxhold:
                if pnl_pct > 0 and ext > 0:
                    in_ext = True; ext_start = i; be_p = ep
                else:
                    ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                if side == "L":
                    net = (ex_p - ep) / ep * NOTIONAL - 4.0
                else:
                    net = (ep - ex_p) / ep * NOTIONAL - 4.0
                trades.append({"bar": entry_bar, "pnl": net, "reason": ex_r,
                               "month": month_keys[entry_bar]})
                day_pnl += net; month_pnl += net
                last_exit = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
                    consec_pause = i + 24
                in_pos = False; in_ext = False

        if in_pos: continue
        if i - last_exit < cooldown: continue
        if i < consec_pause: continue
        if day_pnl <= -200: continue
        if month_pnl <= monthly_loss: continue
        if month_entries >= 20: continue
        if hours_arr[i] in block_hours or days_arr[i] in block_days: continue

        gk_val = gk_p[i]
        if gk_val != gk_val or gk_val >= gk_thresh / 100.0: continue

        if bo_arr[i]:
            ep = o[i + 1]; entry_bar = i
            in_pos = True; in_ext = False
            month_entries += 1

    return trades


BH = {0, 1, 2, 12}
BD_V11E = {"Monday", "Saturday", "Sunday"}
BD_NEW = {"Saturday", "Sunday"}  # Remove Monday

# ===== 1. Side-by-side comparison =====
print("\n" + "=" * 60, flush=True)
print("1. L: Monday blocked vs unblocked", flush=True)
print("=" * 60, flush=True)

for label, bd in [("V11-E (Mon blocked)", BD_V11E), ("NEW (Mon allowed)", BD_NEW)]:
    trades = backtest("L", gk_p_5_20, 25, 0.035, 6, 0.035, 6, -75, BH, bd)
    tdf = pd.DataFrame(trades)
    is_t = tdf[tdf["bar"] < IS_END]
    oos_t = tdf[tdf["bar"] >= IS_END]
    print(f"\n  {label}:", flush=True)
    for name, sub in [("IS", is_t), ("OOS", oos_t)]:
        pnl = sub["pnl"].sum()
        wr = (sub["pnl"] > 0).mean() * 100 if len(sub) > 0 else 0
        reasons = sub["reason"].value_counts().to_dict() if len(sub) > 0 else {}
        print(f"    {name}: {len(sub)}t ${pnl:.0f} WR{wr:.0f}% | {reasons}", flush=True)

# ===== 2. Walk-Forward =====
print("\n" + "=" * 60, flush=True)
print("2. Walk-Forward (6-fold + 8-fold)", flush=True)
print("=" * 60, flush=True)

def walk_forward(side, gk_p, gk_t, tp, mh, sn, cd, ml, bh, bd, folds):
    fold_size = TOTAL // folds
    results = []
    for test_fold in range(folds):
        test_start = test_fold * fold_size
        test_end = (test_fold + 1) * fold_size if test_fold < folds - 1 else TOTAL

        train_pnl = 0
        for train_fold in range(folds):
            if train_fold == test_fold: continue
            ts = train_fold * fold_size
            te = (train_fold + 1) * fold_size if train_fold < folds - 1 else TOTAL
            tr = backtest(side, gk_p, gk_t, tp, mh, sn, cd, ml, bh, bd, ts, te)
            train_pnl += sum(t["pnl"] for t in tr)

        test_tr = backtest(side, gk_p, gk_t, tp, mh, sn, cd, ml, bh, bd, test_start, test_end)
        test_pnl = sum(t["pnl"] for t in test_tr)
        results.append({"fold": test_fold + 1, "train": train_pnl, "test": test_pnl,
                        "n": len(test_tr), "pass": train_pnl > 0 and test_pnl > 0})
    return results

for folds in [6, 8]:
    print(f"\n  --- {folds}-fold ---", flush=True)

    # L new (Mon allowed)
    wf = walk_forward("L", gk_p_5_20, 25, 0.035, 6, 0.035, 6, -75, BH, BD_NEW, folds)
    passes = sum(1 for r in wf if r["pass"])
    print(f"  L (Mon allowed): {passes}/{folds}", flush=True)
    for r in wf:
        s = "PASS" if r["pass"] else "FAIL"
        print(f"    Fold {r['fold']}: train ${r['train']:>7.0f} | test ${r['test']:>7.0f} ({r['n']}t) {s}", flush=True)

    # S (unchanged)
    wf = walk_forward("S", gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, BH, BD_V11E, folds)
    passes = sum(1 for r in wf if r["pass"])
    print(f"  S (unchanged): {passes}/{folds}", flush=True)
    for r in wf:
        s = "PASS" if r["pass"] else "FAIL"
        print(f"    Fold {r['fold']}: train ${r['train']:>7.0f} | test ${r['test']:>7.0f} ({r['n']}t) {s}", flush=True)

# ===== 3. Monthly Report =====
print("\n" + "=" * 60, flush=True)
print("3. Full Monthly Report (OOS)", flush=True)
print("=" * 60, flush=True)

trades_l = backtest("L", gk_p_5_20, 25, 0.035, 6, 0.035, 6, -75, BH, BD_NEW)
trades_s = backtest("S", gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, BH, BD_V11E)

l_oos = [t for t in trades_l if t["bar"] >= IS_END]
s_oos = [t for t in trades_s if t["bar"] >= IS_END]
l_is = [t for t in trades_l if t["bar"] < IS_END]
s_is = [t for t in trades_s if t["bar"] < IS_END]

l_monthly = {}
for t in l_oos:
    l_monthly[t["month"]] = l_monthly.get(t["month"], 0) + t["pnl"]
s_monthly = {}
for t in s_oos:
    s_monthly[t["month"]] = s_monthly.get(t["month"], 0) + t["pnl"]

v11e = {
    "2025-04": 224, "2025-05": 503, "2025-06": 19, "2025-07": 109,
    "2025-08": 119, "2025-09": 162, "2025-10": 185, "2025-11": 152,
    "2025-12": 199, "2026-01": 84, "2026-02": 629, "2026-03": 413, "2026-04": -8,
}

months = sorted(set(list(l_monthly.keys()) + list(s_monthly.keys()) + list(v11e.keys())))
print(f"\n{'Month':>8} | {'V13-L':>7} | {'V13-S':>7} | {'V13':>7} | {'V11-E':>7} | {'Diff':>7}", flush=True)
print("-" * 55, flush=True)

v13_total = 0
for m in months:
    ml = l_monthly.get(m, 0)
    ms = s_monthly.get(m, 0)
    mt = ml + ms
    v11 = v11e.get(m, 0)
    v13_total += mt
    print(f"{m:>8} | ${ml:>6.0f} | ${ms:>6.0f} | ${mt:>6.0f} | ${v11:>6} | ${mt-v11:>+6.0f}", flush=True)

print(f"{'TOTAL':>8} | {'':>8} | {'':>8} | ${v13_total:>6.0f} | ${'2801':>5} | ${v13_total-2801:>+6.0f}", flush=True)

# Stats
all_oos = l_oos + s_oos
oos_pnls = [t["pnl"] for t in all_oos]
is_pnl = sum(t["pnl"] for t in l_is + s_is)
oos_pnl = sum(oos_pnls)
pm = sum(1 for m in months if l_monthly.get(m, 0) + s_monthly.get(m, 0) > 0)
worst_m = min(l_monthly.get(m, 0) + s_monthly.get(m, 0) for m in months)
cum = np.cumsum(sorted([t["pnl"] for t in all_oos], key=lambda x: [t["bar"] for t in all_oos][[t["pnl"] for t in all_oos].index(x)]))
# Better MDD calc
sorted_oos = sorted(all_oos, key=lambda t: t["bar"])
cum = np.cumsum([t["pnl"] for t in sorted_oos])
mdd = float(np.min(cum - np.maximum.accumulate(cum)))

# Daily
daily = {}
for t in all_oos:
    d = day_keys[t["bar"]]
    daily[d] = daily.get(d, 0) + t["pnl"]
worst_day = min(daily.values()) if daily else 0

oos_wr = sum(1 for t in all_oos if t["pnl"] > 0) / len(all_oos) * 100

print(f"\n=== Goal Check ===", flush=True)
print(f"  OOS > $2,801:     ${oos_pnl:.0f} {'PASS' if oos_pnl > 2801 else 'FAIL'}", flush=True)
print(f"  IS > $500:        ${is_pnl:.0f} {'PASS' if is_pnl > 500 else 'FAIL'}", flush=True)
print(f"  PM >= 11/13:      {pm}/{len(months)} {'PASS' if pm >= 11 else 'FAIL'}", flush=True)
print(f"  Worst M >= -$150: ${worst_m:.0f} {'PASS' if worst_m >= -150 else 'FAIL'}", flush=True)
print(f"  MDD <= $500:      ${mdd:.0f} {'PASS' if mdd >= -500 else 'FAIL'}", flush=True)
print(f"  Worst Day >= -$250: ${worst_day:.0f} {'PASS' if worst_day >= -250 else 'FAIL'}", flush=True)
print(f"  OOS WR:           {oos_wr:.1f}%", flush=True)
print(f"  L+S trades:       {len(all_oos)}", flush=True)

print("\nDone.", flush=True)
