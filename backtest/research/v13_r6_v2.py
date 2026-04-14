"""
V13-R6 v2: L 策略深度優化（極速版）
pre-compute 所有 datetime 字串，避免熱迴圈內的 pd.Timestamp 呼叫
"""
import pandas as pd
import numpy as np
import sys

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2

# Pre-compute datetime strings (avoids pd.Timestamp in hot loop)
day_keys = np.array([str(pd.Timestamp(d).strftime("%Y-%m-%d")) for d in dt])
month_keys = np.array([str(pd.Timestamp(d).strftime("%Y-%m")) for d in dt])
hours_arr = pd.to_datetime(dt).hour.values
days_arr = np.array([pd.Timestamp(d).day_name() for d in dt])

# GK
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

# Breakout
c_s = pd.Series(c)
bo_up = {}
for p in [10, 15, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values

# Block sets (use arrays for faster lookup)
BH_SET = {0, 1, 2, 12}
BD_SET = {"Monday", "Saturday", "Sunday"}

print("Pre-computing GK pctiles...", flush=True)
gk_cache = {}
combos = [(5,20,100), (10,20,100), (10,30,100), (7,20,100), (7,30,100),
          (5,15,100), (5,30,100), (3,20,100),
          (5,20,75), (10,20,75), (10,30,75)]
for idx, (f, s, p) in enumerate(combos):
    gk_cache[(f,s,p)] = compute_gk_pctile(f, s, p)
    print(f"  [{idx+1}/{len(combos)}] GK({f}/{s})pw{p}", flush=True)
print("Done.", flush=True)


def backtest_l(gk_p, gk_thresh_100, bo_arr, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, ext=2):
    """Ultra-fast L backtest. Returns (is_pnl, is_n, oos_pnl, oos_n, oos_wr, mdd)."""
    NOTIONAL = 4000
    SLIP_FACTOR = 1.25  # 1 + 0.25

    in_pos = False
    ep = 0.0
    entry_bar = 0
    in_ext = False
    ext_start = 0
    be_p = 0.0

    is_pnl = 0.0; is_n = 0
    oos_pnl = 0.0; oos_n = 0; oos_wins = 0
    oos_pnls = []
    last_exit = -999

    # Use arrays for daily/monthly tracking (simpler than dict for speed)
    cur_day = ""
    cur_month = ""
    day_pnl = 0.0
    month_pnl = 0.0
    month_entries = 0
    consec_losses = 0
    consec_pause = -1

    for i in range(200, TOTAL - 1):
        dk = day_keys[i]
        mk = month_keys[i]

        # Day/month rollover
        if dk != cur_day:
            cur_day = dk
            day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk
            month_pnl = 0.0
            month_entries = 0

        # Exit
        if in_pos:
            held = i - entry_bar
            pnl_pct = (c[i] - ep) / ep

            hit_tp = (h[i] - ep) / ep >= tp_pct
            hit_sl = (l[i] - ep) / ep <= -safenet_pct

            ex_p = 0.0
            exited = False

            if hit_sl:
                ex_p = ep * (1 - safenet_pct * SLIP_FACTOR)
                exited = True
            elif hit_tp:
                ex_p = ep * (1 + tp_pct)
                exited = True
            elif in_ext:
                ext_held = i - ext_start
                if l[i] <= be_p:
                    ex_p = be_p
                    exited = True
                elif ext_held >= ext:
                    ex_p = c[i]
                    exited = True
            elif held >= maxhold:
                if pnl_pct > 0 and ext > 0:
                    in_ext = True
                    ext_start = i
                    be_p = ep
                else:
                    ex_p = c[i]
                    exited = True

            if exited:
                net = (ex_p - ep) / ep * NOTIONAL - 4.0
                if entry_bar >= IS_END:
                    oos_pnl += net
                    oos_n += 1
                    if net > 0:
                        oos_wins += 1
                    oos_pnls.append(net)
                else:
                    is_pnl += net
                    is_n += 1
                day_pnl += net
                month_pnl += net
                last_exit = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= 4:
                        consec_pause = i + 24
                else:
                    consec_losses = 0
                in_pos = False
                in_ext = False

        # Entry
        if in_pos:
            continue
        if i - last_exit < cooldown:
            continue
        if i < consec_pause:
            continue
        if day_pnl <= -200:
            continue
        if month_pnl <= monthly_loss:
            continue
        if month_entries >= 20:
            continue
        if hours_arr[i] in BH_SET or days_arr[i] in BD_SET:
            continue

        gk_val = gk_p[i]
        if gk_val != gk_val or gk_val >= gk_thresh_100:  # NaN check + threshold
            continue

        if bo_arr[i]:
            ep = o[i + 1]
            entry_bar = i
            in_pos = True
            in_ext = False
            month_entries += 1

    wr = (oos_wins / oos_n * 100) if oos_n > 0 else 0
    mdd = 0.0
    if oos_pnls:
        cum = np.cumsum(oos_pnls)
        mdd = float(np.min(cum - np.maximum.accumulate(cum)))

    return is_pnl, is_n, oos_pnl, oos_n, wr, mdd


# ===== Phase 1: Wide scan =====
print("\n" + "=" * 60, flush=True)
print("Phase 1: Wide L scan", flush=True)
print("=" * 60, flush=True)

results = []
total = 0
for key, gk_p in gk_cache.items():
    fast, slow, pw = key
    for gk_t in [20, 25, 30, 35]:
        gk_t100 = gk_t / 100.0
        for bo_p in [10, 15, 20]:
            bo_arr = bo_up[bo_p]
            for tp in [0.025, 0.03, 0.035, 0.04, 0.045, 0.05]:
                for mh in [4, 5, 6, 7, 8]:
                    for sn in [0.03, 0.035, 0.04]:
                        total += 1
                        is_pnl, is_n, oos_pnl, oos_n, wr, mdd = backtest_l(
                            gk_p, gk_t100, bo_arr, tp, mh, sn, 6, -75)
                        if is_n < 5 or oos_n < 5 or is_pnl <= 0:
                            continue
                        results.append((oos_pnl, fast, slow, pw, gk_t, bo_p, tp, mh, sn,
                                       is_pnl, is_n, oos_n, wr, mdd))

results.sort(reverse=True)
print(f"Scanned {total} configs, {len(results)} passed IS>0", flush=True)

print(f"\nTOP 20 L:", flush=True)
for r in results[:20]:
    print(f"  GK({r[1]}/{r[2]})pw{r[3]} <{r[4]} BO{r[5]} TP{r[6]:.1%} MH{r[7]} SN{r[8]:.1%}"
          f" | IS:{r[10]}t ${r[9]:.0f} | OOS:{r[11]}t ${r[0]:.0f} WR{r[12]:.0f}% MDD${r[13]:.0f}",
          flush=True)

# Fast window distribution
if len(results) >= 50:
    from collections import Counter
    print("\n--- Fast window in TOP 50 ---", flush=True)
    fc = Counter(r[1] for r in results[:50])
    for f, cnt in sorted(fc.items()):
        print(f"  fast={f}: {cnt}/50", flush=True)

# ===== Phase 2: Session Filter =====
print(f"\n{'='*60}", flush=True)
print("Phase 2: Session Filter", flush=True)
print(f"{'='*60}", flush=True)

gk_520 = gk_cache[(5, 20, 100)]

# Hour blocks
for label, bh in [("V11-E {0,1,2,12}", {0,1,2,12}), ("drop 12", {0,1,2}),
                   ("add 13", {0,1,2,12,13}), ("add 11", {0,1,2,11,12}),
                   ("none", set()), ("add 17,18", {0,1,2,12,17,18})]:
    # Need to temporarily change BH_SET
    old_bh = BH_SET.copy()
    BH_SET.clear()
    BH_SET.update(bh)
    is_p, is_n, oos_p, oos_n, wr, _ = backtest_l(gk_520, 0.25, bo_up[15], 0.035, 6, 0.035, 6, -75)
    BH_SET.clear()
    BH_SET.update(old_bh)
    print(f"  {label:>25}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}%", flush=True)

# Day blocks
for label, bd in [("V11-E Mon,Sat,Sun", {"Monday","Saturday","Sunday"}),
                   ("Sat,Sun only", {"Saturday","Sunday"}),
                   ("add Fri", {"Monday","Friday","Saturday","Sunday"}),
                   ("none", set())]:
    old_bd = BD_SET.copy()
    BD_SET.clear()
    BD_SET.update(bd)
    is_p, is_n, oos_p, oos_n, wr, _ = backtest_l(gk_520, 0.25, bo_up[15], 0.035, 6, 0.035, 6, -75)
    BD_SET.clear()
    BD_SET.update(old_bd)
    print(f"  {label:>25}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}%", flush=True)

# ===== Phase 3: Cooldown & Monthly Loss =====
print(f"\n{'='*60}", flush=True)
print("Phase 3: CD & Monthly Loss", flush=True)
print(f"{'='*60}", flush=True)

for cd in [3, 4, 5, 6, 7, 8]:
    is_p, is_n, oos_p, oos_n, wr, _ = backtest_l(gk_520, 0.25, bo_up[15], 0.035, 6, 0.035, cd, -75)
    print(f"  CD={cd}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}%", flush=True)

for ml in [-50, -75, -100, -150, -200]:
    is_p, is_n, oos_p, oos_n, wr, _ = backtest_l(gk_520, 0.25, bo_up[15], 0.035, 6, 0.035, 6, ml)
    print(f"  ML=${ml}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}%", flush=True)

# Summary
print(f"\n{'='*60}", flush=True)
print("SUMMARY", flush=True)
print(f"{'='*60}", flush=True)

if results:
    best = results[0]
    print(f"\nR5 L:   GK(5/20)pw100 <25 BO15 TP3.5% MH6 SN3.5% | OOS $1,439", flush=True)
    print(f"Best L: GK({best[1]}/{best[2]})pw{best[3]} <{best[4]} BO{best[5]} TP{best[6]:.1%} MH{best[7]} SN{best[8]:.1%}"
          f" | IS ${best[9]:.0f} | OOS ${best[0]:.0f}", flush=True)
    print(f"Delta: ${best[0] - 1439:+.0f}", flush=True)
    combo = best[0] + 2087
    print(f"L+S OOS: ${combo:.0f} (R5: $3,526, V11-E: $2,801)", flush=True)

print("\nDone.", flush=True)
