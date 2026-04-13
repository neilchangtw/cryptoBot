"""V11 R1b: Validate top combos from R1 sweep with WF + monthly detail"""
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
                pnl = ((xp/ep - 1) if side == "L" else (1 - xp/ep)) * NOTIONAL - FEE
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
        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_t and brk[i] and i + 1 < end:
            ep = arr_o[i+1]; idx = i+1; in_pos = True; m_ent += 1
    return trades


def full_report(name, l_cfg, s_cfg):
    """Full validation report for a config."""
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"  L: GK<{l_cfg['gk']} BL15 TP{l_cfg['tp']}% MH{l_cfg['mh']} SN{l_cfg['sn']}% cd{l_cfg['cd']} mc{l_cfg['mcap']}")
    print(f"  S: GK<{s_cfg['gk']} BL15 TP{s_cfg['tp']}% MH{s_cfg['mh']} SN{s_cfg['sn']}% cd{s_cfg['cd']} mc{s_cfg['mcap']}")
    print(f"{'='*70}")

    # IS
    is_l = run_side("L", l_cfg['gk'], l_cfg['tp'], l_cfg['mh'], l_cfg['sn'], l_cfg['cd'], l_cfg['mcap'], WARMUP, IS_END)
    is_s = run_side("S", s_cfg['gk'], s_cfg['tp'], s_cfg['mh'], s_cfg['sn'], s_cfg['cd'], s_cfg['mcap'], WARMUP, IS_END)
    is_lp = sum(t['pnl'] for t in is_l); is_sp = sum(t['pnl'] for t in is_s)
    is_lwr = sum(1 for t in is_l if t['pnl']>0)/len(is_l)*100 if is_l else 0
    is_swr = sum(1 for t in is_s if t['pnl']>0)/len(is_s)*100 if is_s else 0
    print(f"\nIS L: {len(is_l)}t ${is_lp:+.0f} WR {is_lwr:.1f}%")
    print(f"IS S: {len(is_s)}t ${is_sp:+.0f} WR {is_swr:.1f}%")
    print(f"IS Total: ${is_lp+is_sp:+.0f}")

    # OOS
    oos_l = run_side("L", l_cfg['gk'], l_cfg['tp'], l_cfg['mh'], l_cfg['sn'], l_cfg['cd'], l_cfg['mcap'], IS_END, TOTAL)
    oos_s = run_side("S", s_cfg['gk'], s_cfg['tp'], s_cfg['mh'], s_cfg['sn'], s_cfg['cd'], s_cfg['mcap'], IS_END, TOTAL)
    oos_lp = sum(t['pnl'] for t in oos_l); oos_sp = sum(t['pnl'] for t in oos_s)
    oos_lwr = sum(1 for t in oos_l if t['pnl']>0)/len(oos_l)*100 if oos_l else 0
    oos_swr = sum(1 for t in oos_s if t['pnl']>0)/len(oos_s)*100 if oos_s else 0

    # Exit distribution
    for label, trades in [("OOS L", oos_l), ("OOS S", oos_s)]:
        if not trades: continue
        t = pd.DataFrame(trades)
        lw = t[t['pnl']>0]; lo = t[t['pnl']<=0]
        gw = lw['pnl'].sum() if len(lw) else 0
        gl = abs(lo['pnl'].sum()) if len(lo) else 0.001
        pf = gw/gl
        cum = t['pnl'].cumsum(); mdd = abs((cum - cum.cummax()).min())
        wr = len(lw)/len(t)*100
        print(f"\n{label}: {len(t)}t ${t['pnl'].sum():+.0f} WR {wr:.1f}% PF {pf:.2f} MDD ${mdd:.0f}")
        for et in ['TP', 'MaxHold', 'SafeNet']:
            et_df = t[t['reason'] == et]
            if len(et_df) > 0:
                print(f"  {et}: {len(et_df)}t avg ${et_df['pnl'].mean():+.1f} WR {(et_df['pnl']>0).sum()/len(et_df)*100:.0f}%")

    # Monthly detail
    l_m = pd.DataFrame(oos_l).groupby("month")["pnl"].sum() if oos_l else pd.Series(dtype=float)
    s_m = pd.DataFrame(oos_s).groupby("month")["pnl"].sum() if oos_s else pd.Series(dtype=float)
    months = sorted(set(l_m.index) | set(s_m.index))

    # V10 reference monthly
    v10_l_m = {"2025-04":-89,"2025-05":229,"2025-06":-129,"2025-07":169,"2025-08":-88,
               "2025-09":182,"2025-10":-52,"2025-11":163,"2025-12":127,"2026-01":-94,
               "2026-02":294,"2026-03":161,"2026-04":139}
    v10_s_m = {"2025-04":29,"2025-05":220,"2025-06":0,"2025-07":-56,"2025-08":-49,
               "2025-09":116,"2025-10":313,"2025-11":29,"2025-12":32,"2026-01":179,
               "2026-02":253,"2026-03":225,"2026-04":-114}

    print(f"\n{'Month':>10} {'L':>7} {'S':>7} {'L+S':>7} | {'V10_L':>7} {'V10_S':>7} {'V10':>7} | {'Delta':>7}")
    total_v11 = total_v10 = 0; pm = 0; worst = 999
    for m in months:
        lp = l_m.get(m, 0); sp = s_m.get(m, 0); t = lp + sp
        v10_lp = v10_l_m.get(m, 0); v10_sp = v10_s_m.get(m, 0); v10_t = v10_lp + v10_sp
        delta = t - v10_t
        total_v11 += t; total_v10 += v10_t
        if t > 0: pm += 1
        worst = min(worst, t)
        print(f"  {m:>10} {lp:>+7.0f} {sp:>+7.0f} {t:>+7.0f} | {v10_lp:>+7.0f} {v10_sp:>+7.0f} {v10_t:>+7.0f} | {delta:>+7.0f}")
    print(f"  {'TOTAL':>10} {'':>7} {'':>7} {total_v11:>+7.0f} | {'':>7} {'':>7} {total_v10:>+7.0f} | {total_v11-total_v10:>+7.0f}")
    print(f"  PM: {pm}/{len(months)}, worst: ${worst:.0f}")

    # Combined MDD
    all_t = sorted(oos_l + oos_s, key=lambda x: x.get('held', 0))
    cum = pd.Series([t['pnl'] for t in all_t]).cumsum()
    cmdd = abs((cum - cum.cummax()).min())
    print(f"  Combined MDD: ${cmdd:.0f}")

    # Walk-Forward
    for nfolds in [6, 8]:
        fold_size = (TOTAL - WARMUP) // nfolds
        wf_results = []
        for fold in range(nfolds):
            s = WARMUP + fold * fold_size
            e = s + fold_size if fold < nfolds - 1 else TOTAL
            fl = run_side("L", l_cfg['gk'], l_cfg['tp'], l_cfg['mh'], l_cfg['sn'], l_cfg['cd'], l_cfg['mcap'], s, e)
            fs = run_side("S", s_cfg['gk'], s_cfg['tp'], s_cfg['mh'], s_cfg['sn'], s_cfg['cd'], s_cfg['mcap'], s, e)
            lp = sum(t['pnl'] for t in fl); sp = sum(t['pnl'] for t in fs)
            wf_results.append(lp + sp)
        pos = sum(1 for r in wf_results if r > 0)
        print(f"  WF {nfolds}-fold: {pos}/{nfolds} positive — " + " | ".join(f"${r:+.0f}" for r in wf_results))

    return {"oos_total": total_v11, "pm": pm, "worst": worst, "mdd": cmdd}


