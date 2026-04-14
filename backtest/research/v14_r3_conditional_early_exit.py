"""
V14-R3: Conditional Early Exit -- 基於早期 PnL 的動態 MH 縮短
==============================================================
R1 發現：
  - L: bar3 negative -> 55% MH rate, bar3 positive -> 9% MH rate
  - S: bar3 negative -> 52% MH rate, bar3 positive -> 29% MH rate
  - MH 虧損交易 avg MFE 很早出現 (L median bar 2, S median bar 2)

R2 發現：
  - MFE trailing 對 L 有效 (+$128) 但對 S 無效 (TP 太低被誤殺)

R3 策略：
  方案 A: 動態 MH 縮短 -- 若 bar N close PnL < threshold，將 MH 縮短
  方案 B: 條件式提前出場 -- bar N 為負且連續 M 根為負，提前出場
  方案 C: 虧損加速器 -- 若 bar N PnL < deep_threshold，立即出場（MH 虧損的 deep cut）

出場優先順序：SL -> TP -> EarlyExit -> MH(ext+BE)
"""
import pandas as pd
import numpy as np
from itertools import product

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2

# ===== GK indicators =====
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


def backtest_v14_early_exit(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                            cooldown, monthly_loss, block_hours, block_days,
                            start_bar, end_bar, ext=2, be_trail=True,
                            # --- Early exit params ---
                            check_bar=None,        # which bar to check PnL (e.g., 2, 3, 4)
                            exit_thresh=None,       # if close PnL (%) at check_bar <= this, trigger action
                            action="reduce_mh",     # "reduce_mh" or "exit_now"
                            reduced_mh=None,        # new MH if action="reduce_mh"
                            # --- Consecutive negative bars ---
                            consec_neg_bars=None,   # if N consecutive bars are negative, trigger action
                            consec_neg_action="exit_now",
                            ):
    """V13 backtest with conditional early exit mechanisms."""
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
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * SLIP)
                hit_be = in_ext and l[i] <= be_price
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * SLIP)
                hit_be = in_ext and h[i] >= be_price

            # Track bar close PnLs for consecutive check
            bar_pnls.append(cur_pnl_pct)

            ex_p = None; ex_r = None

            # Priority 1: SL
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            # Priority 2: TP
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"

            # Priority 3a: Single-bar check early exit
            if ex_p is None and check_bar is not None and exit_thresh is not None:
                if held == check_bar:
                    if cur_pnl_pct <= exit_thresh:
                        if action == "exit_now":
                            ex_p, ex_r = c[i], "EarlyExit"
                        elif action == "reduce_mh" and reduced_mh is not None:
                            effective_mh = reduced_mh  # will be used below
                        # else: no action

            # Priority 3b: Consecutive negative bars check
            if ex_p is None and consec_neg_bars is not None and not in_ext:
                if held >= consec_neg_bars:
                    last_n = bar_pnls[-consec_neg_bars:]
                    if all(p < 0 for p in last_n):
                        if consec_neg_action == "exit_now":
                            ex_p, ex_r = c[i], "ConsecExit"

            # Priority 4: Extension / MH
            if ex_p is None:
                # Use effective_mh if reduced
                eff_mh = effective_mh if 'effective_mh' in dir() else maxhold
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
                })
                day_pnl += net; month_pnl += net
                last_exit = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
                    consec_pause = i + 24
                in_pos = False; in_ext = False
                if 'effective_mh' in dir():
                    del effective_mh

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
            bar_pnls = []
            in_pos = True; in_ext = False
            month_entries += 1

    return trades


def eval_trades(trades):
    if not trades:
        return {"pnl": 0, "n": 0, "wr": 0, "mdd": 0, "worst_mo": 0,
                "pos_months": 0, "total_months": 0, "exit_dist": {}, "avg_held": 0}
    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    mdd = abs(min(dd)) if len(dd) > 0 else 0
    monthly = {}
    for t in trades:
        mo = t["entry_month"]
        monthly[mo] = monthly.get(mo, 0) + t["pnl"]
    worst_mo = min(monthly.values()) if monthly else 0
    pos_months = sum(1 for v in monthly.values() if v > 0)
    exits = {}
    for t in trades:
        r = t["reason"]
        exits[r] = exits.get(r, 0) + 1
    avg_held = np.mean([t["held"] for t in trades])
    return {
        "pnl": total, "n": n, "wr": wr, "mdd": mdd,
        "worst_mo": worst_mo, "pos_months": pos_months,
        "total_months": len(monthly), "exit_dist": exits, "avg_held": avg_held,
    }


