"""
V21 Path B — V14.1 改良候選

B1: Signal Clustering Filter — L/S 連續 signal 只取第一個（擴展 cooldown）
B2: Path-dependent Exit — L 在 bar 2/3 drawdown 深則早切/縮短 MH
B3: Conditional Non-trading — L-only 連敗 → 跳過下 N 個 L signal

所有變體使用 V14 完整框架（GK pctile + breakout + session + SafeNet 25% 穿透
+ TP + MFE trail + Conditional MH + Ext BE + Daily/Monthly caps + Consec pause）
只對單一維度擾動，衡量相對 V14 的改善。

IS = 前 50%, OOS = 後 50%
"""
import pandas as pd
import numpy as np

# ============================================================
# Data + Indicators
# ============================================================
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df = df.sort_values("datetime").reset_index(drop=True)

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2
print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

FEE = 4.0
NOTIONAL = 4000.0

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
        result[i] = np.sum(valid < valid[-1]) / (len(valid) - 1) * 100 if len(valid) > 1 else 50
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
days = pd.to_datetime(dt).weekday
BH = {0, 1, 2, 12}
BD_L = {5, 6}         # Sat, Sun
BD_S = {0, 5, 6}      # Mon, Sat, Sun

# ============================================================
# V14 Backtest engine (with per-variant hooks)
# ============================================================
def backtest(
    side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
    cooldown, monthly_loss, block_hours, block_days,
    start_bar, end_bar, ext=2,
    # L-only V14 features
    mfe_act=None, trail_dd=None, min_bar_trail=1,
    check_bar=None, exit_thresh=None, reduced_mh=None,
    # B1: extra cooldown after a negative trade
    cd_after_loss=None,
    # B2: path-dependent early exit at bar 2 / 3
    b2_bar2_thresh=None, b2_bar2_reduce_mh=None,
    b2_bar3_cut_thresh=None,
    # B3: L-only consec-loss → skip next N signals
    b3_consec_thresh=None, b3_skip_next=None,
):
    trades = []
    in_pos = False
    in_ext = False
    last_exit = -999
    last_pnl = 0.0  # for B1
    cur_day = ""; day_pnl = 0.0
    cur_month = ""; month_pnl = 0.0; month_entries = 0
    consec_losses = 0; consec_pause = -1
    # B3
    l_consec_losses = 0
    l_skip_remaining = 0

    # Mutable closure-like state
    entry_price = 0.0
    entry_bar = -1
    running_mfe = 0.0
    eff_mh = maxhold
    ext_start = -1
    be_price = 0.0
    cur_mh_reduced = False

    for i in range(max(200, start_bar), min(end_bar, TOTAL - 1)):
        bar_dt = pd.Timestamp(dt[i])
        dk = bar_dt.strftime("%Y-%m-%d")
        mk = bar_dt.strftime("%Y-%m")
        if dk != cur_day:
            cur_day = dk; day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk; month_pnl = 0.0; month_entries = 0

        # ---- Exit logic ----
        if in_pos:
            held = i - entry_bar
            ep = entry_price

            if side == "L":
                cur_pnl_pct = (c[i] - ep) / ep
                intra_best = (h[i] - ep) / ep
                # SafeNet with 25% penetration (production slippage model)
                sl_level = ep * (1 - safenet_pct)
                hit_sl = l[i] <= sl_level
                sl_exit = sl_level - (sl_level - l[i]) * 0.25 if hit_sl else 0.0
                hit_tp = h[i] >= ep * (1 + tp_pct)
                tp_exit = ep * (1 + tp_pct)
                hit_be = in_ext and l[i] <= be_price
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                intra_best = (ep - l[i]) / ep
                sl_level = ep * (1 + safenet_pct)
                hit_sl = h[i] >= sl_level
                sl_exit = sl_level + (h[i] - sl_level) * 0.25 if hit_sl else 0.0
                hit_tp = l[i] <= ep * (1 - tp_pct)
                tp_exit = ep * (1 - tp_pct)
                hit_be = in_ext and h[i] >= be_price

            running_mfe = max(running_mfe, intra_best)

            ex_p = None; ex_r = None

            if hit_sl:
                ex_p, ex_r = sl_exit, "SafeNet"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif mfe_act is not None and trail_dd is not None:
                if (held >= min_bar_trail and running_mfe >= mfe_act
                        and cur_pnl_pct <= running_mfe - trail_dd):
                    ex_p, ex_r = c[i], "MFE-trail"

            # B2: Path-dependent early cut at bar 3
            if ex_p is None and side == "L" and b2_bar3_cut_thresh is not None:
                if held == 3 and cur_pnl_pct <= b2_bar3_cut_thresh:
                    ex_p, ex_r = c[i], "B2-cut3"

            if ex_p is None:
                # Conditional MH check (V14 default at bar check_bar)
                if (check_bar is not None and exit_thresh is not None
                        and reduced_mh is not None and not cur_mh_reduced):
                    if held == check_bar and cur_pnl_pct <= exit_thresh:
                        eff_mh = reduced_mh
                        cur_mh_reduced = True
                # B2: stronger reduction at bar 2 (overlay)
                if (b2_bar2_thresh is not None and b2_bar2_reduce_mh is not None
                        and side == "L" and not cur_mh_reduced):
                    if held == 2 and cur_pnl_pct <= b2_bar2_thresh:
                        eff_mh = b2_bar2_reduce_mh
                        cur_mh_reduced = True

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
                    "side": side,
                    "entry_bar": entry_bar, "exit_bar": i, "pnl": net,
                    "reason": ex_r, "held": held,
                    "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m"),
                    "entry_dt": str(pd.Timestamp(dt[entry_bar])),
                    "exit_dt": str(pd.Timestamp(dt[i])),
                })
                day_pnl += net; month_pnl += net
                last_exit = i; last_pnl = net
                if net < 0:
                    consec_losses += 1
                    if side == "L":
                        l_consec_losses += 1
                else:
                    consec_losses = 0
                    if side == "L":
                        l_consec_losses = 0
                if consec_losses >= 4:
                    consec_pause = i + 24
                # B3 trigger
                if (b3_consec_thresh is not None and b3_skip_next is not None
                        and side == "L" and l_consec_losses >= b3_consec_thresh):
                    l_skip_remaining = b3_skip_next
                    l_consec_losses = 0
                in_pos = False; in_ext = False; cur_mh_reduced = False

        # ---- Entry logic ----
        if in_pos: continue
        cd_eff = cooldown
        # B1: extra cooldown after a losing trade
        if cd_after_loss is not None and last_pnl < 0:
            cd_eff = max(cd_eff, cd_after_loss)
        if i - last_exit < cd_eff: continue
        if i < consec_pause: continue
        if day_pnl <= -200: continue
        if month_pnl <= monthly_loss: continue
        if month_entries >= 20: continue
        if hours[i] in block_hours or days[i] in block_days: continue

        gk_val = gk_p[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh: continue

        bo = bo_up[i] if side == "L" else bo_dn[i]
        if not bo: continue

        # B3 skip
        if side == "L" and l_skip_remaining > 0:
            l_skip_remaining -= 1
            continue

        # Enter
        entry_price = o[i + 1]
        entry_bar = i
        running_mfe = 0.0
        eff_mh = maxhold
        cur_mh_reduced = False
        in_pos = True; in_ext = False
        month_entries += 1

    return trades


# ============================================================
# Variant configs (V14 core kwargs)
# ============================================================
V14_L_KW = dict(
    side="L", gk_p=gk_p_l, gk_thresh=25, tp_pct=0.035, maxhold=6, safenet_pct=0.035,
    cooldown=6, monthly_loss=-75, block_hours=BH, block_days=BD_L, ext=2,
    mfe_act=0.010, trail_dd=0.008, min_bar_trail=1,
    check_bar=2, exit_thresh=-0.01, reduced_mh=5,
)
V14_S_KW = dict(
    side="S", gk_p=gk_p_s, gk_thresh=35, tp_pct=0.02, maxhold=10, safenet_pct=0.04,
    cooldown=8, monthly_loss=-150, block_hours=BH, block_days=BD_S, ext=2,
)

def run(side_kw, start, end, **overrides):
    kw = {**side_kw, **overrides, "start_bar": start, "end_bar": end}
    return backtest(**kw)


# ============================================================
# Metrics
# ============================================================
def metrics(trades, label=""):
    if not trades:
        return {"label": label, "n": 0, "pnl": 0, "wr": 0, "pf": 0, "mdd": 0, "worst_mo": 0, "pos_months": 0, "total_months": 0}
    pnls = np.array([t["pnl"] for t in trades])
    total = pnls.sum()
    wr = (pnls > 0).mean() * 100
    gw = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls < 0].sum())
    pf = gw / gl if gl > 0 else np.inf
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    mdd = float((peak - eq).max()) if len(eq) else 0
    monthly = {}
    for t in trades:
        monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
    worst_mo = min(monthly.values()) if monthly else 0
    pos_months = sum(1 for v in monthly.values() if v > 0)
    return {"label": label, "n": len(pnls), "pnl": float(total), "wr": float(wr),
            "pf": float(pf), "mdd": mdd, "worst_mo": float(worst_mo),
            "pos_months": pos_months, "total_months": len(monthly)}


