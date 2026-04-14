"""
V14-R1: V13 MaxHold 虧損出場特徵分析
=====================================
目標：分析 MH 虧損出場的交易特徵，找出可預測的模式。

分析項目：
1. MH 虧損交易在 bar 1-6/10 的 PnL 軌跡
2. 「從頭虧」vs「先賺後虧」的分類
3. 進場 GK pctile 分佈（MH 虧損 vs TP 獲利）
4. 可預測特徵
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

o = df["open"].values.astype(float)
h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
dt = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2

# ===== GK indicators =====
gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_s = pd.Series(gk_raw)

def rolling_mean(arr, w):
    s = pd.Series(arr)
    return s.rolling(w).mean().values

def fast_rolling_pctile(arr, window):
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        w = arr[i - window + 1:i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < window // 2:
            continue
        result[i] = np.sum(valid < valid[-1]) / (len(valid) - 1) if len(valid) > 1 else 0.5
    return result

def compute_gk_pctile(fast_w, slow_w, pw):
    gk_ma_fast = rolling_mean(gk_raw, fast_w)
    gk_ma_slow = rolling_mean(gk_raw, slow_w)
    ratio = gk_ma_fast / gk_ma_slow
    ratio_shifted = np.full(len(ratio), np.nan)
    ratio_shifted[1:] = ratio[:-1]
    return fast_rolling_pctile(ratio_shifted, pw)

print("Computing indicators...", flush=True)
gk_p_l = compute_gk_pctile(5, 20, 100)
gk_p_s = compute_gk_pctile(10, 30, 100)

# Breakout
c_s = pd.Series(c)
bo_up = (c_s > c_s.shift(1).rolling(15).max()).values
bo_dn = (c_s < c_s.shift(1).rolling(15).min()).values

# Session
hours = pd.to_datetime(dt).hour
days = np.array([pd.Timestamp(d).day_name() for d in dt])
BH = {0, 1, 2, 12}
BD_L = {"Saturday", "Sunday"}
BD_S = {"Monday", "Saturday", "Sunday"}

# ATR for later analysis
atr_arr = pd.Series(np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))).rolling(14).mean().values

print("Done.", flush=True)

# ===== V13 Backtest with full trade lifecycle tracking =====
FEE = 4.0
NOTIONAL = 4000
SLIP = 1.25  # match R8 backtest convention

def backtest_v13_detailed(side, gk_p, gk_thresh, tp_pct, maxhold, safenet_pct,
                          cooldown, monthly_loss, block_hours, block_days,
                          start_bar, end_bar, ext=2, be_trail=True):
    """V13 backtest with bar-by-bar PnL tracking."""
    trades = []
    in_pos = False
    in_ext = False
    last_exit = -999
    cur_day = ""; day_pnl = 0.0
    cur_month = ""; month_pnl = 0.0; month_entries = 0
    consec_losses = 0; consec_pause = -1

    for i in range(max(200, start_bar), min(end_bar, TOTAL - 1)):
        bar_dt = pd.Timestamp(dt[i])
        dk = bar_dt.strftime("%Y-%m-%d")
        mk = bar_dt.strftime("%Y-%m")
        if dk != cur_day:
            cur_day = dk; day_pnl = 0.0
        if mk != cur_month:
            cur_month = mk; month_pnl = 0.0; month_entries = 0

        if in_pos:
            held = i - entry_bar
            ep = entry_price

            if side == "L":
                cur_pnl_pct = (c[i] - ep) / ep
                hit_tp = (h[i] - ep) / ep >= tp_pct
                hit_sl = (l[i] - ep) / ep <= -safenet_pct
                tp_exit = ep * (1 + tp_pct)
                sl_exit = ep * (1 - safenet_pct * SLIP)
                hit_be = in_ext and l[i] <= be_price
                # bar-level PnL tracking
                bar_pnl_pct = (c[i] - ep) / ep * 100
                bar_high_pnl = (h[i] - ep) / ep * 100
                bar_low_pnl = (l[i] - ep) / ep * 100
            else:
                cur_pnl_pct = (ep - c[i]) / ep
                hit_tp = (ep - l[i]) / ep >= tp_pct
                hit_sl = (ep - h[i]) / ep <= -safenet_pct
                tp_exit = ep * (1 - tp_pct)
                sl_exit = ep * (1 + safenet_pct * SLIP)
                hit_be = in_ext and h[i] >= be_price
                bar_pnl_pct = (ep - c[i]) / ep * 100
                bar_high_pnl = (ep - l[i]) / ep * 100  # best for short
                bar_low_pnl = (ep - h[i]) / ep * 100   # worst for short

            # Track bar data
            trade_bars.append({
                "bar": held,
                "close_pnl": bar_pnl_pct,
                "best_pnl": bar_high_pnl,
                "worst_pnl": bar_low_pnl,
                "gk_pctile": gk_p[i] if not np.isnan(gk_p[i]) else None,
                "atr": atr_arr[i] if not np.isnan(atr_arr[i]) else None,
            })

            ex_p = None; ex_r = None
            if hit_sl:
                ex_p, ex_r = sl_exit, "SL"
            elif hit_tp:
                ex_p, ex_r = tp_exit, "TP"
            elif in_ext:
                if hit_be:
                    ex_p, ex_r = be_price, "BE"
                elif i - ext_start >= ext:
                    ex_p, ex_r = c[i], "MH-ext"
            elif held >= maxhold:
                if cur_pnl_pct > 0 and ext > 0:
                    in_ext = True; ext_start = i; be_price = ep
                else:
                    ex_p, ex_r = c[i], "MH"

            if ex_p is not None:
                raw = ((ex_p - ep) / ep if side == "L" else (ep - ex_p) / ep) * NOTIONAL
                net = raw - FEE
                # Find max favorable excursion and max adverse excursion
                mfe = max(b["best_pnl"] for b in trade_bars)
                mae = min(b["worst_pnl"] for b in trade_bars)

                trades.append({
                    "entry_bar": entry_bar,
                    "entry_price": ep,
                    "exit_bar": i,
                    "exit_price": ex_p,
                    "held": held,
                    "pnl": net,
                    "reason": ex_r,
                    "gk_at_entry": entry_gk,
                    "atr_at_entry": entry_atr,
                    "bars": list(trade_bars),  # full lifecycle
                    "mfe_pct": mfe,
                    "mae_pct": mae,
                    "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m"),
                    "entry_dt": str(pd.Timestamp(dt[entry_bar])),
                })
                day_pnl += net; month_pnl += net
                last_exit = i
                consec_losses = consec_losses + 1 if net < 0 else 0
                if consec_losses >= 4:
                    consec_pause = i + 24
                in_pos = False; in_ext = False

        if in_pos: continue
        if i - last_exit < cooldown: continue
        if i < consec_pause: continue
        if day_pnl <= -200: continue
        if month_pnl <= monthly_loss: continue
        if month_entries >= 20: continue
        if hours[i] in block_hours or days[i] in block_days: continue

        gk_val = gk_p[i]
        if np.isnan(gk_val) or gk_val >= gk_thresh: continue

        bo = bo_up[i] if side == "L" else bo_dn[i]
        if bo:
            entry_price = o[i + 1]
            entry_bar = i
            entry_gk = gk_val
            entry_atr = atr_arr[i] if not np.isnan(atr_arr[i]) else None
            trade_bars = []
            in_pos = True; in_ext = False
            month_entries += 1

    return trades


# ===== Run V13 baseline =====
print("\n" + "=" * 70)
print("V14-R1: V13 MH 虧損出場特徵分析")
print("=" * 70)

# L
trades_l = backtest_v13_detailed("L", gk_p_l, 0.25, 0.035, 6, 0.035, 6, -75, BH, BD_L, 0, TOTAL)
l_is = [t for t in trades_l if t["entry_bar"] < IS_END]
l_oos = [t for t in trades_l if t["entry_bar"] >= IS_END]

# S
trades_s = backtest_v13_detailed("S", gk_p_s, 0.35, 0.02, 10, 0.04, 8, -150, BH, BD_S, 0, TOTAL)
s_is = [t for t in trades_s if t["entry_bar"] < IS_END]
s_oos = [t for t in trades_s if t["entry_bar"] >= IS_END]

print(f"\nL: IS {len(l_is)}t ${sum(t['pnl'] for t in l_is):.0f} | OOS {len(l_oos)}t ${sum(t['pnl'] for t in l_oos):.0f}")
print(f"S: IS {len(s_is)}t ${sum(t['pnl'] for t in s_is):.0f} | OOS {len(s_oos)}t ${sum(t['pnl'] for t in s_oos):.0f}")

# ===== 1. Exit distribution =====
print("\n" + "=" * 70)
print("1. V13 出場分佈")
print("=" * 70)

def print_exit_dist(trades, label):
    if not trades:
        print(f"  {label}: no trades")
        return
    reasons = {}
    for t in trades:
        r = t["reason"]
        if r not in reasons:
            reasons[r] = {"n": 0, "pnl": 0, "wins": 0}
        reasons[r]["n"] += 1
        reasons[r]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            reasons[r]["wins"] += 1
    total = len(trades)
    print(f"\n  {label} ({total}t, ${sum(t['pnl'] for t in trades):.0f}):")
    for r in ["TP", "MH", "MH-ext", "BE", "SL"]:
        if r in reasons:
            d = reasons[r]
            avg = d["pnl"] / d["n"]
            wr = d["wins"] / d["n"] * 100
            print(f"    {r:8s}: {d['n']:3d}t ({d['n']/total*100:4.0f}%)  avg ${avg:+7.1f}  WR {wr:.0f}%")

print_exit_dist(l_oos, "L OOS")
print_exit_dist(s_oos, "S OOS")
print_exit_dist(l_is, "L IS")
print_exit_dist(s_is, "S IS")


# ===== 2. MH 虧損交易 PnL 軌跡分析 =====
print("\n" + "=" * 70)
print("2. MH 虧損出場 — 逐 bar PnL 軌跡")
print("=" * 70)

def analyze_mh_trajectory(trades, label, maxhold):
    mh_losses = [t for t in trades if t["reason"] == "MH" and t["pnl"] < 0]
    tp_wins = [t for t in trades if t["reason"] == "TP"]
    mh_ext = [t for t in trades if t["reason"] == "MH-ext"]

    if not mh_losses:
        print(f"\n  {label}: no MH loss trades")
        return

    print(f"\n  {label}: {len(mh_losses)} MH 虧損交易")
    print(f"  avg PnL: ${np.mean([t['pnl'] for t in mh_losses]):.1f}")
    print(f"  avg MFE: {np.mean([t['mfe_pct'] for t in mh_losses]):.2f}%")
    print(f"  avg MAE: {np.mean([t['mae_pct'] for t in mh_losses]):.2f}%")

    # Average PnL at each bar
    print(f"\n  逐 bar 平均 close PnL (%):")
    print(f"  {'bar':>4s} | {'MH虧損':>8s} | {'TP獲利':>8s} | {'MH-ext':>8s} | {'差異':>8s}")
    print(f"  {'----':>4s}-+-{'--------':>8s}-+-{'--------':>8s}-+-{'--------':>8s}-+-{'--------':>8s}")

    for bar_idx in range(maxhold + 3):
        mh_vals = [t["bars"][bar_idx]["close_pnl"] for t in mh_losses
                   if bar_idx < len(t["bars"])]
        tp_vals = [t["bars"][bar_idx]["close_pnl"] for t in tp_wins
                   if bar_idx < len(t["bars"])]
        ext_vals = [t["bars"][bar_idx]["close_pnl"] for t in mh_ext
                    if bar_idx < len(t["bars"])]

        mh_avg = np.mean(mh_vals) if mh_vals else float('nan')
        tp_avg = np.mean(tp_vals) if tp_vals else float('nan')
        ext_avg = np.mean(ext_vals) if ext_vals else float('nan')
        diff = mh_avg - tp_avg if not (np.isnan(mh_avg) or np.isnan(tp_avg)) else float('nan')

        n_str = f"({len(mh_vals)})" if mh_vals else ""
        print(f"  {bar_idx+1:4d} | {mh_avg:+7.2f}% | {tp_avg:+7.2f}% | {ext_avg:+7.2f}% | {diff:+7.2f}%")

    # Classification: 從頭虧 vs 先賺後虧
    print(f"\n  分類：「從頭虧」vs「先賺後虧」:")
    always_losing = 0
    was_winning = 0
    for t in mh_losses:
        max_pos = max(b["best_pnl"] for b in t["bars"])
        if max_pos > 0.5:  # 曾經超過 +0.5%
            was_winning += 1
        else:
            always_losing += 1
    print(f"    從頭虧（MFE < 0.5%）: {always_losing}t ({always_losing/len(mh_losses)*100:.0f}%)")
    print(f"    先賺後虧（MFE >= 0.5%）: {was_winning}t ({was_winning/len(mh_losses)*100:.0f}%)")

    # 再細分先賺後虧的 MFE
    if was_winning > 0:
        ww_mfe = [t["mfe_pct"] for t in mh_losses if max(b["best_pnl"] for b in t["bars"]) > 0.5]
        print(f"    先賺後虧的 MFE 分佈: min={min(ww_mfe):.2f}% median={np.median(ww_mfe):.2f}% max={max(ww_mfe):.2f}%")
        # 這些交易在哪一個 bar 達到 MFE？
        mfe_bars = []
        for t in mh_losses:
            if max(b["best_pnl"] for b in t["bars"]) > 0.5:
                best_bar = max(range(len(t["bars"])), key=lambda j: t["bars"][j]["best_pnl"])
                mfe_bars.append(best_bar + 1)
        print(f"    MFE 出現在 bar: {sorted(mfe_bars)}")

    return mh_losses, tp_wins

print("\n--- L (OOS) ---")
l_mh_oos, l_tp_oos = analyze_mh_trajectory(l_oos, "L OOS", 6)
print("\n--- S (OOS) ---")
s_mh_oos, s_tp_oos = analyze_mh_trajectory(s_oos, "S OOS", 10)
print("\n--- L (IS) ---")
l_mh_is, l_tp_is = analyze_mh_trajectory(l_is, "L IS", 6)
print("\n--- S (IS) ---")
s_mh_is, s_tp_is = analyze_mh_trajectory(s_is, "S IS", 10)


# ===== 3. GK pctile 分佈比較 =====
print("\n" + "=" * 70)
print("3. 進場 GK pctile 分佈：MH 虧損 vs TP 獲利")
print("=" * 70)

def compare_gk(mh_trades, tp_trades, label):
    if not mh_trades or not tp_trades:
        return
    mh_gk = [t["gk_at_entry"] * 100 for t in mh_trades if t["gk_at_entry"] is not None]
    tp_gk = [t["gk_at_entry"] * 100 for t in tp_trades if t["gk_at_entry"] is not None]
    print(f"\n  {label}:")
    print(f"    MH虧損 GK: n={len(mh_gk)} mean={np.mean(mh_gk):.1f} median={np.median(mh_gk):.1f} "
          f"[{np.min(mh_gk):.1f} - {np.max(mh_gk):.1f}]")
    print(f"    TP獲利 GK: n={len(tp_gk)} mean={np.mean(tp_gk):.1f} median={np.median(tp_gk):.1f} "
          f"[{np.min(tp_gk):.1f} - {np.max(tp_gk):.1f}]")

    # 分段比較
    bins = [(0, 10), (10, 15), (15, 20), (20, 25), (25, 35)]
    print(f"    GK 分段:")
    for lo, hi in bins:
        mh_n = sum(1 for g in mh_gk if lo <= g < hi)
        tp_n = sum(1 for g in tp_gk if lo <= g < hi)
        mh_pct = mh_n / len(mh_gk) * 100 if mh_gk else 0
        tp_pct = tp_n / len(tp_gk) * 100 if tp_gk else 0
        print(f"      GK {lo:2d}-{hi:2d}: MH {mh_n:2d}t ({mh_pct:4.0f}%) | TP {tp_n:2d}t ({tp_pct:4.0f}%)")

compare_gk(l_mh_oos, l_tp_oos, "L OOS")
compare_gk(s_mh_oos, s_tp_oos, "S OOS")
compare_gk(l_mh_is, l_tp_is, "L IS")
compare_gk(s_mh_is, s_tp_is, "S IS")


# ===== 4. 早期 PnL 預測力 =====
print("\n" + "=" * 70)
print("4. 早期 PnL 作為 MH 虧損預測器")
print("=" * 70)

def early_pnl_predictor(trades, label, check_bars=[1, 2, 3]):
    """Check if PnL at bar N can predict final exit reason."""
    if not trades:
        return
    print(f"\n  {label} ({len(trades)}t):")

    for bar_n in check_bars:
        print(f"\n    Bar {bar_n} close PnL 作為預測器:")
        # Split by bar N PnL sign
        pos_at_n = [t for t in trades if len(t["bars"]) > bar_n - 1
                    and t["bars"][bar_n - 1]["close_pnl"] > 0]
        neg_at_n = [t for t in trades if len(t["bars"]) > bar_n - 1
                    and t["bars"][bar_n - 1]["close_pnl"] <= 0]

        for subset, lbl in [(pos_at_n, f"bar{bar_n} 正"), (neg_at_n, f"bar{bar_n} 負")]:
            if not subset:
                continue
            reasons = {}
            for t in subset:
                r = t["reason"]
                reasons[r] = reasons.get(r, 0) + 1
            total = len(subset)
            parts = [f"{r}:{n}" for r, n in sorted(reasons.items())]
            mh_rate = reasons.get("MH", 0) / total * 100
            avg_pnl = np.mean([t["pnl"] for t in subset])
            print(f"      {lbl}: {total:3d}t -> {', '.join(parts)} | MH率 {mh_rate:.0f}% | avg ${avg_pnl:+.0f}")

    # More granular: bar 2 PnL threshold scan
    print(f"\n    Bar 2 close PnL 閾值掃描:")
    for thresh in [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0]:
        below = [t for t in trades if len(t["bars"]) > 1
                 and t["bars"][1]["close_pnl"] <= thresh]
        if not below:
            continue
        mh_n = sum(1 for t in below if t["reason"] == "MH")
        avg = np.mean([t["pnl"] for t in below])
        print(f"      bar2 <= {thresh:+.1f}%: {len(below):3d}t MH率 {mh_n/len(below)*100:.0f}% avg ${avg:+.0f}")

early_pnl_predictor(l_oos, "L OOS")
early_pnl_predictor(s_oos, "S OOS")
early_pnl_predictor(l_is, "L IS")
early_pnl_predictor(s_is, "S IS")


# ===== 5. ATR at entry =====
print("\n" + "=" * 70)
print("5. ATR(14) at entry: MH 虧損 vs TP 獲利")
print("=" * 70)

def compare_atr(mh_trades, tp_trades, label):
    if not mh_trades or not tp_trades:
        return
    mh_atr = [t["atr_at_entry"] for t in mh_trades if t["atr_at_entry"] is not None]
    tp_atr = [t["atr_at_entry"] for t in tp_trades if t["atr_at_entry"] is not None]
    if not mh_atr or not tp_atr:
        return
    print(f"\n  {label}:")
    print(f"    MH虧損 ATR: mean=${np.mean(mh_atr):.1f} median=${np.median(mh_atr):.1f}")
    print(f"    TP獲利 ATR: mean=${np.mean(tp_atr):.1f} median=${np.median(tp_atr):.1f}")

compare_atr(l_mh_oos, l_tp_oos, "L OOS")
compare_atr(s_mh_oos, s_tp_oos, "S OOS")


# ===== 6. MFE timing: 贏家 vs 輸家的獲利時間分佈 =====
print("\n" + "=" * 70)
print("6. MFE timing（最大浮盈出現在哪個 bar）")
print("=" * 70)

def mfe_timing(trades, label):
    tp_trades = [t for t in trades if t["reason"] == "TP"]
    mh_trades = [t for t in trades if t["reason"] == "MH" and t["pnl"] < 0]
    ext_trades = [t for t in trades if t["reason"] == "MH-ext"]

    for subset, name in [(tp_trades, "TP"), (mh_trades, "MH虧損"), (ext_trades, "MH-ext")]:
        if not subset:
            continue
        mfe_bars = []
        for t in subset:
            best_bar = max(range(len(t["bars"])), key=lambda j: t["bars"][j]["best_pnl"])
            mfe_bars.append(best_bar + 1)
        print(f"  {label} {name}: MFE bar avg={np.mean(mfe_bars):.1f} "
              f"median={np.median(mfe_bars):.0f} [{min(mfe_bars)}-{max(mfe_bars)}]")

mfe_timing(l_oos, "L OOS")
mfe_timing(s_oos, "S OOS")


# ===== 7. 如果提前出場（bar 3 / bar 4），效果如何？=====
print("\n" + "=" * 70)
print("7. MH 虧損交易假設提前出場的效果")
print("=" * 70)

def early_exit_what_if(mh_trades, label):
    if not mh_trades:
        return
    print(f"\n  {label}: {len(mh_trades)} MH 虧損交易")
    print(f"  actual avg PnL: ${np.mean([t['pnl'] for t in mh_trades]):.1f}")

    for early_bar in [2, 3, 4, 5]:
        early_pnls = []
        for t in mh_trades:
            if early_bar - 1 < len(t["bars"]):
                close_pnl_pct = t["bars"][early_bar - 1]["close_pnl"] / 100
                pnl = close_pnl_pct * NOTIONAL - FEE
                early_pnls.append(pnl)
        if early_pnls:
            better = sum(1 for p in early_pnls if p > t["pnl"])
            print(f"  bar {early_bar} 出場: avg ${np.mean(early_pnls):.1f} | "
                  f"比 MH 好: {sum(1 for i,t2 in enumerate(mh_trades) if early_pnls[i] > t2['pnl'])}/{len(mh_trades)}")

early_exit_what_if(l_mh_oos, "L OOS MH虧損")
early_exit_what_if(s_mh_oos, "S OOS MH虧損")


# ===== 8. 總結：哪些特徵最有預測力？ =====
print("\n" + "=" * 70)
print("8. 總結：可利用的出場改進方向")
print("=" * 70)

# Check: bar 2 PnL < 0 -> what % are MH losses?
for trades, label in [(l_oos, "L OOS"), (s_oos, "S OOS"), (l_is, "L IS"), (s_is, "S IS")]:
    all_t = len(trades)
    b2_neg = [t for t in trades if len(t["bars"]) > 1 and t["bars"][1]["close_pnl"] < 0]
    b2_neg_mh = [t for t in b2_neg if t["reason"] == "MH"]
    b2_pos = [t for t in trades if len(t["bars"]) > 1 and t["bars"][1]["close_pnl"] >= 0]
    b2_pos_tp = [t for t in b2_pos if t["reason"] == "TP"]
    print(f"  {label}: bar2<0 -> {len(b2_neg)}t (MH {len(b2_neg_mh)}t = {len(b2_neg_mh)/len(b2_neg)*100:.0f}% MH率) | "
          f"bar2>=0 -> {len(b2_pos)}t (TP {len(b2_pos_tp)}t = {len(b2_pos_tp)/len(b2_pos)*100:.0f}% TP率)")

print("\nDone.")
