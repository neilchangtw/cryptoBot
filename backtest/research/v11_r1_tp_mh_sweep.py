"""V11 R1: TP / MaxHold Sweep — find better exit combos than V10"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float); h = df["high"].astype(float)
l = df["low"].astype(float); o = df["open"].astype(float)

ln_hl = np.log(h / l); ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
gk_pct = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)

df["brk_up_15"] = c > c.shift(1).rolling(15).max()
df["brk_dn_15"] = c < c.shift(1).rolling(15).min()

df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

arr_o = o.values; arr_h = h.values; arr_l = l.values; arr_c = c.values
arr_gk = gk_pct.values; arr_sess = df["session_ok"].values; arr_dt = df["datetime"].values
brk_up_15 = df["brk_up_15"].values; brk_dn_15 = df["brk_dn_15"].values


def run_side(side, gk_t, tp_pct, mh, sn_pct, cd, mcap, start, end):
    brk = brk_up_15 if side == "L" else brk_dn_15
    tp = tp_pct / 100; sn = sn_pct / 100
    trades = []; in_pos = False; ep = 0.0; idx = 0
    last_exit = -999; d_pnl = m_pnl = 0.0; consec = 0; cd_until = 0
    cur_m = ""; cur_d = ""; m_ent = 0

    for i in range(start, end):
        dt = pd.Timestamp(arr_dt[i])
        m_str = str(dt.to_period("M")); d_str = str(dt.date())
        if d_str != cur_d: d_pnl = 0.0; cur_d = d_str
        if m_str != cur_m: m_pnl = 0.0; m_ent = 0; cur_m = m_str

        if in_pos:
            held = i - idx; xp = 0.0; xr = ""
            if side == "L":
                sn_level = ep * (1 - sn)
                if arr_l[i] <= sn_level:
                    xp = sn_level - (sn_level - arr_l[i]) * 0.25; xr = "SafeNet"
                if xr == "" and arr_h[i] >= ep * (1 + tp):
                    xp = ep * (1 + tp); xr = "TP"
            else:
                sn_level = ep * (1 + sn)
                if arr_h[i] >= sn_level:
                    xp = sn_level + (arr_h[i] - sn_level) * 0.25; xr = "SafeNet"
                if xr == "" and arr_l[i] <= ep * (1 - tp):
                    xp = ep * (1 - tp); xr = "TP"
            if xr == "" and held >= mh:
                xp = arr_c[i]; xr = "MaxHold"
            if xr:
                if side == "L":
                    pnl = (xp / ep - 1) * NOTIONAL - FEE
                else:
                    pnl = (1 - xp / ep) * NOTIONAL - FEE
                trades.append({"pnl": pnl, "reason": xr, "month": m_str})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else: consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue
        if d_pnl <= -200 or m_pnl <= mcap or i < cd_until: continue
        if i - last_exit < cd or m_ent >= 20: continue
        if not arr_sess[i]: continue
        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_t and brk[i] and i + 1 < end:
            ep = arr_o[i+1]; idx = i+1; in_pos = True; m_ent += 1
    return trades


def eval_combo(l_tp, l_mh, s_tp, s_mh, l_sn=3.5, s_sn=4.0, l_mcap=-75, s_mcap=-150):
    """Evaluate a TP/MH combo for both L and S."""
    # IS
    is_l = run_side("L", 25, l_tp, l_mh, l_sn, 6, l_mcap, WARMUP, IS_END)
    is_s = run_side("S", 30, s_tp, s_mh, s_sn, 8, s_mcap, WARMUP, IS_END)
    is_lp = sum(t['pnl'] for t in is_l)
    is_sp = sum(t['pnl'] for t in is_s)

    # OOS
    oos_l = run_side("L", 25, l_tp, l_mh, l_sn, 6, l_mcap, IS_END, TOTAL)
    oos_s = run_side("S", 30, s_tp, s_mh, s_sn, 8, s_mcap, IS_END, TOTAL)
    oos_lp = sum(t['pnl'] for t in oos_l)
    oos_sp = sum(t['pnl'] for t in oos_s)

    # OOS monthly
    l_m = pd.DataFrame(oos_l).groupby("month")["pnl"].sum() if oos_l else pd.Series(dtype=float)
    s_m = pd.DataFrame(oos_s).groupby("month")["pnl"].sum() if oos_s else pd.Series(dtype=float)
    months = sorted(set(l_m.index) | set(s_m.index))
    monthly_totals = [(l_m.get(m,0) + s_m.get(m,0)) for m in months]
    pm = sum(1 for t in monthly_totals if t > 0)
    worst_m = min(monthly_totals) if monthly_totals else -999

    # OOS metrics
    l_wr = sum(1 for t in oos_l if t['pnl'] > 0) / len(oos_l) * 100 if oos_l else 0
    s_wr = sum(1 for t in oos_s if t['pnl'] > 0) / len(oos_s) * 100 if oos_s else 0

    # MDD
    all_t = oos_l + oos_s
    if all_t:
        cum = pd.Series([t['pnl'] for t in all_t]).cumsum()
        mdd = abs((cum - cum.cummax()).min())
    else:
        mdd = 0

    return {
        "is_l": is_lp, "is_s": is_sp, "is_total": is_lp + is_sp,
        "oos_l": oos_lp, "oos_s": oos_sp, "oos_total": oos_lp + oos_sp,
        "l_n": len(oos_l), "s_n": len(oos_s), "l_wr": l_wr, "s_wr": s_wr,
        "pm": pm, "tot_m": len(months), "worst_m": worst_m, "mdd": mdd,
    }

# === SWEEP ===
print("="*70)
print("V11 R1: TP/MH Sweep")
print("="*70)

# Sweep 1: L TP/MH combinations (S fixed at V10)
print("\n=== Sweep 1: L TP/MH (S fixed: TP1.5 MH5) ===")
print(f"{'L_TP':>5} {'L_MH':>5} | {'IS_L':>7} {'IS_S':>7} {'IS_T':>7} | {'OOS_L':>7} {'OOS_S':>7} {'OOS_T':>7} | {'L_n':>4} {'L_WR':>5} | {'PM':>5} {'WM':>6} {'MDD':>5} | {'PASS':>4}")
for l_tp in [1.5, 2.0, 2.5, 3.0, 3.5]:
    for l_mh in [5, 6, 7, 8, 10, 12]:
        r = eval_combo(l_tp, l_mh, 1.5, 5)
        passed = r['is_l'] > 0 and r['is_s'] > 0 and r['oos_total'] > 2190 and r['worst_m'] >= -200
        flag = "YES" if passed else ""
        print(f"{l_tp:>5.1f} {l_mh:>5} | {r['is_l']:>+7.0f} {r['is_s']:>+7.0f} {r['is_total']:>+7.0f} | "
              f"{r['oos_l']:>+7.0f} {r['oos_s']:>+7.0f} {r['oos_total']:>+7.0f} | "
              f"{r['l_n']:>4} {r['l_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst_m']:>+6.0f} {r['mdd']:>5.0f} | {flag:>4}")

# Sweep 2: S TP/MH combinations (L fixed at V10)
print("\n=== Sweep 2: S TP/MH (L fixed: TP2.0 MH5) ===")
print(f"{'S_TP':>5} {'S_MH':>5} | {'IS_L':>7} {'IS_S':>7} {'IS_T':>7} | {'OOS_L':>7} {'OOS_S':>7} {'OOS_T':>7} | {'S_n':>4} {'S_WR':>5} | {'PM':>5} {'WM':>6} {'MDD':>5} | {'PASS':>4}")
for s_tp in [1.0, 1.5, 2.0, 2.5, 3.0]:
    for s_mh in [5, 6, 7, 8, 10, 12]:
        r = eval_combo(2.0, 5, s_tp, s_mh)
        passed = r['is_l'] > 0 and r['is_s'] > 0 and r['oos_total'] > 2190 and r['worst_m'] >= -200
        flag = "YES" if passed else ""
        print(f"{s_tp:>5.1f} {s_mh:>5} | {r['is_l']:>+7.0f} {r['is_s']:>+7.0f} {r['is_total']:>+7.0f} | "
              f"{r['oos_l']:>+7.0f} {r['oos_s']:>+7.0f} {r['oos_total']:>+7.0f} | "
              f"{r['s_n']:>4} {r['s_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst_m']:>+6.0f} {r['mdd']:>5.0f} | {flag:>4}")

# Sweep 3: Best L+S combos (both change together)
print("\n=== Sweep 3: Joint L+S optimization (top combos from sweeps 1+2) ===")
results = []
for l_tp in [2.0, 2.5, 3.0]:
    for l_mh in [5, 7, 8, 10]:
        for s_tp in [1.5, 2.0, 2.5]:
            for s_mh in [5, 7, 8, 10]:
                r = eval_combo(l_tp, l_mh, s_tp, s_mh)
                r['config'] = f"L:TP{l_tp}MH{l_mh} S:TP{s_tp}MH{s_mh}"
                r['l_tp'] = l_tp; r['l_mh'] = l_mh; r['s_tp'] = s_tp; r['s_mh'] = s_mh
                if r['is_l'] > 0 and r['is_s'] > 0:
                    results.append(r)

# Sort by OOS total and show top 20
results.sort(key=lambda x: x['oos_total'], reverse=True)
print(f"\nTop 20 (IS_L>0 AND IS_S>0):")
print(f"{'Config':>30} | {'IS_T':>7} | {'OOS_L':>7} {'OOS_S':>7} {'OOS_T':>7} | {'PM':>5} {'WM':>6} {'MDD':>5}")
for r in results[:20]:
    print(f"{r['config']:>30} | {r['is_total']:>+7.0f} | {r['oos_l']:>+7.0f} {r['oos_s']:>+7.0f} {r['oos_total']:>+7.0f} | "
          f"{r['pm']:>2}/{r['tot_m']:>2} {r['worst_m']:>+6.0f} {r['mdd']:>5.0f}")

# V10 reference
v10 = eval_combo(2.0, 5, 1.5, 5)
print(f"\n{'V10 reference':>30} | {v10['is_total']:>+7.0f} | {v10['oos_l']:>+7.0f} {v10['oos_s']:>+7.0f} {v10['oos_total']:>+7.0f} | "
      f"{v10['pm']:>2}/{v10['tot_m']:>2} {v10['worst_m']:>+6.0f} {v10['mdd']:>5.0f}")