def print_row(m, is_m=None):
    if is_m is None:
        print(f"  {m['label']:<30} | n={m['n']:>3} PnL ${m['pnl']:>6.0f} WR {m['wr']:>4.1f}% PF {m['pf']:>4.2f} MDD ${m['mdd']:>5.0f} {m['pos_months']:>2}/{m['total_months']:<2}mo+ worst ${m['worst_mo']:>5.0f}")
    else:
        print(f"  {m['label']:<30} | IS ${is_m['pnl']:>6.0f} OOS ${m['pnl']:>6.0f}  n={m['n']:>3}/{is_m['n']:<3}  WR {m['wr']:>4.1f}%  MDD ${m['mdd']:>5.0f}  {m['pos_months']}/{m['total_months']}mo+ worst ${m['worst_mo']:>5.0f}")


# ============================================================
# 1. V14 Baseline
# ============================================================
print("\n" + "=" * 78)
print("1. V14 Baseline (reference)")
print("=" * 78)

v14_l_is = run(V14_L_KW, 0, IS_END)
v14_l_oos = run(V14_L_KW, IS_END, TOTAL)
v14_s_is = run(V14_S_KW, 0, IS_END)
v14_s_oos = run(V14_S_KW, IS_END, TOTAL)

m = {
    "L_IS": metrics(v14_l_is, "V14 L IS"),
    "L_OOS": metrics(v14_l_oos, "V14 L OOS"),
    "S_IS": metrics(v14_s_is, "V14 S IS"),
    "S_OOS": metrics(v14_s_oos, "V14 S OOS"),
}
for v in m.values(): print_row(v)

