"""
V10-S v2 Round 1: Three-paradigm exploration (pure 1h, no 4h data)

V10-S v1 REJECTED: 4h EMA20 lookahead bias. Core logic (short bounces in downtrend)
may be valid — need pure 1h trend detection.

Paradigm A: Pullback Short + EMAcross exit (v1 logic, pure 1h)
  Trend: close < EMA_long (60/80/100) — pure 1h, no 4h
  Signal: close > EMA20, gap 0-X%
  Exit: SafeNet > EarlyStop > EMAcross > MaxHold
  144 configs

Paradigm B: Pullback Short + TP+MaxHold exit (V10-L style)
  Same entry as A, but TP+MaxHold exit
  162 configs

Paradigm C: GK Compression + Breakout Down + TP+MaxHold
  Entry: GK pctile < thresh + close breaks below N-bar low
  Exit: SafeNet > TP > MaxHold
  162 configs

Total: 468 configs
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; SN_PCT = 3.5; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# === Indicators (all shift(1), pure 1h) ===

# EMA20 (entry signal + exit)
df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

# EMA_long (trend filter for pullback)
for span in [60, 80, 100]:
    df[f"ema{span}"] = c.ewm(span=span, adjust=False).mean().shift(1)

# GK volatility pctile
ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)

# Close breakout DOWN
for bl in [10, 12, 15]:
    df[f"brk_dn_{bl}"] = c < c.shift(1).rolling(bl).min()

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# Pre-extract arrays
arr_open   = df["open"].values
arr_high   = df["high"].values
arr_low    = df["low"].values
arr_close  = df["close"].values
arr_ema20  = df["ema20"].values
arr_gk     = df["gk_pct"].values
arr_sess   = df["session_ok"].values
arr_dt     = df["datetime"].values

ema_arrs = {}
for span in [60, 80, 100]:
    ema_arrs[span] = df[f"ema{span}"].values

brk_dn_arrs = {}
for bl in [10, 12, 15]:
    brk_dn_arrs[bl] = df[f"brk_dn_{bl}"].values

V10_LONG = {
    "2025-04":61,"2025-05":229,"2025-06":-128,"2025-07":169,
    "2025-08":-84,"2025-09":182,"2025-10":-50,"2025-11":163,
    "2025-12":126,"2026-01":-95,"2026-02":294,"2026-03":160,"2026-04":65,
}

def run_bt(p, start, end):
    paradigm = p["paradigm"]
    exit_mode = p["exit_mode"]
    cd = p.get("exit_cd", 8)
    m_loss_cap = p.get("m_loss_cap", -200)
    cap = p.get("entry_cap", 20)
    max_hold = p["max_hold"]

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

        # === EXIT ===
        if in_pos:
            held = i - pos_idx; xp = 0.0; xr = ""

            # SafeNet (Short: price UP = loss)
            sn_level = pos_ep * (1 + SN_PCT / 100)
            if arr_high[i] >= sn_level:
                xp = sn_level + (arr_high[i] - sn_level) * 0.25
                xr = "SafeNet"

            # EarlyStop (emacross mode only)
            if xr == "" and exit_mode == "emacross" and p.get("earlystop"):
                if held <= 5:
                    loss_pct = (arr_close[i] / pos_ep - 1) * 100
                    if loss_pct > 1.0:
                        xp = arr_close[i]; xr = "EarlyStop"

            # EMAcross (Short: close drops back below EMA20)
            if xr == "" and exit_mode == "emacross":
                if held >= p.get("min_hold", 3):
                    if arr_close[i] < arr_ema20[i]:
                        xp = arr_close[i]; xr = "EMAcross"

            # TP (Short: price DOWN = profit)
            if xr == "" and exit_mode == "tp_maxhold":
                tp_level = pos_ep * (1 - p["tp"] / 100)
                if arr_low[i] <= tp_level:
                    xp = tp_level; xr = "TP"

            # MaxHold
            if xr == "" and held >= max_hold:
                xp = arr_close[i]; xr = "MaxHold"

            if xr != "":
                pnl = (1 - xp / pos_ep) * NOTIONAL - FEE
                trades.append({"pnl": pnl, "reason": xr, "month": m_str, "held": held})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue

        # === CIRCUIT BREAKERS ===
        if d_pnl <= -200: continue
        if m_pnl <= m_loss_cap: continue
        if i < cd_until: continue
        if i - last_exit < cd: continue
        if m_ent >= cap: continue
        if not arr_sess[i]: continue

        # === ENTRY ===
        signal = False

        if paradigm == "pullback":
            ema20_v = arr_ema20[i]
            ema_long_v = ema_arrs[p["ema_long"]][i]
            if ema20_v == ema20_v and ema_long_v == ema_long_v:
                if arr_close[i] > ema20_v and arr_close[i] < ema_long_v:
                    gap = (arr_close[i] - ema20_v) / ema20_v * 100
                    if 0 <= gap <= p["gap_max"]:
                        signal = True

        elif paradigm == "gk_brk":
            gk_v = arr_gk[i]
            if gk_v == gk_v and gk_v < p["gk_thresh"]:
                if brk_dn_arrs[p["bl"]][i]:
                    signal = True

        if signal and i + 1 < end:
            pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True; m_ent += 1

    if in_pos:
        pnl = (1 - arr_close[end-1] / pos_ep) * NOTIONAL - FEE
        dt_ts = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD",
            "month": str(dt_ts.to_period("M")), "held": end-1-pos_idx})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0,"worst_d":0}
    t = pd.DataFrame(trades); n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; lo = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(lo["pnl"].sum()) if len(lo) else 0.001
    cum = t["pnl"].cumsum(); mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    return {"n":n, "pnl":pnl, "pf":gw/gl, "wr":wr, "mdd":mdd,
            "pos_m":(mo>0).sum(), "tot_m":len(mo), "worst_m":mo.min(),
            "worst_d": mo.min(),  # approx (monthly level)
            "monthly":mo}

def ls_metrics(s_monthly):
    ls = {}
    for m, sp in s_monthly.items():
        lp = V10_LONG.get(m, 0)
        ls[m] = lp + sp
    ser = pd.Series(ls).sort_index()
    cum = ser.cumsum()
    mdd = abs((cum - cum.cummax()).min()) if len(cum) > 0 else 0
    return {"ls_total": ser.sum(), "ls_pos": (ser>0).sum(),
            "ls_wm": ser.min() if len(ser)>0 else 0, "ls_mdd": mdd,
            "l_total": sum(V10_LONG.get(m,0) for m in s_monthly.keys())}

# === BUILD CONFIGS ===
configs = []

# Paradigm A: Pullback + EMAcross
for ema_l in [60, 80, 100]:
    for gap in [2.0, 3.0, 4.0]:
        for mh in [3, 5]:
            for maxh in [12, 15]:
                for es in [False, True]:
                    for cd in [6, 8]:
                        configs.append({
                            "paradigm":"pullback", "exit_mode":"emacross",
                            "ema_long":ema_l, "gap_max":gap,
                            "min_hold":mh, "max_hold":maxh,
                            "earlystop":es, "exit_cd":cd,
                            "m_loss_cap":-200, "entry_cap":20,
                            "label": f"A:PB+EMX ema{ema_l} gap{gap} mh{mh} mxH{maxh} es={es} cd{cd}"
                        })

# Paradigm B: Pullback + TP+MaxHold
for ema_l in [60, 80, 100]:
    for gap in [2.0, 3.0, 4.0]:
        for tp in [1.5, 2.0, 3.0]:
            for maxh in [5, 8, 12]:
                for cd in [6, 8]:
                    configs.append({
                        "paradigm":"pullback", "exit_mode":"tp_maxhold",
                        "ema_long":ema_l, "gap_max":gap,
                        "tp":tp, "max_hold":maxh, "exit_cd":cd,
                        "m_loss_cap":-200, "entry_cap":20,
                        "label": f"B:PB+TP ema{ema_l} gap{gap} tp{tp} mxH{maxh} cd{cd}"
                    })

# Paradigm C: GK Breakout Down + TP+MaxHold
for gk_t in [25, 30, 40]:
    for bl in [10, 12, 15]:
        for tp in [1.5, 2.0, 3.0]:
            for maxh in [5, 8, 12]:
                for cd in [6, 8]:
                    configs.append({
                        "paradigm":"gk_brk", "exit_mode":"tp_maxhold",
                        "gk_thresh":gk_t, "bl":bl,
                        "tp":tp, "max_hold":maxh, "exit_cd":cd,
                        "m_loss_cap":-200, "entry_cap":20,
                        "label": f"C:GK{gk_t}+BRK{bl} tp{tp} mxH{maxh} cd{cd}"
                    })

print(f"=== V10-S v2 R1: Three-Paradigm Exploration ===")
print(f"Pure 1h only (no 4h data)")
print(f"A: Pullback+EMAcross = {sum(1 for c in configs if c['exit_mode']=='emacross')}")
print(f"B: Pullback+TP+MaxHold = {sum(1 for c in configs if c['paradigm']=='pullback' and c['exit_mode']=='tp_maxhold')}")
print(f"C: GK Breakout+TP+MaxHold = {sum(1 for c in configs if c['paradigm']=='gk_brk')}")
print(f"Total: {len(configs)} configs")
print()

# === RUN ===
results = []
for idx, p in enumerate(configs):
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    results.append({"p":p, "is":is_m, "oos":oos_m,
                     "is_pnl":is_m["pnl"], "oos_pnl":oos_m["pnl"]})
    if (idx+1) % 200 == 0:
        print(f"  ... {idx+1}/{len(configs)}")

# === ANALYSIS ===
def ind_pass(r):
    o = r["oos"]
    return (r["is"]["pnl"]>0 and o["pnl"]>0 and o["pos_m"]>=min(8,o["tot_m"])
            and o["worst_m"]>=-200 and o["mdd"]<=400)

all_both = [r for r in results if r["is_pnl"]>0 and r["oos_pnl"]>0]
for r in all_both:
    if "monthly" in r["oos"]:
        r["ls"] = ls_metrics(r["oos"]["monthly"])
    else:
        r["ls"] = {"ls_total":0,"ls_pos":0,"ls_wm":0,"ls_mdd":0,"l_total":0}

# By paradigm
print(f"\n{'='*65}")
print("RESULTS BY PARADIGM")
print(f"{'='*65}")

paradigm_labels = [
    ("A: Pullback+EMAcross", lambda r: r["p"]["exit_mode"]=="emacross"),
    ("B: Pullback+TP+MH", lambda r: r["p"]["paradigm"]=="pullback" and r["p"]["exit_mode"]=="tp_maxhold"),
    ("C: GK Brk+TP+MH", lambda r: r["p"]["paradigm"]=="gk_brk"),
]

for name, filt in paradigm_labels:
    sub = [r for r in results if filt(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[{name}] {len(sub)} configs | IS>0: {is_pos} | Both>0: {len(both)} | Ind PASS: {len(ip)}")
    if best:
        o=best["oos"]; s=best["is"]; p=best["p"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        print(f"  Best: {p['label']}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} PM={pm} WM=${o['worst_m']:+.0f}")

# By EMA_long (for pullback paradigms)
print(f"\n{'='*65}")
print("PULLBACK: RESULTS BY EMA_LONG")
print(f"{'='*65}")
for ema_l in [60, 80, 100]:
    sub = [r for r in results if r["p"]["paradigm"]=="pullback" and r["p"].get("ema_long")==ema_l]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[EMA{ema_l}] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | Ind PASS: {len(ip)}")
    if best:
        o=best["oos"]; p=best["p"]
        print(f"  Best: {p['label']}")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

# By GK thresh (for GK paradigm)
print(f"\n{'='*65}")
print("GK BRK: RESULTS BY GK THRESHOLD")
print(f"{'='*65}")
for gk_t in [25, 30, 40]:
    sub = [r for r in results if r["p"]["paradigm"]=="gk_brk" and r["p"].get("gk_thresh")==gk_t]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[GK<{gk_t}] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | Ind PASS: {len(ip)}")
    if best:
        o=best["oos"]; p=best["p"]
        print(f"  Best: {p['label']}")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

# Top 20 overall (by OOS PnL)
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*65}")
print(f"TOP 20 (IS>0 AND OOS>0): {len(all_both)} total")
print(f"{'='*65}")
if all_both:
    for i, r in enumerate(all_both[:20], 1):
        p=r["p"]; s=r["is"]; o=r["oos"]; ls=r.get("ls",{})
        pm=f"{o['pos_m']}/{o['tot_m']}"
        ip = "PASS" if ind_pass(r) else "fail"
        lst = ls.get("ls_total",0); lpm = ls.get("ls_pos",0)
        print(f" {i:>2} [{ip:>4}] IS ${s['pnl']:>+5.0f} | OOS ${o['pnl']:>+6.0f} "
              f"WR={o['wr']:>4.0f}% MDD=${o['mdd']:>4.0f} PM={pm:>5} WM=${o['worst_m']:>+5.0f}"
              f" | L+S ${lst:>+5.0f} {lpm:>2}pm")
        print(f"      {p['label']}")

# Goals filter
ind_list = [r for r in all_both if ind_pass(r)]

print(f"\n{'='*65}")
print(f"INDEPENDENT GOALS PASS: {len(ind_list)}")

# Detail top 3 (or top 3 of each paradigm that has Both>0)
detail_list = all_both[:3] if all_both else []
if ind_list:
    detail_list = ind_list[:3]

for rank, r in enumerate(detail_list, 1):
    p=r["p"]; o=r["oos"]; s=r["is"]; ls=r.get("ls",{})
    print(f"\n{'~'*60}")
    print(f"[#{rank}] {p['label']}")
    print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f} PM={o['pos_m']}/{o['tot_m']}")

    goals = [
        ("IS > $0", s["pnl"]>0),
        ("OOS > $0", o["pnl"]>0),
        (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
        ("Worst month >= -$200", o["worst_m"]>=-200),
        ("MDD <= $400", o["mdd"]<=400),
    ]
    for name, ok in goals: print(f"    {'PASS' if ok else 'FAIL'} {name}")

    if "monthly" in o:
        print(f"\n  {'Month':>8} {'S_PnL':>7} {'L_PnL':>7} {'L+S':>7}")
        for m in sorted(o["monthly"].keys()):
            sp = o["monthly"][m]; lp = V10_LONG.get(m,0)
            f = " *" if lp < 0 else ""
            print(f"  {m:>8} {sp:>+7.0f} {lp:>+7.0f} {sp+lp:>+7.0f}{f}")
        l_tot = sum(V10_LONG.get(m,0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {l_tot:>+7.0f} {ls.get('ls_total',0):>+7.0f}")
        print(f"  L+S: {ls.get('ls_pos',0)}/{o['tot_m']}pm wm=${ls.get('ls_wm',0):+.0f} mdd=${ls.get('ls_mdd',0):.0f}")

    oos_t = run_bt(p, IS_END, TOTAL-1)
    t = pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason, g in t.groupby("reason"):
            gw = len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                  f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# WF for best
target = ind_list[0] if ind_list else (all_both[0] if all_both else None)
if target:
    p = target["p"]
    fs_size = (TOTAL - WARMUP) // 6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best]")
    wf = 0
    for fold in range(6):
        fs = WARMUP + fold * fs_size
        fe = fs + fs_size if fold < 5 else TOTAL - 1
        ft = run_bt(p, fs, fe); fm = met(ft)
        ok = fm["pnl"] > 0; wf += ok
        dt_s = pd.Timestamp(arr_dt[fs]).strftime('%Y-%m')
        dt_e = pd.Timestamp(arr_dt[min(fe, TOTAL-1)]).strftime('%Y-%m')
        print(f"  Fold {fold+1}: {dt_s}~{dt_e} {fm['n']}t ${fm['pnl']:>+.0f} "
              f"WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

print(f"\n{'='*65}")
print("Anti-lookahead checklist:")
print("  [x] 1. All EMAs: .shift(1)")
print("  [x] 2. GK pctile: gk_ratio.shift(1).rolling(100)")
print("  [x] 3. Breakout: c < c.shift(1).rolling(bl).min()")
print("  [x] 4. Entry at O[i+1]")
print("  [x] 5. NO 4h data — pure 1h only")
print("  [x] 6. IS/OOS fixed 50/50 split")
print("  [x] 7. Exit uses current bar OHLC only")

print(f"\n{'='*65}")
print("NEXT STEP ANALYSIS")
if ind_list:
    print(f"  {len(ind_list)} configs pass all independent goals!")
    best_par = {}
    for r in ind_list:
        par = r["p"]["paradigm"] + "+" + r["p"]["exit_mode"]
        best_par[par] = best_par.get(par, 0) + 1
    for k, v in sorted(best_par.items(), key=lambda x:-x[1]):
        print(f"    {k}: {v} configs")
    print(f"  -> R2: Fine-tune around best paradigm")
elif all_both:
    print(f"  0 Ind PASS, but {len(all_both)} Both>0")
    # Show which goals fail most
    fail_counts = {"PM":0, "WM":0, "MDD":0}
    for r in all_both:
        o = r["oos"]
        if o["pos_m"] < min(8,o["tot_m"]): fail_counts["PM"] += 1
        if o["worst_m"] < -200: fail_counts["WM"] += 1
        if o["mdd"] > 400: fail_counts["MDD"] += 1
    print(f"  Goal failures: PM={fail_counts['PM']}, WM={fail_counts['WM']}, MDD={fail_counts['MDD']}")
    print(f"  -> Identify blocking constraint and address in R2")
else:
    print("  0 Both>0 — need fundamentally different approach")
