"""
V14-R5: Champion Validation
============================
Champion L config: MFE trail (mfe=1.0%, dd=1.0%, mb=1) + Conditional MH (bar2<=-1%, MH->4)
S config: V13 baseline (unchanged)

Validation:
  1. 8-fold walk-forward (train 2 seg, test 1 seg)
  2. Monthly PnL comparison (V13 vs V14)
  3. Parameter robustness (+/- perturbation)
  4. Trade-level diff: which trades changed
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


# ===== Champion L config =====
CHAMP_L_KW = {
    "mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1,
    "check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4,
}

def run_l(start, end, **extra_kw):
    kw = {**CHAMP_L_KW, **extra_kw}
    return backtest("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, start, end, 2, **kw)

def run_l_baseline(start, end):
    return backtest("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, start, end, 2)

def run_s(start, end):
    return backtest("S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S, start, end, 2)


# ===== 1. Walk-forward 8-fold =====
print("\n" + "=" * 70)
print("1. Walk-Forward Validation (8-fold)")
print("=" * 70)

seg = TOTAL // 8
print(f"\n  Total bars: {TOTAL}, segment size: {seg}")
print(f"  8 segments, train on 2, test on 1, 6 test folds\n")

wf_results_champ = []
wf_results_base = []
for fold in range(6):
    ts = (fold + 2) * seg
    te = (fold + 3) * seg
    start_dt = pd.Timestamp(dt[ts]).strftime("%Y-%m-%d")
    end_dt = pd.Timestamp(dt[min(te, TOTAL-1)]).strftime("%Y-%m-%d")

    champ_trades = run_l(ts, te)
    base_trades = run_l_baseline(ts, te)

    champ_pnl = sum(t["pnl"] for t in champ_trades)
    base_pnl = sum(t["pnl"] for t in base_trades)
    wf_results_champ.append(champ_pnl)
    wf_results_base.append(base_pnl)

    print(f"  Fold {fold+1} [{start_dt} ~ {end_dt}]: "
          f"Champion ${champ_pnl:>7.0f} ({len(champ_trades):>2}t) | "
          f"Baseline ${base_pnl:>7.0f} ({len(base_trades):>2}t) | "
          f"delta ${champ_pnl - base_pnl:>+6.0f}")

champ_pass = sum(1 for p in wf_results_champ if p > 0)
base_pass = sum(1 for p in wf_results_base if p > 0)
print(f"\n  Champion WF: {champ_pass}/6 PASS (total ${sum(wf_results_champ):.0f})")
print(f"  Baseline WF: {base_pass}/6 PASS (total ${sum(wf_results_base):.0f})")

# ===== 2. Monthly PnL comparison =====
print("\n" + "=" * 70)
print("2. Monthly PnL Comparison (OOS period)")
print("=" * 70)

champ_l = run_l(0, TOTAL)
base_l = run_l_baseline(0, TOTAL)
base_s = run_s(0, TOTAL)

champ_l_oos = [t for t in champ_l if t["entry_bar"] >= IS_END]
base_l_oos = [t for t in base_l if t["entry_bar"] >= IS_END]
base_s_oos = [t for t in base_s if t["entry_bar"] >= IS_END]

# Monthly breakdown
def monthly_pnl(trades):
    m = {}
    for t in trades:
        mo = t["entry_month"]
        m[mo] = m.get(mo, 0) + t["pnl"]
    return m

champ_monthly = monthly_pnl(champ_l_oos)
base_l_monthly = monthly_pnl(base_l_oos)
base_s_monthly = monthly_pnl(base_s_oos)

all_months = sorted(set(list(champ_monthly.keys()) + list(base_l_monthly.keys()) + list(base_s_monthly.keys())))
oos_months = [m for m in all_months if pd.Timestamp(m + "-01") >= pd.Timestamp(dt[IS_END])]

print(f"\n  {'Month':<10} | {'V13 L':>8} {'V14 L':>8} {'delta':>7} | {'S':>8} | {'V13 L+S':>8} {'V14 L+S':>8} {'delta':>7}")
print("  " + "-" * 90)

v13_total = 0; v14_total = 0
for mo in oos_months:
    bl = base_l_monthly.get(mo, 0)
    cl = champ_monthly.get(mo, 0)
    s = base_s_monthly.get(mo, 0)
    v13_ls = bl + s
    v14_ls = cl + s
    v13_total += v13_ls
    v14_total += v14_ls
    delta_l = cl - bl
    delta_ls = v14_ls - v13_ls
    marker = " **" if abs(delta_l) >= 50 else ""
    print(f"  {mo:<10} | ${bl:>7.0f} ${cl:>7.0f} {delta_l:>+6.0f} | ${s:>7.0f} | ${v13_ls:>7.0f} ${v14_ls:>7.0f} {delta_ls:>+6.0f}{marker}")

print(f"\n  V13 L+S total: ${v13_total:.0f}")
print(f"  V14 L+S total: ${v14_total:.0f}")
print(f"  V13 positive months: {sum(1 for mo in oos_months if base_l_monthly.get(mo,0) + base_s_monthly.get(mo,0) > 0)}/{len(oos_months)}")
print(f"  V14 positive months: {sum(1 for mo in oos_months if champ_monthly.get(mo,0) + base_s_monthly.get(mo,0) > 0)}/{len(oos_months)}")

# ===== 3. Parameter robustness =====
print("\n" + "=" * 70)
print("3. Parameter Robustness (L Champion +/- perturbation)")
print("=" * 70)

perturbations = [
    ("Champion (exact)", CHAMP_L_KW),
    ("mfe_act 0.8%", {**CHAMP_L_KW, "mfe_act": 0.008}),
    ("mfe_act 1.2%", {**CHAMP_L_KW, "mfe_act": 0.012}),
    ("mfe_act 1.5%", {**CHAMP_L_KW, "mfe_act": 0.015}),
    ("trail_dd 0.8%", {**CHAMP_L_KW, "trail_dd": 0.008}),
    ("trail_dd 1.2%", {**CHAMP_L_KW, "trail_dd": 0.012}),
    ("trail_dd 1.5%", {**CHAMP_L_KW, "trail_dd": 0.015}),
    ("min_bar 2", {**CHAMP_L_KW, "min_bar_trail": 2}),
    ("min_bar 3", {**CHAMP_L_KW, "min_bar_trail": 3}),
    ("check_bar 3", {**CHAMP_L_KW, "check_bar": 3}),
    ("exit_thresh -0.5%", {**CHAMP_L_KW, "exit_thresh": -0.005}),
    ("exit_thresh -1.5%", {**CHAMP_L_KW, "exit_thresh": -0.015}),
    ("reduced_mh 3", {**CHAMP_L_KW, "reduced_mh": 3}),
    ("reduced_mh 5", {**CHAMP_L_KW, "reduced_mh": 5}),
    # No MFE trail (R3 only)
    ("R3 only", {"check_bar": 2, "exit_thresh": -0.01, "reduced_mh": 4}),
    # No conditional MH (R2 only)
    ("R2 only", {"mfe_act": 0.01, "trail_dd": 0.01, "min_bar_trail": 1}),
]

print(f"\n  {'Config':<30} | {'IS':>7} {'OOS':>7} {'delta':>7} {'WR':>4} {'MDD':>5} | WF")
print("  " + "-" * 80)

baseline_oos_pnl = eval_trades(base_l_oos)["pnl"]

for label, kw in perturbations:
    trades = backtest("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, 0, TOTAL, 2, **kw)
    is_t = [t for t in trades if t["entry_bar"] < IS_END]
    oos_t = [t for t in trades if t["entry_bar"] >= IS_END]
    is_m = eval_trades(is_t)
    oos_m = eval_trades(oos_t)
    # WF
    seg8 = TOTAL // 8
    wf_pass = 0
    for fold in range(6):
        ts = (fold + 2) * seg8
        te = (fold + 3) * seg8
        tt = backtest("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, ts, te, 2, **kw)
        if sum(t["pnl"] for t in tt) > 0:
            wf_pass += 1
    delta = oos_m["pnl"] - baseline_oos_pnl
    print(f"  {label:<30} | ${is_m['pnl']:>6.0f} ${oos_m['pnl']:>6.0f} {delta:>+6.0f} {oos_m['wr']:>3.0f}% ${oos_m['mdd']:>4.0f} | {wf_pass}/6")

# ===== 4. Trade-level diff =====
print("\n" + "=" * 70)
print("4. Trade-Level Diff (OOS: V14 Champion vs V13 Baseline)")
print("=" * 70)

# Match by entry_bar
base_by_bar = {t["entry_bar"]: t for t in base_l_oos}
champ_by_bar = {t["entry_bar"]: t for t in champ_l_oos}

# Trades that changed exit reason or PnL
changed = []
for bar in sorted(set(list(base_by_bar.keys()) + list(champ_by_bar.keys()))):
    bt = base_by_bar.get(bar)
    ct = champ_by_bar.get(bar)
    if bt and ct:
        if bt["reason"] != ct["reason"] or abs(bt["pnl"] - ct["pnl"]) > 1:
            changed.append((bar, bt, ct))
    elif bt and not ct:
        changed.append((bar, bt, None))
    elif ct and not bt:
        changed.append((bar, None, ct))

print(f"\n  Total changed trades: {len(changed)}")
print(f"\n  {'Entry Date':<20} | {'V13 exit':>10} {'V13 PnL':>8} | {'V14 exit':>10} {'V14 PnL':>8} | {'delta':>8}")
print("  " + "-" * 85)

total_delta = 0
for bar, bt, ct in changed[:40]:
    v13_r = bt["reason"] if bt else "---"
    v13_p = bt["pnl"] if bt else 0
    v14_r = ct["reason"] if ct else "---"
    v14_p = ct["pnl"] if ct else 0
    delta = v14_p - v13_p
    total_delta += delta
    entry_dt = bt["entry_dt"] if bt else ct["entry_dt"]
    print(f"  {entry_dt:<20} | {v13_r:>10} ${v13_p:>7.0f} | {v14_r:>10} ${v14_p:>7.0f} | ${delta:>+7.0f}")

if len(changed) > 40:
    print(f"  ... and {len(changed) - 40} more trades")
print(f"\n  Total delta from changed trades: ${total_delta:.0f}")

# Summary of changes by exit reason transition
print("\n  Exit reason transitions:")
transitions = {}
for _, bt, ct in changed:
    key = f"{bt['reason'] if bt else 'NEW'} -> {ct['reason'] if ct else 'GONE'}"
    if key not in transitions:
        transitions[key] = {"count": 0, "delta": 0}
    transitions[key]["count"] += 1
    transitions[key]["delta"] += (ct["pnl"] if ct else 0) - (bt["pnl"] if bt else 0)

for key in sorted(transitions, key=lambda k: transitions[k]["delta"], reverse=True):
    t = transitions[key]
    print(f"    {key:<25}: {t['count']:>3}t  delta ${t['delta']:>+7.0f}")

# ===== Final Summary =====
print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)

champ_is = eval_trades([t for t in champ_l if t["entry_bar"] < IS_END])
champ_oos = eval_trades(champ_l_oos)
base_is = eval_trades([t for t in base_l if t["entry_bar"] < IS_END])
base_oos = eval_trades(base_l_oos)
s_oos = eval_trades(base_s_oos)

print(f"""
  V14 Champion L: MFE trail (1.0%/1.0%/mb1) + Conditional MH (bar2<=-1% -> MH4)
  S: V13 baseline (unchanged)

  L comparison:
    Metric       |   V13    |   V14    | delta
    IS PnL       | ${base_is['pnl']:>7.0f}  | ${champ_is['pnl']:>7.0f}  | ${champ_is['pnl'] - base_is['pnl']:>+6.0f}
    OOS PnL      | ${base_oos['pnl']:>7.0f}  | ${champ_oos['pnl']:>7.0f}  | ${champ_oos['pnl'] - base_oos['pnl']:>+6.0f}
    OOS WR       |   {base_oos['wr']:>4.0f}%  |   {champ_oos['wr']:>4.0f}%  | {champ_oos['wr'] - base_oos['wr']:>+5.0f}%
    OOS MDD      | ${base_oos['mdd']:>7.0f}  | ${champ_oos['mdd']:>7.0f}  | ${champ_oos['mdd'] - base_oos['mdd']:>+6.0f}
    OOS worst mo | ${base_oos['worst_mo']:>7.0f}  | ${champ_oos['worst_mo']:>7.0f}  | ${champ_oos['worst_mo'] - base_oos['worst_mo']:>+6.0f}
    WF (8-fold)  |   {base_pass}/6    |   {champ_pass}/6    |

  L+S combined (OOS):
    V13: ${base_oos['pnl'] + s_oos['pnl']:.0f}  (L ${base_oos['pnl']:.0f} + S ${s_oos['pnl']:.0f})
    V14: ${champ_oos['pnl'] + s_oos['pnl']:.0f}  (L ${champ_oos['pnl']:.0f} + S ${s_oos['pnl']:.0f})
    delta: ${champ_oos['pnl'] - base_oos['pnl']:+.0f} ({(champ_oos['pnl'] - base_oos['pnl']) / base_oos['pnl'] * 100:+.1f}% L improvement)
""")

print("Done.")