v14_ls_is = m["L_IS"]["pnl"] + m["S_IS"]["pnl"]
v14_ls_oos = m["L_OOS"]["pnl"] + m["S_OOS"]["pnl"]
print(f"\n  V14 L+S IS  ${v14_ls_is:.0f}")
print(f"  V14 L+S OOS ${v14_ls_oos:.0f}  ← baseline to beat")


# ============================================================
# 2. B1: Signal Clustering Filter (extended cooldown after loss)
# ============================================================
print("\n" + "=" * 78)
print("2. B1: Signal Clustering Filter — cooldown_after_loss sweep")
print("=" * 78)
print("  (after a losing trade, require CD_L >= X bars before next signal)")
print()

b1_results = []
for cd_loss_l in [8, 12, 15, 20, 24]:
    for cd_loss_s in [10, 15, 20, 24]:
        t_l_is = run(V14_L_KW, 0, IS_END, cd_after_loss=cd_loss_l)
        t_s_is = run(V14_S_KW, 0, IS_END, cd_after_loss=cd_loss_s)
        t_l_oos = run(V14_L_KW, IS_END, TOTAL, cd_after_loss=cd_loss_l)
        t_s_oos = run(V14_S_KW, IS_END, TOTAL, cd_after_loss=cd_loss_s)
        is_total = sum(t["pnl"] for t in t_l_is) + sum(t["pnl"] for t in t_s_is)
        oos_total = sum(t["pnl"] for t in t_l_oos) + sum(t["pnl"] for t in t_s_oos)
        l_oos_pnl = sum(t["pnl"] for t in t_l_oos)
        s_oos_pnl = sum(t["pnl"] for t in t_s_oos)
        l_oos_n = len(t_l_oos); s_oos_n = len(t_s_oos)
        b1_results.append({
            "cd_l": cd_loss_l, "cd_s": cd_loss_s,
            "is": is_total, "oos": oos_total,
            "l_oos": l_oos_pnl, "s_oos": s_oos_pnl,
            "ln": l_oos_n, "sn": s_oos_n,
        })

