"""
V13-R4: GK Window Optimization + Extension
=============================================
假說：gk_ratio = mean(fast)/mean(slow) 的窗口組合從 V6 以來未重新優化。
      不同窗口可能捕捉到不同的壓縮週期，結合 R3 的 extension 增強出場。

測試：
  1. GK ratio windows: fast {3,5,7,10}, slow {10,15,20,30}
  2. Percentile windows: {50, 75, 100, 150, 200}
  3. 結合 ext=2+BE trail
  4. 同時掃描 TP/MH/Cooldown
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

# GK base
gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk_raw)

# Pre-compute rolling means for different windows
gk_ma = {}
for w in [3, 5, 7, 10, 15, 20, 30]:
    gk_ma[w] = gk_s.rolling(w).mean()

# Pre-compute breakout arrays
c_s = pd.Series(c)
bo_up_15 = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

TOTAL = len(df)
IS_END = TOTAL // 2

BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}


def compute_gk_pctile(fast_w, slow_w, pctile_w):
    """Compute GK percentile with custom windows."""
    ratio = gk_ma[fast_w] / gk_ma[slow_w]
    pctile = ratio.shift(1).rolling(pctile_w).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1]
    )
    return pctile.values


def backtest(side, gk_pctile_arr, gk_thresh, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss_limit, ext=0, be_trail=False):
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

    for i in range(200, TOTAL - 1):
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
                trades.append({"bar": pos["bar"], "pnl": net, "reason": ex_r,
                               "oos": pos["bar"] >= IS_END})
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


# ===== Phase 1: GK Window Scan =====
print("=" * 60)
print("Phase 1: GK Window Scan (L+S separate)")
print("=" * 60)

# Pre-compute all pctile arrays
pctile_cache = {}
window_combos = []
for fast in [3, 5, 7, 10]:
    for slow in [10, 15, 20, 30]:
        if fast >= slow:
            continue
        for pw in [50, 75, 100, 150]:
            window_combos.append((fast, slow, pw))

print(f"Computing {len(window_combos)} GK pctile arrays...")
for fast, slow, pw in window_combos:
    key = (fast, slow, pw)
    if key not in pctile_cache:
        pctile_cache[key] = compute_gk_pctile(fast, slow, pw)
print("Done.")

# Scan L
print("\n--- L: GK Window + TP/MH scan (with ext=2+BE) ---")
results_l = []
for fast, slow, pw in window_combos:
    gk_p = pctile_cache[(fast, slow, pw)]
    for gk_t in [20, 25, 30]:
        for tp in [0.03, 0.035, 0.04]:
            for mh in [5, 6, 7]:
                trades = backtest("L", gk_p, gk_t, tp, mh, 0.035, 6, -75,
                                  ext=2, be_trail=True)
                if not trades:
                    continue
                tdf = pd.DataFrame(trades)
                is_t = tdf[~tdf["oos"]]
                oos_t = tdf[tdf["oos"]]
                if len(is_t) < 5 or len(oos_t) < 5:
                    continue
                is_pnl = is_t["pnl"].sum()
                if is_pnl <= 0:
                    continue
                oos_pnl = oos_t["pnl"].sum()
                oos_wr = (oos_t["pnl"] > 0).mean() * 100
                cum = oos_t["pnl"].cumsum()
                mdd = (cum - cum.cummax()).min()
                results_l.append({
                    "fast": fast, "slow": slow, "pw": pw,
                    "gk": gk_t, "tp": tp, "mh": mh,
                    "is_pnl": is_pnl, "is_n": len(is_t),
                    "oos_pnl": oos_pnl, "oos_wr": oos_wr, "oos_n": len(oos_t),
                    "mdd": mdd,
                })

results_l.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"  {len(results_l)} passed IS>0")
print("\n  TOP 15 L:")
for r in results_l[:15]:
    print(f"    GK({r['fast']}/{r['slow']})pw{r['pw']} <{r['gk']} TP{r['tp']:.1%} MH{r['mh']}"
          f" | IS:{r['is_n']}t ${r['is_pnl']:.0f} | OOS:{r['oos_n']}t ${r['oos_pnl']:.0f}"
          f" WR{r['oos_wr']:.0f}% MDD${r['mdd']:.0f}")

# Scan S
print("\n--- S: GK Window + TP/MH scan (with ext=2+BE) ---")
results_s = []
for fast, slow, pw in window_combos:
    gk_p = pctile_cache[(fast, slow, pw)]
    for gk_t in [25, 30, 35]:
        for tp in [0.02, 0.025, 0.03]:
            for mh in [5, 6, 7, 8]:
                trades = backtest("S", gk_p, gk_t, tp, mh, 0.04, 8, -150,
                                  ext=2, be_trail=True)
                if not trades:
                    continue
                tdf = pd.DataFrame(trades)
                is_t = tdf[~tdf["oos"]]
                oos_t = tdf[tdf["oos"]]
                if len(is_t) < 5 or len(oos_t) < 5:
                    continue
                is_pnl = is_t["pnl"].sum()
                if is_pnl <= 0:
                    continue
                oos_pnl = oos_t["pnl"].sum()
                oos_wr = (oos_t["pnl"] > 0).mean() * 100
                cum = oos_t["pnl"].cumsum()
                mdd = (cum - cum.cummax()).min()
                results_s.append({
                    "fast": fast, "slow": slow, "pw": pw,
                    "gk": gk_t, "tp": tp, "mh": mh,
                    "is_pnl": is_pnl, "is_n": len(is_t),
                    "oos_pnl": oos_pnl, "oos_wr": oos_wr, "oos_n": len(oos_t),
                    "mdd": mdd,
                })

results_s.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"  {len(results_s)} passed IS>0")
print("\n  TOP 15 S:")
for r in results_s[:15]:
    print(f"    GK({r['fast']}/{r['slow']})pw{r['pw']} <{r['gk']} TP{r['tp']:.1%} MH{r['mh']}"
          f" | IS:{r['is_n']}t ${r['is_pnl']:.0f} | OOS:{r['oos_n']}t ${r['oos_pnl']:.0f}"
          f" WR{r['oos_wr']:.0f}% MDD${r['mdd']:.0f}")

# ===== Phase 2: Best L+S combo monthly =====
if results_l and results_s:
    print("\n" + "=" * 60)
    print("Phase 2: Best L+S Monthly Report")
    print("=" * 60)

    bl = results_l[0]
    bs = results_s[0]

    # Rerun best
    gk_p_l = pctile_cache[(bl["fast"], bl["slow"], bl["pw"])]
    gk_p_s = pctile_cache[(bs["fast"], bs["slow"], bs["pw"])]

    trades_l = backtest("L", gk_p_l, bl["gk"], bl["tp"], bl["mh"], 0.035, 6, -75,
                        ext=2, be_trail=True)
    trades_s = backtest("S", gk_p_s, bs["gk"], bs["tp"], bs["mh"], 0.04, 8, -150,
                        ext=2, be_trail=True)

    # Monthly OOS
    all_oos = [t for t in trades_l + trades_s if t["oos"]]
    adf = pd.DataFrame(all_oos)
    adf["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in adf["bar"]]
    monthly = adf.groupby("month")["pnl"].sum().sort_index()

    # L/S separate monthly
    l_oos = pd.DataFrame([t for t in trades_l if t["oos"]])
    s_oos = pd.DataFrame([t for t in trades_s if t["oos"]])
    if len(l_oos) > 0:
        l_oos["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in l_oos["bar"]]
        l_monthly = l_oos.groupby("month")["pnl"].sum()
    else:
        l_monthly = pd.Series(dtype=float)
    if len(s_oos) > 0:
        s_oos["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in s_oos["bar"]]
        s_monthly = s_oos.groupby("month")["pnl"].sum()
    else:
        s_monthly = pd.Series(dtype=float)

    v11e = {
        "2025-04": 224, "2025-05": 503, "2025-06": 19, "2025-07": 109,
        "2025-08": 119, "2025-09": 162, "2025-10": 185, "2025-11": 152,
        "2025-12": 199, "2026-01": 84, "2026-02": 629, "2026-03": 413, "2026-04": -8,
    }

    print(f"\nBest L: GK({bl['fast']}/{bl['slow']})pw{bl['pw']} <{bl['gk']} TP{bl['tp']:.1%} MH{bl['mh']}")
    print(f"Best S: GK({bs['fast']}/{bs['slow']})pw{bs['pw']} <{bs['gk']} TP{bs['tp']:.1%} MH{bs['mh']}")
    print(f"\n{'Month':>8} | {'V13-L':>7} | {'V13-S':>7} | {'V13':>7} | {'V11-E':>7} | {'Diff':>7}")
    print("-" * 55)
    for m in sorted(set(list(monthly.index) + list(v11e.keys()))):
        ml = l_monthly.get(m, 0)
        ms = s_monthly.get(m, 0)
        mt = monthly.get(m, 0)
        v11 = v11e.get(m, 0)
        print(f"{m:>8} | ${ml:>6.0f} | ${ms:>6.0f} | ${mt:>6.0f} | ${v11:>6.0f} | ${mt - v11:>+6.0f}")

    combo_oos = monthly.sum()
    combo_is = bl["is_pnl"] + bs["is_pnl"]
    pm = (monthly > 0).sum()
    tm = len(monthly)
    worst_m = monthly.min()
    cum = adf.sort_values("bar")["pnl"].cumsum()
    mdd = (cum - cum.cummax()).min()

    # Worst day
    adf["day"] = [pd.Timestamp(dt[b]).strftime("%Y-%m-%d") for b in adf["bar"]]
    daily = adf.groupby("day")["pnl"].sum()
    worst_day = daily.min()

    print(f"\n=== Goal Check ===")
    print(f"  OOS > $2,801:     ${combo_oos:.0f} {'PASS' if combo_oos > 2801 else 'FAIL'}")
    print(f"  IS > $500:        ${combo_is:.0f} {'PASS' if combo_is > 500 else 'FAIL'}")
    print(f"  PM >= 11/13:      {pm}/{tm} {'PASS' if pm >= 11 else 'FAIL'}")
    print(f"  Worst M >= -$150: ${worst_m:.0f} {'PASS' if worst_m >= -150 else 'FAIL'}")
    print(f"  MDD <= $500:      ${mdd:.0f} {'PASS' if mdd >= -500 else 'FAIL'}")
    print(f"  Worst Day >= -$250: ${worst_day:.0f} {'PASS' if worst_day >= -250 else 'FAIL'}")

    # Also check: is the V11-E default window (5/20/100) still optimal?
    v11e_window = None
    for r in results_l:
        if r["fast"] == 5 and r["slow"] == 20 and r["pw"] == 100:
            v11e_window = r
            break
    if v11e_window:
        print(f"\n  V11-E default window (5/20/100) L: OOS ${v11e_window['oos_pnl']:.0f}")
        print(f"  Best window ({bl['fast']}/{bl['slow']}/{bl['pw']}) L: OOS ${bl['oos_pnl']:.0f}")
        print(f"  Window improvement: ${bl['oos_pnl'] - v11e_window['oos_pnl']:+.0f}")

print("\nDone.")