def walk_forward(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                 cooldown, monthly_loss, block_hours, block_days, ext=2, **kwargs):
    seg = TOTAL // 6
    wf_results = []
    for fold in range(4):
        test_start = (fold + 2) * seg
        test_end = (fold + 3) * seg
        test_trades = backtest_v14_early_exit(
            side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
            cooldown, monthly_loss, block_hours, block_days,
            test_start, test_end, ext, True, **kwargs
        )
        wf_pnl = sum(t["pnl"] for t in test_trades)
        wf_results.append(wf_pnl)
    return sum(1 for p in wf_results if p > 0), wf_results


# ===== Define test configs =====
# Config structure: (label, L_kwargs, S_kwargs)

configs = []

# 0. Baseline (V13)
configs.append(("BASELINE (V13)", {}, {}))

# --- Plan A: Single-bar check + exit_now ---
for check_bar in [2, 3, 4]:
    for thresh in [-0.015, -0.010, -0.008, -0.005, -0.003, 0.0]:
        label = f"A: bar{check_bar} <={thresh*100:+.1f}% exit"
        configs.append((label,
                        {"check_bar": check_bar, "exit_thresh": thresh, "action": "exit_now"},
                        {"check_bar": check_bar, "exit_thresh": thresh, "action": "exit_now"}))

# --- Plan B: Single-bar check + reduce_mh ---
for check_bar in [2, 3]:
    for thresh in [-0.010, -0.005, 0.0]:
        for red_mh_l in [3, 4]:
            for red_mh_s in [5, 6, 7]:
                label = f"B: bar{check_bar} <={thresh*100:+.1f}% MH L->{red_mh_l} S->{red_mh_s}"
                configs.append((label,
                                {"check_bar": check_bar, "exit_thresh": thresh,
                                 "action": "reduce_mh", "reduced_mh": red_mh_l},
                                {"check_bar": check_bar, "exit_thresh": thresh,
                                 "action": "reduce_mh", "reduced_mh": red_mh_s}))

# --- Plan C: Consecutive negative bars + exit ---
for cn in [2, 3, 4]:
    configs.append((f"C: {cn} consec neg -> exit",
                    {"consec_neg_bars": cn, "consec_neg_action": "exit_now"},
                    {"consec_neg_bars": cn, "consec_neg_action": "exit_now"}))

print(f"\nTotal configs: {len(configs)}")
print("=" * 80)

