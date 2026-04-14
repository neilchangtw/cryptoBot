"""
V13-R6: L 策略深度優化
=======================
R4 發現 S 用 GK(10/30) 大幅改善，但 L 仍用 GK(5/20)。
本輪擴大 L 的搜索空間：
  1. GK 窗口全掃（含 fast=10 + 更寬 TP/MH 範圍）
  2. Session filter 優化（是否有更好的 block hours）
  3. 不同 Breakout 週期
  4. 不同 SafeNet
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
gk_s = pd.Series(gk_raw)
gk_ma = {}
for w in [3, 5, 7, 10, 15, 20, 30]:
    gk_ma[w] = gk_s.rolling(w).mean()

def compute_gk_pctile(fast, slow, pw):
    ratio = gk_ma[fast] / gk_ma[slow]
    return ratio.shift(1).rolling(pw).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1]
    ).values

# Pre-compute key GK pctiles
print("Computing GK pctiles...")
gk_cache = {}
for fast, slow, pw in [(5,20,100), (10,20,100), (10,30,100), (7,20,100), (7,30,100),
                        (5,15,100), (5,30,100), (3,20,100), (3,15,100),
                        (5,20,75), (10,20,75), (10,30,75),
                        (5,20,150), (10,20,150), (10,30,150)]:
    gk_cache[(fast,slow,pw)] = compute_gk_pctile(fast, slow, pw)
print(f"Computed {len(gk_cache)} arrays.")

# Breakout arrays
c_s = pd.Series(c)
bo_up = {}
for p in [10, 12, 15, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])


def backtest_l(gk_p, gk_thresh, bo_period, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, block_hours, block_days, ext=2, be_trail=True):
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25
    bu = bo_up[bo_period]
    pos = None
    trades = []
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
                if ext > 0 and cur_pnl > 0:
                    pos["in_ext"] = True
                    pos["ext_start"] = i
                    if be_trail:
                        pos["be_p"] = ep
                else:
                    ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                net = (ex_p - ep) / ep * NOTIONAL - FEE
                trades.append({"bar": pos["bar"], "pnl": net, "reason": ex_r,
                               "oos": pos["bar"] >= IS_END})
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

    return trades


BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}

# ===== Phase 1: Wide L scan =====
print("\n" + "=" * 60)
print("Phase 1: Wide L GK Window Scan")
print("=" * 60)

results = []
total_configs = 0

for (fast, slow, pw), gk_p in gk_cache.items():
    for gk_t in [20, 25, 30, 35]:
        for bo in [10, 15, 20]:
            for tp in [0.025, 0.03, 0.035, 0.04, 0.045, 0.05]:
                for mh in [4, 5, 6, 7, 8]:
                    for sn in [0.03, 0.035, 0.04]:
                        total_configs += 1
                        tr = backtest_l(gk_p, gk_t, bo, tp, mh, sn, 6, -75, BH, BD)
                        if not tr:
                            continue
                        tdf = pd.DataFrame(tr)
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
                        results.append({
                            "fast": fast, "slow": slow, "pw": pw,
                            "gk": gk_t, "bo": bo, "tp": tp, "mh": mh, "sn": sn,
                            "is_pnl": is_pnl, "is_n": len(is_t),
                            "oos_pnl": oos_pnl, "oos_wr": oos_wr, "oos_n": len(oos_t),
                            "mdd": mdd,
                        })

results.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"Scanned {total_configs} configs, {len(results)} passed IS>0")

print("\nTOP 20 L:")
for r in results[:20]:
    print(f"  GK({r['fast']}/{r['slow']})pw{r['pw']} <{r['gk']} BO{r['bo']} TP{r['tp']:.1%} MH{r['mh']} SN{r['sn']:.1%}"
          f" | IS:{r['is_n']}t ${r['is_pnl']:.0f} | OOS:{r['oos_n']}t ${r['oos_pnl']:.0f} WR{r['oos_wr']:.0f}% MDD${r['mdd']:.0f}")

# Check: which fast windows dominate?
print("\n--- Fast window distribution in TOP 50 ---")
if len(results) >= 50:
    from collections import Counter
    fast_counts = Counter(r["fast"] for r in results[:50])
    for f, cnt in sorted(fast_counts.items()):
        print(f"  fast={f}: {cnt}/50")

# ===== Phase 2: Session Filter Optimization =====
print("\n" + "=" * 60)
print("Phase 2: Session Filter Optimization (using R5 best L)")
print("=" * 60)

# Best L from R5: GK(5/20)pw100 <25 BO15 TP3.5% MH6 SN3.5%
gk_p_520 = gk_cache[(5, 20, 100)]

# Test different hour blocks
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
    tr = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, -75, bh, BD)
    if not tr:
        print(f"  {label}: 0 trades")
        continue
    tdf = pd.DataFrame(tr)
    is_t = tdf[~tdf["oos"]]
    oos_t = tdf[tdf["oos"]]
    is_pnl = is_t["pnl"].sum() if len(is_t) > 0 else 0
    oos_pnl = oos_t["pnl"].sum() if len(oos_t) > 0 else 0
    oos_wr = (oos_t["pnl"] > 0).mean() * 100 if len(oos_t) > 0 else 0
    print(f"  {label:>25}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# Day blocks
day_sets = [
    ({"Monday", "Saturday", "Sunday"}, "V11-E default"),
    ({"Saturday", "Sunday"}, "drop Mon"),
    ({"Monday", "Friday", "Saturday", "Sunday"}, "add Fri"),
    ({"Saturday", "Sunday", "Monday", "Tuesday"}, "add Tue"),
    (set(), "no day filter"),
]

print("\n--- L: Different day blocks ---")
for bd, label in day_sets:
    tr = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, -75, BH, bd)
    if not tr:
        print(f"  {label}: 0 trades")
        continue
    tdf = pd.DataFrame(tr)
    is_t = tdf[~tdf["oos"]]
    oos_t = tdf[tdf["oos"]]
    is_pnl = is_t["pnl"].sum() if len(is_t) > 0 else 0
    oos_pnl = oos_t["pnl"].sum() if len(oos_t) > 0 else 0
    oos_wr = (oos_t["pnl"] > 0).mean() * 100 if len(oos_t) > 0 else 0
    print(f"  {label:>25}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== Phase 3: Cooldown scan =====
print("\n" + "=" * 60)
print("Phase 3: L Cooldown Optimization")
print("=" * 60)

for cd in [3, 4, 5, 6, 7, 8]:
    tr = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, cd, -75, BH, BD)
    if not tr:
        continue
    tdf = pd.DataFrame(tr)
    is_t = tdf[~tdf["oos"]]
    oos_t = tdf[tdf["oos"]]
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()
    oos_wr = (oos_t["pnl"] > 0).mean() * 100
    print(f"  CD={cd}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== Phase 4: Monthly loss limit =====
print("\n" + "=" * 60)
print("Phase 4: L Monthly Loss Limit")
print("=" * 60)

for ml in [-50, -75, -100, -125, -150, -200]:
    tr = backtest_l(gk_p_520, 25, 15, 0.035, 6, 0.035, 6, ml, BH, BD)
    if not tr:
        continue
    tdf = pd.DataFrame(tr)
    is_t = tdf[~tdf["oos"]]
    oos_t = tdf[tdf["oos"]]
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()
    oos_wr = (oos_t["pnl"] > 0).mean() * 100
    print(f"  ML=${ml}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos_t)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== Summary =====
print("\n" + "=" * 60)
print("SUMMARY: Best L vs R5 L")
print("=" * 60)

if results:
    best = results[0]
    print(f"\nR5 L:   GK(5/20)pw100 <25 BO15 TP3.5% MH6 SN3.5% | OOS $1,439")
    print(f"Best L: GK({best['fast']}/{best['slow']})pw{best['pw']} <{best['gk']} BO{best['bo']} TP{best['tp']:.1%} MH{best['mh']} SN{best['sn']:.1%}"
          f" | IS ${best['is_pnl']:.0f} | OOS ${best['oos_pnl']:.0f}")
    print(f"Improvement: ${best['oos_pnl'] - 1439:+.0f}")

    # Combined with R5 S
    combo = best['oos_pnl'] + 2087  # R5 S OOS
    print(f"\nL+S OOS: ${combo:.0f} (R5 was $3,526, V11-E $2,801)")

print("\nDone.")
