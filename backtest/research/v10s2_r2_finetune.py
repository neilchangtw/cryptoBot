"""
V10-S v2 Round 2: Fine-tune GK Breakout + TP+MaxHold (paradigm C winner from R1)

R1 findings:
  - Paradigm C (GK Brk + TP+MH) = 16 Ind PASS out of 162 configs
  - Sweet spot: GK30, BL15, TP 1.5-2.0, maxH 5, cd 6-8
  - Paradigms A/B dead (0 Ind PASS)

R2 changes:
  - Finer grid around sweet spot (GK 25-33, BL 13-16)
  - SafeNet as variable (was fixed 3.5% in R1)
  - Monthly loss cap as variable (was fixed -200 in R1)
  - 5,184 configs
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# === Indicators (all shift(1), pure 1h) ===
df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

# GK volatility pctile
ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)

# Close breakout DOWN (finer BL range)
for bl in [13, 14, 15, 16]:
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

brk_dn_arrs = {}
for bl in [13, 14, 15, 16]:
    brk_dn_arrs[bl] = df[f"brk_dn_{bl}"].values

V10_LONG = {
    "2025-04":61,"2025-05":229,"2025-06":-128,"2025-07":169,
    "2025-08":-84,"2025-09":182,"2025-10":-50,"2025-11":163,
    "2025-12":126,"2026-01":-95,"2026-02":294,"2026-03":160,"2026-04":65,
}

def run_bt(p, start, end):
    bl = p["bl"]; tp_pct = p["tp"]; max_hold = p["max_hold"]
    cd = p["exit_cd"]; sn_pct = p["sn"]
    m_loss_cap = p["m_loss_cap"]; cap = p["entry_cap"]
    gk_thresh = p["gk_thresh"]
    brk_arr = brk_dn_arrs[bl]

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
            sn_level = pos_ep * (1 + sn_pct / 100)
            if arr_high[i] >= sn_level:
                xp = sn_level + (arr_high[i] - sn_level) * 0.25
                xr = "SafeNet"

            # TP (Short: price DOWN = profit)
            if xr == "":
                tp_level = pos_ep * (1 - tp_pct / 100)
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

        # === ENTRY (GK Breakout Down) ===
        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_thresh:
            if brk_arr[i]:
                if i + 1 < end:
                    pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True; m_ent += 1

    if in_pos:
        pnl = (1 - arr_close[end-1] / pos_ep) * NOTIONAL - FEE
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

# === BUILD CONFIGS ===
configs = []
for gk_t in [25, 28, 30, 33]:
    for bl in [13, 14, 15, 16]:
        for tp in [1.0, 1.5, 2.0, 2.5]:
            for maxh in [4, 5, 6]:
                for sn in [2.5, 3.0, 3.5, 4.0]:
                    for mcap in [-75, -100, -150]:
                        for cd in [4, 6, 8]:
                            configs.append({
                                "gk_thresh":gk_t, "bl":bl,
                                "tp":tp, "max_hold":maxh, "sn":sn,
                                "m_loss_cap":mcap, "entry_cap":20, "exit_cd":cd,
                                "label": f"GK{gk_t}+BRK{bl} tp{tp} mxH{maxh} sn{sn} mc{mcap} cd{cd}"
                            })

print(f"=== V10-S v2 R2: Fine-tune GK Breakout + TP+MaxHold ===")
print(f"Pure 1h only | {len(configs)} configs")
print(f"GK [25,28,30,33] x BL [13,14,15,16] x TP [1.0,1.5,2.0,2.5]")
print(f"maxH [4,5,6] x SN [2.5,3.0,3.5,4.0] x mCap [-75,-100,-150] x cd [4,6,8]")
print()

# === RUN ===
results = []
for idx, p in enumerate(configs):
    if (idx+1) % 500 == 0:
        print(f"  ... {idx+1}/{len(configs)}")
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)

    ls = ls_metrics(oos_m["monthly"]) if oos_m["n"] > 0 else {"ls_total":0,"ls_pos":0,"ls_wm":0,"ls_mdd":0}

    # Independent goals
    g_is  = is_m["pnl"] > 0
    g_oos = oos_m["pnl"] > 0
    g_pm  = oos_m["pos_m"] >= 8
    g_wm  = oos_m["worst_m"] >= -200
    g_mdd = oos_m["mdd"] <= 400
    ind_pass = g_is and g_oos and g_pm and g_wm and g_mdd

    # Combined goals
    g_ls_pm  = ls["ls_pos"] >= 11
    g_ls_wm  = ls["ls_wm"] >= -150
    g_ls_mdd = ls["ls_mdd"] <= 500
    g_ls_net = ls["ls_total"] > 1090  # L-only OOS
    all_pass = ind_pass and g_ls_pm and g_ls_wm and g_ls_mdd and g_ls_net

    results.append({
        "p": p, "label": p["label"],
        "is_m": is_m, "oos_m": oos_m, "ls": ls,
        "ind_pass": ind_pass, "all_pass": all_pass,
        "g_is": g_is, "g_oos": g_oos, "g_pm": g_pm, "g_wm": g_wm, "g_mdd": g_mdd,
        "g_ls_pm": g_ls_pm, "g_ls_wm": g_ls_wm, "g_ls_mdd": g_ls_mdd, "g_ls_net": g_ls_net,
    })

# === ANALYSIS ===
both_pos = [r for r in results if r["g_is"] and r["g_oos"]]
ind_pass = [r for r in results if r["ind_pass"]]
all_pass_list = [r for r in results if r["all_pass"]]

print(f"=" * 65)
print(f"SUMMARY: {len(configs)} configs tested")
print(f"  IS>0: {sum(1 for r in results if r['g_is'])}")
print(f"  Both>0: {len(both_pos)}")
print(f"  Ind PASS: {len(ind_pass)}")
print(f"  ALL PASS (Ind + Combined): {len(all_pass_list)}")
print(f"=" * 65)

# Results by SN
print(f"\nBY SafeNet:")
for sn in [2.5, 3.0, 3.5, 4.0]:
    sub = [r for r in results if r["p"]["sn"] == sn]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  SN={sn}%: Ind PASS={ip}, ALL PASS={ap}")

# Results by mCap
print(f"\nBY Monthly Cap:")
for mc in [-75, -100, -150]:
    sub = [r for r in results if r["p"]["m_loss_cap"] == mc]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  mCap={mc}: Ind PASS={ip}, ALL PASS={ap}")

# Results by GK
print(f"\nBY GK Threshold:")
for gk in [25, 28, 30, 33]:
    sub = [r for r in results if r["p"]["gk_thresh"] == gk]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  GK<{gk}: Ind PASS={ip}, ALL PASS={ap}")

# Results by BL
print(f"\nBY Breakout Length:")
for bl in [13, 14, 15, 16]:
    sub = [r for r in results if r["p"]["bl"] == bl]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  BL={bl}: Ind PASS={ip}, ALL PASS={ap}")

# Results by TP
print(f"\nBY TP:")
for tp in [1.0, 1.5, 2.0, 2.5]:
    sub = [r for r in results if r["p"]["tp"] == tp]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  TP={tp}%: Ind PASS={ip}, ALL PASS={ap}")

# Results by maxH
print(f"\nBY MaxHold:")
for mh in [4, 5, 6]:
    sub = [r for r in results if r["p"]["max_hold"] == mh]
    ip = sum(1 for r in sub if r["ind_pass"])
    ap = sum(1 for r in sub if r["all_pass"])
    print(f"  maxH={mh}: Ind PASS={ip}, ALL PASS={ap}")

# TOP 20 ALL PASS
print(f"\n{'='*65}")
print(f"TOP 20 ALL PASS (sorted by L+S total):")
all_pass_sorted = sorted(all_pass_list, key=lambda r: r["ls"]["ls_total"], reverse=True)[:20]
for rank, r in enumerate(all_pass_sorted, 1):
    im = r["is_m"]; om = r["oos_m"]; ls = r["ls"]
    print(f"  {rank:2d}. IS {im['n']:3d}t ${im['pnl']:+.0f} WR={im['wr']:.0f}% | "
          f"OOS {om['n']:3d}t ${om['pnl']:+.0f} WR={om['wr']:.0f}% MDD=${om['mdd']:.0f} WM=${om['worst_m']:.0f} PM={om['pos_m']}/{om['tot_m']} | "
          f"L+S ${ls['ls_total']:+.0f} {ls['ls_pos']}pm wm=${ls['ls_wm']:.0f}")
    print(f"      {r['label']}")

# If no ALL PASS, show top Ind PASS
if not all_pass_list:
    print(f"\nNo ALL PASS found. TOP 20 Ind PASS (sorted by OOS PnL):")
    ind_sorted = sorted(ind_pass, key=lambda r: r["oos_m"]["pnl"], reverse=True)[:20]
    for rank, r in enumerate(ind_sorted, 1):
        im = r["is_m"]; om = r["oos_m"]; ls = r["ls"]
        fail_reasons = []
        if not r["g_ls_pm"]: fail_reasons.append(f"ls_pm={ls['ls_pos']}")
        if not r["g_ls_wm"]: fail_reasons.append(f"ls_wm=${ls['ls_wm']:.0f}")
        if not r["g_ls_mdd"]: fail_reasons.append(f"ls_mdd=${ls['ls_mdd']:.0f}")
        if not r["g_ls_net"]: fail_reasons.append(f"ls_net=${ls['ls_total']:.0f}")
        print(f"  {rank:2d}. IS {im['n']:3d}t ${im['pnl']:+.0f} WR={im['wr']:.0f}% | "
              f"OOS {om['n']:3d}t ${om['pnl']:+.0f} WR={om['wr']:.0f}% MDD=${om['mdd']:.0f} WM=${om['worst_m']:.0f} PM={om['pos_m']}/{om['tot_m']} | "
              f"L+S ${ls['ls_total']:+.0f} {ls['ls_pos']}pm wm=${ls['ls_wm']:.0f} | FAIL: {', '.join(fail_reasons)}")
        print(f"      {r['label']}")

# DETAILED TOP 5
print(f"\n{'='*65}")
detail_list = all_pass_sorted[:5] if all_pass_list else sorted(ind_pass, key=lambda r: r["oos_m"]["pnl"], reverse=True)[:5]
for rank, r in enumerate(detail_list, 1):
    im = r["is_m"]; om = r["oos_m"]; ls = r["ls"]
    print(f"\n{'~'*60}")
    print(f"[#{rank}] {r['label']}")
    print(f"  IS:  {im['n']}t ${im['pnl']:+.0f} PF={im['pf']:.2f} WR={im['wr']:.1f}%")
    print(f"  OOS: {om['n']}t ${om['pnl']:+.0f} PF={om['pf']:.2f} WR={om['wr']:.1f}% MDD=${om['mdd']:.0f} WM=${om['worst_m']:.0f} PM={om['pos_m']}/{om['tot_m']}")

    # Goals check
    for g, label in [("g_is","IS>$0"),("g_oos","OOS>$0"),("g_pm","PM>=8"),("g_wm","WM>=-$200"),("g_mdd","MDD<=$400"),
                      ("g_ls_pm","L+S PM>=11"),("g_ls_wm","L+S WM>=-$150"),("g_ls_mdd","L+S MDD<=$500"),("g_ls_net","L+S>L-only")]:
        status = "PASS" if r[g] else "FAIL"
        print(f"    {status} {label}")

    # Monthly breakdown
    s_mo = om["monthly"]
    print(f"\n     Month   S_PnL   L_PnL     L+S")
    for m in sorted(s_mo.index):
        sp = s_mo[m]; lp = V10_LONG.get(m, 0); lsp = sp + lp
        flag = " *" if lsp < 0 else ""
        print(f"   {m}    {sp:+.0f}     {lp:+.0f}    {lsp:+.0f}{flag}")
    print(f"     TOTAL   {om['pnl']:+.0f}   {sum(V10_LONG.get(m,0) for m in s_mo.index):+.0f}   {ls['ls_total']:+.0f}")
    print(f"  L+S: {ls['ls_pos']}/13pm wm=${ls['ls_wm']:.0f} mdd=${ls['ls_mdd']:.0f}")

    # Exit reasons
    if om["n"] > 0:
        t = pd.DataFrame(run_bt(r["p"], IS_END, TOTAL-1))
        if len(t) > 0:
            print(f"\n  Exit reasons:")
            for reason in sorted(t["reason"].unique()):
                sub = t[t["reason"]==reason]
                print(f"    {reason:>10s}: {len(sub):3d}t avg=${sub['pnl'].mean():+.1f} WR={len(sub[sub['pnl']>0])/len(sub)*100:.0f}% hold={sub['held'].mean():.1f}b")

# Walk-Forward 6-fold for top 3
print(f"\n{'='*65}")
top_for_wf = all_pass_sorted[:3] if all_pass_list else sorted(ind_pass, key=lambda r: r["oos_m"]["pnl"], reverse=True)[:3]
for rank, r in enumerate(top_for_wf, 1):
    p = r["p"]
    fold_size = (TOTAL - WARMUP) // 6
    wf_pass = 0; wf_total = 6
    print(f"\nWalk-Forward 6-Fold [#{rank} {r['label']}]")
    for fold in range(6):
        fs = WARMUP + fold * fold_size
        fe = fs + fold_size if fold < 5 else TOTAL - 1
        ft = run_bt(p, fs, fe)
        fm = met(ft)
        status = "PASS" if fm["pnl"] > 0 else "FAIL"
        if fm["pnl"] > 0: wf_pass += 1
        dt_s = pd.Timestamp(arr_dt[fs]).strftime("%Y-%m")
        dt_e = pd.Timestamp(arr_dt[min(fe, TOTAL-1)]).strftime("%Y-%m")
        print(f"  Fold {fold+1}: {dt_s}~{dt_e} {fm['n']}t ${fm['pnl']:+.0f} WR={fm['wr']:.0f}% {status}")
    print(f"  WF: {wf_pass}/{wf_total}")

print(f"\n{'='*65}")
print(f"NEXT STEP: Based on R2 results, identify which parameters are most stable")
print(f"  and design R3 to push toward ALL GOALS PASS.")