print(f"  {'CD_L':>5} {'CD_S':>5} | {'IS':>7} {'OOS':>7} {'vs V14 IS':>10} {'vs V14 OOS':>11} | {'L OOS':>7} {'S OOS':>7} {'Ln':>3} {'Sn':>3}")
print("  " + "-" * 95)
for r in b1_results:
    d_is = r["is"] - v14_ls_is
    d_oos = r["oos"] - v14_ls_oos
    tag = ""
    if d_oos > 100 and d_is > 0:
        tag = " ★"
    print(f"  {r['cd_l']:>5} {r['cd_s']:>5} | ${r['is']:>6.0f} ${r['oos']:>6.0f} ${d_is:>+9.0f} ${d_oos:>+10.0f} | ${r['l_oos']:>6.0f} ${r['s_oos']:>6.0f} {r['ln']:>3} {r['sn']:>3}{tag}")

b1_best_is = max(b1_results, key=lambda r: r["is"])
print(f"\n  Best B1 by IS: cd_l={b1_best_is['cd_l']} cd_s={b1_best_is['cd_s']} IS ${b1_best_is['is']:.0f} OOS ${b1_best_is['oos']:.0f}")


# ============================================================
# 3. B2: Path-dependent Exit
# ============================================================
print("\n" + "=" * 78)
print("3. B2: Path-dependent Exit — L bar 2/3 deep drawdown")
print("=" * 78)
print("  (layer on top of V14's bar2<=-1% -> MH=5)")
print("  bar2_thresh: if bar 2 close pnl% <= X → MH reduced to Y (more aggressive than V14 5)")
print("  bar3_cut:    if bar 3 close pnl% <= X → immediate exit")
print()

b2_cfgs = [
    # (label, b2_bar2_thresh, b2_bar2_reduce_mh, b2_bar3_cut_thresh)
    ("B2-null (=V14)",       None,   None, None),
    ("B2a bar2<=-1.5% MH3",  -0.015, 3,    None),
    ("B2b bar2<=-2.0% MH3",  -0.020, 3,    None),
    ("B2c bar2<=-1.5% MH2",  -0.015, 2,    None),
    ("B2d bar2<=-2.0% MH2",  -0.020, 2,    None),
    ("B2e bar3<=-2.0% cut",  None,   None, -0.020),
    ("B2f bar3<=-1.5% cut",  None,   None, -0.015),
    ("B2g bar3<=-2.5% cut",  None,   None, -0.025),
    ("B2h b2+b3 combo A",    -0.015, 3,    -0.020),
    ("B2i b2+b3 combo B",    -0.020, 3,    -0.025),
]

b2_results = []
for lab, b2t, b2m, b3c in b2_cfgs:
    t_l_is = run(V14_L_KW, 0, IS_END,
                 b2_bar2_thresh=b2t, b2_bar2_reduce_mh=b2m, b2_bar3_cut_thresh=b3c)
    t_l_oos = run(V14_L_KW, IS_END, TOTAL,
                  b2_bar2_thresh=b2t, b2_bar2_reduce_mh=b2m, b2_bar3_cut_thresh=b3c)
    is_pnl = sum(t["pnl"] for t in t_l_is)
    oos_pnl = sum(t["pnl"] for t in t_l_oos)
    ls_is = is_pnl + m["S_IS"]["pnl"]  # S unchanged
    ls_oos = oos_pnl + m["S_OOS"]["pnl"]
    b2_results.append({"lab": lab, "l_is": is_pnl, "l_oos": oos_pnl,
                       "ls_is": ls_is, "ls_oos": ls_oos,
                       "ln_is": len(t_l_is), "ln_oos": len(t_l_oos)})

