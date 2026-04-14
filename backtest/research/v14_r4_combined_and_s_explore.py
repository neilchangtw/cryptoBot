"""
V14-R4: Combined L improvements + S independent exploration
============================================================
R2: MFE trailing best for L: mfe=1.0% dd=0.8% mb=2 -> +$128 OOS
R3: Conditional MH best for L: bar2<=-1.0% MH->4 -> +$91 OOS
    Both R2/R3: S baseline is best, all modifications hurt S.

R4 plan:
  Part 1: Combine R2+R3 for L (MFE trail + conditional MH)
  Part 2: S independent exploration
    a) Reduce MH globally (7,8,9 vs 10)
    b) Remove extension for S (ext=0)
    c) Adjust TP (1.5%, 1.8%, 2.0%, 2.5%, 3.0%)
    d) Adjust SafeNet (3.0%, 3.5%, 4.0%, 4.5%, 5.0%)
    e) Combined: best MH + best TP
  Part 3: Final L+S champion candidate
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


def backtest_full(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                  cooldown, monthly_loss, block_hours, block_days,
                  start_bar, end_bar, ext=2, be_trail=True,
                  # MFE trailing (R2)
                  mfe_act=None, trail_dd=None, min_bar_trail=1,
                  # Conditional MH (R3)
                  check_bar=None, exit_thresh=None, reduced_mh=None):
    """Unified backtest with all V14 exit mechanisms."""
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

            # Conditional MH reduction check
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
            eff_mh = maxhold  # reset each trade
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
        exits[t["reason"]] = exits.get(t["reason"], 0) + 1
    return {"pnl": total, "n": n, "wr": wr, "mdd": mdd,
            "worst_mo": worst_mo, "pos_months": pos_months,
            "total_months": len(monthly), "exit_dist": exits}


def walk_forward(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                 cooldown, monthly_loss, block_hours, block_days, ext=2, **kwargs):
    seg = TOTAL // 6
    wf_results = []
    for fold in range(4):
        ts = (fold + 2) * seg
        te = (fold + 3) * seg
        tt = backtest_full(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                           cooldown, monthly_loss, block_hours, block_days, ts, te, ext, True, **kwargs)
        wf_results.append(sum(t["pnl"] for t in tt))
    return sum(1 for p in wf_results if p > 0), wf_results


def run_config(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, block_hours, block_days, ext=2, **kwargs):
    trades = backtest_full(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                           cooldown, monthly_loss, block_hours, block_days, 0, TOTAL, ext, True, **kwargs)
    is_t = [t for t in trades if t["entry_bar"] < IS_END]
    oos_t = [t for t in trades if t["entry_bar"] >= IS_END]
    wf, wfp = walk_forward(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                            cooldown, monthly_loss, block_hours, block_days, ext, **kwargs)
    return eval_trades(is_t), eval_trades(oos_t), wf, wfp


# ================================================================
# Part 1: L combinations (R2 MFE trail + R3 conditional MH)
# ================================================================
print("\n" + "=" * 80)
print("Part 1: L Strategy — R2+R3 Combined")
print("=" * 80)

l_configs = [
    ("BASELINE (V13)", {}),
    ("R2: mfe=1.0/dd=0.8/mb=2", {"mfe_act": 0.01, "trail_dd": 0.008, "min_bar_trail": 2}),
    ("R2: mfe=1.0/dd=1.0/mb=1", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1}),
    ("R2: mfe=1.0/dd=1.0/mb=2", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 2}),
    ("R3: bar2<-1%/MH4", {"check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
    # Combined R2+R3
    ("R2+R3: mfe1.0/0.8/2+bar2<-1%/MH4",
     {"mfe_act": 0.01, "trail_dd": 0.008, "min_bar_trail": 2,
      "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
    ("R2+R3: mfe1.0/1.0/2+bar2<-1%/MH4",
     {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 2,
      "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
    ("R2+R3: mfe1.0/0.8/2+bar2<-0.5%/MH4",
     {"mfe_act": 0.01, "trail_dd": 0.008, "min_bar_trail": 2,
      "check_bar": 2, "exit_thresh": -0.005, "reduced_mh": 4}),
    ("R2+R3: mfe1.0/1.0/1+bar2<-1%/MH4",
     {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
      "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
]

print(f"\n{'Config':<42} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} {'WstMo':>6} {'WF':>4} | OOS exits")
print("-" * 135)

l_results = []
baseline_l_oos = None
for label, kw in l_configs:
    is_m, oos_m, wf, _ = run_config("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, 2, **kw)
    if baseline_l_oos is None:
        baseline_l_oos = oos_m["pnl"]
    delta = oos_m["pnl"] - baseline_l_oos
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    print(f"{label:<42} | ${is_m['pnl']:>6.0f} ${oos_m['pnl']:>6.0f} {delta:>+6.0f} {oos_m['wr']:>3.0f}% ${oos_m['mdd']:>4.0f} ${oos_m['worst_mo']:>5.0f} {wf}/4 | {exits_str}")
    l_results.append((label, kw, is_m, oos_m, wf))

# ================================================================
# Part 2: S independent exploration
# ================================================================
print("\n" + "=" * 80)
print("Part 2: S Strategy — Independent Exploration")
print("=" * 80)

s_configs = [
    ("BASELINE (MH10/TP2.0/SL4.0/ext2)", {"maxhold": 10, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    # a) MH reduction
    ("MH=7", {"maxhold": 7, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    ("MH=8", {"maxhold": 8, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    ("MH=9", {"maxhold": 9, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    ("MH=12", {"maxhold": 12, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    ("MH=14", {"maxhold": 14, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 2}),
    # b) Remove extension
    ("MH=10 ext=0", {"maxhold": 10, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 0}),
    ("MH=12 ext=0", {"maxhold": 12, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 0}),
    ("MH=14 ext=0", {"maxhold": 14, "tp_pct": 0.02, "safenet_pct": 0.04, "ext": 0}),
    # c) TP adjustment
    ("TP=1.5%", {"maxhold": 10, "tp_pct": 0.015, "safenet_pct": 0.04, "ext": 2}),
    ("TP=1.8%", {"maxhold": 10, "tp_pct": 0.018, "safenet_pct": 0.04, "ext": 2}),
    ("TP=2.5%", {"maxhold": 10, "tp_pct": 0.025, "safenet_pct": 0.04, "ext": 2}),
    ("TP=3.0%", {"maxhold": 10, "tp_pct": 0.03, "safenet_pct": 0.04, "ext": 2}),
    ("TP=3.5%", {"maxhold": 10, "tp_pct": 0.035, "safenet_pct": 0.04, "ext": 2}),
    # d) SafeNet adjustment
    ("SL=3.0%", {"maxhold": 10, "tp_pct": 0.02, "safenet_pct": 0.03, "ext": 2}),
    ("SL=3.5%", {"maxhold": 10, "tp_pct": 0.02, "safenet_pct": 0.035, "ext": 2}),
    ("SL=5.0%", {"maxhold": 10, "tp_pct": 0.02, "safenet_pct": 0.05, "ext": 2}),
    # e) Combined promising
    ("MH=12 TP=1.8%", {"maxhold": 12, "tp_pct": 0.018, "safenet_pct": 0.04, "ext": 2}),
    ("MH=12 TP=2.5%", {"maxhold": 12, "tp_pct": 0.025, "safenet_pct": 0.04, "ext": 2}),
    ("MH=8 TP=1.8%", {"maxhold": 8, "tp_pct": 0.018, "safenet_pct": 0.04, "ext": 2}),
    ("MH=8 TP=2.5%", {"maxhold": 8, "tp_pct": 0.025, "safenet_pct": 0.04, "ext": 2}),
    ("MH=14 TP=2.5%", {"maxhold": 14, "tp_pct": 0.025, "safenet_pct": 0.04, "ext": 2}),
    ("MH=14 TP=2.5% ext=0", {"maxhold": 14, "tp_pct": 0.025, "safenet_pct": 0.04, "ext": 0}),
    ("MH=10 TP=2.5% SL=3.5%", {"maxhold": 10, "tp_pct": 0.025, "safenet_pct": 0.035, "ext": 2}),
    ("MH=12 TP=2.5% SL=3.5%", {"maxhold": 12, "tp_pct": 0.025, "safenet_pct": 0.035, "ext": 2}),
]

print(f"\n{'Config':<42} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} {'WstMo':>6} {'WF':>4} | OOS exits")
print("-" * 135)

s_results = []
baseline_s_oos = None
for label, kw in s_configs:
    mh = kw.pop("maxhold")
    tp = kw.pop("tp_pct")
    sl = kw.pop("safenet_pct")
    ext_val = kw.pop("ext")
    is_m, oos_m, wf, _ = run_config("S", gk_p_s, 0.35, tp, mh, sl, 8, -150, BH, BD_S, ext_val, **kw)
    if baseline_s_oos is None:
        baseline_s_oos = oos_m["pnl"]
    delta = oos_m["pnl"] - baseline_s_oos
    exits_str = " ".join(f"{k}:{v}" for k, v in sorted(oos_m["exit_dist"].items()))
    print(f"{label:<42} | ${is_m['pnl']:>6.0f} ${oos_m['pnl']:>6.0f} {delta:>+6.0f} {oos_m['wr']:>3.0f}% ${oos_m['mdd']:>4.0f} ${oos_m['worst_mo']:>5.0f} {wf}/4 | {exits_str}")
    s_results.append((label, is_m, oos_m, wf))

# ================================================================
# Part 3: Champion candidate (best L + best S)
# ================================================================
print("\n" + "=" * 80)
print("Part 3: Champion Candidates")
print("=" * 80)

# Find best L (by OOS PnL among 4/4 WF)
best_l = max(l_results, key=lambda x: x[3]["pnl"] if x[4] >= 3 else -9999)
# Find best S (by OOS PnL among 4/4 WF)
best_s = max(s_results, key=lambda x: x[2]["pnl"] if x[3] >= 3 else -9999)

baseline_total = baseline_l_oos + baseline_s_oos

print(f"\n  V13 Baseline: L ${baseline_l_oos:.0f} + S ${baseline_s_oos:.0f} = ${baseline_total:.0f}")
print(f"\n  Best L: {best_l[0]}")
print(f"    IS: ${best_l[2]['pnl']:.0f} OOS: ${best_l[3]['pnl']:.0f} WR={best_l[3]['wr']:.0f}% MDD=${best_l[3]['mdd']:.0f} WF={best_l[4]}/4")
print(f"    Exits: {' '.join(f'{k}:{v}' for k,v in sorted(best_l[3]['exit_dist'].items()))}")

print(f"\n  Best S: {best_s[0]}")
print(f"    IS: ${best_s[1]['pnl']:.0f} OOS: ${best_s[2]['pnl']:.0f} WR={best_s[2]['wr']:.0f}% MDD=${best_s[2]['mdd']:.0f} WF={best_s[3]}/4")
print(f"    Exits: {' '.join(f'{k}:{v}' for k,v in sorted(best_s[2]['exit_dist'].items()))}")

champion = best_l[3]["pnl"] + best_s[2]["pnl"]
print(f"\n  Champion L+S: ${champion:.0f} (vs baseline ${baseline_total:.0f}, delta ${champion - baseline_total:+.0f})")

print("\nDone.")
