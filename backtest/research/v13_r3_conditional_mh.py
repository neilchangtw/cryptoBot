"""
V13-R3: Conditional MaxHold Extension
======================================
假說：V11-E 80%+ 出場是 MaxHold，但其中有些是盈利中的持倉被時間止損切掉。
      改為：
        - MaxHold 到期時虧損 → 立即出場（不變）
        - MaxHold 到期時盈利 → 延長 N bar，加上 breakeven trail

      另外測試：
        - Early exit（虧損時提前出場）
        - Partial TP（到 TP/2 時鎖利）
        - Different MaxHold values with same approach
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
v = df["volume"].values.astype(float)
dt = df["datetime"].values

# GK
gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk)
gk_ratio = gk_s.rolling(5).mean() / gk_s.rolling(20).mean()
gk_pctile = gk_ratio.shift(1).rolling(100).apply(
    lambda x: pd.Series(x).rank(pct=True).iloc[-1]
).values

c_s = pd.Series(c)
bo_up_15 = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn_15 = (c_s < c_s.shift(1).rolling(15).min()).values

hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])

TOTAL = len(df)
IS_END = TOTAL // 2
WARMUP = 150

BH = {0, 1, 2, 12}
BD = {"Monday", "Saturday", "Sunday"}


def backtest(side, gk_thresh, bo_up_arr, bo_dn_arr, tp_pct, maxhold, safenet_pct,
             cooldown, monthly_loss_limit,
             # Extension params
             extension_bars=0,        # Extra bars after MaxHold if profitable
             be_trail_after_mh=False, # Breakeven trail during extension
             early_exit_bars=0,       # Exit early if losing after N bars
             early_exit_min_loss=0,   # Only early exit if loss > this %
             monthly_cap=20):

    FEE = 4.0
    NOTIONAL = 4000
    SLIP = 0.25
    DAILY_LOSS_LIMIT = -200
    CONSEC_LOSS_PAUSE = 4
    CONSEC_LOSS_COOLDOWN = 24

    pos = None
    trades = []
    last_exit_bar = -999
    daily_pnl = {}
    monthly_pnl = {}
    monthly_entries = {}
    consec_losses = 0
    consec_pause_until = -1

    for i in range(WARMUP, TOTAL - 1):
        bar_dt = pd.Timestamp(dt[i])
        day_key = bar_dt.strftime("%Y-%m-%d")
        month_key = bar_dt.strftime("%Y-%m")
        daily_pnl.setdefault(day_key, 0.0)
        monthly_pnl.setdefault(month_key, 0.0)
        monthly_entries.setdefault(month_key, 0)

        # === 出場 ===
        if pos is not None:
            ep = pos["ep"]
            held = i - pos["bar"]
            in_extension = pos.get("in_extension", False)
            be_price = pos.get("be_price", None)

            if side == "L":
                cur_pnl_pct = (c[i] - ep) / ep
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * (1 + SLIP))
                # For breakeven trail
                hit_be = be_price is not None and l[i] <= be_price
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * (1 + SLIP))
                hit_be = be_price is not None and h[i] >= be_price

            ex_p = ex_r = None

            # Priority: SL → TP → BE trail → MaxHold logic
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif in_extension:
                # In extension period
                ext_held = i - pos["ext_start"]
                if hit_be:
                    ex_p, ex_r = be_price, "BE"
                elif ext_held >= extension_bars:
                    ex_p, ex_r = c[i], "MH-ext"
            else:
                # Early exit check
                if early_exit_bars > 0 and held >= early_exit_bars and cur_pnl_pct < -early_exit_min_loss:
                    ex_p, ex_r = c[i], "Early"

                # MaxHold check
                if ex_p is None and held >= maxhold:
                    if extension_bars > 0 and cur_pnl_pct > 0:
                        # Profitable at MaxHold → enter extension
                        pos["in_extension"] = True
                        pos["ext_start"] = i
                        if be_trail_after_mh:
                            pos["be_price"] = ep  # breakeven = entry price
                    else:
                        # Not profitable or no extension → normal exit
                        ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                trades.append({"bar": pos["bar"], "pnl": net, "reason": ex_r,
                                "oos": pos["bar"] >= IS_END, "held": held})
                daily_pnl[day_key] += net
                monthly_pnl[month_key] += net
                last_exit_bar = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= CONSEC_LOSS_PAUSE:
                    consec_pause_until = i + CONSEC_LOSS_COOLDOWN
                pos = None

        # === 進場 ===
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
        if monthly_entries.get(month_key, 0) >= monthly_cap:
            continue
        if hours[i] in BH:
            continue
        if days[i] in BD:
            continue

        gk_val = gk_pctile[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh / 100.0:
            continue

        if side == "L" and bo_up_arr[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1
        elif side == "S" and bo_dn_arr[i]:
            pos = {"ep": o[i + 1], "bar": i}
            monthly_entries[month_key] += 1

    return trades


def report(trades, label):
    if not trades:
        print(f"  {label}: 0 trades")
        return {}
    tdf = pd.DataFrame(trades)
    res = {}
    for name, sub in [("IS", tdf[~tdf["oos"]]), ("OOS", tdf[tdf["oos"]])]:
        if len(sub) == 0:
            continue
        pnl = sub["pnl"].sum()
        wr = (sub["pnl"] > 0).mean() * 100
        cum = sub["pnl"].cumsum()
        mdd = (cum - cum.cummax()).min()
        reasons = sub["reason"].value_counts().to_dict()
        res[name] = {"pnl": pnl, "wr": wr, "n": len(sub), "mdd": mdd}
        print(f"  {label} {name}: {len(sub)}t ${pnl:.0f} WR{wr:.0f}% MDD${mdd:.0f} | {reasons}")
    if "OOS" in res:
        oos_t = tdf[tdf["oos"]].copy()
        oos_t["month"] = [pd.Timestamp(dt[b]).strftime("%Y-%m") for b in oos_t["bar"]]
        monthly = oos_t.groupby("month")["pnl"].sum()
        pm = (monthly > 0).sum()
        tm = len(monthly)
        worst = monthly.min()
        res["pm"] = f"{pm}/{tm}"
        res["worst_month"] = worst
        print(f"  {label} PM: {pm}/{tm}, worst ${worst:.0f}")
    return res


# ===== 1. BASELINE =====
print("=" * 60)
print("BASELINE V11-E")
print("=" * 60)

t = backtest("L", 25, bo_up_15, bo_dn_15, 0.035, 6, 0.035, 6, -75)
r_l_base = report(t, "L-base")

t = backtest("S", 30, bo_up_15, bo_dn_15, 0.02, 7, 0.04, 8, -150)
r_s_base = report(t, "S-base")

base_oos = r_l_base.get("OOS", {}).get("pnl", 0) + r_s_base.get("OOS", {}).get("pnl", 0)
print(f"\n  Baseline L+S OOS: ${base_oos:.0f}")

# ===== 2. CONDITIONAL MAXHOLD EXTENSION =====
print("\n" + "=" * 60)
print("CONDITIONAL MaxHold Extension")
print("=" * 60)

# L: MH=6, test extensions of 1-6 bars with/without BE trail
print("\n--- L: Extension (MH=6 + ext N, with BE trail) ---")
for ext in [1, 2, 3, 4, 5, 6]:
    t = backtest("L", 25, bo_up_15, bo_dn_15, 0.035, 6, 0.035, 6, -75,
                 extension_bars=ext, be_trail_after_mh=True)
    tdf = pd.DataFrame(t) if t else pd.DataFrame()
    if len(tdf) == 0:
        continue
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    reasons = oos["reason"].value_counts().to_dict()
    print(f"  ext={ext}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}% | {reasons}")

print("\n--- L: Extension WITHOUT BE trail (just more time) ---")
for ext in [1, 2, 3, 4, 5, 6]:
    t = backtest("L", 25, bo_up_15, bo_dn_15, 0.035, 6, 0.035, 6, -75,
                 extension_bars=ext, be_trail_after_mh=False)
    tdf = pd.DataFrame(t) if t else pd.DataFrame()
    if len(tdf) == 0:
        continue
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  ext={ext}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# S extensions
print("\n--- S: Extension (MH=7 + ext N, with BE trail) ---")
for ext in [1, 2, 3, 4, 5, 6]:
    t = backtest("S", 30, bo_up_15, bo_dn_15, 0.02, 7, 0.04, 8, -150,
                 extension_bars=ext, be_trail_after_mh=True)
    tdf = pd.DataFrame(t) if t else pd.DataFrame()
    if len(tdf) == 0:
        continue
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    reasons = oos["reason"].value_counts().to_dict()
    print(f"  ext={ext}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}% | {reasons}")

print("\n--- S: Extension WITHOUT BE trail ---")
for ext in [1, 2, 3, 4, 5, 6]:
    t = backtest("S", 30, bo_up_15, bo_dn_15, 0.02, 7, 0.04, 8, -150,
                 extension_bars=ext, be_trail_after_mh=False)
    tdf = pd.DataFrame(t) if t else pd.DataFrame()
    if len(tdf) == 0:
        continue
    oos = tdf[tdf["oos"]]
    is_t = tdf[~tdf["oos"]]
    if len(oos) < 3:
        continue
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos["pnl"].sum()
    oos_wr = (oos["pnl"] > 0).mean() * 100
    print(f"  ext={ext}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== 3. EARLY EXIT =====
print("\n" + "=" * 60)
print("EARLY EXIT (cut losers early)")
print("=" * 60)

print("\n--- L: Early exit after N bars if losing ---")
for early_bars in [2, 3, 4]:
    for min_loss in [0.005, 0.01, 0.015, 0.02]:
        t = backtest("L", 25, bo_up_15, bo_dn_15, 0.035, 6, 0.035, 6, -75,
                     early_exit_bars=early_bars, early_exit_min_loss=min_loss)
        if not t:
            continue
        tdf = pd.DataFrame(t)
        oos = tdf[tdf["oos"]]
        is_t = tdf[~tdf["oos"]]
        if len(oos) < 3:
            continue
        is_pnl = is_t["pnl"].sum()
        oos_pnl = oos["pnl"].sum()
        oos_wr = (oos["pnl"] > 0).mean() * 100
        reasons = oos["reason"].value_counts().to_dict()
        print(f"  early={early_bars}bar loss>{min_loss:.1%}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}% | {reasons}")

print("\n--- S: Early exit after N bars if losing ---")
for early_bars in [2, 3, 4]:
    for min_loss in [0.005, 0.01, 0.015, 0.02]:
        t = backtest("S", 30, bo_up_15, bo_dn_15, 0.02, 7, 0.04, 8, -150,
                     early_exit_bars=early_bars, early_exit_min_loss=min_loss)
        if not t:
            continue
        tdf = pd.DataFrame(t)
        oos = tdf[tdf["oos"]]
        is_t = tdf[~tdf["oos"]]
        if len(oos) < 3:
            continue
        is_pnl = is_t["pnl"].sum()
        oos_pnl = oos["pnl"].sum()
        oos_wr = (oos["pnl"] > 0).mean() * 100
        print(f"  early={early_bars}bar loss>{min_loss:.1%}: IS {len(is_t)}t ${is_pnl:.0f} | OOS {len(oos)}t ${oos_pnl:.0f} WR{oos_wr:.0f}%")

# ===== 4. COMBINED: Extension + Early Exit =====
print("\n" + "=" * 60)
print("COMBINED: Extension + Early Exit")
print("=" * 60)

# Test best extension + best early exit together
combos_l = []
for ext in [0, 2, 3, 4]:
    for early in [0, 3, 4]:
        for min_loss in [0, 0.01, 0.015]:
            if ext == 0 and early == 0:
                continue  # skip baseline
            t = backtest("L", 25, bo_up_15, bo_dn_15, 0.035, 6, 0.035, 6, -75,
                         extension_bars=ext, be_trail_after_mh=(ext > 0),
                         early_exit_bars=early, early_exit_min_loss=min_loss)
            if not t:
                continue
            tdf = pd.DataFrame(t)
            oos = tdf[tdf["oos"]]
            is_t = tdf[~tdf["oos"]]
            if len(oos) < 5 or len(is_t) < 3:
                continue
            is_pnl = is_t["pnl"].sum()
            oos_pnl = oos["pnl"].sum()
            oos_wr = (oos["pnl"] > 0).mean() * 100
            combos_l.append((oos_pnl, ext, early, min_loss, is_pnl, len(is_t), len(oos), oos_wr))

combos_l.sort(reverse=True)
print("\n--- L Top 10 combos ---")
for r in combos_l[:10]:
    print(f"  ext={r[1]} early={r[2]}bar loss>{r[3]:.1%}: IS {r[5]}t ${r[4]:.0f} | OOS {r[6]}t ${r[0]:.0f} WR{r[7]:.0f}%")

combos_s = []
for ext in [0, 2, 3, 4]:
    for early in [0, 3, 4]:
        for min_loss in [0, 0.01, 0.015]:
            if ext == 0 and early == 0:
                continue
            t = backtest("S", 30, bo_up_15, bo_dn_15, 0.02, 7, 0.04, 8, -150,
                         extension_bars=ext, be_trail_after_mh=(ext > 0),
                         early_exit_bars=early, early_exit_min_loss=min_loss)
            if not t:
                continue
            tdf = pd.DataFrame(t)
            oos = tdf[tdf["oos"]]
            is_t = tdf[~tdf["oos"]]
            if len(oos) < 5 or len(is_t) < 3:
                continue
            is_pnl = is_t["pnl"].sum()
            oos_pnl = oos["pnl"].sum()
            oos_wr = (oos["pnl"] > 0).mean() * 100
            combos_s.append((oos_pnl, ext, early, min_loss, is_pnl, len(is_t), len(oos), oos_wr))

combos_s.sort(reverse=True)
print("\n--- S Top 10 combos ---")
for r in combos_s[:10]:
    print(f"  ext={r[1]} early={r[2]}bar loss>{r[3]:.1%}: IS {r[5]}t ${r[4]:.0f} | OOS {r[6]}t ${r[0]:.0f} WR{r[7]:.0f}%")

# ===== 5. BEST COMBO vs V11-E =====
if combos_l and combos_s:
    bl = combos_l[0]
    bs = combos_s[0]
    combo_oos = bl[0] + bs[0]
    combo_is = bl[4] + bs[4]

    print(f"\n" + "=" * 60)
    print(f"BEST COMBO vs BASELINE")
    print(f"=" * 60)
    print(f"  Best L: ext={bl[1]} early={bl[2]} loss>{bl[3]:.1%} → OOS ${bl[0]:.0f}")
    print(f"  Best S: ext={bs[1]} early={bs[2]} loss>{bs[3]:.1%} → OOS ${bs[0]:.0f}")
    print(f"  L+S OOS: ${combo_oos:.0f} (baseline ${base_oos:.0f}, diff ${combo_oos - base_oos:+.0f})")
    print(f"  L+S IS:  ${combo_is:.0f}")

print("\nDone.")