# ===== Run all configs =====
results = []
for idx, (label, l_kw, s_kw) in enumerate(configs):
    # L
    l_trades = backtest_v14_early_exit(
        "L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, 0, TOTAL, 2, True, **l_kw)
    l_is = eval_trades([t for t in l_trades if t["entry_bar"] < IS_END])
    l_oos = eval_trades([t for t in l_trades if t["entry_bar"] >= IS_END])
    l_wf, _ = walk_forward("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, 2, **l_kw)

    # S
    s_trades = backtest_v14_early_exit(
        "S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S, 0, TOTAL, 2, True, **s_kw)
    s_is = eval_trades([t for t in s_trades if t["entry_bar"] < IS_END])
    s_oos = eval_trades([t for t in s_trades if t["entry_bar"] >= IS_END])
    s_wf, _ = walk_forward("S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S, 2, **s_kw)

    results.append({
        "label": label, "l_is": l_is, "l_oos": l_oos, "l_wf": l_wf,
        "s_is": s_is, "s_oos": s_oos, "s_wf": s_wf,
    })
    if (idx + 1) % 20 == 0:
        print(f"  [{idx+1}/{len(configs)}]...", flush=True)

print(f"  [{len(configs)}/{len(configs)}] done.", flush=True)

# ===== Print Results =====
print("\n" + "=" * 80)
print("V14-R3: Conditional Early Exit Results")
print("=" * 80)

baseline = results[0]
bl_ls_oos = baseline["l_oos"]["pnl"] + baseline["s_oos"]["pnl"]

# --- L-only ranking ---
print("\n--- L Strategy (OOS PnL ranking, top 25) ---")
print(f"{'Config':<42} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} {'WF':>4} | OOS exits")
print("-" * 130)

l_ranked = sorted(range(len(results)), key=lambda i: results[i]["l_oos"]["pnl"], reverse=True)
for rank, idx in enumerate(l_ranked[:25]):
    r = results[idx]
    m = r["l_oos"]
    delta = m["pnl"] - baseline["l_oos"]["pnl"]
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(m["exit_dist"].items()))
    marker = " <--" if idx == 0 else ""
    print(f"{r['label']:<42} | ${r['l_is']['pnl']:>6.0f} ${m['pnl']:>6.0f} {delta:>+6.0f} {m['wr']:>3.0f}% ${m['mdd']:>4.0f} {r['l_wf']}/4 | {exits_str}{marker}")

# --- S-only ranking ---
print("\n--- S Strategy (OOS PnL ranking, top 25) ---")
print(f"{'Config':<42} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} {'WF':>4} | OOS exits")
print("-" * 130)

s_ranked = sorted(range(len(results)), key=lambda i: results[i]["s_oos"]["pnl"], reverse=True)
for rank, idx in enumerate(s_ranked[:25]):
    r = results[idx]
    m = r["s_oos"]
    delta = m["pnl"] - baseline["s_oos"]["pnl"]
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(m["exit_dist"].items()))
    marker = " <--" if idx == 0 else ""
    print(f"{r['label']:<42} | ${r['s_is']['pnl']:>6.0f} ${m['pnl']:>6.0f} {delta:>+6.0f} {m['wr']:>3.0f}% ${m['mdd']:>4.0f} {r['s_wf']}/4 | {exits_str}{marker}")

# --- Combined L+S ranking (same config) ---
print("\n--- L+S Combined (same config, OOS ranking, top 25) ---")
print(f"{'Config':<42} | {'L OOS':>7} {'S OOS':>7} {'L+S':>7} {'delta':>7} | {'L WF':>4} {'S WF':>4}")
print("-" * 110)

ls_ranked = sorted(range(len(results)),
                   key=lambda i: results[i]["l_oos"]["pnl"] + results[i]["s_oos"]["pnl"],
                   reverse=True)
for rank, idx in enumerate(ls_ranked[:25]):
    r = results[idx]
    ls_pnl = r["l_oos"]["pnl"] + r["s_oos"]["pnl"]
    delta = ls_pnl - bl_ls_oos
    marker = " <--" if idx == 0 else ""
    print(f"{r['label']:<42} | ${r['l_oos']['pnl']:>6.0f} ${r['s_oos']['pnl']:>6.0f} ${ls_pnl:>6.0f} {delta:>+6.0f} | "
          f"{r['l_wf']}/4  {r['s_wf']}/4{marker}")

# --- Best independent L + Best independent S ---
print("\n--- Best L + Best S (independent) ---")
best_l_idx = l_ranked[0]
best_s_idx = s_ranked[0]
bl_r = results[best_l_idx]
bs_r = results[best_s_idx]
print(f"Best L: {bl_r['label']} -> OOS ${bl_r['l_oos']['pnl']:.0f} WF {bl_r['l_wf']}/4")
print(f"Best S: {bs_r['label']} -> OOS ${bs_r['s_oos']['pnl']:.0f} WF {bs_r['s_wf']}/4")
indep_total = bl_r['l_oos']['pnl'] + bs_r['s_oos']['pnl']
print(f"Combined: ${indep_total:.0f} (vs baseline ${bl_ls_oos:.0f}, delta ${indep_total - bl_ls_oos:+.0f})")

# --- Detailed view of top 3 ---
print("\n" + "=" * 80)
print("Top 3 Detailed (L+S combined)")
print("=" * 80)

for rank, idx in enumerate(ls_ranked[:3]):
    r = results[idx]
    ls_pnl = r["l_oos"]["pnl"] + r["s_oos"]["pnl"]
    print(f"\n  #{rank+1}: {r['label']}")
    for side_label, side_key in [("L", "l"), ("S", "s")]:
        for period, pkey in [("IS", f"{side_key}_is"), ("OOS", f"{side_key}_oos")]:
            m = r[pkey]
            exits_str = " ".join(f"{k}:{v}" for k, v in sorted(m["exit_dist"].items()))
            print(f"    {side_label} {period}: ${m['pnl']:>7.0f} n={m['n']} WR={m['wr']:.0f}% MDD=${m['mdd']:.0f} worst=${m['worst_mo']:.0f} | {exits_str}")
    print(f"    WF: L {r['l_wf']}/4, S {r['s_wf']}/4")
    print(f"    L+S OOS: ${ls_pnl:.0f} (delta ${ls_pnl - bl_ls_oos:+.0f})")

print("\nDone.")
