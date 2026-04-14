"""V12 R2: Momentum Exhaustion Short + TP+MaxHold Grid Search"""
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
v = df["volume"].astype(float)

# Session
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# Streak: consecutive up closes (shift(1) for no lookahead)
streak = pd.Series(0, index=df.index)
for i in range(1, len(df)):
    if c.iloc[i] > c.iloc[i-1]:
        streak.iloc[i] = streak.iloc[i-1] + 1
    else:
        streak.iloc[i] = 0
df["streak_up"] = streak.shift(1)

# TBR percentile
if "taker_buy_base_vol" in df.columns:
    df["tbr"] = df["taker_buy_base_vol"].astype(float) / v
else:
    df["tbr"] = 0.5
df["tbr_pct"] = df["tbr"].shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100 if len(s)>1 else 50, raw=False)

# EMA deviation (shift(1))
ema20 = c.ewm(span=20).mean()
df["ema_dev"] = ((c - ema20) / ema20 * 100).shift(1)

arr_o = o.values; arr_h = h.values; arr_l = l.values; arr_c = c.values
arr_dt = df["datetime"].values; arr_sess = df["session_ok"].values
arr_streak = df["streak_up"].values
arr_tbr_pct = df["tbr_pct"].values
arr_ema_dev = df["ema_dev"].values


def run_short(streak_min, tbr_min, ema_dev_min, tp_pct, mh, sn_pct, cd, mcap, start, end):
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
            sn_level = ep * (1 + sn)
            if arr_h[i] >= sn_level:
                xp = sn_level + (arr_h[i] - sn_level) * 0.25; xr = "SafeNet"
            if xr == "" and arr_l[i] <= ep * (1 - tp):
                xp = ep * (1 - tp); xr = "TP"
            if xr == "" and held >= mh:
                xp = arr_c[i]; xr = "MaxHold"
            if xr:
                pnl = (1 - xp/ep) * NOTIONAL - FEE
                trades.append({"pnl": pnl, "reason": xr, "month": m_str, "held": held})
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

        sk = arr_streak[i]
        tbr = arr_tbr_pct[i]
        edev = arr_ema_dev[i]
        if sk != sk or tbr != tbr or edev != edev: continue

        if sk >= streak_min and tbr >= tbr_min and edev >= ema_dev_min:
            if i + 1 < end:
                ep = arr_o[i+1]; idx = i+1; in_pos = True; m_ent += 1
    return trades


# ===== Grid Search =====
print("="*70)
print("  V12 R2: Momentum Exhaustion Short + TP+MaxHold")
print("="*70)

results = []
for streak_min in [2, 3, 4]:
    for tbr_min in [0, 60, 70, 80]:
        for ema_dev_min in [0, 1.0, 2.0, 3.0]:
            for tp_pct in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    is_t = run_short(streak_min, tbr_min, ema_dev_min, tp_pct, mh, 4.0, 8, -150, WARMUP, IS_END)
                    is_pnl = sum(t["pnl"] for t in is_t)
                    if is_pnl <= 0 or len(is_t) < 5: continue

                    oos_t = run_short(streak_min, tbr_min, ema_dev_min, tp_pct, mh, 4.0, 8, -150, IS_END, TOTAL)
                    oos_pnl = sum(t["pnl"] for t in oos_t)

                    oos_m = pd.DataFrame(oos_t).groupby("month")["pnl"].sum() if oos_t else pd.Series(dtype=float)
                    months = sorted(oos_m.index)
                    pm = sum(1 for m in months if oos_m[m] > 0)
                    worst = oos_m.min() if len(oos_m) else -999

                    is_wr = sum(1 for t in is_t if t["pnl"]>0)/len(is_t)*100
                    oos_wr = sum(1 for t in oos_t if t["pnl"]>0)/len(oos_t)*100 if oos_t else 0

                    results.append({
                        "cfg": f"sk{streak_min} tbr{tbr_min} ed{ema_dev_min} tp{tp_pct} mh{mh}",
                        "is_n": len(is_t), "is_pnl": is_pnl, "is_wr": is_wr,
                        "oos_n": len(oos_t), "oos_pnl": oos_pnl, "oos_wr": oos_wr,
                        "pm": pm, "tot_m": len(months), "worst": worst,
                        "streak_min": streak_min, "tbr_min": tbr_min, "ema_dev_min": ema_dev_min,
                        "tp_pct": tp_pct, "mh": mh,
                    })

results.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nTotal configs with IS>0: {len(results)}")
print(f"\n--- Top 20 by OOS PnL ---")
hdr = f"{'Config':>35} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}"
print(hdr)
for r in results[:20]:
    print(f"{r['cfg']:>35} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")

# Best candidate monthly detail + V11-E comparison
if results:
    best = results[0]
    print(f"\n--- Best: {best['cfg']} Monthly Detail ---")
    oos_t = run_short(best["streak_min"], best["tbr_min"], best["ema_dev_min"],
                      best["tp_pct"], best["mh"], 4.0, 8, -150, IS_END, TOTAL)
    oos_m = pd.DataFrame(oos_t).groupby("month")["pnl"].sum()

    v11e_l = {"2025-04":117,"2025-05":329,"2025-06":19,"2025-07":248,"2025-08":115,
              "2025-09":62,"2025-10":-111,"2025-11":139,"2025-12":105,
              "2026-01":-24,"2026-02":355,"2026-03":117,"2026-04":166}
    v11e_s = {"2025-04":106,"2025-05":174,"2025-06":0,"2025-07":-139,"2025-08":4,
              "2025-09":100,"2025-10":296,"2025-11":13,"2025-12":94,
              "2026-01":108,"2026-02":274,"2026-03":296,"2026-04":-175}

    months = sorted(set(oos_m.index) | set(v11e_l.keys()))
    print(f"{'Month':>10} {'L(V11E)':>8} {'S(new)':>8} {'L+Snew':>8} | {'S(V11E)':>8} {'L+Sv11':>8} | {'Delta':>7}")
    t_new = t_old = 0; pm_new = pm_old = 0; wm_new = 999; wm_old = 999
    for m in months:
        lp = v11e_l.get(m, 0)
        sp_new = oos_m.get(m, 0); sp_old = v11e_s.get(m, 0)
        ls_new = lp + sp_new; ls_old = lp + sp_old
        t_new += ls_new; t_old += ls_old
        if ls_new > 0: pm_new += 1
        if ls_old > 0: pm_old += 1
        wm_new = min(wm_new, ls_new); wm_old = min(wm_old, ls_old)
        print(f"  {m:>10} {lp:>+8.0f} {sp_new:>+8.0f} {ls_new:>+8.0f} | {sp_old:>+8.0f} {ls_old:>+8.0f} | {ls_new-ls_old:>+7.0f}")
    print(f"  {'TOTAL':>10} {'':>8} {'':>8} {t_new:>+8.0f} | {'':>8} {t_old:>+8.0f} | {t_new-t_old:>+7.0f}")
    print(f"  PM: {pm_new}/{len(months)} vs {pm_old}/{len(months)}")
    print(f"  Worst: ${wm_new:.0f} vs ${wm_old:.0f}")

    # Complementarity check
    print(f"\n--- Complementarity Check ---")
    l_weak = {"2025-06": "L weak", "2025-09": "L weak", "2025-10": "L loss", "2026-01": "L loss"}
    for m, reason in l_weak.items():
        sp = oos_m.get(m, 0)
        print(f"  {m} ({reason}): S(new)=${sp:+.0f}, S(V11E)=${v11e_s.get(m,0):+.0f}")
