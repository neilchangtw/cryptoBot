"""
V13-R9: L 策略精調 + Breakout Period + Extension
R8 已優化 S。L 還有幾個未測維度：
  1. Breakout period (10/12/15/20)
  2. Extension (0-5)
  3. TP fine-tune (2.5%-5.0%)
  4. MH fine-tune (4-10)
  5. GK thresh fine-tune
  6. SafeNet fine-tune
  7. Cooldown fine-tune
注意：L block_days={Sat,Sun} (R7)
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

print("Computing GK pctile...", flush=True)
gk_p_5_20 = compute_gk_pctile(5, 20, 100)
print("Done.", flush=True)

c_s = pd.Series(c)
bo_up = {}
for p in [10, 12, 15, 18, 20]:
    bo_up[p] = (c_s > c_s.shift(1).rolling(p).max()).values

BH = {0, 1, 2, 12}
BD_L = {"Saturday", "Sunday"}


def backtest_l(gk_p, gk_thresh, bo_arr, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, ext=2):
    NOTIONAL = 4000; SLIP = 1.25
    in_pos = False; ep = 0.0; entry_bar = 0
    in_ext = False; ext_start = 0; be_p = 0.0

    is_pnl = 0.0; is_n = 0
    oos_pnl = 0.0; oos_n = 0; oos_wins = 0
    oos_pnls = []
    last_exit = -999
    cur_day = ""; cur_month = ""
    day_pnl = 0.0; month_pnl = 0.0; month_entries = 0
    consec_losses = 0; consec_pause = -1

    for i in range(200, TOTAL - 1):
        dk = day_keys[i]; mk = month_keys[i]
        if dk != cur_day:
            cur_day = dk; day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk; month_pnl = 0.0; month_entries = 0

        if in_pos:
            held = i - entry_bar
            pnl_pct = (c[i] - ep) / ep
            hit_tp = (h[i] - ep) / ep >= tp_pct
            hit_sl = (l[i] - ep) / ep <= -safenet_pct
            tp_exit = ep * (1 + tp_pct)
            sl_exit = ep * (1 - safenet_pct * SLIP)
            hit_be = in_ext and l[i] <= be_p

            ex_p = None
            if hit_sl:
                ex_p = sl_exit
            elif hit_tp:
                ex_p = tp_exit
            elif in_ext:
                if hit_be:
                    ex_p = be_p
                elif i - ext_start >= ext:
                    ex_p = c[i]
            elif held >= maxhold:
                if pnl_pct > 0 and ext > 0:
                    in_ext = True; ext_start = i; be_p = ep
                else:
                    ex_p = c[i]

            if ex_p is not None:
                net = (ex_p - ep) / ep * NOTIONAL - 4.0
                if entry_bar >= IS_END:
                    oos_pnl += net; oos_n += 1
                    if net > 0: oos_wins += 1
                    oos_pnls.append(net)
                else:
                    is_pnl += net; is_n += 1
                day_pnl += net; month_pnl += net
                last_exit = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4: consec_pause = i + 24
                in_pos = False; in_ext = False

        if in_pos: continue
        if i - last_exit < cooldown: continue
        if i < consec_pause: continue
        if day_pnl <= -200: continue
        if month_pnl <= monthly_loss: continue
        if month_entries >= 20: continue
        if hours_arr[i] in BH or days_arr[i] in BD_L: continue

        gk_val = gk_p[i]
        if gk_val != gk_val or gk_val >= gk_thresh / 100.0: continue

        if bo_arr[i]:
            ep = o[i + 1]; entry_bar = i
            in_pos = True; in_ext = False
            month_entries += 1

    wr = (oos_wins / oos_n * 100) if oos_n > 0 else 0
    mdd = 0.0
    if oos_pnls:
        cum = np.cumsum(oos_pnls)
        mdd = float(np.min(cum - np.maximum.accumulate(cum)))
    return is_pnl, is_n, oos_pnl, oos_n, wr, mdd


# ===== Phase 1: Breakout Period =====
print("\n" + "=" * 60, flush=True)
print("Phase 1: L Breakout Period", flush=True)
print("=" * 60, flush=True)

for bo in [10, 12, 15, 18, 20]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[bo], 0.035, 6, 0.035, 6, -75)
    print(f"  BO{bo:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 2: Extension =====
print("\n" + "=" * 60, flush=True)
print("Phase 2: L Extension", flush=True)
print("=" * 60, flush=True)

for ext in [0, 1, 2, 3, 4, 5]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], 0.035, 6, 0.035, 6, -75, ext=ext)
    print(f"  ext={ext}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 3: TP =====
print("\n" + "=" * 60, flush=True)
print("Phase 3: L TP Fine-tune", flush=True)
print("=" * 60, flush=True)

for tp in [0.025, 0.03, 0.035, 0.04, 0.045, 0.05, 0.06]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], tp, 6, 0.035, 6, -75)
    print(f"  TP{tp:.1%}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 4: MaxHold =====
print("\n" + "=" * 60, flush=True)
print("Phase 4: L MaxHold Fine-tune", flush=True)
print("=" * 60, flush=True)

for mh in [4, 5, 6, 7, 8, 9, 10]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], 0.035, mh, 0.035, 6, -75)
    print(f"  MH={mh:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 5: GK Threshold =====
print("\n" + "=" * 60, flush=True)
print("Phase 5: L GK Threshold", flush=True)
print("=" * 60, flush=True)

for gt in [20, 22, 25, 28, 30, 35]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, gt, bo_up[15], 0.035, 6, 0.035, 6, -75)
    print(f"  GK<{gt:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 6: SafeNet =====
print("\n" + "=" * 60, flush=True)
print("Phase 6: L SafeNet", flush=True)
print("=" * 60, flush=True)

for sn in [0.025, 0.03, 0.035, 0.04, 0.045, 0.05]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], 0.035, 6, sn, 6, -75)
    print(f"  SN{sn:.1%}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 7: Cooldown =====
print("\n" + "=" * 60, flush=True)
print("Phase 7: L Cooldown", flush=True)
print("=" * 60, flush=True)

for cd in [3, 4, 5, 6, 7, 8]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], 0.035, 6, 0.035, cd, -75)
    print(f"  CD={cd}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 8: Monthly Loss =====
print("\n" + "=" * 60, flush=True)
print("Phase 8: L Monthly Loss", flush=True)
print("=" * 60, flush=True)

for ml in [-50, -75, -100, -150, -200]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, 25, bo_up[15], 0.035, 6, 0.035, 6, ml)
    print(f"  ML=${ml:>4}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 9: Promising combos =====
print("\n" + "=" * 60, flush=True)
print("Phase 9: L Promising Combos", flush=True)
print("=" * 60, flush=True)

combos = [
    ("R7 baseline", 25, 15, 0.035, 6, 0.035, 6, -75, 2),
    ("ext=3",       25, 15, 0.035, 6, 0.035, 6, -75, 3),
    ("TP4.0%",      25, 15, 0.04,  6, 0.035, 6, -75, 2),
    ("MH7",         25, 15, 0.035, 7, 0.035, 6, -75, 2),
    ("MH7+ext3",    25, 15, 0.035, 7, 0.035, 6, -75, 3),
    ("TP4%+MH7",    25, 15, 0.04,  7, 0.035, 6, -75, 2),
    ("GK28",        28, 15, 0.035, 6, 0.035, 6, -75, 2),
    ("SN4.0%",      25, 15, 0.035, 6, 0.04,  6, -75, 2),
    ("CD5",         25, 15, 0.035, 6, 0.035, 5, -75, 2),
    ("ML-100",      25, 15, 0.035, 6, 0.035, 6, -100, 2),
]

for label, gt, bo, tp, mh, sn, cd, ml, ext in combos:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_l(
        gk_p_5_20, gt, bo_up[bo], tp, mh, sn, cd, ml, ext=ext)
    print(f"  {label:>18}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

print("\nDone.", flush=True)