print(f"  {'Config':<26} | {'L IS':>7} {'L OOS':>7} {'vs V14 L OOS':>13} | {'L+S IS':>7} {'L+S OOS':>7} {'vs V14 OOS':>11}")
print("  " + "-" * 95)
v14_l_oos_pnl = m["L_OOS"]["pnl"]
v14_l_is_pnl = m["L_IS"]["pnl"]
for r in b2_results:
    dl = r["l_oos"] - v14_l_oos_pnl
    dls = r["ls_oos"] - v14_ls_oos
    tag = ""
    if dl > 100 and r["l_is"] >= v14_l_is_pnl:
        tag = " ★"
    print(f"  {r['lab']:<26} | ${r['l_is']:>6.0f} ${r['l_oos']:>6.0f} ${dl:>+12.0f} | ${r['ls_is']:>6.0f} ${r['ls_oos']:>6.0f} ${dls:>+10.0f}{tag}")


# ============================================================
# 4. B3: Conditional Non-trading (L-only consec-loss skip)
# ============================================================
print("\n" + "=" * 78)
print("4. B3: Conditional Non-trading — L consec-loss → skip next N L signals")
print("=" * 78)
print("  (V14 has global 4-consec-loss → 24bar pause. B3 is L-specific.)")
print()

b3_cfgs = [
    ("B3-null (=V14)",        None, None),
    ("B3a 2L-loss skip 1",    2, 1),
    ("B3b 2L-loss skip 2",    2, 2),
    ("B3c 2L-loss skip 3",    2, 3),
    ("B3d 3L-loss skip 1",    3, 1),
    ("B3e 3L-loss skip 2",    3, 2),
    ("B3f 3L-loss skip 3",    3, 3),
    ("B3g 4L-loss skip 1",    4, 1),
    ("B3h 4L-loss skip 2",    4, 2),
]

b3_results = []
for lab, thresh, skip in b3_cfgs:
    t_l_is = run(V14_L_KW, 0, IS_END, b3_consec_thresh=thresh, b3_skip_next=skip)
    t_l_oos = run(V14_L_KW, IS_END, TOTAL, b3_consec_thresh=thresh, b3_skip_next=skip)
    is_pnl = sum(t["pnl"] for t in t_l_is)
    oos_pnl = sum(t["pnl"] for t in t_l_oos)
    ls_is = is_pnl + m["S_IS"]["pnl"]
    ls_oos = oos_pnl + m["S_OOS"]["pnl"]
    b3_results.append({"lab": lab, "l_is": is_pnl, "l_oos": oos_pnl,
                       "ls_is": ls_is, "ls_oos": ls_oos,
                       "ln_is": len(t_l_is), "ln_oos": len(t_l_oos)})

print(f"  {'Config':<24} | {'L IS':>7} {'L OOS':>7} {'vs V14 L OOS':>13} | {'L+S IS':>7} {'L+S OOS':>7} {'vs V14 OOS':>11} | Ln IS/OOS")
print("  " + "-" * 110)
for r in b3_results:
    dl = r["l_oos"] - v14_l_oos_pnl
    dls = r["ls_oos"] - v14_ls_oos
    tag = ""
    if dl > 100 and r["l_is"] >= v14_l_is_pnl:
        tag = " ★"
    print(f"  {r['lab']:<24} | ${r['l_is']:>6.0f} ${r['l_oos']:>6.0f} ${dl:>+12.0f} | ${r['ls_is']:>6.0f} ${r['ls_oos']:>6.0f} ${dls:>+10.0f} | {r['ln_is']}/{r['ln_oos']}{tag}")


# ============================================================
# Summary — identify IS-best candidates
# ============================================================
print("\n" + "=" * 78)
print("SUMMARY — Candidates to audit")
print("=" * 78)
print("""
Path B rule:
  1. Must improve IS L+S (G1 IS-first)
  2. OOS directionally positive vs V14 (G3)
  3. Gains must be from obvious structural change, not overfit

Recommended next steps for any star candidate:
  - Run full 10-Gate audit
  - G4 parameter neighborhood (+/-20%)
  - G6 IS/OOS swap test (<50% degradation)
  - G7 6-window walk forward (>=4 positive)
  - G8 time reversal (must stay positive or degrade <50%)
""")
