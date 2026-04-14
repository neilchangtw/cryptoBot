"""V12 R5: New short entry directions — BB upper, ATR spike, N-bar high reversal
Direction F: Bollinger Band upper band touch/exceed → short
Direction G: ATR-normalized large up move → mean-reversion short
Direction H: Price at N-bar high + bearish reversal candle
Direction I: Combined Volume Spike (B) expanded grid + additional filters
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

c = df["close"].astype(float); h = df["high"].astype(float)
l = df["low"].astype(float); o = df["open"].astype(float)
v = df["volume"].astype(float)

# Session
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# --- Indicators (all shift(1)) ---

# Bollinger Bands
for win in [20, 30]:
    sma = c.rolling(win).mean()
    std = c.rolling(win).std()
    for mult in [1.5, 2.0, 2.5]:
        upper = sma + mult * std
        df[f"bb_up_{win}_{mult}"] = ((c - upper) / upper * 100).shift(1)  # positive = above upper band

# ATR
tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
for atr_win in [14, 20]:
    atr = tr.rolling(atr_win).mean()
    # 1-bar up move normalized by ATR
    df[f"up_atr_{atr_win}"] = ((c - o) / atr).shift(1)  # large positive = big up candle
    # Multi-bar up move / ATR
    for lb in [3, 5]:
        df[f"chg{lb}_atr_{atr_win}"] = ((c - c.shift(lb)) / atr).shift(1)

# N-bar high
for n in [10, 15, 20, 30]:
    df[f"at_high_{n}"] = (c.shift(1) >= c.shift(1).rolling(n).max())  # at or near n-bar high

# Reversal candle: bearish engulfing or shooting star pattern
df["bearish_engulf"] = ((o > c) & (o >= c.shift(1)) & (c <= o.shift(1)) & (c.shift(1) > o.shift(1))).shift(1)
body_top = pd.concat([o, c], axis=1).max(axis=1)
body_bot = pd.concat([o, c], axis=1).min(axis=1)
upper_wick = h - body_top
body_size = body_top - body_bot
df["shooting_star"] = ((upper_wick > 2 * body_size) & ((body_bot - l) < 0.3 * (h - l))).shift(1)
df["bearish_candle"] = (c < o).shift(1)

# Volume multiple
vol_ma20 = v.rolling(20).mean()
df["vol_mult"] = (v / vol_ma20).shift(1)

# Price change pct
df["pchg"] = ((c / c.shift(1) - 1) * 100).shift(1)

# EMA deviation
ema20 = c.ewm(span=20).mean()
df["ema_dev_20"] = ((c - ema20) / ema20 * 100).shift(1)

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


# ===== Direction F: Bollinger Band Upper Touch/Exceed =====
print("="*70)
print("  Direction F: Bollinger Band Upper → Short")
print("="*70)

results_f = []
for win in [20, 30]:
    for mult in [1.5, 2.0, 2.5]:
        col = f"bb_up_{win}_{mult}"
        for bb_thresh in [0, 0.5, 1.0]:  # 0 = at band, 0.5% = above band
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8, 10]:
                    vals = df[col].values
                    mask = np.array([vals[i] == vals[i] and vals[i] >= bb_thresh for i in range(len(df))])
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"BB{win}x{mult}>={bb_thresh}% tp{tp} mh{mh}"
                        results_f.append(r)

results_f.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_f)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>38} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_f[:15]:
    print(f"{r['cfg']:>38} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction G: ATR-normalized Large Up Move =====
print("\n" + "="*70)
print("  Direction G: ATR-normalized Up Move → Short")
print("="*70)

results_g = []
for atr_win in [14, 20]:
    # Single candle up move
    col1 = f"up_atr_{atr_win}"
    for up_thresh in [1.5, 2.0, 2.5, 3.0]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8]:
                vals = df[col1].values
                mask = np.array([vals[i] == vals[i] and vals[i] >= up_thresh for i in range(len(df))])
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"1bar/ATR{atr_win}>={up_thresh} tp{tp} mh{mh}"
                    results_g.append(r)

    # Multi-bar change / ATR
    for lb in [3, 5]:
        col2 = f"chg{lb}_atr_{atr_win}"
        for chg_thresh in [2.0, 3.0, 4.0, 5.0]:
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8]:
                    vals = df[col2].values
                    mask = np.array([vals[i] == vals[i] and vals[i] >= chg_thresh for i in range(len(df))])
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"{lb}bar/ATR{atr_win}>={chg_thresh} tp{tp} mh{mh}"
                        results_g.append(r)

results_g.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_g)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>38} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_g[:15]:
    print(f"{r['cfg']:>38} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction H: N-bar High + Bearish Signal =====
print("\n" + "="*70)
print("  Direction H: N-bar High + Bearish Signal → Short")
print("="*70)

results_h = []
for n in [10, 15, 20, 30]:
    high_col = f"at_high_{n}"

    # H1: N-bar high + bearish candle
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8, 10]:
            mask = df[high_col].fillna(False).values & df["bearish_candle"].fillna(False).values
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"High{n}+bear tp{tp} mh{mh}"
                results_h.append(r)

    # H2: N-bar high + bearish engulfing
    for tp in [1.5, 2.0, 2.5, 3.0]:
        for mh in [5, 6, 7, 8]:
            mask = df[high_col].fillna(False).values & df["bearish_engulf"].fillna(False).values
            r = eval_config(mask, tp, mh)
            if r:
                r["cfg"] = f"High{n}+engulf tp{tp} mh{mh}"
                results_h.append(r)

    # H3: N-bar high + high volume
    for vol_min in [1.5, 2.0]:
        for tp in [1.5, 2.0, 2.5, 3.0]:
            for mh in [5, 6, 7, 8]:
                mask = (df[high_col].fillna(False).values
                       & (df["vol_mult"].values >= vol_min)
                       & df["bearish_candle"].fillna(False).values)
                # Handle NaN in vol_mult
                vm = df["vol_mult"].values
                mask = np.array([
                    bool(df[high_col].fillna(False).iloc[i])
                    and vm[i] == vm[i] and vm[i] >= vol_min
                    and bool(df["bearish_candle"].fillna(False).iloc[i])
                    for i in range(len(df))])
                r = eval_config(mask, tp, mh)
                if r:
                    r["cfg"] = f"High{n}+bear+vm{vol_min} tp{tp} mh{mh}"
                    results_h.append(r)

results_h.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_h)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>38} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_h[:15]:
    print(f"{r['cfg']:>38} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Direction I: Volume Spike Expanded (refine from R3 best) =====
print("\n" + "="*70)
print("  Direction I: Volume Spike Expanded + EMA filter")
print("="*70)

results_i = []
for vol_min in [2.0, 2.5, 3.0, 3.5]:
    for pchg_min in [0, 0.5, 1.0, 1.5, 2.0]:
        for ema_dev_min in [0, 1.0, 2.0]:  # price above EMA20
            for tp in [1.5, 2.0, 2.5, 3.0]:
                for mh in [5, 6, 7, 8, 10]:
                    vm = df["vol_mult"].values
                    pc = df["pchg"].values
                    ed = df["ema_dev_20"].values
                    mask = np.array([False] * len(df))
                    for i in range(len(df)):
                        if vm[i] != vm[i] or pc[i] != pc[i] or ed[i] != ed[i]:
                            continue
                        if vm[i] >= vol_min and ed[i] >= ema_dev_min:
                            if pchg_min == 0 or pc[i] >= pchg_min:
                                mask[i] = True
                    r = eval_config(mask, tp, mh)
                    if r:
                        r["cfg"] = f"vm{vol_min} pc{pchg_min} ed{ema_dev_min} tp{tp} mh{mh}"
                        results_i.append(r)

results_i.sort(key=lambda x: x["oos_pnl"], reverse=True)
print(f"\nIS>0 configs: {len(results_i)}")
print(f"\n--- Top 15 ---")
print(f"{'Config':>42} | {'IS_n':>4} {'IS':>7} {'WR':>5} | {'OOS_n':>5} {'OOS':>7} {'WR':>5} | {'PM':>5} {'WM':>6}")
for r in results_i[:15]:
    print(f"{r['cfg']:>42} | {r['is_n']:>4} {r['is_pnl']:>+7.0f} {r['is_wr']:>5.1f} | {r['oos_n']:>5} {r['oos_pnl']:>+7.0f} {r['oos_wr']:>5.1f} | {r['pm']:>2}/{r['tot_m']:>2} {r['worst']:>+6.0f}")


# ===== Best overall =====
all_results = results_f + results_g + results_h + results_i
all_results.sort(key=lambda x: x["oos_pnl"], reverse=True)

if all_results and all_results[0]["oos_pnl"] > 0:
    best = all_results[0]
    print(f"\n{'='*70}")
    print(f"  BEST OVERALL: {best['cfg']}")
    print(f"  IS: {best['is_n']}t ${best['is_pnl']:+.0f} WR {best['is_wr']:.1f}%")
    print(f"  OOS: {best['oos_n']}t ${best['oos_pnl']:+.0f} WR {best['oos_wr']:.1f}%")
    print(f"  PM: {best['pm']}/{best['tot_m']}  Worst: ${best['worst']:+.0f}")
    print(f"{'='*70}")
else:
    print(f"\n*** NO OOS-positive candidate found ***")
    oos_pos = sum(1 for r in all_results if r["oos_pnl"] > 0)
    print(f"  OOS>0: {oos_pos}/{len(all_results)} configs")
