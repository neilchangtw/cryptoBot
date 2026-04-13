"""V11 Research — V10 Baseline (matching original research scripts exactly)"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; BASE_FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# GK volatility — rank percentile (matching research scripts)
ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
gk_pct = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)

# Breakout — current close vs shifted rolling (matching research)
for bl in [10, 12, 15, 18, 20]:
    df[f"brk_up_{bl}"] = c > c.shift(1).rolling(bl).max()
    df[f"brk_dn_{bl}"] = c < c.shift(1).rolling(bl).min()

# Session
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# Pre-extract arrays
arr_open  = o.values
arr_high  = h.values
arr_low   = l.values
arr_close = c.values
arr_gk    = gk_pct.values
arr_sess  = df["session_ok"].values
arr_dt    = df["datetime"].values

brk_up = {}; brk_dn = {}
for bl in [10, 12, 15, 18, 20]:
    brk_up[bl] = df[f"brk_up_{bl}"].values
    brk_dn[bl] = df[f"brk_dn_{bl}"].values


def run_long(p, start, end, fee=BASE_FEE, sn_pen=0.25):
    """Run L (long) strategy."""
    gk_t = p["gk"]; bl = p["bl"]; tp = p["tp"] / 100; mh = p["mh"]
    sn = p["sn"] / 100; cd = p["cd"]; mcap = p["mcap"]; cap = p.get("cap", 20)
    brk = brk_up[bl]

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
            sn_level = ep * (1 - sn)
            if arr_low[i] <= sn_level:
                xp = sn_level - (sn_level - arr_low[i]) * sn_pen
                xr = "SafeNet"
            if xr == "":
                tp_level = ep * (1 + tp)
                if arr_high[i] >= tp_level:
                    xp = tp_level; xr = "TP"
            if xr == "" and held >= mh:
                xp = arr_close[i]; xr = "MaxHold"
            if xr:
                pnl = (xp / ep - 1) * NOTIONAL - fee
                trades.append({"pnl": pnl, "reason": xr, "month": m_str, "held": held, "date": d_str})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else: consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue
        if d_pnl <= -200 or m_pnl <= mcap or i < cd_until: continue
        if i - last_exit < cd or m_ent >= cap: continue
        if not arr_sess[i]: continue

        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_t and brk[i]:
            if i + 1 < end:
                ep = arr_open[i+1]; idx = i+1; in_pos = True; m_ent += 1

    if in_pos:
        pnl = (arr_close[end-1] / ep - 1) * NOTIONAL - fee
        dt = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD", "month": str(dt.to_period("M")), "held": end-1-idx, "date": str(dt.date())})
    return trades


def run_short(p, start, end, fee=BASE_FEE, sn_pen=0.25):
    """Run S (short) strategy."""
    gk_t = p["gk"]; bl = p["bl"]; tp = p["tp"] / 100; mh = p["mh"]
    sn = p["sn"] / 100; cd = p["cd"]; mcap = p["mcap"]; cap = p.get("cap", 20)
    brk = brk_dn[bl]

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
            if arr_high[i] >= sn_level:
                xp = sn_level + (arr_high[i] - sn_level) * sn_pen
                xr = "SafeNet"
            if xr == "":
                tp_level = ep * (1 - tp)
                if arr_low[i] <= tp_level:
                    xp = tp_level; xr = "TP"
            if xr == "" and held >= mh:
                xp = arr_close[i]; xr = "MaxHold"
            if xr:
                pnl = (1 - xp / ep) * NOTIONAL - fee
                trades.append({"pnl": pnl, "reason": xr, "month": m_str, "held": held, "date": d_str})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else: consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue
        if d_pnl <= -200 or m_pnl <= mcap or i < cd_until: continue
        if i - last_exit < cd or m_ent >= cap: continue
        if not arr_sess[i]: continue

        gk_v = arr_gk[i]
        if gk_v == gk_v and gk_v < gk_t and brk[i]:
            if i + 1 < end:
                ep = arr_open[i+1]; idx = i+1; in_pos = True; m_ent += 1

    if in_pos:
        pnl = (1 - arr_close[end-1] / ep) * NOTIONAL - fee
        dt = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD", "month": str(dt.to_period("M")), "held": end-1-idx, "date": str(dt.date())})
    return trades


def metrics(trades):
    if not trades: return {"n":0, "pnl":0, "pf":0, "wr":0, "mdd":0, "monthly": pd.Series(dtype=float)}
    t = pd.DataFrame(trades); n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; lo = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(lo["pnl"].sum()) if len(lo) else 0.001
    cum = t["pnl"].cumsum(); mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    exit_dist = t.groupby("reason").agg(count=("pnl","count"), avg_pnl=("pnl","mean"), wr=("pnl", lambda x: (x>0).sum()/len(x)*100))
    return {"n":n, "pnl":pnl, "pf":gw/gl, "wr":wr, "mdd":mdd,
            "pos_m":(mo>0).sum(), "tot_m":len(mo), "worst_m":mo.min(),
            "monthly":mo, "exit_dist": exit_dist}


def combined_monthly(l_trades, s_trades, label=""):
    l_m = pd.DataFrame(l_trades).groupby("month")["pnl"].sum() if l_trades else pd.Series(dtype=float)
    s_m = pd.DataFrame(s_trades).groupby("month")["pnl"].sum() if s_trades else pd.Series(dtype=float)
    months = sorted(set(l_m.index) | set(s_m.index))
    print(f"\n{'Month':>10} {'L':>8} {'S':>8} {'Total':>8}")
    total_l = total_s = 0; pm = 0; worst = 999
    for m in months:
        lp = l_m.get(m, 0); sp = s_m.get(m, 0); t = lp + sp
        total_l += lp; total_s += sp
        if t > 0: pm += 1
        worst = min(worst, t)
        print(f"  {m:>10} {lp:>+8.0f} {sp:>+8.0f} {t:>+8.0f}")
    total = total_l + total_s
    print(f"  {'TOTAL':>10} {total_l:>+8.0f} {total_s:>+8.0f} {total:>+8.0f}")
    print(f"  PM: {pm}/{len(months)}, worst: ${worst:.0f}")
    # Combined MDD
    all_trades = l_trades + s_trades
    if all_trades:
        at = pd.DataFrame(all_trades)
        at["exit_bar_approx"] = range(len(at))  # approximate ordering
        cum = at.sort_values("exit_bar_approx")["pnl"].cumsum()
        cmdd = abs((cum - cum.cummax()).min())
        print(f"  Combined MDD: ${cmdd:.0f}")
    return {"total": total, "pm": pm, "months": len(months), "worst": worst}


# === V10 CONFIGS ===
V10_L = {"gk":25, "bl":15, "tp":2.0, "mh":5, "sn":3.5, "cd":6, "mcap":-75}
V10_S = {"gk":30, "bl":15, "tp":1.5, "mh":5, "sn":4.0, "cd":8, "mcap":-150}

print("="*70)
print("V10 BASELINE (research-matching indicators)")
print(f"Data: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]} ({TOTAL} bars)")
print(f"IS: bar {WARMUP}-{IS_END} | OOS: bar {IS_END}-{TOTAL}")
print("="*70)

# IS
is_l = run_long(V10_L, WARMUP, IS_END)
is_s = run_short(V10_S, WARMUP, IS_END)
ml = metrics(is_l); ms = metrics(is_s)
print(f"\nIS L: {ml['n']}t ${ml['pnl']:+.0f} WR {ml['wr']:.1f}% PF {ml['pf']:.2f} MDD ${ml['mdd']:.0f}")
if 'exit_dist' in ml and len(ml.get('exit_dist',[])) > 0: print(ml['exit_dist'].to_string())
print(f"IS S: {ms['n']}t ${ms['pnl']:+.0f} WR {ms['wr']:.1f}% PF {ms['pf']:.2f} MDD ${ms['mdd']:.0f}")
if 'exit_dist' in ms and len(ms.get('exit_dist',[])) > 0: print(ms['exit_dist'].to_string())

# OOS
oos_l = run_long(V10_L, IS_END, TOTAL)
oos_s = run_short(V10_S, IS_END, TOTAL)
ml = metrics(oos_l); ms = metrics(oos_s)
print(f"\nOOS L: {ml['n']}t ${ml['pnl']:+.0f} WR {ml['wr']:.1f}% PF {ml['pf']:.2f} MDD ${ml['mdd']:.0f}")
if 'exit_dist' in ml and len(ml.get('exit_dist',[])) > 0: print(ml['exit_dist'].to_string())
print(f"OOS S: {ms['n']}t ${ms['pnl']:+.0f} WR {ms['wr']:.1f}% PF {ms['pf']:.2f} MDD ${ms['mdd']:.0f}")
if 'exit_dist' in ms and len(ms.get('exit_dist',[])) > 0: print(ms['exit_dist'].to_string())

print("\n--- OOS L+S Monthly ---")
combined_monthly(oos_l, oos_s)

# Walk-Forward
print("\n--- Walk-Forward 6-fold ---")
fold_size = (TOTAL - WARMUP) // 6
for fold in range(6):
    s = WARMUP + fold * fold_size
    e = s + fold_size if fold < 5 else TOTAL
    fl = run_long(V10_L, s, e); fs = run_short(V10_S, s, e)
    lp = sum(t['pnl'] for t in fl); sp = sum(t['pnl'] for t in fs)
    print(f"  Fold {fold+1}: L ${lp:+.0f}  S ${sp:+.0f}  Total ${lp+sp:+.0f}")

print("\n--- Walk-Forward 8-fold ---")
fold_size = (TOTAL - WARMUP) // 8
for fold in range(8):
    s = WARMUP + fold * fold_size
    e = s + fold_size if fold < 7 else TOTAL
    fl = run_long(V10_L, s, e); fs = run_short(V10_S, s, e)
    lp = sum(t['pnl'] for t in fl); sp = sum(t['pnl'] for t in fs)
    print(f"  Fold {fold+1}: L ${lp:+.0f}  S ${sp:+.0f}  Total ${lp+sp:+.0f}")
