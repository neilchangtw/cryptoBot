"""
V13-R8: S 策略深度優化
======================
R7 後 L 已優化（WF 6/6+7/8），S 仍有空間：
  1. Session filter: Monday block 是否對 S 也不需要？
  2. Hour block 微調
  3. Cooldown / Monthly Loss 微調
  4. GK thresh 微調（已確認 <35 最佳，但 32-38 range？）
  5. Extension 微調（ext=1 vs 2 vs 3 for S）
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

print("Computing GK pctiles...", flush=True)
gk_p_10_30 = compute_gk_pctile(10, 30, 100)
print("Done.", flush=True)

c_s = pd.Series(c)
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values


def backtest_s(gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
               cooldown, monthly_loss, block_hours, block_days,
               ext=2):
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
            pnl_pct = (ep - c[i]) / ep
            hit_tp = (ep - l[i]) / ep >= tp_pct
            hit_sl = (ep - h[i]) / ep <= -safenet_pct
            tp_exit = ep * (1 - tp_pct)
            sl_exit = ep * (1 + safenet_pct * SLIP)
            hit_be = in_ext and h[i] >= be_p

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
                net = (ep - ex_p) / ep * NOTIONAL - 4.0
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
        if hours_arr[i] in block_hours or days_arr[i] in block_days: continue

        gk_val = gk_p[i]
        if gk_val != gk_val or gk_val >= gk_thresh / 100.0: continue

        if bo_dn_15[i]:
            ep = o[i + 1]; entry_bar = i
            in_pos = True; in_ext = False
            month_entries += 1

    wr = (oos_wins / oos_n * 100) if oos_n > 0 else 0
    mdd = 0.0
    if oos_pnls:
        cum = np.cumsum(oos_pnls)
        mdd = float(np.min(cum - np.maximum.accumulate(cum)))
    return is_pnl, is_n, oos_pnl, oos_n, wr, mdd


BH = {0, 1, 2, 12}
BD_V11E = {"Monday", "Saturday", "Sunday"}

# ===== Phase 1: Day Block =====
print("\n" + "=" * 60, flush=True)
print("Phase 1: S Day Block", flush=True)
print("=" * 60, flush=True)

for label, bd in [("Mon,Sat,Sun (V11-E)", {"Monday","Saturday","Sunday"}),
                   ("Sat,Sun only", {"Saturday","Sunday"}),
                   ("Mon,Fri,Sat,Sun", {"Monday","Friday","Saturday","Sunday"}),
                   ("Sat only", {"Saturday"}),
                   ("Sun only", {"Sunday"}),
                   ("none", set())]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, BH, bd)
    print(f"  {label:>25}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 2: Hour Block =====
print("\n" + "=" * 60, flush=True)
print("Phase 2: S Hour Block", flush=True)
print("=" * 60, flush=True)

for label, bh in [("{0,1,2,12} (V11-E)", {0,1,2,12}),
                   ("{0,1,2}", {0,1,2}),
                   ("{0,1,2,12,13}", {0,1,2,12,13}),
                   ("{0,1,2,11,12}", {0,1,2,11,12}),
                   ("{0,1,2,3}", {0,1,2,3}),
                   ("{0,1,2,3,12}", {0,1,2,3,12}),
                   ("none", set()),
                   ("{12}", {12}),
                   ("{0,1,2,12,23}", {0,1,2,12,23})]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, bh, BD_V11E)
    print(f"  {label:>25}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 3: Cooldown =====
print("\n" + "=" * 60, flush=True)
print("Phase 3: S Cooldown", flush=True)
print("=" * 60, flush=True)

for cd in [4, 5, 6, 7, 8, 9, 10, 12]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, 0.04, cd, -150, BH, BD_V11E)
    print(f"  CD={cd:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 4: Monthly Loss =====
print("\n" + "=" * 60, flush=True)
print("Phase 4: S Monthly Loss", flush=True)
print("=" * 60, flush=True)

for ml in [-75, -100, -125, -150, -175, -200, -250, -300]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, 0.04, 8, ml, BH, BD_V11E)
    print(f"  ML=${ml:>4}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 5: GK Threshold fine =====
print("\n" + "=" * 60, flush=True)
print("Phase 5: S GK Threshold Fine-tune", flush=True)
print("=" * 60, flush=True)

for gt in [28, 30, 32, 33, 34, 35, 36, 37, 38, 40]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, gt, 0.02, 8, 0.04, 8, -150, BH, BD_V11E)
    print(f"  GK<{gt:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 6: Extension =====
print("\n" + "=" * 60, flush=True)
print("Phase 6: S Extension", flush=True)
print("=" * 60, flush=True)

for ext in [0, 1, 2, 3, 4, 5]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, BH, BD_V11E, ext=ext)
    print(f"  ext={ext}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 7: TP fine =====
print("\n" + "=" * 60, flush=True)
print("Phase 7: S TP Fine-tune", flush=True)
print("=" * 60, flush=True)

for tp in [0.015, 0.018, 0.02, 0.022, 0.025, 0.03]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, tp, 8, 0.04, 8, -150, BH, BD_V11E)
    print(f"  TP{tp:.1%}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 8: SafeNet fine =====
print("\n" + "=" * 60, flush=True)
print("Phase 8: S SafeNet Fine-tune", flush=True)
print("=" * 60, flush=True)

for sn in [0.03, 0.035, 0.04, 0.045, 0.05, 0.055]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, 8, sn, 8, -150, BH, BD_V11E)
    print(f"  SN{sn:.1%}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

# ===== Phase 9: MH fine =====
print("\n" + "=" * 60, flush=True)
print("Phase 9: S MaxHold Fine-tune", flush=True)
print("=" * 60, flush=True)

for mh in [6, 7, 8, 9, 10, 12]:
    is_p, is_n, oos_p, oos_n, wr, mdd = backtest_s(
        gk_p_10_30, 35, 0.02, mh, 0.04, 8, -150, BH, BD_V11E)
    print(f"  MH={mh:>2}: IS {is_n}t ${is_p:.0f} | OOS {oos_n}t ${oos_p:.0f} WR{wr:.0f}% MDD${mdd:.0f}", flush=True)

print("\nDone.", flush=True)
