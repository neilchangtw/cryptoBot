"""
V10-L Round 6: Mean Reversion Dip Buying
R1-R5 found: GK breakout Long fails worst_month and MDD goals (structural).
             WR 35-42% with volatile monthly returns.
Paradigm shift: Dip buying for higher WR and more consistent months.

V10 R1 data: consecutive >=3 red bars -> next bar WR 59.2%
Design: Buy after sharp drops, exit with TP (capture bounce quickly).
        Higher WR, smaller wins, lower MDD.

Entry signals tested:
  A) Consecutive red bars (close < open for N bars)
  B) Consecutive close-down bars (close < close[-1])
  C) Large single-bar drop (ret < -X%)
  D) Close below lower Bollinger Band
  E) RSI-like: ret_sign(N) < threshold (proportion of positive returns)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np
from itertools import product

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2

DAILY_LOSS_LIMIT = -200; MONTHLY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)
v = df["volume"].astype(float)

# Indicators (shift(1))
df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)
df["ret"] = c.pct_change()

# Consecutive red bars (close < open)
red = (c < o).astype(int)
for n in [3, 4, 5]:
    df[f"consec_red_{n}"] = red.rolling(n).sum().shift(1) == n

# Consecutive close-down bars
down = (c < c.shift(1)).astype(int)
for n in [3, 4, 5]:
    df[f"consec_down_{n}"] = down.rolling(n).sum().shift(1) == n

# Single bar drop
for pct in [2, 3, 4]:
    df[f"big_drop_{pct}"] = df["ret"].shift(1) < -pct/100

# Close below EMA20 (shifted) — dip below mean
df["below_ema20"] = c < df["ema20"]

# RetSign < threshold (proportion of positive returns in window)
ret_pos = (df["ret"] > 0).astype(float)
for w in [10, 15, 20]:
    df[f"retsign_{w}"] = ret_pos.rolling(w).mean().shift(1)

# TBR (taker buy ratio) percentile
tbr = (df["tbv"].astype(float) / v.replace(0, np.nan)).fillna(0.5)
df["tbr_pct"] = tbr.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100 if len(s)>1 and not s.isna().any() else 50,
    raw=False)

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

V10_SHORT = {
    "2025-04":527,"2025-05":93,"2025-06":388,"2025-07":76,"2025-08":392,
    "2025-09":324,"2025-10":467,"2025-11":332,"2025-12":286,
    "2026-01":18,"2026-02":406,"2026-03":98,
}

def run_bt(df, p, start, end):
    sn = p["safenet"]; tp = p["tp_pct"]
    maxh = p["max_hold"]; cd = p.get("exit_cd", 6)
    cap = p.get("entry_cap", 20)
    signal_type = p["signal"]
    signal_param = p.get("signal_param", 3)

    trades=[]; pos=None; last_exit=-999
    d_pnl=m_pnl=0.0; consec=0; cd_until=0
    cur_m=cur_d=None; m_ent=0

    for i in range(start, end):
        b=df.iloc[i]; bm=b["datetime"].to_period("M"); bd=b["datetime"].date()
        if bd!=cur_d: d_pnl=0.0; cur_d=bd
        if bm!=cur_m: m_pnl=0.0; m_ent=0; cur_m=bm

        if pos is not None:
            held=i-pos["idx"]; ep=pos["ep"]
            xp=xr=None

            # SafeNet
            snl=ep*(1-sn/100)
            if b["low"]<=snl: xp=snl-(snl-b["low"])*0.25; xr="SafeNet"

            # TP
            if xp is None:
                tp_level=ep*(1+tp/100)
                if b["high"]>=tp_level: xp=tp_level; xr="TP"

            # MaxHold
            if xp is None and held>=maxh: xp=b["close"]; xr="MaxHold"

            if xp is not None:
                pnl=(xp/ep-1)*NOTIONAL-FEE
                trades.append({"pnl":pnl,"reason":xr,"month":str(bm),
                    "entry_dt":pos["dt"],"exit_dt":b["datetime"],"held":held})
                d_pnl+=pnl; m_pnl+=pnl
                if pnl<0:
                    consec+=1
                    if consec>=CONSEC_LOSS_PAUSE: cd_until=i+CONSEC_LOSS_COOLDOWN
                else: consec=0
                last_exit=i; pos=None

        if pos is not None: continue
        if d_pnl<=DAILY_LOSS_LIMIT: continue
        if m_pnl<=MONTHLY_LOSS_LIMIT: continue
        if i<cd_until: continue
        if i-last_exit<cd: continue
        if m_ent>=cap: continue
        if not b["session_ok"]: continue

        # Entry signal
        entry = False
        if signal_type == "consec_red":
            col = f"consec_red_{signal_param}"
            entry = b.get(col, False) if col in df.columns else False
        elif signal_type == "consec_down":
            col = f"consec_down_{signal_param}"
            entry = b.get(col, False) if col in df.columns else False
        elif signal_type == "big_drop":
            col = f"big_drop_{signal_param}"
            entry = b.get(col, False) if col in df.columns else False
        elif signal_type == "retsign_low":
            # RetSign < threshold
            w = p.get("rs_window", 15)
            thresh = p.get("rs_thresh", 0.35)
            col = f"retsign_{w}"
            val = b.get(col, 0.5) if col in df.columns else 0.5
            entry = (not pd.isna(val)) and val < thresh
        elif signal_type == "consec_red_tbr":
            # Consecutive red + low TBR
            col = f"consec_red_{signal_param}"
            cr = b.get(col, False) if col in df.columns else False
            tbr_thresh = p.get("tbr_thresh", 30)
            tbr_val = b.get("tbr_pct", 50)
            entry = cr and (not pd.isna(tbr_val)) and tbr_val < tbr_thresh

        if entry and i+1 < len(df):
            pos={"ep":df.iloc[i+1]["open"],"dt":df.iloc[i+1]["datetime"],"idx":i+1}
            m_ent+=1

    if pos:
        b=df.iloc[end-1]
        pnl=(b["close"]/pos["ep"]-1)*NOTIONAL-FEE
        trades.append({"pnl":pnl,"reason":"EOD","month":str(b["datetime"].to_period("M")),
            "entry_dt":pos["dt"],"exit_dt":b["datetime"],"held":end-1-pos["idx"]})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t=pd.DataFrame(trades); n=len(t); pnl=t["pnl"].sum()
    w=t[t["pnl"]>0]; l=t[t["pnl"]<=0]
    wr=len(w)/n*100
    gw=w["pnl"].sum() if len(w) else 0
    gl=abs(l["pnl"].sum()) if len(l) else 0.001
    cum=t["pnl"].cumsum(); mdd=abs((cum-cum.cummax()).min())
    mo=t.groupby("month")["pnl"].sum()
    t["date"]=pd.to_datetime(t["exit_dt"]).dt.date
    daily=t.groupby("date")["pnl"].sum()
    return {"n":n,"pnl":pnl,"pf":gw/gl,"wr":wr,"mdd":mdd,
            "pos_m":(mo>0).sum(),"tot_m":len(mo),"worst_m":mo.min(),
            "monthly":mo,"worst_day":daily.min() if len(daily) else 0}

# ─── Build configs ────────────────────────────────────
configs = []

# A: Consecutive red bars
for n in [3, 4, 5]:
    for tp in [1.0, 1.5, 2.0, 3.0]:
        for maxh in [5, 8, 10, 15]:
            for sn in [3.5, 4.5]:
                for cd in [4, 6, 8]:
                    configs.append({"signal":"consec_red","signal_param":n,
                        "tp_pct":tp,"max_hold":maxh,"safenet":sn,"exit_cd":cd,"entry_cap":20})

# B: Consecutive close-down
for n in [3, 4, 5]:
    for tp in [1.0, 1.5, 2.0, 3.0]:
        for maxh in [5, 8, 10, 15]:
            for sn in [3.5]:
                for cd in [4, 6]:
                    configs.append({"signal":"consec_down","signal_param":n,
                        "tp_pct":tp,"max_hold":maxh,"safenet":sn,"exit_cd":cd,"entry_cap":20})

# C: Big single drop
for pct in [2, 3, 4]:
    for tp in [1.0, 1.5, 2.0, 3.0]:
        for maxh in [5, 8, 10]:
            for sn in [3.5]:
                for cd in [4, 6]:
                    configs.append({"signal":"big_drop","signal_param":pct,
                        "tp_pct":tp,"max_hold":maxh,"safenet":sn,"exit_cd":cd,"entry_cap":20})

# D: RetSign low (oversold)
for w in [10, 15, 20]:
    for thresh in [0.30, 0.35, 0.40]:
        for tp in [1.0, 1.5, 2.0, 3.0]:
            for maxh in [8, 10, 15]:
                for sn in [3.5]:
                    configs.append({"signal":"retsign_low","rs_window":w,"rs_thresh":thresh,
                        "tp_pct":tp,"max_hold":maxh,"safenet":sn,"exit_cd":6,"entry_cap":20})

# E: Consecutive red + TBR low
for n in [3, 4]:
    for tbr_t in [20, 30, 40]:
        for tp in [1.0, 1.5, 2.0, 3.0]:
            for maxh in [5, 8, 10]:
                for sn in [3.5]:
                    configs.append({"signal":"consec_red_tbr","signal_param":n,"tbr_thresh":tbr_t,
                        "tp_pct":tp,"max_hold":maxh,"safenet":sn,"exit_cd":6,"entry_cap":20})

print(f"=== V10-L R6: Dip Buying (Mean Reversion) ===")
print(f"Paradigm shift: Buy dips, TP exit, high WR target")
print(f"5 signal types, {len(configs)} total configs")
print()

results = []
for idx, p in enumerate(configs):
    is_t = run_bt(df, p, WARMUP, IS_END)
    oos_t = run_bt(df, p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    results.append({"p":p, "is":is_m, "oos":oos_m,
                     "is_pnl":is_m["pnl"], "oos_pnl":oos_m["pnl"]})
    if (idx+1) % 200 == 0:
        print(f"  ... {idx+1}/{len(configs)}")

# ─── Analysis ─────────────────────────────────────────
print(f"\n{'='*65}")
print("RESULTS BY SIGNAL TYPE")
print(f"{'='*65}")

for sig in ["consec_red", "consec_down", "big_drop", "retsign_low", "consec_red_tbr"]:
    sub = [r for r in results if r["p"]["signal"]==sig]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    goal = [r for r in both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[{sig}] {len(sub)} configs | IS>0: {is_pos} | Both>0: {len(both)} | Goals PASS: {len(goal)}")
    if best:
        o=best["oos"]; s=best["is"]; p=best["p"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        extras = ""
        if "rs_window" in p: extras += f" w={p['rs_window']} th={p['rs_thresh']}"
        if "tbr_thresh" in p: extras += f" tbr<{p['tbr_thresh']}"
        print(f"  Best: param={p.get('signal_param','')}{extras} TP={p['tp_pct']}% "
              f"maxH={p['max_hold']} SN={p['safenet']} cd={p.get('exit_cd',6)}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} PM={pm} WM=${o['worst_m']:+.0f}")

# Top 20 overall
all_both = [r for r in results if r["is_pnl"]>0 and r["oos_pnl"]>0]
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*65}")
print(f"TOP 20 (IS>0 AND OOS>0): {len(all_both)} total")
if all_both:
    for i, r in enumerate(all_both[:20], 1):
        p=r["p"]; s=r["is"]; o=r["oos"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        sig_s = p["signal"][:12]
        extras = f"p={p.get('signal_param','')}"
        if "rs_window" in p: extras = f"w={p['rs_window']}t={p['rs_thresh']}"
        if "tbr_thresh" in p: extras += f"t{p['tbr_thresh']}"
        print(f"  {i:>2} {sig_s:>12} {extras:>12} TP={p['tp_pct']:>3} mxH={p['max_hold']:>2} "
              f"SN={p['safenet']} cd={p.get('exit_cd',6)} | "
              f"IS:{s['n']:>3}t ${s['pnl']:>+5.0f} WR={s['wr']:>4.0f} | "
              f"OOS:{o['n']:>3}t ${o['pnl']:>+5.0f} WR={o['wr']:>4.0f} "
              f"MDD={o['mdd']:>4.0f} PM={pm} WM={o['worst_m']:>+5.0f}")

# Goals filter
wm_pass = [r for r in all_both if r["oos"]["worst_m"]>=-200]
mdd_pass = [r for r in all_both if r["oos"]["mdd"]<=400]
all_pass = [r for r in all_both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]
print(f"\nWorst month >= -$200: {len(wm_pass)}")
print(f"MDD <= $400: {len(mdd_pass)}")
print(f"ALL GOALS PASS: {len(all_pass)}")

# Detail top 3
for rank, r in enumerate(all_both[:3], 1):
    p=r["p"]; o=r["oos"]; s=r["is"]
    print(f"\n{'─'*60}")
    extras = f"param={p.get('signal_param','')}"
    if "rs_window" in p: extras = f"w={p['rs_window']} thresh={p['rs_thresh']}"
    if "tbr_thresh" in p: extras += f" tbr<{p['tbr_thresh']}"
    print(f"[#{rank}] {p['signal']} {extras} TP={p['tp_pct']}% maxH={p['max_hold']} "
          f"SN={p['safenet']}% cd={p.get('exit_cd',6)}")
    print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f} WD=${o.get('worst_day',0):.0f}")

    goals = [
        ("IS > $0", s["pnl"]>0),
        ("OOS > $0", o["pnl"]>0),
        (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
        ("Worst month >= -$200", o["worst_m"]>=-200),
        ("MDD <= $400", o["mdd"]<=400),
    ]
    for name, ok in goals: print(f"    {'PASS' if ok else 'FAIL'} {name}")

    if "monthly" in o:
        print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
        ls_t=0; neg_ls=0
        for m, lp in sorted(o["monthly"].items()):
            sp=V10_SHORT.get(m,0); ls=lp+sp; ls_t+=ls
            if ls<0: neg_ls+=1
            f=" *" if sp<100 else ""
            print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls:>+7.0f}{f}")
        st=sum(V10_SHORT.get(m,0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {st:>+7.0f} {ls_t:>+7.0f}")
        print(f"  L+S pos months: {len(o['monthly'])-neg_ls}/{len(o['monthly'])}")

    oos_t=run_bt(df,p,IS_END,TOTAL-1)
    t=pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason,g in t.groupby("reason"):
            gw=len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                  f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# WF for best
if all_both:
    p=all_both[0]["p"]
    fs_size=(TOTAL-WARMUP)//6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best]")
    wf=0
    for fold in range(6):
        fs=WARMUP+fold*fs_size
        fe=fs+fs_size if fold<5 else TOTAL-1
        ft=run_bt(df,p,fs,fe); fm=met(ft)
        ok=fm["pnl"]>0; wf+=ok
        print(f"  Fold {fold+1}: {df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

print(f"\nAnti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6")
print(f"  All dip signals: .shift(1)")
print(f"  TBR pctile: .shift(1).rolling(100)")
print(f"  Entry: O[i+1]")
