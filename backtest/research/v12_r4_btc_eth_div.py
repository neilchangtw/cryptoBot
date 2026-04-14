"""V12 R4: BTC-ETH Divergence Short + TP+MaxHold
Direction C: When ETH significantly outperforms BTC, short ETH (mean-reversion).
V9 signal + V10-L exit paradigm = completely new combination.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

btc_cols = btc[["datetime", "close"]].rename(columns={"close": "btc_close"})
df = eth.merge(btc_cols, on="datetime", how="left")

NOTIONAL = 4000; FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float); h = df["high"].astype(float)
l = df["low"].astype(float); o = df["open"].astype(float)
btc_c = df["btc_close"].astype(float)

# Session filter
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# --- Relative Return (ETH - BTC) ---
for lb in [3, 5, 7, 10, 15]:
    eth_ret = c.pct_change(lb)
    btc_ret = btc_c.pct_change(lb)
    df[f"rel_ret_{lb}"] = (eth_ret - btc_ret).shift(1)

# Also compute rolling z-score of rel_ret for adaptive threshold
for lb in [5, 10]:
    rr = df[f"rel_ret_{lb}"]
    rr_mean = rr.rolling(100).mean()
    rr_std = rr.rolling(100).std()
    df[f"rel_ret_{lb}_z"] = ((rr - rr_mean) / rr_std)

# V11-E S baseline: GK compression breakout
ln_hl = np.log(h / l); ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100 if len(s)>1 else 50, raw=False)
df["brk_dn_15"] = c < c.shift(1).rolling(15).min()

arr_o = o.values; arr_h = h.values; arr_l = l.values; arr_c = c.values
arr_dt = df["datetime"].values; arr_sess = df["session_ok"].values


def run_short_generic(entry_mask, tp_pct, mh, sn_pct, cd, mcap, start, end):
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

        if entry_mask[i] and i + 1 < end:
            ep = arr_o[i+1]; idx = i+1; in_pos = True; m_ent += 1
    return trades


def eval_config(entry_mask, tp_pct, mh, sn_pct=4.0, cd=8, mcap=-150):
    is_t = run_short_generic(entry_mask, tp_pct, mh, sn_pct, cd, mcap, WARMUP, IS_END)
    is_pnl = sum(t["pnl"] for t in is_t)
    if is_pnl <= 0 or len(is_t) < 5:
        return None

    oos_t = run_short_generic(entry_mask, tp_pct, mh, sn_pct, cd, mcap, IS_END, TOTAL)
    oos_pnl = sum(t["pnl"] for t in oos_t)
    is_wr = sum(1 for t in is_t if t["pnl"]>0)/len(is_t)*100
    oos_wr = sum(1 for t in oos_t if t["pnl"]>0)/len(oos_t)*100 if oos_t else 0

    oos_m = pd.DataFrame(oos_t).groupby("month")["pnl"].sum() if oos_t else pd.Series(dtype=float)
    pm = sum(1 for m in oos_m.index if oos_m[m] > 0)
    worst = oos_m.min() if len(oos_m) else -999

    return {
        "is_n": len(is_t), "is_pnl": is_pnl, "is_wr": is_wr,
        "oos_n": len(oos_t), "oos_pnl": oos_pnl, "oos_wr": oos_wr,
        "pm": pm, "tot_m": len(oos_m), "worst": worst,
        "tp_pct": tp_pct, "mh": mh,
    }


# ===== Direction C1: Simple Relative Return Threshold =====
print("="*70)
print("  C1: ETH outperforms BTC → Short ETH (rel_ret > threshold)")
print("="*70)

results_c1 = []
for lb in [3, 5, 7, 10, 15]:
    col = f"rel_ret_{lb}"
    for thresh in [0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                vals = df[col].values
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if vals[i] == vals[i] and vals[i] >= thresh:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"lb{lb} th{thresh} tp{tp} mh{mh}"
                    r["lb"] = lb; r["thresh"] = thresh
                    results_c1.append(r)

results_c1.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_c1)}")
print(f"\n--- Top 20 by OOS PnL ---")
print(f"{'Config':>35} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_c1[:20]:
    print(f"{r['cfg']:>35} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction C2: Z-score based (adaptive threshold) =====
print("\n" + "="*70)
print("  C2: ETH-BTC rel_ret z-score > threshold → Short ETH")
print("="*70)

results_c2 = []
for lb in [5, 10]:
    col = f"rel_ret_{lb}_z"
    for z_thresh in [1.0, 1.5, 2.0, 2.5]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                vals = df[col].values
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if vals[i] == vals[i] and vals[i] >= z_thresh:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"lb{lb} z{z_thresh} tp{tp} mh{mh}"
                    results_c2.append(r)

results_c2.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_c2)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>35} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_c2[:15]:
    print(f"{r['cfg']:>35} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction C3: Combo — RelRet + Volume Spike =====
print("\n" + "="*70)
print("  C3: RelRet + Volume Spike combo")
print("="*70)

vol_ma20 = df["volume"].astype(float).rolling(20).mean()
df["vol_mult"] = (df["volume"].astype(float) / vol_ma20).shift(1)
df["candle_up"] = (c > o).shift(1).fillna(False).astype(bool)

results_c3 = []
for lb in [3, 5, 7]:
    col = f"rel_ret_{lb}"
    for thresh in [0.01, 0.015, 0.02]:
        for vol_min in [1.5, 2.0]:
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    rr_vals = df[col].values
                    vm_vals = df["vol_mult"].values
                    cu_vals = df["candle_up"].values
                    mask = np.array([False] * len(df))
                    for i in range(len(df)):
                        rr = rr_vals[i]; vm = vm_vals[i]
                        if rr == rr and vm == vm and rr >= thresh and vm >= vol_min and cu_vals[i]:
                            mask[i] = True
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"lb{lb} th{thresh} vm{vol_min} tp{tp} mh{mh}"
                        results_c3.append(r)

results_c3.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_c3)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>42} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_c3[:15]:
    print(f"{r['cfg']:>42} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== V11-E S Baseline =====
print("\n" + "="*70)
print("  V11-E S Baseline (GK<30 + BRK_DN_15)")
print("="*70)
mask_v11e = (df["gk_pct"] < 30).values & df["brk_dn_15"].values
r = eval_config(mask_v11e, 2.0, 7, sn_pct=4.0, cd=8, mcap=-150)
if r:
    print(f"  V11-E S: IS {r['is_n']}t ${r['is_pnl']:+.0f} WR {r['is_wr']:.1f}% | OOS {r['oos_n']}t ${r['oos_pnl']:+.0f} WR {r['oos_wr']:.1f}% | PM {r['pm']}/{r['tot_m']} WM ${r['worst']:+.0f}")


# ===== Best overall + monthly detail =====
all_results = results_c1 + results_c2 + results_c3
all_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

if all_results and all_results[0]["oos_pnl"] > 0:
    best = all_results[0]
    print(f"\n{'='*70}")
    print(f"  BEST OVERALL: {best['cfg']}")
    print(f"  IS: {best['is_n']}t ${best['is_pnl']:+.0f} WR {best['is_wr']:.1f}%")
    print(f"  OOS: {best['oos_n']}t ${best['oos_pnl']:+.0f} WR {best['oos_wr']:.1f}%")
    print(f"  PM: {best['pm']}/{best['tot_m']}  Worst: ${best['worst']:+.0f}")
    print(f"{'='*70}")

    # Monthly detail for best config
    # Re-run to get monthly breakdown
    # Figure out which sub-result list it came from
    cfg_str = best["cfg"]
    print(f"\n--- Monthly Detail: {cfg_str} ---")

    # Parse config to rebuild mask
    # Easiest: just re-run with the best params
    if best in results_c1:
        lb = best["lb"]; thresh = best["thresh"]
        col = f"rel_ret_{lb}"
        vals = df[col].values
        mask = np.array([False] * len(df))
        for i in range(len(df)):
            if vals[i] == vals[i] and vals[i] >= thresh:
                mask[i] = True
    elif best in results_c2:
        # z-score based - parse from cfg
        parts = cfg_str.split()
        lb = int(parts[0][2:]); z_thresh = float(parts[1][1:])
        col = f"rel_ret_{lb}_z"
        vals = df[col].values
        mask = np.array([False] * len(df))
        for i in range(len(df)):
            if vals[i] == vals[i] and vals[i] >= z_thresh:
                mask[i] = True
    else:
        # C3 combo - parse from cfg
        parts = cfg_str.split()
        lb = int(parts[0][2:]); thresh = float(parts[1][2:])
        vol_min = float(parts[2][2:])
        col = f"rel_ret_{lb}"
        rr_vals = df[col].values
        vm_vals = df["vol_mult"].values
        cu_vals = df["candle_up"].values
        mask = np.array([False] * len(df))
        for i in range(len(df)):
            rr = rr_vals[i]; vm = vm_vals[i]
            if rr == rr and vm == vm and rr >= thresh and vm >= vol_min and cu_vals[i]:
                mask[i] = True

    oos_t = run_short_generic(mask, best["tp_pct"], best["mh"], 4.0, 8, -150, IS_END, TOTAL)
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
else:
    print(f"\n*** NO OOS-positive candidate found across all C directions ***")
    oos_pos = sum(1 for r in all_results if r["oos_pnl"] > 0)
    print(f"  OOS>0: {oos_pos}/{len(all_results)} configs")
