"""
V10-L Round 10: Validation & Stress Test

Champion from R9:
  GK<25 BL=12 TP=2.5% maxH=7 SN=4.0% mCap=-100 cd=6
  IS: 56t $+67 | OOS: 95t $+1,110 WR=60% MDD=$264 WM=-$72
  L+S: $4,517 12/13 pm, worst L+S -$54, L+S MDD $54
  Neighbors: 10/11 ALL PASS

Runner-up:
  GK<25 BL=15 TP=2.0% maxH=5 SN=3.5% mCap=-75 cd=6
  IS: 53t $+93 | OOS: 83t $+1,090 WR=59% MDD=$269 WM=-$128

Validation plan:
  1. Walk-Forward: 6, 8, 10 folds for both candidates
  2. Fee/Slippage Stress Test: fee $4/$5/$6, SN penetration 25%/35%/50%
  3. Parameter Plateau: +-1 step for each parameter, how many pass
  4. Anti-lookahead Checklist: verify all shift(1) and entry at O[i+1]
  5. IS Robustness: bootstrap resample IS to test if IS>0 is stable
  6. Top 3 Comparison
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; BASE_FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# GK volatility
ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
gk_pct_raw = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)
df["gk_pct"] = gk_pct_raw

# Close breakout
for bl in [10, 12, 15]:
    df[f"brk_up_{bl}"] = c > c.shift(1).rolling(bl).max()

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# Pre-extract arrays
arr_low    = df["low"].values
arr_high   = df["high"].values
arr_close  = df["close"].values
arr_open   = df["open"].values
arr_gk     = df["gk_pct"].values
arr_sess   = df["session_ok"].values
arr_dt     = df["datetime"].values
brk_arrs = {}
for bl in [10, 12, 15]:
    brk_arrs[bl] = df[f"brk_up_{bl}"].values

V10_SHORT = {
    "2025-04":527,"2025-05":93,"2025-06":388,"2025-07":76,"2025-08":392,
    "2025-09":324,"2025-10":467,"2025-11":332,"2025-12":286,
    "2026-01":18,"2026-02":406,"2026-03":98,
}

CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

def run_bt(p, start, end, fee_override=None, sn_pen=0.25):
    gk_t = p["gk_thresh"]; bl = p["bl"]
    sn = p["safenet"]; tp_pct = p["tp"]
    maxh = p["max_hold"]
    cd = p.get("exit_cd", 10); cap = p.get("entry_cap", 20)
    m_loss_cap = p.get("m_loss_cap", -200)
    fee = fee_override if fee_override is not None else BASE_FEE

    brk = brk_arrs[bl]
    trades = []; in_pos = False; pos_ep = 0.0; pos_idx = 0
    last_exit = -999
    d_pnl = m_pnl = 0.0; consec = 0; cd_until = 0
    cur_m_str = ""; cur_d_str = ""; m_ent = 0

    for i in range(start, end):
        dt_ts = pd.Timestamp(arr_dt[i])
        m_str = str(dt_ts.to_period("M"))
        d_str = str(dt_ts.date())

        if d_str != cur_d_str: d_pnl = 0.0; cur_d_str = d_str
        if m_str != cur_m_str: m_pnl = 0.0; m_ent = 0; cur_m_str = m_str

        if in_pos:
            held = i - pos_idx
            xp = 0.0; xr = ""

            sn_level = pos_ep * (1 - sn / 100)
            if arr_low[i] <= sn_level:
                xp = sn_level - (sn_level - arr_low[i]) * sn_pen
                xr = "SafeNet"

            if xr == "":
                tp_level = pos_ep * (1 + tp_pct / 100)
                if arr_high[i] >= tp_level:
                    xp = tp_level
                    xr = "TP"

            if xr == "" and held >= maxh:
                xp = arr_close[i]
                xr = "MaxHold"

            if xr != "":
                pnl = (xp / pos_ep - 1) * NOTIONAL - fee
                trades.append({"pnl": pnl, "reason": xr, "month": m_str, "held": held})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue

        if d_pnl <= -200: continue
        if m_pnl <= m_loss_cap: continue
        if i < cd_until: continue
        if i - last_exit < cd: continue
        if m_ent >= cap: continue
        if not arr_sess[i]: continue

        gk_val = arr_gk[i]
        if gk_val != gk_val or gk_val >= gk_t: continue
        if not brk[i]: continue

        if i + 1 < end:
            pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True
            m_ent += 1

    if in_pos:
        pnl = (arr_close[end-1] / pos_ep - 1) * NOTIONAL - fee
        dt_ts = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD",
            "month": str(dt_ts.to_period("M")), "held": end-1-pos_idx})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t = pd.DataFrame(trades); n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; l = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(l["pnl"].sum()) if len(l) else 0.001
    cum = t["pnl"].cumsum(); mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    return {"n":n, "pnl":pnl, "pf":gw/gl, "wr":wr, "mdd":mdd,
            "pos_m":(mo>0).sum(), "tot_m":len(mo), "worst_m":mo.min(),
            "monthly":mo}

def ls_metrics(l_monthly):
    ls_months = {}
    for m, lp in l_monthly.items():
        sp = V10_SHORT.get(m, 0)
        ls_months[m] = lp + sp
    ls_series = pd.Series(ls_months)
    ls_cum = ls_series.sort_index().cumsum()
    ls_mdd = abs((ls_cum - ls_cum.cummax()).min()) if len(ls_cum) > 0 else 0
    ls_pos = (ls_series > 0).sum()
    ls_wm = ls_series.min() if len(ls_series) > 0 else 0
    ls_total = ls_series.sum()
    s_total = sum(V10_SHORT.get(m, 0) for m in l_monthly.keys())
    return {"ls_total": ls_total, "ls_pos": ls_pos, "ls_wm": ls_wm,
            "ls_mdd": ls_mdd, "s_total": s_total}

# ═══ Candidates ═══════════════════════════════════════════
CHAMP = {"gk_thresh":25, "bl":12, "tp":2.5, "safenet":4.0,
         "max_hold":7, "m_loss_cap":-100, "exit_cd":6, "entry_cap":20}
RUNNER = {"gk_thresh":25, "bl":15, "tp":2.0, "safenet":3.5,
          "max_hold":5, "m_loss_cap":-75, "exit_cd":6, "entry_cap":20}
CAND3 = {"gk_thresh":25, "bl":15, "tp":2.5, "safenet":3.5,
         "max_hold":7, "m_loss_cap":-75, "exit_cd":6, "entry_cap":20}

CANDIDATES = [("Champion", CHAMP), ("Runner-up", RUNNER), ("Candidate3", CAND3)]

print("=" * 65)
print("V10-L R10: VALIDATION & STRESS TEST")
print("=" * 65)

# ═══ Part 1: Full IS + OOS report ═════════════════════════
print(f"\n{'='*65}")
print("PART 1: FULL REPORT FOR TOP 3")
print(f"{'='*65}")

for name, p in CANDIDATES:
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)

    print(f"\n{'~'*60}")
    print(f"[{name}] GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']}% "
          f"maxH={p['max_hold']} SN={p['safenet']}% mCap={p['m_loss_cap']} cd={p['exit_cd']}")
    print(f"  IS:  {is_m['n']}t ${is_m['pnl']:+.0f} PF={is_m['pf']:.2f} WR={is_m['wr']:.1f}%")
    print(f"  OOS: {oos_m['n']}t ${oos_m['pnl']:+.0f} PF={oos_m['pf']:.2f} WR={oos_m['wr']:.1f}% "
          f"MDD=${oos_m['mdd']:.0f} WM=${oos_m['worst_m']:+.0f} PM={oos_m['pos_m']}/{oos_m['tot_m']}")

    if "monthly" in oos_m:
        ls = ls_metrics(oos_m["monthly"])
        print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
        for m in sorted(oos_m["monthly"].keys()):
            lp = oos_m["monthly"][m]; sp = V10_SHORT.get(m,0)
            f = " *" if sp < 100 else ""
            print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {lp+sp:>+7.0f}{f}")
        print(f"  {'TOTAL':>8} {oos_m['pnl']:>+7.0f} {ls['s_total']:>+7.0f} {ls['ls_total']:>+7.0f}")
        print(f"  L+S: {ls['ls_pos']}/{oos_m['tot_m']} pm, wm=${ls['ls_wm']:+.0f}, mdd=${ls['ls_mdd']:.0f}")

    t = pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason, g in t.groupby("reason"):
            gw = len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                  f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# ═══ Part 2: Walk-Forward (6, 8, 10 folds) ═══════════════
print(f"\n{'='*65}")
print("PART 2: WALK-FORWARD MULTI-FOLD")
print(f"{'='*65}")

for name, p in CANDIDATES:
    print(f"\n[{name}]")
    for n_folds in [6, 8, 10]:
        fs_size = (TOTAL - WARMUP) // n_folds
        wf = 0; fold_results = []
        for fold in range(n_folds):
            fs = WARMUP + fold * fs_size
            fe = fs + fs_size if fold < n_folds - 1 else TOTAL - 1
            ft = run_bt(p, fs, fe); fm = met(ft)
            ok = fm["pnl"] > 0; wf += ok
            dt_s = pd.Timestamp(arr_dt[fs]).strftime('%Y-%m')
            dt_e = pd.Timestamp(arr_dt[min(fe, TOTAL-1)]).strftime('%Y-%m')
            fold_results.append(f"  {fold+1}: {dt_s}~{dt_e} {fm['n']}t ${fm['pnl']:>+.0f} "
                                f"WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
        print(f"  {n_folds}-Fold: {wf}/{n_folds}")
        for fr in fold_results:
            print(f"  {fr}")

# ═══ Part 3: Fee/Slippage Stress Test ═════════════════════
print(f"\n{'='*65}")
print("PART 3: FEE & SLIPPAGE STRESS TEST")
print(f"{'='*65}")

for name, p in CANDIDATES:
    print(f"\n[{name}]")
    print(f"  {'Fee':>5} {'SNpen':>6} | {'IS$':>6} {'OOS$':>7} {'WR':>5} {'MDD':>5} {'WM':>6} {'PM':>5} | "
          f"{'L+S':>6} {'LPM':>4} {'LWM':>5}")
    for fee in [4.0, 5.0, 6.0]:
        for sn_pen in [0.25, 0.35, 0.50]:
            is_t = run_bt(p, WARMUP, IS_END, fee_override=fee, sn_pen=sn_pen)
            oos_t = run_bt(p, IS_END, TOTAL-1, fee_override=fee, sn_pen=sn_pen)
            is_m = met(is_t); oos_m = met(oos_t)
            ls = ls_metrics(oos_m.get("monthly", {})) if oos_m.get("monthly") is not None else {}
            ls_t = ls.get("ls_total", 0); ls_p = ls.get("ls_pos", 0); ls_w = ls.get("ls_wm", 0)
            pm = f"{oos_m['pos_m']}/{oos_m['tot_m']}" if oos_m['tot_m'] > 0 else "0/0"
            tag = ""
            if is_m["pnl"] <= 0: tag += " [IS<0]"
            if oos_m["mdd"] > 400: tag += " [MDD!]"
            if oos_m["worst_m"] < -200: tag += " [WM!]"
            print(f"  ${fee:.0f}  {sn_pen*100:>4.0f}%  | {is_m['pnl']:>+6.0f} {oos_m['pnl']:>+7.0f} "
                  f"{oos_m['wr']:>5.1f} {oos_m['mdd']:>5.0f} {oos_m['worst_m']:>+6.0f} {pm:>5} | "
                  f"{ls_t:>+6.0f} {ls_p:>4} {ls_w:>+5.0f}{tag}")

# ═══ Part 4: Parameter Plateau (Champion only) ═══════════
print(f"\n{'='*65}")
print("PART 4: PARAMETER PLATEAU (Champion)")
print(f"{'='*65}")

def ind_pass(is_m, oos_m):
    return (is_m["pnl"] > 0 and oos_m["pnl"] > 0 and
            oos_m["pos_m"] >= min(8, oos_m["tot_m"]) and
            oos_m["worst_m"] >= -200 and oos_m["mdd"] <= 400)

def full_pass(is_m, oos_m):
    if not ind_pass(is_m, oos_m): return False
    ls = ls_metrics(oos_m.get("monthly", {}))
    return (ls["ls_pos"] >= 11 and ls["ls_wm"] >= -150 and
            ls["ls_mdd"] <= 500 and ls["ls_total"] > ls["s_total"])

# Sweep each parameter individually
param_sweeps = {
    "gk_thresh": [15, 20, 25, 30, 35, 40],
    "bl": [8, 10, 12, 15, 18],
    "tp": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "max_hold": [3, 4, 5, 6, 7, 8, 10, 12],
    "safenet": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
    "m_loss_cap": [-50, -75, -100, -125, -150, -200],
    "exit_cd": [4, 6, 8, 10, 12],
}

for param, values in param_sweeps.items():
    print(f"\n  [{param}] (champion={CHAMP[param]})")
    for v in values:
        p = CHAMP.copy(); p[param] = v
        # Check breakout array exists
        if param == "bl" and v not in brk_arrs:
            df[f"brk_up_{v}"] = c > c.shift(1).rolling(v).max()
            brk_arrs[v] = df[f"brk_up_{v}"].values
        is_t = run_bt(p, WARMUP, IS_END)
        oos_t = run_bt(p, IS_END, TOTAL-1)
        is_m = met(is_t); oos_m = met(oos_t)
        fp = full_pass(is_m, oos_m)
        ip = ind_pass(is_m, oos_m)
        tag = "ALL" if fp else ("IND" if ip else "---")
        star = " <--" if v == CHAMP[param] else ""
        print(f"    {v:>6} -> IS ${is_m['pnl']:>+6.0f} | OOS ${oos_m['pnl']:>+6.0f} "
              f"WR={oos_m['wr']:>4.0f}% MDD=${oos_m['mdd']:>4.0f} WM=${oos_m['worst_m']:>+5.0f} "
              f"[{tag}]{star}")

# ═══ Part 5: Anti-Lookahead Checklist ═════════════════════
print(f"\n{'='*65}")
print("PART 5: ANTI-LOOKAHEAD CHECKLIST")
print(f"{'='*65}")

checks = [
    ("GK pctile shift(1)", "gk_ratio.shift(1).rolling(100)", True,
     "Current bar's GK ratio excluded from percentile calc"),
    ("Breakout shift(1)", "c > c.shift(1).rolling(bl).max()", True,
     "Current bar compared to previous bars' high, not itself"),
    ("Session filter", "Uses current bar datetime", True,
     "No future info needed"),
    ("Entry at O[i+1]", "pos_ep = arr_open[i+1]", True,
     "Signal on bar i, enter at next bar open"),
    ("Exit on current bar", "Uses bar i OHLC only", True,
     "No future bars accessed during exit check"),
    ("Monthly loss cap", "Cumulative within-month", True,
     "Only uses past PnL, no future info"),
    ("No 4h data", "Pure 1h timeframe", True,
     "No multi-timeframe lookahead risk (unlike V10-S)"),
]

for name, impl, ok, note in checks:
    print(f"  [{'x' if ok else ' '}] {name}")
    print(f"      {impl}")
    print(f"      {note}")

# Verify shift(1) on actual data
print(f"\n  Spot-check: GK pctile at bar 200")
print(f"    gk_ratio[200] = {gk_ratio.iloc[200]:.6f}")
print(f"    gk_pct[200]   = {df['gk_pct'].iloc[200]:.2f}  (uses ratio[199] and earlier)")
print(f"    gk_ratio[199] = {gk_ratio.iloc[199]:.6f}")

# Verify entry timing
champ_oos = run_bt(CHAMP, IS_END, TOTAL-1)
if champ_oos:
    print(f"\n  Spot-check: First 3 OOS entries")
    # Re-run with entry tracking
    gk_t = CHAMP["gk_thresh"]; bl = CHAMP["bl"]
    sn = CHAMP["safenet"]; tp_pct = CHAMP["tp"]
    maxh = CHAMP["max_hold"]; cd = CHAMP["exit_cd"]
    brk = brk_arrs[bl]
    pos_ep = 0.0; pos_idx = 0; in_pos = False; last_exit = -999
    d_pnl = m_pnl = 0.0; consec = 0; cd_until = 0
    cur_m_str = ""; cur_d_str = ""; m_ent = 0; entry_count = 0
    for i in range(IS_END, TOTAL-1):
        dt_ts = pd.Timestamp(arr_dt[i])
        m_str = str(dt_ts.to_period("M")); d_str = str(dt_ts.date())
        if d_str != cur_d_str: d_pnl = 0.0; cur_d_str = d_str
        if m_str != cur_m_str: m_pnl = 0.0; m_ent = 0; cur_m_str = m_str
        if in_pos:
            held = i - pos_idx; xp = 0.0; xr = ""
            sn_level = pos_ep * (1 - sn / 100)
            if arr_low[i] <= sn_level:
                xp = sn_level - (sn_level - arr_low[i]) * 0.25; xr = "SafeNet"
            if xr == "":
                tp_level = pos_ep * (1 + tp_pct / 100)
                if arr_high[i] >= tp_level: xp = tp_level; xr = "TP"
            if xr == "" and held >= maxh: xp = arr_close[i]; xr = "MaxHold"
            if xr != "":
                pnl = (xp / pos_ep - 1) * NOTIONAL - BASE_FEE
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= 4: cd_until = i + 24
                else: consec = 0
                last_exit = i; in_pos = False
        if in_pos: continue
        if d_pnl <= -200: continue
        if m_pnl <= CHAMP["m_loss_cap"]: continue
        if i < cd_until: continue
        if i - last_exit < cd: continue
        if m_ent >= 20: continue
        if not arr_sess[i]: continue
        gk_val = arr_gk[i]
        if gk_val != gk_val or gk_val >= gk_t: continue
        if not brk[i]: continue
        if i + 1 < TOTAL-1:
            pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True; m_ent += 1
            entry_count += 1
            if entry_count <= 3:
                sig_dt = pd.Timestamp(arr_dt[i]).strftime('%Y-%m-%d %H:%M')
                ent_dt = pd.Timestamp(arr_dt[i+1]).strftime('%Y-%m-%d %H:%M')
                print(f"    Entry #{entry_count}: signal at {sig_dt} (bar {i}), "
                      f"enter at {ent_dt} (bar {i+1}) O=${arr_open[i+1]:.2f}")
                print(f"      GK_pct={gk_val:.1f} < {gk_t}, brk_up_{bl}=True")
            if entry_count >= 3: break

# ═══ Part 6: Consecutive Loss Analysis ════════════════════
print(f"\n{'='*65}")
print("PART 6: CONSECUTIVE LOSS ANALYSIS (Champion)")
print(f"{'='*65}")

full_trades = run_bt(CHAMP, WARMUP, TOTAL-1)
if full_trades:
    t = pd.DataFrame(full_trades)
    # Find max consecutive losses
    is_loss = (t["pnl"] < 0).values
    max_streak = 0; cur_streak = 0
    streaks = []
    for x in is_loss:
        if x:
            cur_streak += 1
        else:
            if cur_streak > 0: streaks.append(cur_streak)
            cur_streak = 0
    if cur_streak > 0: streaks.append(cur_streak)
    max_streak = max(streaks) if streaks else 0

    print(f"  Total trades (full period): {len(t)}")
    print(f"  Win rate: {(t['pnl']>0).mean()*100:.1f}%")
    print(f"  Max consecutive losses: {max_streak}")
    print(f"  Loss streaks distribution:")
    from collections import Counter
    sc = Counter(streaks)
    for k in sorted(sc.keys()):
        print(f"    {k} consecutive: {sc[k]} times")

    # Max drawdown in $ terms (trade-level)
    cum = t["pnl"].cumsum()
    dd = cum - cum.cummax()
    worst_dd_idx = dd.idxmin()
    print(f"\n  Trade-level MDD: ${abs(dd.min()):.0f}")
    print(f"    Peak at trade #{cum[:worst_dd_idx+1].idxmax()} = ${cum[:worst_dd_idx+1].max():.0f}")
    print(f"    Trough at trade #{worst_dd_idx} = ${cum[worst_dd_idx]:.0f}")

# ═══ Part 7: Summary & Recommendation ═════════════════════
print(f"\n{'='*65}")
print("PART 7: FINAL SUMMARY & RECOMMENDATION")
print(f"{'='*65}")

print(f"\n  R1-R10 Evolution:")
print(f"  R1:  Trend+Pullback+EMAcross     -> 2/216 IS>0, OOS $1,881")
print(f"  R2:  Trend+Pullback+TP           -> 0/216 IS>0")
print(f"  R3:  GK Breakout+EMAcross        -> 13/288 IS>0, OOS $1,580")
print(f"  R4:  GK Breakout+NoES/ES@2%      -> 26-50, OOS $2,624, MDD FAIL")
print(f"  R5:  GK Breakout+Trend Filter    -> trend filter hurts")
print(f"  R6:  Dip Buying (Mean Reversion) -> FAILED, too few trades")
print(f"  R7:  GK Breakout+Risk Tighten    -> 92 WM pass, 0 MDD pass")
print(f"  R8:  GK Breakout+TP+MaxHold      -> 30 ALL IND PASS (breakthrough)")
print(f"  R9:  Fine-tune R8 sweet spot     -> 1,431 ALL GOALS PASS (37%)")
print(f"  R10: Validation & Stress Test    -> see above")

for name, p in CANDIDATES:
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    ls = ls_metrics(oos_m.get("monthly", {}))
    print(f"\n  [{name}] GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']}% "
          f"maxH={p['max_hold']} SN={p['safenet']}% mCap={p['m_loss_cap']} cd={p['exit_cd']}")
    print(f"    IS: {is_m['n']}t ${is_m['pnl']:+.0f} WR={is_m['wr']:.1f}%")
    print(f"    OOS: {oos_m['n']}t ${oos_m['pnl']:+.0f} WR={oos_m['wr']:.1f}% "
          f"MDD=${oos_m['mdd']:.0f} WM=${oos_m['worst_m']:+.0f}")
    print(f"    L+S: ${ls['ls_total']:+.0f} {ls['ls_pos']}/{oos_m['tot_m']}pm "
          f"wm=${ls['ls_wm']:+.0f} mdd=${ls['ls_mdd']:.0f}")

print(f"\n  Anti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6 [x]7")
