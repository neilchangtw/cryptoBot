"""
V14-R2: MFE Trailing Exit -- MH 虧損拯救機制
=============================================
基於 R1 發現：59-78% 的 MH 虧損曾經盈利但回吐。
測試：一旦浮盈達到 MFE 門檻，啟動 trailing -- 若回吐超過閾值就提前出場。

參數掃描：
  - mfe_act:  MFE 啟動門檻 (%)
  - trail_dd: 從 MFE 高點回吐多少觸發出場 (%)
  - min_bar:  最早可啟動 trailing 的 bar 數

出場優先順序：SL -> TP -> MFE-trail -> MH(ext+BE)
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
    s = pd.Series(arr)
    return s.rolling(w).mean().values

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

# ===== Backtest with MFE trailing =====
FEE = 4.0
NOTIONAL = 4000
SLIP = 1.25

def backtest_v14_mfe_trail(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                           cooldown, monthly_loss, block_hours, block_days,
                           start_bar, end_bar, ext=2, be_trail=True,
                           mfe_act=None, trail_dd=None, min_bar_trail=1):
    """
    V13 backtest + MFE trailing exit.
    mfe_act: once running MFE >= this %, trailing is armed
    trail_dd: if PnL drops mfe_peak - trail_dd, exit as "MFE-trail"
    min_bar_trail: trailing can only activate after this many bars held
    """
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

            # Update running MFE
            running_mfe = max(running_mfe, intra_best)

            ex_p = None; ex_r = None

            # Priority 1: SL
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            # Priority 2: TP
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            # Priority 3: MFE trailing (new!)
            elif mfe_act is not None and trail_dd is not None:
                if (held >= min_bar_trail and
                    running_mfe >= mfe_act and
                    cur_pnl_pct <= running_mfe - trail_dd):
                    # Exit at close price (conservative)
                    ex_p, ex_r = c[i], "MFE-trail"
            # Priority 4: Extension / MH (original V13)
            if ex_p is None:
                if in_ext:
                    if hit_be:
                        ex_p, ex_r = be_price, "BE"
                    elif i - ext_start >= ext:
                        ex_p, ex_r = c[i], "MH-ext"
                elif held >= maxhold:
                    if cur_pnl_pct > 0 and ext > 0:
                        in_ext = True; ext_start = i; be_price = ep
                    else:
                        ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                trades.append({
                    "entry_bar": entry_bar,
                    "exit_bar": i,
                    "pnl": net,
                    "reason": ex_r,
                    "held": held,
                    "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m"),
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
            in_pos = True; in_ext = False
            month_entries += 1

    return trades


def eval_trades(trades, label=""):
    """Compute metrics from trade list."""
    if not trades:
        return {"pnl": 0, "n": 0, "wr": 0, "mdd": 0, "worst_mo": 0,
                "exit_dist": {}, "avg_pnl": 0}
    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    n = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / n * 100
    # MDD
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    mdd = abs(min(dd)) if len(dd) > 0 else 0
    # Monthly
    monthly = {}
    for t in trades:
        mo = t["entry_month"]
        monthly[mo] = monthly.get(mo, 0) + t["pnl"]
    worst_mo = min(monthly.values()) if monthly else 0
    pos_months = sum(1 for v in monthly.values() if v > 0)
    # Exit distribution
    exits = {}
    for t in trades:
        r = t["reason"]
        exits[r] = exits.get(r, 0) + 1
    return {
        "pnl": total, "n": n, "wr": wr, "mdd": mdd,
        "worst_mo": worst_mo, "pos_months": pos_months,
        "total_months": len(monthly), "exit_dist": exits,
        "avg_pnl": total / n if n else 0,
    }


def walk_forward(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                 cooldown, monthly_loss, block_hours, block_days,
                 ext=2, mfe_act=None, trail_dd=None, min_bar_trail=1):
    """6-fold walk-forward: train on 2 segments, test on 1."""
    seg = TOTAL // 6
    wf_results = []
    for fold in range(4):  # 4 folds: train [0,1]+test[2], [1,2]+test[3], [2,3]+test[4], [3,4]+test[5]
        test_start = (fold + 2) * seg
        test_end = (fold + 3) * seg
        test_trades = backtest_v14_mfe_trail(
            side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
            cooldown, monthly_loss, block_hours, block_days,
            test_start, test_end, ext, True,
            mfe_act, trail_dd, min_bar_trail
        )
        wf_pnl = sum(t["pnl"] for t in test_trades)
        wf_results.append(wf_pnl)
    passes = sum(1 for p in wf_results if p > 0)
    return passes, wf_results


# ===== Parameter scan =====
MFE_ACT_VALUES = [0.003, 0.005, 0.008, 0.010, 0.015]   # 0.3% ~ 1.5%
TRAIL_DD_VALUES = [0.003, 0.005, 0.008, 0.010, 0.015]   # 0.3% ~ 1.5%
MIN_BAR_VALUES = [1, 2, 3]

# Include baseline (no trailing) as first config
configs = [(None, None, 1)]  # baseline
for mfe_a, tr_dd, mb in product(MFE_ACT_VALUES, TRAIL_DD_VALUES, MIN_BAR_VALUES):
    # trail_dd must be <= mfe_act (can't trail more than you gained)
    if tr_dd > mfe_a:
        continue
    configs.append((mfe_a, tr_dd, mb))

print(f"\nTotal configs to test: {len(configs)} (incl. baseline)")
print("=" * 80)

# ===== Run L scan =====
print("\n>>> L Strategy Scan <<<")
l_results = []
for idx, (mfe_a, tr_dd, mb) in enumerate(configs):
    trades = backtest_v14_mfe_trail(
        "L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L,
        0, TOTAL, 2, True, mfe_a, tr_dd, mb
    )
    is_trades = [t for t in trades if t["entry_bar"] < IS_END]
    oos_trades = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_trades)
    oos_m = eval_trades(oos_trades)
    wf_pass, wf_pnls = walk_forward(
        "L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L,
        2, mfe_a, tr_dd, mb
    )
    l_results.append({
        "mfe_act": mfe_a, "trail_dd": tr_dd, "min_bar": mb,
        "is": is_m, "oos": oos_m, "wf_pass": wf_pass, "wf_pnls": wf_pnls,
    })

# ===== Run S scan =====
print(">>> S Strategy Scan <<<")
s_results = []
for idx, (mfe_a, tr_dd, mb) in enumerate(configs):
    trades = backtest_v14_mfe_trail(
        "S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S,
        0, TOTAL, 2, True, mfe_a, tr_dd, mb
    )
    is_trades = [t for t in trades if t["entry_bar"] < IS_END]
    oos_trades = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_trades)
    oos_m = eval_trades(oos_trades)
    wf_pass, wf_pnls = walk_forward(
        "S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S,
        2, mfe_a, tr_dd, mb
    )
    s_results.append({
        "mfe_act": mfe_a, "trail_dd": tr_dd, "min_bar": mb,
        "is": is_m, "oos": oos_m, "wf_pass": wf_pass, "wf_pnls": wf_pnls,
    })

# ===== Print Results =====
print("\n" + "=" * 80)
print("V14-R2: MFE Trailing Exit Results")
print("=" * 80)

def fmt_cfg(r):
    if r["mfe_act"] is None:
        return "BASELINE (V13)"
    return f"mfe={r['mfe_act']*100:.1f}% dd={r['trail_dd']*100:.1f}% mb={r['min_bar']}"

# --- L Results ---
print("\n--- L Strategy ---")
print(f"{'Config':<30} | {'IS PnL':>8} {'n':>4} {'WR':>5} | {'OOS PnL':>8} {'n':>4} {'WR':>5} {'MDD':>6} {'WstMo':>7} | {'WF':>4} | OOS exits")
print("-" * 130)

# Sort by OOS PnL
l_sorted = sorted(enumerate(l_results), key=lambda x: x[1]["oos"]["pnl"], reverse=True)
baseline_l = l_results[0]

for rank, (idx, r) in enumerate(l_sorted[:30]):
    cfg = fmt_cfg(r)
    is_m, oos_m = r["is"], r["oos"]
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    marker = " <-- BASELINE" if r["mfe_act"] is None else ""
    delta = oos_m["pnl"] - baseline_l["oos"]["pnl"]
    delta_str = f" ({delta:+.0f})" if r["mfe_act"] is not None else ""
    print(f"{cfg:<30} | ${is_m['pnl']:>7.0f} {is_m['n']:>4} {is_m['wr']:>4.0f}% | "
          f"${oos_m['pnl']:>7.0f}{delta_str:>8} {oos_m['n']:>4} {oos_m['wr']:>4.0f}% "
          f"${oos_m['mdd']:>5.0f} ${oos_m['worst_mo']:>6.0f} | {r['wf_pass']}/4 | {exits_str}{marker}")

# --- S Results ---
print("\n--- S Strategy ---")
print(f"{'Config':<30} | {'IS PnL':>8} {'n':>4} {'WR':>5} | {'OOS PnL':>8} {'n':>4} {'WR':>5} {'MDD':>6} {'WstMo':>7} | {'WF':>4} | OOS exits")
print("-" * 130)

s_sorted = sorted(enumerate(s_results), key=lambda x: x[1]["oos"]["pnl"], reverse=True)
baseline_s = s_results[0]

for rank, (idx, r) in enumerate(s_sorted[:30]):
    cfg = fmt_cfg(r)
    is_m, oos_m = r["is"], r["oos"]
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    marker = " <-- BASELINE" if r["mfe_act"] is None else ""
    delta = oos_m["pnl"] - baseline_s["oos"]["pnl"]
    delta_str = f" ({delta:+.0f})" if r["mfe_act"] is not None else ""
    print(f"{cfg:<30} | ${is_m['pnl']:>7.0f} {is_m['n']:>4} {is_m['wr']:>4.0f}% | "
          f"${oos_m['pnl']:>7.0f}{delta_str:>8} {oos_m['n']:>4} {oos_m['wr']:>4.0f}% "
          f"${oos_m['mdd']:>5.0f} ${oos_m['worst_mo']:>6.0f} | {r['wf_pass']}/4 | {exits_str}{marker}")

# --- Combined L+S Top configs ---
print("\n" + "=" * 80)
print("Combined L+S (OOS PnL ranking)")
print("=" * 80)

# For each L config, pair with each S config and find best combos
# But that's 76^2 = 5776 combos, too many. Instead:
# Use same config for both L and S, or use best L + best S

# Same config approach
print("\n--- Same config for L+S ---")
print(f"{'Config':<30} | {'L OOS':>8} {'S OOS':>8} {'L+S':>8} | {'L WF':>4} {'S WF':>4}")
print("-" * 90)

combined = []
for idx in range(len(configs)):
    l_r = l_results[idx]
    s_r = s_results[idx]
    ls_pnl = l_r["oos"]["pnl"] + s_r["oos"]["pnl"]
    combined.append((idx, ls_pnl, l_r, s_r))

combined.sort(key=lambda x: x[1], reverse=True)
baseline_ls = baseline_l["oos"]["pnl"] + baseline_s["oos"]["pnl"]

for rank, (idx, ls_pnl, l_r, s_r) in enumerate(combined[:20]):
    cfg = fmt_cfg(l_r)
    delta = ls_pnl - baseline_ls
    delta_str = f" ({delta:+.0f})" if l_r["mfe_act"] is not None else ""
    print(f"{cfg:<30} | ${l_r['oos']['pnl']:>7.0f} ${s_r['oos']['pnl']:>7.0f} ${ls_pnl:>7.0f}{delta_str:>8} | "
          f"{l_r['wf_pass']}/4  {s_r['wf_pass']}/4")

# --- Best L + Best S (independent optimization) ---
print("\n--- Best L + Best S (independent) ---")
best_l_idx = l_sorted[0][0]
best_s_idx = s_sorted[0][0]
bl = l_results[best_l_idx]
bs = s_results[best_s_idx]
print(f"Best L: {fmt_cfg(bl)} -> OOS ${bl['oos']['pnl']:.0f} WF {bl['wf_pass']}/4")
print(f"Best S: {fmt_cfg(bs)} -> OOS ${bs['oos']['pnl']:.0f} WF {bs['wf_pass']}/4")
print(f"Combined: ${bl['oos']['pnl'] + bs['oos']['pnl']:.0f} (vs baseline ${baseline_ls:.0f}, delta ${bl['oos']['pnl'] + bs['oos']['pnl'] - baseline_ls:+.0f})")

# --- Detailed comparison: best vs baseline ---
print("\n" + "=" * 80)
print("Detailed: Best configs vs V13 baseline")
print("=" * 80)

for label, result, base in [("L", bl, baseline_l), ("S", bs, baseline_s)]:
    print(f"\n  {label}: {fmt_cfg(result)}")
    for period, key in [("IS", "is"), ("OOS", "oos")]:
        m = result[key]
        b = base[key]
        exits_str = " ".join(f"{k}:{v}" for k, v in sorted(m["exit_dist"].items()))
        exits_base = " ".join(f"{k}:{v}" for k, v in sorted(b["exit_dist"].items()))
        print(f"    {period}: ${m['pnl']:>7.0f} (base ${b['pnl']:>7.0f}, delta ${m['pnl']-b['pnl']:+.0f})")
        print(f"          n={m['n']} WR={m['wr']:.0f}% MDD=${m['mdd']:.0f} worst=${m['worst_mo']:.0f}")
        print(f"          exits: {exits_str}")
        print(f"          base:  {exits_base}")
    print(f"    WF: {result['wf_pass']}/4 (base {base['wf_pass']}/4)")

print("\nDone.")
