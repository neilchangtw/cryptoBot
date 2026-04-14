"""
V13-R5 Detail Report: 月報 + 出場分佈 + 完整統計
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

# GK
gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk_raw)
gk_ma = {}
for w in [5, 10, 20, 30]:
    gk_ma[w] = gk_s.rolling(w).mean()

def compute_gk_pctile(fast, slow, pw):
    ratio = gk_ma[fast] / gk_ma[slow]
    return ratio.shift(1).rolling(pw).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1]
    ).values

print("Computing GK pctile...")
gk_p_5_20 = compute_gk_pctile(5, 20, 100)
gk_p_10_30 = compute_gk_pctile(10, 30, 100)
print("Done.")

c_s = pd.Series(c)
bo_up_15 = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}


def backtest_full(side, gk_pctile_arr, gk_thresh, tp_pct, maxhold, safenet_pct,
                  cooldown, monthly_loss_limit, ext=0, be_trail=False):
    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25

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
                pnl_pct = (ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep
                trades.append({
                    "side": side,
                    "entry_bar": pos["bar"],
                    "exit_bar": i,
                    "entry_price": ep,
                    "exit_price": ex_p,
                    "pnl": net,
                    "pnl_pct": pnl_pct,
                    "reason": ex_r,
                    "held": held,
                    "is_oos": "OOS" if pos["bar"] >= IS_END else "IS",
                    "month": pd.Timestamp(dt[pos["bar"]]).strftime("%Y-%m"),
                    "day": pd.Timestamp(dt[pos["bar"]]).strftime("%Y-%m-%d"),
                    "entry_dt": str(pd.Timestamp(dt[pos["bar"]])),
                    "exit_dt": str(pd.Timestamp(dt[i])),
                })
                daily_pnl[day_key] += net
                monthly_pnl[month_key] += net
                last_exit_bar = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
                    consec_pause_until = i + 24
                pos = None

        if pos is not None:
            continue
        if i - last_exit_bar < cooldown:
            continue
        if i < consec_pause_until:
            continue
        if daily_pnl.get(day_key, 0) <= -200:
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


# Run full backtests
trades_l = backtest_full("L", gk_p_5_20, 25, 0.035, 6, 0.035, 6, -75, ext=2, be_trail=True)
trades_s = backtest_full("S", gk_p_10_30, 35, 0.02, 8, 0.04, 8, -150, ext=2, be_trail=True)

all_trades = trades_l + trades_s
tdf = pd.DataFrame(all_trades)

# ===== 1. Monthly Report =====
print("=" * 80)
print("1. MONTHLY REPORT (OOS)")
print("=" * 80)

oos = tdf[tdf["is_oos"] == "OOS"]
l_oos = oos[oos["side"] == "L"]
s_oos = oos[oos["side"] == "S"]

l_monthly = l_oos.groupby("month")["pnl"].agg(["sum", "count"]).rename(columns={"sum": "pnl", "count": "n"})
s_monthly = s_oos.groupby("month")["pnl"].agg(["sum", "count"]).rename(columns={"sum": "pnl", "count": "n"})
all_monthly = oos.groupby("month")["pnl"].agg(["sum", "count"]).rename(columns={"sum": "pnl", "count": "n"})

v11e_l = {"2025-04": 117, "2025-05": 329, "2025-06": 19, "2025-07": 248,
           "2025-08": 115, "2025-09": 62, "2025-10": -111, "2025-11": 139,
           "2025-12": 105, "2026-01": -24, "2026-02": 355, "2026-03": 117, "2026-04": 166}
v11e_s = {"2025-04": 106, "2025-05": 174, "2025-06": 0, "2025-07": -139,
           "2025-08": 4, "2025-09": 100, "2025-10": 296, "2025-11": 13,
           "2025-12": 94, "2026-01": 108, "2026-02": 274, "2026-03": 296, "2026-04": -175}

months = sorted(set(list(all_monthly.index) + list(v11e_l.keys())))

print(f"\n{'Month':>8} | {'V13-L':>7}({'n':>2}) | {'V13-S':>7}({'n':>2}) | {'V13':>7}({'n':>2}) | {'V11E-L':>7} | {'V11E-S':>7} | {'V11-E':>7} | {'Diff':>7}")
print("-" * 95)

v13_total = 0
v11e_total = 0
for m in months:
    ml = l_monthly.loc[m, "pnl"] if m in l_monthly.index else 0
    ml_n = int(l_monthly.loc[m, "n"]) if m in l_monthly.index else 0
    ms = s_monthly.loc[m, "pnl"] if m in s_monthly.index else 0
    ms_n = int(s_monthly.loc[m, "n"]) if m in s_monthly.index else 0
    mt = all_monthly.loc[m, "pnl"] if m in all_monthly.index else 0
    mt_n = int(all_monthly.loc[m, "n"]) if m in all_monthly.index else 0
    v11l = v11e_l.get(m, 0)
    v11s = v11e_s.get(m, 0)
    v11 = v11l + v11s
    v13_total += mt
    v11e_total += v11
    marker = " **" if mt > v11 else ""
    print(f"{m:>8} | ${ml:>6.0f}({ml_n:>2}) | ${ms:>6.0f}({ms_n:>2}) | ${mt:>6.0f}({mt_n:>2}) | ${v11l:>6} | ${v11s:>6} | ${v11:>6} | ${mt-v11:>+6.0f}{marker}")

print(f"{'TOTAL':>8} | {'':>10} | {'':>10} | ${v13_total:>6.0f}{'':>4} | {'':>8} | {'':>8} | ${v11e_total:>6} | ${v13_total-v11e_total:>+6.0f}")

# PM
pm_v13 = sum(1 for m in months if (all_monthly.loc[m, "pnl"] if m in all_monthly.index else 0) > 0)
pm_v11 = sum(1 for m in months if v11e_l.get(m, 0) + v11e_s.get(m, 0) > 0)
print(f"\nPositive months: V13 {pm_v13}/{len(months)} | V11-E {pm_v11}/{len(months)}")

# ===== 2. IS Report =====
print("\n" + "=" * 80)
print("2. IS REPORT")
print("=" * 80)

is_t = tdf[tdf["is_oos"] == "IS"]
l_is = is_t[is_t["side"] == "L"]
s_is = is_t[is_t["side"] == "S"]

print(f"\n  L IS: {len(l_is)}t ${l_is['pnl'].sum():.0f} WR{(l_is['pnl']>0).mean()*100:.0f}%")
print(f"  S IS: {len(s_is)}t ${s_is['pnl'].sum():.0f} WR{(s_is['pnl']>0).mean()*100:.0f}%")
print(f"  Total IS: {len(is_t)}t ${is_t['pnl'].sum():.0f}")

l_is_monthly = l_is.groupby("month")["pnl"].sum()
s_is_monthly = s_is.groupby("month")["pnl"].sum()
is_monthly = is_t.groupby("month")["pnl"].sum()

print(f"\n  IS Monthly:")
for m in sorted(is_monthly.index):
    ml = l_is_monthly.get(m, 0)
    ms = s_is_monthly.get(m, 0)
    mt = is_monthly.get(m, 0)
    print(f"    {m}: L ${ml:>6.0f} | S ${ms:>6.0f} | Total ${mt:>6.0f}")

# ===== 3. Exit Reason Distribution =====
print("\n" + "=" * 80)
print("3. EXIT REASON DISTRIBUTION")
print("=" * 80)

for label, subset in [("L IS", l_is), ("L OOS", l_oos), ("S IS", s_is), ("S OOS", s_oos)]:
    if len(subset) == 0:
        continue
    print(f"\n  {label}:")
    for reason in ["TP", "MH", "MH-ext", "BE", "SL"]:
        r_sub = subset[subset["reason"] == reason]
        if len(r_sub) == 0:
            continue
        n = len(r_sub)
        pct = n / len(subset) * 100
        avg_pnl = r_sub["pnl"].mean()
        total_pnl = r_sub["pnl"].sum()
        avg_held = r_sub["held"].mean()
        wr = (r_sub["pnl"] > 0).mean() * 100
        print(f"    {reason:>6}: {n:>3}t ({pct:>4.0f}%) avg${avg_pnl:>+7.1f} total${total_pnl:>+7.0f} held{avg_held:.1f}bar WR{wr:.0f}%")

# ===== 4. Detailed Statistics =====
print("\n" + "=" * 80)
print("4. DETAILED STATISTICS (OOS)")
print("=" * 80)

for label, subset in [("L", l_oos), ("S", s_oos), ("L+S", oos)]:
    if len(subset) == 0:
        continue
    pnl = subset["pnl"]
    cum = pnl.cumsum()
    mdd = (cum - cum.cummax()).min()
    winners = pnl[pnl > 0]
    losers = pnl[pnl <= 0]

    print(f"\n  --- {label} ---")
    print(f"  Trades: {len(subset)}")
    print(f"  PnL: ${pnl.sum():.0f}")
    print(f"  WR: {(pnl > 0).mean()*100:.1f}%")
    print(f"  Avg PnL: ${pnl.mean():.1f}")
    print(f"  Avg Win: ${winners.mean():.1f} ({len(winners)}t)")
    print(f"  Avg Loss: ${losers.mean():.1f} ({len(losers)}t)")
    print(f"  PF: {winners.sum() / abs(losers.sum()):.2f}" if losers.sum() != 0 else "  PF: inf")
    print(f"  Max Win: ${pnl.max():.0f}")
    print(f"  Max Loss: ${pnl.min():.0f}")
    print(f"  MDD: ${mdd:.0f}")
    print(f"  Avg Held: {subset['held'].mean():.1f} bars")

    # Consecutive wins/losses
    signs = (pnl > 0).values
    max_consec_win = max_consec_loss = cur = 0
    is_win = True
    for s in signs:
        if s == is_win:
            cur += 1
        else:
            if is_win:
                max_consec_win = max(max_consec_win, cur)
            else:
                max_consec_loss = max(max_consec_loss, cur)
            is_win = s
            cur = 1
    if is_win:
        max_consec_win = max(max_consec_win, cur)
    else:
        max_consec_loss = max(max_consec_loss, cur)
    print(f"  Max Consec Win: {max_consec_win}")
    print(f"  Max Consec Loss: {max_consec_loss}")

# ===== 5. Worst Days =====
print("\n" + "=" * 80)
print("5. WORST DAYS (OOS)")
print("=" * 80)

daily = oos.groupby("day")["pnl"].sum().sort_values()
print("\n  Bottom 10 days:")
for d, pnl in daily.head(10).items():
    day_trades = oos[oos["day"] == d]
    sides = day_trades["side"].value_counts().to_dict()
    reasons = day_trades["reason"].value_counts().to_dict()
    print(f"    {d}: ${pnl:>+7.0f} | {sides} | {reasons}")

print("\n  Top 10 days:")
for d, pnl in daily.tail(10).items():
    print(f"    {d}: ${pnl:>+7.0f}")

# ===== 6. Walk-Forward Summary =====
print("\n" + "=" * 80)
print("6. WALK-FORWARD SUMMARY")
print("=" * 80)
print("""
  6-fold WF:
    L: 5/6 (Fold 1 FAIL: -$100)
    S: 5/6 (Fold 1 FAIL: -$164)

  8-fold WF:
    L: 6/8 (Fold 2,3 FAIL: -$98, -$33)
    S: 7/8 (Fold 1 FAIL: -$346)

  V11-E Baseline 6-fold:
    L: 4/6
    S: 5/6
""")

print("Done.")