# === CANDIDATES ===
configs = [
    ("V10 baseline",
     {"gk":25, "tp":2.0, "mh":5, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":1.5, "mh":5, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-A: L:TP3.5MH6 S:V10",
     {"gk":25, "tp":3.5, "mh":6, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":1.5, "mh":5, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-B: L:TP3.0MH6 S:V10",
     {"gk":25, "tp":3.0, "mh":6, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":1.5, "mh":5, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-C: L:V10 S:TP2.0MH7",
     {"gk":25, "tp":2.0, "mh":5, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":2.0, "mh":7, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-D: L:TP2.5MH5 S:TP2.0MH7",
     {"gk":25, "tp":2.5, "mh":5, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":2.0, "mh":7, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-E: L:TP3.5MH6 S:TP2.0MH7",
     {"gk":25, "tp":3.5, "mh":6, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":2.0, "mh":7, "sn":4.0, "cd":8, "mcap":-150}),

    ("V11-F: L:TP3.0MH8 S:TP2.0MH7",
     {"gk":25, "tp":3.0, "mh":8, "sn":3.5, "cd":6, "mcap":-75},
     {"gk":30, "tp":2.0, "mh":7, "sn":4.0, "cd":8, "mcap":-150}),
]

for name, l_cfg, s_cfg in configs:
    full_report(name, l_cfg, s_cfg)

# Summary comparison
print("\n\n" + "="*70)
print("SUMMARY COMPARISON")
print("="*70)
print(f"{'Config':>35} | {'OOS':>7} | {'PM':>5} | {'WM':>6} | {'MDD':>5}")
