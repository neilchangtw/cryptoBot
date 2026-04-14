"""
V13-R6: L 策略深度優化（向量化版本）
用 numpy 替代 pandas lambda 加速 100x
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
    """Fast rolling mean using cumsum."""
    cs = np.cumsum(np.insert(arr, 0, 0))
    rm = (cs[w:] - cs[:-w]) / w
    result = np.full(len(arr), np.nan)
    result[w-1:] = rm
    return result


def fast_rolling_pctile(arr, window):
    """Fast rolling percentile without pandas lambda."""
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        w = arr[i - window + 1:i + 1]
        valid_mask = ~np.isnan(w)
        if valid_mask.sum() < window // 2:
            continue
        valid = w[valid_mask]
        result[i] = np.sum(valid <= valid[-1]) / len(valid)
    return result


def compute_gk_pctile_fast(fast_w, slow_w, pctile_w):
    """Compute GK pctile using fast numpy methods."""
    gk_ma_fast = rolling_mean(gk_raw, fast_w)
    gk_ma_slow = rolling_mean(gk_raw, slow_w)
    ratio = gk_ma_fast / gk_ma_slow
    # shift(1)
    ratio_shifted = np.full(len(ratio), np.nan)
    ratio_shifted[1:] = ratio[:-1]
    return fast_rolling_pctile(ratio_shifted, pctile_w)


# Pre-compute GK pctile arrays
print("Computing GK pctiles (fast)...")
gk_cache = {}
combos = [
    (5,20,100), (10,20,100), (10,30,100), (7,20,100), (7,30,100),
    (5,15,100), (5,30,100), (3,20,100), (3,15,100),
    (5,20,75), (10,20,75), (10,30,75),
    (5,20,150), (10,20,150), (10,30,150),
]
for i, (f, s, p) in enumerate(combos):
    gk_cache[(f, s, p)] = compute_gk_pctile_fast(f, s, p)
    print(f"  [{i+1}/{len(combos)}] GK({f}/{s})pw{p} done")
print("All done.")

# Breakout arrays
c_s = pd.Series(c)
bo_up = {}
for p in [10, 12, 15, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}


def backtest_l(gk_p, gk_thresh, bo_period, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, block_hours, block_days, ext=2, be_trail=True):
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25
    bu = bo_up[bo_period]
    pos = None
    trades_is_pnl = 0.0
    trades_oos_pnl = 0.0
    trades_is_n = 0
    trades_oos_n = 0
    trades_oos_wins = 0
    trades_oos_pnls = []
    last_exit = -999
    daily_pnl = {}
    monthly_pnl = {}
    monthly_entries = {}
    consec_losses = 0
    consec_pause = -1

    for i in range(200, TOTAL - 1):
        bar_dt = pd.Timestamp(dt[i])
        dk = bar_dt.strftime("%Y-%m-%d")
        mk = bar_dt.strftime("%Y-%m")
        daily_pnl.setdefault(dk, 0.0)
        monthly_pnl.setdefault(mk, 0.0)
        monthly_entries.setdefault(mk, 0)

        if pos is not None:
            ep = pos["ep"]
            held = i - pos["bar"]
            in_ext = pos.get("in_ext", False)
            be_p = pos.get("be_p", None)

            hit_tp = (h[i] - ep) / ep >= tp_pct
            hit_sl = (l[i] - ep) / ep <= -safenet_pct
            cur_pnl = (c[i] - ep) / ep
            tp_exit = ep * (1 + tp_pct)
            sl_exit = ep * (1 - safenet_pct * (1 + SLIP))
            hit_be = be_p is not None and l[i] <= be_p

            ex_p = None
            if hit_sl:
                ex_p = sl_exit
            elif hit_tp:
                ex_p = tp_exit
            elif in_ext:
                ext_held = i - pos["ext_start"]
                if hit_be:
                    ex_p = be_p
                elif ext_held >= ext:
                    ex_p = c[i]
            elif held >= maxhold:
                if ext > 0 and cur_pnl > 0:
                    pos["in_ext"] = True
                    pos["ext_start"] = i
                    if be_trail:
                        pos["be_p"] = ep
                else:
                    ex_p = c[i]

            if ex_p is not None:
                net = (ex_p - ep) / ep * NOTIONAL - FEE
                if pos["bar"] >= IS_END:
                    trades_oos_pnl += net
                    trades_oos_n += 1
                    if net > 0:
                        trades_oos_wins += 1
                    trades_oos_pnls.append(net)
                else:
                    trades_is_pnl += net
                    trades_is_n += 1
                daily_pnl[dk] += net
                monthly_pnl[mk] += net
                last_exit = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
                    consec_pause = i + 24
                pos = None

        if pos is not None:
            continue
        if i - last_exit < cooldown:
            continue
        if i < consec_pause:
            continue
        if daily_pnl.get(dk, 0) <= -200:
            continue
        if monthly_pnl.get(mk, 0) <= monthly_loss:
            continue
        if monthly_entries.get(mk, 0) >= 20:
            continue
        if hours[i] in block_hours or days[i] in block_days:
            continue

        gk_val = gk_p[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh / 100.0:
            continue

        if bu[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[mk] += 1

    oos_wr = (trades_oos_wins / trades_oos_n * 100) if trades_oos_n > 0 else 0
    # MDD
    mdd = 0
    if trades_oos_pnls:
        cum = np.cumsum(trades_oos_pnls)
        mdd = np.min(cum - np.maximum.accumulate(cum))

    return {
        "is_pnl": trades_is_pnl, "is_n": trades_is_n,
        "oos_pnl": trades_oos_pnl, "oos_n": trades_oos_n, "oos_wr": oos_wr,
        "mdd": mdd,
    }


# ===== Phase 1: Wide L scan =====
print("\n" + "=" * 60)
print("Phase 1: Wide L GK Window Scan")
print("=" * 60)

results = []
total_configs = 0

for key, gk_p in gk_cache.items():
    fast, slow, pw = key
    for gk_t in [20, 25, 30, 35]:
        for bo in [10, 15, 20]:
            for tp in [0.025, 0.03, 0.035, 0.04, 0.045, 0.05]:
                for mh in [4, 5, 6, 7, 8]:
                    for sn in [0.03, 0.035, 0.04]:
                        total_configs += 1
                        r = backtest_l(gk_p, gk_t, bo, tp, mh, sn, 6, -75, BH, BD)
                        if r["is_n"] < 5 or r["oos_n"] < 5:
                            continue
                        if r["is_pnl"] <= 0:
                            continue
                        results.append({
                            "fast": fast, "slow": slow, "pw": pw,
                            "gk": gk_t, "bo": bo, "tp": tp, "mh": mh, "sn": sn,
                            **r,
                        })

results.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"Scanned {total_configs} configs, {len(results)} passed IS>0")

print("\nTOP 20 L:")
for r in results[:20]:
    print(f"  GK({r['fast']}/{r['slow']})pw{r['pw']} <{r['gk']} BO{r['bo']} TP{r['tp']:.1%} MH{r['mh']} SN{r['sn']:.1%}"
          f" | IS:{r['is_n']}t ${r['is_pnl']:.0f} | OOS:{r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}% MDD${r['mdd']:.0f}")

# Fast window distribution
print("\n--- Fast window distribution in TOP 50 ---")
if len(results) >= 50:
    from collections import Counter
    fast_counts = Counter(r["fast"] for r in results[:50])
    for f, cnt in sorted(fast_counts.items()):
        print(f"  fast={f}: {cnt}/50")

# ===== Phase 2: Session Filter Optimization =====
print("\n" + "=" * 60)
print("Phase 2: Session Filter Optimization")
print("=" * 60)

gk_p_520 = gk_cache[(5, 20, 100)]

hour_sets = [
    ({0, 1, 2, 12}, "V11-E default"),
    ({0, 1, 2}, "drop 12"),
    ({0, 1, 2, 12, 13}, "add 13"),
    ({0, 1, 2, 11, 12}, "add 11"),
    ({0, 1, 2, 11, 12, 13}, "add 11,13"),
    ({12, 13, 14, 15}, "Asian afternoon"),
    ({0, 1, 2, 12, 13, 14}, "add 13,14"),
    (set(), "no hour filter"),
    ({0, 1, 2, 12, 17, 18}, "add 17,18"),
]

print("\n--- L: Different hour blocks ---")
for bh, label in hour_sets:
    r = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, -75, bh, BD)
    print(f"  {label:>25}: IS {r['is_n']}t ${r['is_pnl']:.0f} | OOS {r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}%")

day_sets = [
    ({"Monday", "Saturday", "Sunday"}, "V11-E default"),
    ({"Saturday", "Sunday"}, "drop Mon"),
    ({"Monday", "Friday", "Saturday", "Sunday"}, "add Fri"),
    (set(), "no day filter"),
]

print("\n--- L: Different day blocks ---")
for bd, label in day_sets:
    r = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, -75, BH, bd)
    print(f"  {label:>25}: IS {r['is_n']}t ${r['is_pnl']:.0f} | OOS {r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}%")

# ===== Phase 3: Cooldown & Monthly Loss =====
print("\n" + "=" * 60)
print("Phase 3: Cooldown & Monthly Loss")
print("=" * 60)

print("\n--- Cooldown ---")
for cd in [3, 4, 5, 6, 7, 8]:
    r = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, cd, -75, BH, BD)
    print(f"  CD={cd}: IS {r['is_n']}t ${r['is_pnl']:.0f} | OOS {r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}%")

print("\n--- Monthly Loss ---")
for ml in [-50, -75, -100, -125, -150, -200]:
    r = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, ml, BH, BD)
    print(f"  ML=${ml}: IS {r['is_n']}t ${r['is_pnl']:.0f} | OOS {r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}%")

# ===== Summary =====
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

if results:
    best = results[0]
    print(f"\nR5 L:   GK(5/20)pw100 <25 BO15 TP3.5% MH6 SN3.5% | OOS $1,439")
    print(f"Best L: GK({best['fast']}/{best['slow']})pw{best['pw']} <{best['gk']} BO{best['bo']} TP{best['tp']:.1%} MH{best['mh']} SN{best['sn']:.1%}"
          f" | IS ${best['is_pnl']:.0f} | OOS ${best['oos_pnl']:.0f} WR{best['oos_wr']:.0f}%")
    print(f"Improvement: ${best['oos_pnl'] - 1439:+.0f}")
    combo = best['oos_pnl'] + 2087
    print(f"\nL+S OOS: ${combo:.0f} (R5 was $3,526, V11-E $2,801)")

print("\nDone.")
