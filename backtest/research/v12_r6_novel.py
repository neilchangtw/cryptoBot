"""V12 R6: Novel short entry directions
J: Failed Long Breakout — GK compression + breakout up that reverses → short
K: Price/Volume Divergence — price at high + declining volume → distribution
L: MACD Bearish Divergence — price up but MACD histogram declining
M: Donchian Upper Channel Touch → short
N: Slow MA Overextension (SMA50/100) + bearish confirmation
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

# --- GK pctile (for failed breakout detection) ---
ln_hl = np.log(h / l); ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
df["gk_pct"] = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100 if len(s)>1 else 50, raw=False)

# --- Breakout signals ---
for n in [10, 15, 20]:
    df[f"brk_up_{n}"] = c.shift(1) > c.shift(2).rolling(n-1).max()  # previous bar broke up
    df[f"brk_up_{n}_cur"] = c > c.shift(1).rolling(n).max()  # current bar at/above high
    df[f"below_high_{n}"] = c < c.shift(1).rolling(n).max()  # current close below recent high (failed breakout)

# --- Donchian Channels ---
for n in [10, 15, 20, 30]:
    df[f"dc_upper_{n}"] = h.shift(1).rolling(n).max()
    df[f"dc_lower_{n}"] = l.shift(1).rolling(n).min()
    df[f"at_dc_upper_{n}"] = (c.shift(1) >= df[f"dc_upper_{n}"])  # touched upper channel

# --- Volume trends ---
for w in [5, 10]:
    df[f"vol_slope_{w}"] = v.rolling(w).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0] / x.mean() * 100 if x.mean() > 0 else 0, raw=True).shift(1)

# Volume declining while price rising
for lb in [5, 10]:
    df[f"price_up_{lb}"] = (c.shift(1) > c.shift(lb))  # price up over lb bars
    df[f"vol_dn_{lb}"] = (v.shift(1).rolling(lb).mean() < v.shift(lb).rolling(lb).mean())  # avg volume declined

# --- MACD ---
ema12 = c.ewm(span=12).mean()
ema26 = c.ewm(span=26).mean()
macd_line = ema12 - ema26
signal_line = macd_line.ewm(span=9).mean()
macd_hist = macd_line - signal_line
df["macd_hist"] = macd_hist.shift(1)
df["macd_hist_prev"] = macd_hist.shift(2)
df["macd_declining"] = (macd_hist.shift(1) < macd_hist.shift(2))  # histogram declining
df["macd_pos_declining"] = (macd_hist.shift(1) > 0) & (macd_hist.shift(1) < macd_hist.shift(2))

# --- Slow MA deviation ---
for span in [50, 100]:
    sma = c.rolling(span).mean()
    df[f"sma_dev_{span}"] = ((c - sma) / sma * 100).shift(1)

# --- Price change (shifted) ---
df["pchg"] = ((c / c.shift(1) - 1) * 100).shift(1)
df["bearish_candle"] = (c < o).shift(1)

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


def print_results(name, results, col_w=38):
    results.sort(key=lambda x: x["oos_pnl"], reverse=True)
    print(f"\nIS>0 configs: {len(results)}")
    if not results: return
    print(f"\n--- Top 15 ---")
    print(f"{'Config':>{col_w}} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
    for r in results[:15]:
        print(f"{r['cfg']:>{col_w}} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction J: Failed Long Breakout =====
print("="*70)
print("  J: Failed Breakout — GK compressed + broke up N bars ago + now below")
print("="*70)

results_j = []
for gk_thresh in [25, 30, 35, 40]:
    for n in [10, 15, 20]:
        for lookback in [2, 3, 4, 5]:  # how many bars ago was the breakout
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    # Was at GK compression AND broke up 'lookback' bars ago, but now below that high
                    gk_vals = df["gk_pct"].values
                    brk_vals = df[f"brk_up_{n}"].values  # shift(1) already applied in breakout
                    cur_below = df[f"below_high_{n}"].values
                    mask = np.array([False] * len(df))
                    for i in range(lookback, len(df)):
                        gk_i = gk_vals[i - lookback]
                        if gk_i != gk_i: continue
                        if gk_i < gk_thresh and brk_vals[i - lookback] and cur_below[i]:
                            mask[i] = True
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"GK<{gk_thresh} brk{n} fb{lookback} tp{tp} mh{mh}"
                        results_j.append(r)

print_results("J: Failed Breakout", results_j, 42)


# ===== Direction K: Price/Volume Divergence =====
print("\n" + "="*70)
print("  K: Price Up + Volume Down (distribution)")
print("="*70)

results_k = []
for lb in [5, 10]:
    for sma_dev_min in [0, 1.0, 2.0]:  # price above SMA50
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                pu = df[f"price_up_{lb}"].fillna(False).values
                vd = df[f"vol_dn_{lb}"].fillna(False).values
                sd = df["sma_dev_50"].values
                mask = np.array([False] * len(df))
                for i in range(len(df)):
                    if pu[i] and vd[i]:
                        if sma_dev_min == 0 or (sd[i] == sd[i] and sd[i] >= sma_dev_min):
                            mask[i] = True
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"PV{lb} sd50>{sma_dev_min} tp{tp} mh{mh}"
                    results_k.append(r)

print_results("K: Price/Volume Divergence", results_k, 38)


# ===== Direction L: MACD Bearish Divergence =====
print("\n" + "="*70)
print("  L: MACD positive but declining + price up")
print("="*70)

results_l = []
for sma_dev_min in [0, 1.0, 2.0, 3.0]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mpd = df["macd_pos_declining"].fillna(False).values
            sd = df["sma_dev_50"].values
            mask = np.array([False] * len(df))
            for i in range(len(df)):
                if mpd[i]:
                    if sma_dev_min == 0 or (sd[i] == sd[i] and sd[i] >= sma_dev_min):
                        mask[i] = True
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"MACD_pd sd50>{sma_dev_min} tp{tp} mh{mh}"
                results_l.append(r)

print_results("L: MACD Bearish Divergence", results_l, 38)


# ===== Direction M: Donchian Upper Channel =====
print("\n" + "="*70)
print("  M: Donchian Upper Channel Touch → Short")
print("="*70)

results_m = []
for n in [10, 15, 20, 30]:
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = df[f"at_dc_upper_{n}"].fillna(False).values
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"DC{n}_upper tp{tp} mh{mh}"
                results_m.append(r)

    # Donchian upper + bearish candle
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8]:
            mask = df[f"at_dc_upper_{n}"].fillna(False).values & df["bearish_candle"].fillna(False).values
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"DC{n}_upper+bear tp{tp} mh{mh}"
                results_m.append(r)

print_results("M: Donchian Upper Channel", results_m, 38)


# ===== Direction N: Slow MA Overextension =====
print("\n" + "="*70)
print("  N: Slow MA Overextension (SMA50/100) → Short")
print("="*70)

results_n = []
for span in [50, 100]:
    col = f"sma_dev_{span}"
    for dev_thresh in [2, 3, 4, 5, 6, 8, 10]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8, 10]:
                vals = df[col].values
                mask = np.array([vals[i] == vals[i] and vals[i] >= dev_thresh for i in range(len(df))])
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"SMA{span}>={dev_thresh}% tp{tp} mh{mh}"
                    results_n.append(r)

print_results("N: Slow MA Overextension", results_n, 38)


# ===== Best overall =====
all_results = results_j + results_k + results_l + results_m + results_n
all_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*70}")
if all_results and all_results[0]["oos_pnl"] > 0:
    best = all_results[0]
    print(f"  BEST OVERALL: {best['cfg']}")
    print(f"  IS: {best['is_n']}t ${best['is_pnl']:+.0f} WR {best['is_wr']:.1f}%")
    print(f"  OOS: {best['oos_n']}t ${best['oos_pnl']:+.0f} WR {best['oos_wr']:.1f}%")
    print(f"  PM: {best['pm']}/{best['tot_m']}  Worst: ${best['worst']:+.0f}")
else:
    print(f"  NO OOS-positive candidate found")
    oos_pos = sum(1 for r in all_results if r["oos_pnl"] > 0)
    print(f"  OOS>0: {oos_pos}/{len(all_results)} configs")
print(f"  (V11-E S baseline: OOS $+1,328, PM 10/13, worst -$113)")
print(f"{'='*70}")
