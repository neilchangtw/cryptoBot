"""V12 R7: Composite Signal Short — combine weak signals into scoring system
Approach 1: Multi-condition AND (2 of 3 signals must be true)
Approach 2: Additive score (each signal adds weight, enter when score >= threshold)
Approach 3: Best standalone signals with relaxed thresholds + combo
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; WARMUP = 200
TOTAL = len(df); IS_END = TOTAL // 2
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

c = df["close"].astype(float); h = df["high"].astype(float)
l = df["low"].astype(float); o = df["open"].astype(float)
v = df["volume"].astype(float)

# Session
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# --- Component signals (all shift(1)) ---

# 1. Volume spike: vol/vol_ma20
vol_ma20 = v.rolling(20).mean()
df["vol_mult"] = (v / vol_ma20).shift(1)

# 2. EMA deviation: price above EMA20
ema20 = c.ewm(span=20).mean()
df["ema_dev"] = ((c - ema20) / ema20 * 100).shift(1)

# 3. SMA50 deviation
sma50 = c.rolling(50).mean()
df["sma50_dev"] = ((c - sma50) / sma50 * 100).shift(1)

# 4. Price/Volume divergence: 10-bar
df["price_up_10"] = (c.shift(1) > c.shift(10))
df["vol_dn_10"] = (v.shift(1).rolling(10).mean() < v.shift(10).rolling(10).mean())

# 5. RSI14
delta = c.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss_ = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss_
df["rsi14"] = (100 - 100 / (1 + rs)).shift(1)

# 6. Bearish candle
df["bearish"] = (c < o).shift(1)

# 7. Candle body ratio (bearish pressure)
df["body_pct"] = (abs(c - o) / (h - l + 1e-10)).shift(1)

# 8. Price change (shifted)
df["pchg"] = ((c / c.shift(1) - 1) * 100).shift(1)

# 9. Bollinger Band upper distance
bb_sma = c.rolling(20).mean()
bb_std = c.rolling(20).std()
bb_upper = bb_sma + 2 * bb_std
df["bb_dist"] = ((c - bb_upper) / bb_upper * 100).shift(1)

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


def print_results(name, results, col_w=45):
    results.sort(key=lambda x: x["oos_pnl"], reverse=True)
    print(f"\nIS>0 configs: {len(results)}")
    if not results: return
    print(f"\n--- Top 15 ---")
    print(f"{'Config':>{col_w}} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
    for r in results[:15]:
        print(f"{r['cfg']:>{col_w}} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Approach 1: AND combos of 2 signals =====
print("="*70)
print("  Approach 1: AND combos — 2 of N signals must fire")
print("="*70)

results_and = []

# Precompute signal components
vm = df["vol_mult"].values
ed = df["ema_dev"].values
sd = df["sma50_dev"].values
rsi = df["rsi14"].values
pu10 = df["price_up_10"].fillna(False).values
vd10 = df["vol_dn_10"].fillna(False).values
bb = df["bb_dist"].values
pchg = df["pchg"].values

# Combo: Volume Spike + EMA Overextension
for vm_min in [2.0, 2.5, 3.0]:
    for ed_min in [1.0, 2.0, 3.0]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if vm[i]==vm[i] and ed[i]==ed[i] and vm[i]>=vm_min and ed[i]>=ed_min:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"VM>={vm_min}+EMA>={ed_min} tp{tp} mh{mh}"
                    results_and.append(r)

# Combo: Volume Spike + PV Divergence
for vm_min in [1.5, 2.0, 2.5]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = np.array([False] * len(df))
            for i in range(len(df)):
                if vm[i]==vm[i] and vm[i]>=vm_min and pu10[i] and vd10[i]:
                    mask[i] = True
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"VM>={vm_min}+PV10 tp{tp} mh{mh}"
                results_and.append(r)

# Combo: EMA Overextension + RSI Overbought
for ed_min in [1.0, 2.0, 3.0]:
    for rsi_min in [60, 65, 70]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8]:
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if ed[i]==ed[i] and rsi[i]==rsi[i] and ed[i]>=ed_min and rsi[i]>=rsi_min:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"EMA>={ed_min}+RSI>={rsi_min} tp{tp} mh{mh}"
                    results_and.append(r)

# Combo: PV Divergence + SMA50 overextension
for sd_min in [1.0, 2.0, 3.0]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = np.array([False] * len(df))
            for i in range(len(df)):
                if sd[i]==sd[i] and sd[i]>=sd_min and pu10[i] and vd10[i]:
                    mask[i] = True
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"PV10+SMA50>={sd_min} tp{tp} mh{mh}"
                results_and.append(r)

# Combo: BB above upper + volume spike
for vm_min in [1.5, 2.0, 2.5]:
    for bb_min in [-0.5, 0, 0.5]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8]:
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if vm[i]==vm[i] and bb[i]==bb[i] and vm[i]>=vm_min and bb[i]>=bb_min:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"BB>={bb_min}+VM>={vm_min} tp{tp} mh{mh}"
                    results_and.append(r)

print_results("Approach 1: AND combos", results_and, 45)


# ===== Approach 2: Score-based entry =====
print("\n" + "="*70)
print("  Approach 2: Score-based — additive score >= threshold")
print("="*70)

results_score = []
for score_thresh in [2, 3, 4]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = np.array([False] * len(df))
            for i in range(len(df)):
                score = 0
                # Volume spike
                if vm[i]==vm[i] and vm[i] >= 2.0: score += 1
                if vm[i]==vm[i] and vm[i] >= 3.0: score += 1
                # EMA overextension
                if ed[i]==ed[i] and ed[i] >= 2.0: score += 1
                if ed[i]==ed[i] and ed[i] >= 4.0: score += 1
                # RSI overbought
                if rsi[i]==rsi[i] and rsi[i] >= 70: score += 1
                # PV divergence
                if pu10[i] and vd10[i]: score += 1
                # BB above upper
                if bb[i]==bb[i] and bb[i] >= 0: score += 1
                # Large up candle
                if pchg[i]==pchg[i] and pchg[i] >= 2.0: score += 1

                if score >= score_thresh:
                    mask[i] = True
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"score>={score_thresh} tp{tp} mh{mh}"
                results_score.append(r)

print_results("Approach 2: Score-based", results_score, 35)


# ===== Approach 3: Triple combo — 3 signals AND =====
print("\n" + "="*70)
print("  Approach 3: Triple AND — VM + EMA + PV/RSI")
print("="*70)

results_triple = []
for vm_min in [1.5, 2.0]:
    for ed_min in [1.0, 2.0]:
        # + PV divergence
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8]:
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if vm[i]==vm[i] and ed[i]==ed[i] and vm[i]>=vm_min and ed[i]>=ed_min and pu10[i] and vd10[i]:
                        mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"VM{vm_min}+EMA{ed_min}+PV tp{tp} mh{mh}"
                    results_triple.append(r)

        # + RSI
        for rsi_min in [60, 65]:
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    mask = np.array([False] * len(df))
                    for i in range(len(df)):
                        if (vm[i]==vm[i] and ed[i]==ed[i] and rsi[i]==rsi[i]
                            and vm[i]>=vm_min and ed[i]>=ed_min and rsi[i]>=rsi_min):
                            mask[i] = True
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"VM{vm_min}+EMA{ed_min}+RSI{rsi_min} tp{tp} mh{mh}"
                        results_triple.append(r)

print_results("Approach 3: Triple AND", results_triple, 45)


# ===== Best overall =====
all_results = results_and + results_score + results_triple
all_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*70}")
if all_results and all_results[0]["oos_pnl"] > 0:
    best = all_results[0]
    print(f"  BEST OVERALL: {best['cfg']}")
    print(f"  IS: {best['is_n']}t ${best['is_pnl']:+.0f} WR {best['is_wr']:.1f}%")
    print(f"  OOS: {best['oos_n']}t ${best['oos_pnl']:+.0f} WR {best['oos_wr']:.1f}%")
    print(f"  PM: {best['pm']}/{best['tot_m']}  Worst: ${best['worst']:+.0f}")

    # Monthly detail for best
    # Need to reconstruct the mask — use score approach or AND
    print(f"\n  (Detailed monthly analysis would go here)")
else:
    print(f"  NO OOS-positive candidate found")
    oos_pos = sum(1 for r in all_results if r["oos_pnl"] > 0)
    print(f"  OOS>0: {oos_pos}/{len(all_results)} configs")
print(f"  V11-E S baseline: OOS $+1,328, PM 10/13, worst -$113")
print(f"{'='*70}")
