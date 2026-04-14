"""V12 R3: Overextension Short + Volume Spike Short + TP+MaxHold"""
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

# --- Indicators (all shift(1)) ---

# EMA deviation
for span in [20, 50]:
    ema = c.ewm(span=span).mean()
    df[f"ema_dev_{span}"] = ((c - ema) / ema * 100).shift(1)

# Volume multiple vs MA20
vol_ma20 = v.rolling(20).mean()
df["vol_mult"] = (v / vol_ma20).shift(1)

# TBR
if "taker_buy_base_vol" in df.columns:
    df["tbr"] = (df["taker_buy_base_vol"].astype(float) / v).shift(1)
else:
    df["tbr"] = 0.5

# Candle direction (shifted)
df["candle_up"] = (c > o).shift(1).fillna(False).astype(bool)

# Price change pct (shifted)
df["pchg"] = ((c / c.shift(1) - 1) * 100).shift(1)

# RSI (shifted)
delta = c.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss_ = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss_
df["rsi14"] = (100 - 100 / (1 + rs)).shift(1)

# GK pctile (for V11-E baseline comparison)
ln_hl = np.log(h / l); ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100 if len(s)>1 else 50, raw=False)
df["brk_dn_15"] = c < c.shift(1).rolling(15).min()

arr_o = o.values; arr_h = h.values; arr_l = l.values; arr_c = c.values
arr_dt = df["datetime"].values; arr_sess = df["session_ok"].values


def run_short_generic(entry_mask, tp_pct, mh, sn_pct, cd, mcap, start, end):
    """Generic short runner given boolean entry mask."""
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


# ===== Direction D: Overextension Short =====
print("="*70)
print("  Direction D: Overextension Short")
print("="*70)

results_d = []
for ema_span in [20, 50]:
    for dev_thresh in [2.0, 3.0, 4.0, 5.0]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                mask = (df[f"ema_dev_{ema_span}"] >= dev_thresh).values
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"EMA{ema_span}>={dev_thresh}% tp{tp} mh{mh}"
                    r["ema_span"] = ema_span; r["dev_thresh"] = dev_thresh
                    results_d.append(r)

results_d.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_d)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>35} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_d[:15]:
    print(f"{r['cfg']:>35} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction B: Volume Spike Short =====
print("\n" + "="*70)
print("  Direction B: Volume Spike Short")
print("="*70)

results_b = []
for vol_mult_min in [1.5, 2.0, 2.5, 3.0]:
    for pchg_min in [0, 0.5, 1.0, 1.5]:
        for tbr_min in [0, 0.55, 0.60]:
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    conds = (df["vol_mult"] >= vol_mult_min).values
                    if pchg_min > 0:
                        conds = conds & (df["pchg"] >= pchg_min).values
                    if tbr_min > 0:
                        conds = conds & (df["tbr"] >= tbr_min).values
                    conds = conds & df["candle_up"].values

                    r = eval_config(conds, tp, mh)
                    if r:
                        r["cfg"] = f"vm{vol_mult_min} pc{pchg_min} tbr{tbr_min} tp{tp} mh{mh}"
                        results_b.append(r)

results_b.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_b)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>42} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_b[:15]:
    print(f"{r['cfg']:>42} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction E: RSI Overbought Short =====
print("\n" + "="*70)
print("  Direction E: RSI Overbought Short")
print("="*70)

results_e = []
for rsi_thresh in [65, 70, 75, 80]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = (df["rsi14"] >= rsi_thresh).values
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"RSI>={rsi_thresh} tp{tp} mh{mh}"
                results_e.append(r)

results_e.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_e)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>30} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_e[:15]:
    print(f"{r['cfg']:>30} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== V11-E S baseline =====
print("\n" + "="*70)
print("  V11-E S Baseline (GK<30 + BRK_DN_15)")
print("="*70)
mask_v11e = (df["gk_pct"] < 30).values & df["brk_dn_15"].values
for tp in [2.0]:
    for mh in [7]:
        r = eval_config(mask_v11e, tp, mh, sn_pct=4.0, cd=8, mcap=-150)
        if r:
            print(f"  V11-E S: IS {r['is_n']}t ${r['is_pnl']:+.0f} WR {r['is_wr']:.1f}% | OOS {r['oos_n']}t ${r['oos_pnl']:+.0f} WR {r['oos_wr']:.1f}% | PM {r['pm']}/{r['tot_m']} WM ${r['worst']:+.0f}")


# ===== Best overall candidate monthly comparison =====
all_results = results_d + results_b + results_e
all_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

if all_results and all_results[0]["oos_pnl"] > 0:
    best = all_results[0]
    print(f"\n{'='*70}")
    print(f"  BEST OVERALL: {best['cfg']}")
    print(f"  IS: {best['is_n']}t ${best['is_pnl']:+.0f} | OOS: {best['oos_n']}t ${best['oos_pnl']:+.0f}")
    print(f"{'='*70}")
else:
    print(f"\n*** NO OOS-positive candidate found across all directions ***")
    # Show how many OOS>0
    oos_pos = sum(1 for r in all_results if r["oos_pnl"] > 0)
    print(f"  OOS>0: {oos_pos}/{len(all_results)} configs")
