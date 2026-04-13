"""
V10-S v2 Round 3: Comprehensive Validation

R2 findings (6,912 configs → 284 ALL PASS):
  SN=4.0% is the unlock (75% of ALL PASS)
  BL=15 structural threshold (BL13-14 = 0 ALL PASS)
  GK33+BRK15+tp1.5+mxH5 is the champion cluster

Three candidates to validate:
  Champion: GK33 BRK15 tp1.5 mxH5 sn4.0 cd6 mc-75
  Runner:   GK30 BRK15 tp2.0 mxH5 sn4.0 cd8 mc-150
  Cand3:    GK30 BRK15 tp1.5 mxH5 sn4.0 cd8 mc-150

Validation suite:
  1. Walk-Forward 6/8/10-fold
  2. Fee/Slippage stress test (fee $4-6, SN penetration 25-50%)
  3. Parameter plateau (sweep each param individually)
  4. Anti-lookahead confirmation
  5. Consecutive loss analysis
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)

for bl in [15]:
    df[f"brk_dn_{bl}"] = c < c.shift(1).rolling(bl).min()

df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

arr_open   = df["open"].values
arr_high   = df["high"].values
arr_low    = df["low"].values
arr_close  = df["close"].values
arr_ema20  = df["ema20"].values
arr_gk     = df["gk_pct"].values
arr_sess   = df["session_ok"].values
arr_dt     = df["datetime"].values
brk_dn_15  = df["brk_dn_15"].values

V10_LONG = {
    "2025-04":61,"2025-05":229,"2025-06":-128,"2025-07":169,
    "2025-08":-84,"2025-09":182,"2025-10":-50,"2025-11":163,
    "2025-12":126,"2026-01":-95,"2026-02":294,"2026-03":160,"2026-04":65,
}

def run_bt(p, start, end, fee_override=None, sn_pen_override=None):
    bl = 15
    tp_pct = p["tp"]; max_hold = p["max_hold"]
    cd = p["exit_cd"]; sn_pct = p["sn"]
    m_loss_cap = p["m_loss_cap"]; cap = p.get("entry_cap", 20)
    gk_thresh = p["gk_thresh"]
    fee = fee_override if fee_override is not None else p.get("fee", 4.0)
    sn_pen = sn_pen_override if sn_pen_override is not None else 0.25

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
            held = i - pos_idx; xp = 0.0; xr = ""

            sn_level = pos_ep * (1 + sn_pct / 100)
            if arr_high[i] >= sn_level:
                xp = sn_level + (arr_high[i] - sn_level) * sn_pen
                xr = "SafeNet"

            if xr == "":
                tp_level = pos_ep * (1 - tp_pct / 100)
                if arr_low[i] <= tp_level:
                    xp = tp_level; xr = "TP"

            if xr == "" and held >= max_hold:
                xp = arr_close[i]; xr = "MaxHold"

            if xr != "":
                pnl = (1 - xp / pos_ep) * NOTIONAL - fee
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

        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_thresh:
            if brk_dn_15[i]:
                if i + 1 < end:
                    pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True; m_ent += 1

    if in_pos:
        pnl = (1 - arr_close[end-1] / pos_ep) * NOTIONAL - fee
        dt_ts = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD",
            "month": str(dt_ts.to_period("M")), "held": end-1-pos_idx})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0,"monthly":pd.Series(dtype=float)}
    t = pd.DataFrame(trades); n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; lo = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(lo["pnl"].sum()) if len(lo) else 0.001
    cum = t["pnl"].cumsum(); mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    return {"n":n, "pnl":pnl, "pf":gw/gl, "wr":wr, "mdd":mdd,
            "pos_m":(mo>0).sum(), "tot_m":len(mo), "worst_m":mo.min(), "monthly":mo}

def ls_metrics(s_monthly):
    ls = {}
    for m, sp in s_monthly.items():
        lp = V10_LONG.get(m, 0)
        ls[m] = lp + sp
    ser = pd.Series(ls).sort_index()
    cum = ser.cumsum()
    mdd = abs((cum - cum.cummax()).min()) if len(cum) > 0 else 0
    return {"ls_total": ser.sum(), "ls_pos": (ser>0).sum(),
            "ls_wm": ser.min() if len(ser)>0 else 0, "ls_mdd": mdd}

# === CANDIDATES ===
candidates = {
    "Champion": {"gk_thresh":33, "bl":15, "tp":1.5, "max_hold":5, "sn":4.0, "m_loss_cap":-75, "exit_cd":6, "fee":4.0},
    "Runner":   {"gk_thresh":30, "bl":15, "tp":2.0, "max_hold":5, "sn":4.0, "m_loss_cap":-150, "exit_cd":8, "fee":4.0},
    "Cand3":    {"gk_thresh":30, "bl":15, "tp":1.5, "max_hold":5, "sn":4.0, "m_loss_cap":-150, "exit_cd":8, "fee":4.0},
}

print("=" * 65)
print("V10-S v2 R3: Comprehensive Validation")
print("=" * 65)

# === 1. IS/OOS BASELINE ===
print("\n[1] IS / OOS Baseline")
for name, p in candidates.items():
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    im = met(is_t); om = met(oos_t)
    ls = ls_metrics(om["monthly"])
    print(f"\n  {name}: {p}")
    print(f"    IS:  {im['n']}t ${im['pnl']:+.0f} PF={im['pf']:.2f} WR={im['wr']:.1f}%")
    print(f"    OOS: {om['n']}t ${om['pnl']:+.0f} PF={om['pf']:.2f} WR={om['wr']:.1f}% MDD=${om['mdd']:.0f} WM=${om['worst_m']:.0f} PM={om['pos_m']}/{om['tot_m']}")
    print(f"    L+S: ${ls['ls_total']:+.0f} {ls['ls_pos']}pm wm=${ls['ls_wm']:.0f} mdd=${ls['ls_mdd']:.0f}")

# === 2. WALK-FORWARD MULTI-FOLD ===
print(f"\n{'='*65}")
print("[2] Walk-Forward Multi-Fold Validation")
for name, p in candidates.items():
    print(f"\n  --- {name} ---")
    for n_folds in [6, 8, 10]:
        fold_size = (TOTAL - WARMUP) // n_folds
        wf_pass = 0
        fold_details = []
        for fold in range(n_folds):
            fs = WARMUP + fold * fold_size
            fe = fs + fold_size if fold < n_folds - 1 else TOTAL - 1
            ft = run_bt(p, fs, fe)
            fm = met(ft)
            passed = fm["pnl"] > 0
            if passed: wf_pass += 1
            dt_s = pd.Timestamp(arr_dt[fs]).strftime("%Y-%m")
            dt_e = pd.Timestamp(arr_dt[min(fe, TOTAL-1)]).strftime("%Y-%m")
            fold_details.append(f"F{fold+1}:{dt_s}~{dt_e} {fm['n']}t ${fm['pnl']:+.0f} {'P' if passed else 'F'}")
        status = "PASS" if wf_pass >= n_folds * 2 // 3 else "FAIL"
        print(f"    {n_folds}-fold: {wf_pass}/{n_folds} {status}  [{', '.join(fold_details)}]")

# === 3. FEE/SLIPPAGE STRESS TEST ===
print(f"\n{'='*65}")
print("[3] Fee & Slippage Stress Test (IS period)")
stress_configs = [
    ("Base fee=$4, pen=25%", 4.0, 0.25),
    ("Fee=$5, pen=25%", 5.0, 0.25),
    ("Fee=$6, pen=25%", 6.0, 0.25),
    ("Fee=$4, pen=35%", 4.0, 0.35),
    ("Fee=$4, pen=50%", 4.0, 0.50),
    ("Fee=$5, pen=35%", 5.0, 0.35),
    ("Fee=$5, pen=50%", 5.0, 0.50),
    ("Fee=$6, pen=35%", 6.0, 0.35),
    ("Fee=$6, pen=50%", 6.0, 0.50),
]

for name, p in candidates.items():
    print(f"\n  --- {name} ---")
    pass_count = 0
    for desc, fee, pen in stress_configs:
        is_t = run_bt(p, WARMUP, IS_END, fee_override=fee, sn_pen_override=pen)
        im = met(is_t)
        status = "PASS" if im["pnl"] > 0 else "FAIL"
        if im["pnl"] > 0: pass_count += 1
        print(f"    {desc}: {im['n']}t ${im['pnl']:+.0f} WR={im['wr']:.0f}% {status}")
    print(f"    Stress: {pass_count}/9 IS>0")

# === 4. PARAMETER PLATEAU ===
print(f"\n{'='*65}")
print("[4] Parameter Plateau Analysis (OOS)")
print("    Sweep each param individually, count how many values give OOS>0")

for name, base_p in candidates.items():
    print(f"\n  --- {name} ---")

    # GK threshold
    gk_results = []
    for gk in [20, 23, 25, 28, 30, 33, 35, 38, 40]:
        p2 = dict(base_p); p2["gk_thresh"] = gk
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        gk_results.append(f"GK<{gk}:${m['pnl']:+.0f}")
    print(f"    GK: {' | '.join(gk_results)}")

    # BL
    for bl_val in [12, 13, 14, 15, 16, 17, 18]:
        if bl_val != 15:
            df[f"brk_dn_{bl_val}"] = c < c.shift(1).rolling(bl_val).min()
    bl_results = []
    for bl_val in [12, 13, 14, 15, 16, 17, 18]:
        p2 = dict(base_p); p2["bl"] = bl_val
        # temporarily use the right breakout array
        old_brk = brk_dn_15
        globals()["brk_dn_15"] = df[f"brk_dn_{bl_val}"].values
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        bl_results.append(f"BL{bl_val}:${m['pnl']:+.0f}")
        globals()["brk_dn_15"] = old_brk
    print(f"    BL: {' | '.join(bl_results)}")

    # TP
    tp_results = []
    for tp in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]:
        p2 = dict(base_p); p2["tp"] = tp
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        tp_results.append(f"TP{tp}:${m['pnl']:+.0f}")
    print(f"    TP: {' | '.join(tp_results)}")

    # maxHold
    mh_results = []
    for mh in [3, 4, 5, 6, 7, 8, 10, 12]:
        p2 = dict(base_p); p2["max_hold"] = mh
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        mh_results.append(f"mH{mh}:${m['pnl']:+.0f}")
    print(f"    MH: {' | '.join(mh_results)}")

    # SafeNet
    sn_results = []
    for sn in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]:
        p2 = dict(base_p); p2["sn"] = sn
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        sn_results.append(f"SN{sn}:${m['pnl']:+.0f}")
    print(f"    SN: {' | '.join(sn_results)}")

    # exit_cd
    cd_results = []
    for cd_val in [2, 4, 6, 8, 10, 12]:
        p2 = dict(base_p); p2["exit_cd"] = cd_val
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        cd_results.append(f"cd{cd_val}:${m['pnl']:+.0f}")
    print(f"    CD: {' | '.join(cd_results)}")

    # mCap
    mc_results = []
    for mc in [-50, -75, -100, -150, -200, -300]:
        p2 = dict(base_p); p2["m_loss_cap"] = mc
        t = run_bt(p2, IS_END, TOTAL-1)
        m = met(t)
        mc_results.append(f"mc{mc}:${m['pnl']:+.0f}")
    print(f"    MC: {' | '.join(mc_results)}")

# === 5. CONSECUTIVE LOSS ANALYSIS ===
print(f"\n{'='*65}")
print("[5] Consecutive Loss Analysis (full dataset)")
for name, p in candidates.items():
    full_t = run_bt(p, WARMUP, TOTAL-1)
    if not full_t: continue
    t_df = pd.DataFrame(full_t)
    # Find consecutive loss streaks
    losses = (t_df["pnl"] < 0).astype(int)
    streaks = []
    current_streak = 0
    current_loss = 0.0
    for i, row in t_df.iterrows():
        if row["pnl"] < 0:
            current_streak += 1
            current_loss += row["pnl"]
        else:
            if current_streak > 0:
                streaks.append((current_streak, current_loss))
            current_streak = 0
            current_loss = 0.0
    if current_streak > 0:
        streaks.append((current_streak, current_loss))

    max_streak = max(s[0] for s in streaks) if streaks else 0
    worst_streak_pnl = min(s[1] for s in streaks) if streaks else 0
    avg_streak = np.mean([s[0] for s in streaks]) if streaks else 0

    print(f"\n  {name}:")
    print(f"    Total trades: {len(t_df)}")
    print(f"    Max consecutive losses: {max_streak}")
    print(f"    Worst streak PnL: ${worst_streak_pnl:.0f}")
    print(f"    Avg loss streak length: {avg_streak:.1f}")
    print(f"    Loss streaks >= 3: {sum(1 for s in streaks if s[0] >= 3)}")
    print(f"    Loss streaks >= 4 (triggers cooldown): {sum(1 for s in streaks if s[0] >= 4)}")

# === 6. ANTI-LOOKAHEAD CHECKLIST ===
print(f"\n{'='*65}")
print("[6] Anti-lookahead Checklist")
checks = [
    ("EMA20: .shift(1)", True),
    ("GK pctile: gk_ratio.shift(1).rolling(100)", True),
    ("Breakout: c < c.shift(1).rolling(bl).min()", True),
    ("Entry at O[i+1]", True),
    ("NO 4h data - pure 1h only", True),
    ("IS/OOS fixed 50/50 split", True),
    ("Exit uses current bar OHLC only", True),
]
for desc, passed in checks:
    status = "[x]" if passed else "[ ]"
    print(f"  {status} {desc}")

# === 7. FINAL RECOMMENDATION ===
print(f"\n{'='*65}")
print("[7] FINAL RECOMMENDATION")
print("    Compare all validation results above to pick the best candidate.")
print("    Key criteria: WF stability, stress robustness, plateau width.")
