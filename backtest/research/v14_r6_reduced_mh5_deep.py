"""
V14-R6: reduced_mh=5 Deep Exploration
======================================
R5 穩健性測試發現 reduced_mh=5 (OOS $2,022, +$281) 比 champion reduced_mh=4 (+$216) 更好。
本輪深入探索 reduced_mh=5 的最佳搭配，並做完整驗證。

掃描：
  1. reduced_mh=5 搭配不同 MFE trail 參數
  2. reduced_mh=5 搭配不同 exit_thresh（bar2 觸發門檻）
  3. reduced_mh=5 搭配不同 check_bar（bar 2 vs 3）
  4. 最佳配置 vs champion vs baseline 完整對比
  5. 8-fold WF + 月度 + 穩健性
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

gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2

def rolling_mean(arr, w):
    return pd.Series(arr).rolling(w).mean().values

def fast_rolling_pctile(arr, window):
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        w = arr[i - window + 1:i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < window // 2:
            continue
        result[i] = np.sum(valid < valid[-1]) / (len(valid) - 1) if len(valid) > 1 else 0.5
    return result

def compute_gk_pctile(fast_w, slow_w, pw):
    gk_ma_fast = rolling_mean(gk_raw, fast_w)
    gk_ma_slow = rolling_mean(gk_raw, slow_w)
    ratio = gk_ma_fast / gk_ma_slow
    ratio_shifted = np.full(len(ratio), np.nan)
    ratio_shifted[1:] = ratio[:-1]
    return fast_rolling_pctile(ratio_shifted, pw)

print("Computing indicators...", flush=True)
gk_p_l = compute_gk_pctile(5, 20, 100)
gk_p_s = compute_gk_pctile(10, 30, 100)

c_s = pd.Series(c)
bo_up = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn = (c_s < c_s.shift(1).rolling(15).min()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])
BH = {0, 1, 2, 12}
BD_L = {"Saturday", "Sunday"}
BD_S = {"Monday", "Saturday", "Sunday"}
print("Done.", flush=True)

FEE = 4.0
NOTIONAL = 4000
SLIP = 1.25


def backtest(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss, block_hours, block_days,
             start_bar, end_bar, ext=2,
             mfe_act=None, trail_dd=None, min_bar_trail=1,
             check_bar=None, exit_thresh=None, reduced_mh=None):
    trades = []
    in_pos = False
    in_ext = False
    last_exit = -999
    cur_day = ""; day_pnl = 0.0
    cur_month = ""; month_pnl = 0.0; month_entries = 0
    consec_losses = 0; consec_pause = -1

    for i in range(max(200, start_bar), min(end_bar, TOTAL - 1)):
        bar_dt = pd.Timestamp(dt[i])
        dk = bar_dt.strftime("%Y-%m-%d")
        mk = bar_dt.strftime("%Y-%m")
        if dk != cur_day:
            cur_day = dk; day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk; month_pnl = 0.0; month_entries = 0

        if in_pos:
            held = i - entry_bar
            ep = entry_price

            if side == "L":
                cur_pnl_pct = (c[i] - ep) / ep
                intra_best = (h[i] - ep) / ep
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * SLIP)
                hit_be = in_ext and l[i] <= be_price
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                intra_best = (ep - l[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * SLIP)
                hit_be = in_ext and h[i] >= be_price

            running_mfe = max(running_mfe, intra_best)

            if check_bar is not None and exit_thresh is not None and reduced_mh is not None:
                if held == check_bar and cur_pnl_pct <= exit_thresh:
                    eff_mh = reduced_mh

            ex_p = None; ex_r = None

            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif mfe_act is not None and trail_dd is not None:
                if (held >= min_bar_trail and running_mfe >= mfe_act
                    and cur_pnl_pct <= running_mfe - trail_dd):
                    ex_p, ex_r = c[i], "MFE-trail"

            if ex_p is None:
                if in_ext:
                    if hit_be:
                        ex_p, ex_r = be_price, "BE"
                    elif i - ext_start >= ext:
                        ex_p, ex_r = c[i], "MH-ext"
                elif held >= eff_mh:
                    if cur_pnl_pct > 0 and ext > 0:
                        in_ext = True; ext_start = i; be_price = ep
                    else:
                        ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                trades.append({
                    "entry_bar": entry_bar, "exit_bar": i, "pnl": net,
                    "reason": ex_r, "held": held,
                    "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m"),
                    "entry_dt": str(pd.Timestamp(dt[entry_bar])),
                })
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
        if hours[i] in block_hours or days[i] in block_days: continue

        gk_val = gk_p[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh: continue

        bo = bo_up[i] if side == "L" else bo_dn[i]
        if bo:
            entry_price = o[i + 1]
            entry_bar = i
            running_mfe = 0.0
            eff_mh = maxhold
            in_pos = True; in_ext = False
            month_entries += 1

    return trades


def eval_trades(trades):
    if not trades:
        return {"pnl": 0, "n": 0, "wr": 0, "mdd": 0, "worst_mo": 0,
                "pos_months": 0, "total_months": 0, "exit_dist": {}}
    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    mdd = abs(min(eq - peak)) if len(eq) > 0 else 0
    monthly = {}
    for t in trades:
        monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
    worst_mo = min(monthly.values()) if monthly else 0
    pos_months = sum(1 for v in monthly.values() if v > 0)
    exits = {}
    for t in trades:
        exits[t["reason"]] = exits.get(t["reason"], 0) + 1
    return {"pnl": total, "n": n, "wr": wr, "mdd": mdd,
            "worst_mo": worst_mo, "pos_months": pos_months,
            "total_months": len(monthly), "exit_dist": exits}


def run_l(start, end, **kw):
    return backtest("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, start, end, 2, **kw)

def run_s(start, end):
    return backtest("S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S, start, end, 2)

def wf8(kw):
    seg = TOTAL // 8
    results = []
    for fold in range(6):
        ts = (fold + 2) * seg
        te = (fold + 3) * seg
        tt = run_l(ts, te, **kw)
        results.append(sum(t["pnl"] for t in tt))
    return sum(1 for p in results if p > 0), results


# ===== Part 1: Scan reduced_mh=5 with different MFE trail + check_bar combos =====
print("\n" + "=" * 80)
print("Part 1: reduced_mh=5 Parameter Scan")
print("=" * 80)

configs = []

# Baseline + R5 champion + R5 reduced_mh=5
configs.append(("BASELINE", {}))
configs.append(("R5 champion (mh4)", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
                                       "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}))
configs.append(("R5 mh5 (exact)", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
                                    "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 5}))

# MFE trail variations with mh5
for mfe_a in [0.008, 0.01, 0.012, 0.015]:
    for tr_dd in [0.008, 0.01, 0.012]:
        if tr_dd > mfe_a: continue
        for mb in [1, 2]:
            label = f"mh5: mfe={mfe_a*100:.1f}/dd={tr_dd*100:.1f}/mb={mb}"
            configs.append((label, {
                "mfe_act": mfe_a, "trail_dd": tr_dd, "min_bar_trail": mb,
                "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 5
            }))

# exit_thresh variations with mh5
for eth in [-0.015, -0.01, -0.008, -0.005]:
    label = f"mh5: thresh={eth*100:.1f}%"
    configs.append((label, {
        "mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
        "check_bar": 2, "exit_thresh": eth, "reduced_mh": 5
    }))

# check_bar=3 with mh5
configs.append(("mh5: check_bar=3", {
    "mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
    "check_bar": 3, "exit_thresh": -0.01, "reduced_mh": 5
}))

# R2-only with no conditional MH (for comparison)
configs.append(("R2 only (no cond MH)", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1}))

# mh5 without MFE trail (R3-style only)
configs.append(("R3 only mh5", {"check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 5}))

print(f"\nTotal configs: {len(configs)}")
print(f"\n{'Config':<40} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} {'WstMo':>6} {'WF':>4} | OOS exits")
print("-" * 135)

results = []
baseline_oos = None
for label, kw in configs:
    trades = run_l(0, TOTAL, **kw)
    is_t = [t for t in trades if t["entry_bar"] < IS_END]
    oos_t = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_t)
    oos_m = eval_trades(oos_t)
    wf, wfp = wf8(kw)
    if baseline_oos is None:
        baseline_oos = oos_m["pnl"]
    delta = oos_m["pnl"] - baseline_oos
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    print(f"{label:<40} | ${is_m['pnl']:>6.0f} ${oos_m['pnl']:>6.0f} {delta:>+6.0f} {oos_m['wr']:>3.0f}% ${oos_m['mdd']:>4.0f} ${oos_m['worst_mo']:>5.0f} {wf}/6 | {exits_str}")
    results.append((label, kw, is_m, oos_m, wf, wfp))

# ===== Part 2: Best config deep validation =====
print("\n" + "=" * 80)
print("Part 2: Top 3 Deep Validation")
print("=" * 80)

# Sort by OOS PnL (skip baseline)
ranked = sorted(range(1, len(results)), key=lambda i: results[i][3]["pnl"], reverse=True)

for rank, idx in enumerate(ranked[:3]):
    label, kw, is_m, oos_m, wf, wfp = results[idx]
    print(f"\n--- #{rank+1}: {label} ---")
    print(f"  IS:  ${is_m['pnl']:.0f} n={is_m['n']} WR={is_m['wr']:.0f}% MDD=${is_m['mdd']:.0f}")
    print(f"  OOS: ${oos_m['pnl']:.0f} n={oos_m['n']} WR={oos_m['wr']:.0f}% MDD=${oos_m['mdd']:.0f} worst=${oos_m['worst_mo']:.0f}")
    print(f"  WF 8-fold: {wf}/6  [{', '.join(f'${p:.0f}' for p in wfp)}]")
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    print(f"  Exits: {exits_str}")

    # Monthly
    trades = run_l(0, TOTAL, **kw)
    oos_trades = [t for t in trades if t["entry_bar"] >= IS_END]
    s_trades = run_s(0, TOTAL)
    s_oos = [t for t in s_trades if t["entry_bar"] >= IS_END]

    l_monthly = {}
    for t in oos_trades:
        l_monthly[t["entry_month"]] = l_monthly.get(t["entry_month"], 0) + t["pnl"]
    s_monthly = {}
    for t in s_oos:
        s_monthly[t["entry_month"]] = s_monthly.get(t["entry_month"], 0) + t["pnl"]

    all_months = sorted(set(list(l_monthly.keys()) + list(s_monthly.keys())))
    print(f"\n  {'Month':<10} | {'L':>7} {'S':>7} {'L+S':>7}")
    print("  " + "-" * 40)
    ls_total = 0
    pos_mo = 0
    for mo in all_months:
        lp = l_monthly.get(mo, 0)
        sp = s_monthly.get(mo, 0)
        ls = lp + sp
        ls_total += ls
        if ls > 0: pos_mo += 1
        print(f"  {mo:<10} | ${lp:>6.0f} ${sp:>6.0f} ${ls:>6.0f}")
    print(f"  Total: ${ls_total:.0f}, positive months: {pos_mo}/{len(all_months)}")

# ===== Part 3: Robustness of best mh5 config =====
print("\n" + "=" * 80)
print("Part 3: Best mh5 Robustness")
print("=" * 80)

best_idx = ranked[0]
best_label, best_kw, _, _, _, _ = results[best_idx]
print(f"\nBest config: {best_label}")

perturbations = [
    ("exact", best_kw),
    ("mfe_act +/-0.2%", {**best_kw, "mfe_act": best_kw.get("mfe_act", 0.01) + 0.002}),
    ("mfe_act -0.2%", {**best_kw, "mfe_act": best_kw.get("mfe_act", 0.01) - 0.002}),
    ("trail_dd +0.2%", {**best_kw, "trail_dd": best_kw.get("trail_dd", 0.01) + 0.002}),
    ("trail_dd -0.2%", {**best_kw, "trail_dd": best_kw.get("trail_dd", 0.01) - 0.002}),
    ("thresh -0.5%", {**best_kw, "exit_thresh": -0.005}),
    ("thresh -1.5%", {**best_kw, "exit_thresh": -0.015}),
    ("mh=4", {**best_kw, "reduced_mh": 4}),
    ("mh=6 (no reduce)", {**best_kw, "reduced_mh": 6}),
]

print(f"\n  {'Perturbation':<25} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} | WF")
print("  " + "-" * 75)

for plabel, pkw in perturbations:
    trades = run_l(0, TOTAL, **pkw)
    is_t = [t for t in trades if t["entry_bar"] < IS_END]
    oos_t = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_t)
    oos_m = eval_trades(oos_t)
    wf, _ = wf8(pkw)
    delta = oos_m["pnl"] - baseline_oos
    print(f"  {plabel:<25} | ${is_m['pnl']:>6.0f} ${oos_m['pnl']:>6.0f} {delta:>+6.0f} {oos_m['wr']:>3.0f}% ${oos_m['mdd']:>4.0f} | {wf}/6")

# ===== Final comparison =====
print("\n" + "=" * 80)
print("FINAL: V13 vs R5 Champion (mh4) vs R6 Best (mh5)")
print("=" * 80)

s_all = run_s(0, TOTAL)
s_oos_m = eval_trades([t for t in s_all if t["entry_bar"] >= IS_END])

for label, kw in [("V13 Baseline", {}),
                   ("R5 Champion (mh4)", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
                                          "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
                   (f"R6 Best (mh5): {best_label}", best_kw)]:
    trades = run_l(0, TOTAL, **kw)
    is_t = [t for t in trades if t["entry_bar"] < IS_END]
    oos_t = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_t)
    oos_m = eval_trades(oos_t)
    wf, wfp = wf8(kw)
    ls_oos = oos_m["pnl"] + s_oos_m["pnl"]
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    print(f"\n  {label}:")
    print(f"    L IS:  ${is_m['pnl']:>7.0f} n={is_m['n']} WR={is_m['wr']:.0f}%")
    print(f"    L OOS: ${oos_m['pnl']:>7.0f} n={oos_m['n']} WR={oos_m['wr']:.0f}% MDD=${oos_m['mdd']:.0f} worst=${oos_m['worst_mo']:.0f}")
    print(f"    WF 8-fold: {wf}/6  [{', '.join(f'${p:.0f}' for p in wfp)}]")
    print(f"    L+S OOS: ${ls_oos:.0f}")
    print(f"    Exits: {exits_str}")

print("\nDone.")
