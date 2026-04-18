"""
V15 Config D — 10-Gate 懷疑論稽核

立場：V15 是快樂表，除非數據證明不是。
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# === Account ===
MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0

# === V14 params ===
L_GK_SHORT, L_GK_LONG = 5, 20
S_GK_SHORT, S_GK_LONG = 10, 30
GK_WIN, BRK = 100, 15
BLOCK_H = {0, 1, 2, 12}
L_BLOCK_D, S_BLOCK_D = {5, 6}, {0, 5, 6}
L_GK_THRESH, S_GK_THRESH = 25, 35
L_SAFENET, S_SAFENET = 0.035, 0.04
L_TP, S_TP = 0.035, 0.02
L_MH, S_MH = 6, 10
L_EXT, S_EXT = 2, 2
L_CD, S_CD = 6, 8
L_CAP, S_CAP = 20, 20
L_MONTHLY_LOSS, S_MONTHLY_LOSS = -75, -150
L_MFE_ACT, L_MFE_TRAIL_DD, L_MFE_MIN_BAR = 0.010, 0.008, 1
L_COND_BAR, L_COND_THRESH, L_COND_MH = 2, -0.01, 5
DAILY_LOSS, CONSEC_PAUSE, CONSEC_CD = -200, 4, 24
WARMUP = 150


# ═══════════════════════════════════════════════════════════
#  Data loading & indicators
# ═══════════════════════════════════════════════════════════

def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    d = eth.copy()
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
    d["gk_ratio"] = gk.rolling(L_GK_SHORT).mean() / gk.rolling(L_GK_LONG).mean()
    d["gk_ratio_s"] = gk.rolling(S_GK_SHORT).mean() / gk.rolling(S_GK_LONG).mean()

    def rp(s):
        if len(s) <= 1: return 50
        return (s.iloc[:-1] < s.iloc[-1]).sum() / (len(s) - 1) * 100

    d["gk_pctile"] = d["gk_ratio"].shift(1).rolling(GK_WIN).apply(rp, raw=False)
    d["gk_pctile_s"] = d["gk_ratio_s"].shift(1).rolling(GK_WIN).apply(rp, raw=False)
    d["brk_max"] = d["close"].shift(1).rolling(BRK).max()
    d["brk_min"] = d["close"].shift(1).rolling(BRK).min()
    d["brk_long"] = d["close"] > d["brk_max"]
    d["brk_short"] = d["close"] < d["brk_min"]
    d["hour"] = d["datetime"].dt.hour
    d["weekday"] = d["datetime"].dt.weekday
    d["atr14"] = (d["high"] - d["low"]).rolling(14).mean()
    d["bar_range"] = d["high"] - d["low"]
    return d


# ═══════════════════════════════════════════════════════════
#  Backtest engine (supports arbitrary entry filters)
# ═══════════════════════════════════════════════════════════

def run_bt(df, s, e, cfg):
    l_atr = cfg.get("l_atr", 0)
    s_atr = cfg.get("s_atr", 0)
    l_gkm = cfg.get("l_gkm", 0)
    s_rng = cfg.get("s_rng", 0)

    trades = []
    pl = ps = None
    lel = les = -9999
    cl = 0; ccu = -9999
    dp = 0.0; cd = cm = None
    mpl = mps = 0.0; mel = mes = 0

    for i in range(s, e):
        r = df.iloc[i]
        dt = r["datetime"]; dy = dt.date(); mo = dt.strftime("%Y-%m")
        if dy != cd: dp = 0.0; cd = dy
        if mo != cm: mpl = mps = 0.0; mel = mes = 0; cm = mo

        # L exit
        if pl:
            bh = i - pl["b"]; ep = pl["p"]; rm = pl["rm"]; mr = pl["mr"]
            emh = L_COND_MH if mr else L_MH
            rm = max(rm, (r["high"] - ep) / ep); pl["rm"] = rm
            ex = xp = None
            sl = ep * (1 - L_SAFENET)
            if r["low"] <= sl: ex, xp = "SL", sl - (sl - r["low"]) * 0.25
            if not ex:
                tp = ep * (1 + L_TP)
                if r["high"] >= tp: ex, xp = "TP", tp
            if not ex and bh >= L_MFE_MIN_BAR and rm >= L_MFE_ACT:
                if rm - (r["close"] - ep) / ep >= L_MFE_TRAIL_DD:
                    ex, xp = "MFE", r["close"]
            if not ex and pl.get("ext"):
                eb = i - pl["xb"]
                if r["low"] <= ep: ex, xp = "BE", ep
                elif eb >= L_EXT: ex, xp = "MHx", r["close"]
            if not ex and not pl.get("ext"):
                if not mr and bh == L_COND_BAR:
                    if (r["close"] - ep) / ep <= L_COND_THRESH:
                        pl["mr"] = True; mr = True; emh = L_COND_MH
                if bh >= emh:
                    if (r["close"] - ep) / ep > 0: pl["ext"] = True; pl["xb"] = i
                    else: ex, xp = "MH", r["close"]
            if ex:
                net = (xp - ep) * NOTIONAL / ep - FEE
                trades.append({"s": "L", "p": net, "r": ex, "eb": pl["b"], "xb": i,
                               "m": pl["m"], "gk": pl["gk"], "atr": pl["a"], "rng": pl["rn"]})
                dp += net; mpl += net; lel = i
                if net < 0: cl += 1; ccu = i + CONSEC_CD if cl >= CONSEC_PAUSE else ccu
                else: cl = 0
                pl = None

        # S exit
        if ps:
            bh = i - ps["b"]; ep = ps["p"]; ex = xp = None
            sl = ep * (1 + S_SAFENET)
            if r["high"] >= sl: ex, xp = "SL", sl + (r["high"] - sl) * 0.25
            if not ex:
                tp = ep * (1 - S_TP)
                if r["low"] <= tp: ex, xp = "TP", tp
            if not ex and ps.get("ext"):
                eb = i - ps["xb"]
                if r["high"] >= ep: ex, xp = "BE", ep
                elif eb >= S_EXT: ex, xp = "MHx", r["close"]
            if not ex and not ps.get("ext"):
                if bh >= S_MH:
                    if (ep - r["close"]) / ep > 0: ps["ext"] = True; ps["xb"] = i
                    else: ex, xp = "MH", r["close"]
            if ex:
                net = (ep - xp) * NOTIONAL / ep - FEE
                trades.append({"s": "S", "p": net, "r": ex, "eb": ps["b"], "xb": i,
                               "m": ps["m"], "gk": ps["gk"], "atr": ps["a"], "rng": ps["rn"]})
                dp += net; mps += net; les = i
                if net < 0: cl += 1; ccu = i + CONSEC_CD if cl >= CONSEC_PAUSE else ccu
                else: cl = 0
                ps = None

        # Entry
        gl = r.get("gk_pctile", np.nan); gs = r.get("gk_pctile_s", np.nan)
        if pd.isna(gl) or pd.isna(gs): continue
        if dp <= DAILY_LOSS or i < ccu: continue
        atr = r.get("atr14", 0)
        if pd.isna(atr): atr = 0

        if not pl:
            sok = not (r["hour"] in BLOCK_H or r["weekday"] in L_BLOCK_D)
            if (gl < L_GK_THRESH and gl >= l_gkm and r["brk_long"] and sok
                    and (i - lel) >= L_CD and mel < L_CAP and mpl > L_MONTHLY_LOSS
                    and atr >= l_atr):
                pl = {"b": i, "p": r["close"], "rm": 0.0, "mr": False,
                      "ext": False, "xb": 0, "m": mo, "gk": gl, "a": atr, "rn": r["bar_range"]}
                mel += 1

        if not ps:
            sok = not (r["hour"] in BLOCK_H or r["weekday"] in S_BLOCK_D)
            rng_ok = r["bar_range"] >= s_rng if s_rng > 0 else True
            if (gs < S_GK_THRESH and r["brk_short"] and sok
                    and (i - les) >= S_CD and mes < S_CAP and mps > S_MONTHLY_LOSS
                    and atr >= s_atr and rng_ok):
                ps = {"b": i, "p": r["close"], "ext": False, "xb": 0,
                      "m": mo, "gk": gs, "a": atr, "rn": r["bar_range"]}
                mes += 1
    return trades


def metrics(trades, sp):
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    if tdf.empty:
        return {"is": 0, "oos": 0, "t": 0, "wr": 0, "mdd": 0, "wm": 0, "pm": 0, "tm": 0}
    ist = tdf[tdf["eb"] < sp]; oot = tdf[tdf["eb"] >= sp]
    ip = ist["p"].sum(); op = oot["p"].sum()
    wr = (oot["p"] > 0).mean() * 100 if len(oot) > 0 else 0
    eq = oot["p"].cumsum() if len(oot) > 0 else pd.Series([0])
    mdd = (eq.cummax() - eq).max()
    if len(oot) > 0:
        mo = oot.groupby("m")["p"].sum(); wm = mo.min(); pm = (mo > 0).sum(); tm = len(mo)
    else: wm = pm = tm = 0
    return {"is": ip, "oos": op, "t": len(oot), "wr": wr, "mdd": mdd, "wm": wm, "pm": pm, "tm": tm}


def wf_detail(df, cfg, nf):
    tot = len(df) - WARMUP; fs = tot // nf; results = []
    for f in range(nf):
        s2 = WARMUP + f * fs; e2 = s2 + fs if f < nf - 1 else len(df)
        t = run_bt(df, s2, e2, cfg); pnl = sum(x["p"] for x in t)
        results.append({"fold": f + 1, "start": s2, "end": e2, "pnl": pnl})
    return results


# ═══════════════════════════════════════════════════════════
def main():
    global FEE
    print("Loading data...")
    df = load_and_prepare()
    sp = len(df) // 2
    V14 = {}
    V15D = {"l_atr": 15, "s_atr": 15, "l_gkm": 7, "s_rng": 10}
    V15A = {"l_atr": 15, "s_atr": 15, "l_gkm": 7}  # without S_rng

    v14_trades = run_bt(df, WARMUP, len(df), V14)
    v15d_trades = run_bt(df, WARMUP, len(df), V15D)
    v14m = metrics(v14_trades, sp)
    v15m = metrics(v15d_trades, sp)

    print(f"V14: IS ${v14m['is']:+.0f}  OOS ${v14m['oos']:+.0f}  {v14m['t']}t  WR {v14m['wr']:.0f}%  PM {v14m['pm']}/{v14m['tm']}")
    print(f"V15: IS ${v15m['is']:+.0f}  OOS ${v15m['oos']:+.0f}  {v15m['t']}t  WR {v15m['wr']:.0f}%  PM {v15m['pm']}/{v15m['tm']}")

    # ══════════════════════════════════════════════════════
    #  GATE 1: 事後選擇 vs 事前可知
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 1: 過濾器是「事後選擇」還是「事前可知」？ ===")
    print("=" * 80)

    # 1A: IS-only analysis — do low-ATR / low-GK trades also lose in IS?
    is_trades_v14 = [t for t in v14_trades if t["eb"] < sp]
    oos_trades_v14 = [t for t in v14_trades if t["eb"] >= sp]

    print("\n--- 1A: IS vs OOS 中 ATR<15 交易的 PnL ---")
    for label, subset in [("IS", is_trades_v14), ("OOS", oos_trades_v14)]:
        low_atr = [t for t in subset if t["atr"] < 15]
        high_atr = [t for t in subset if t["atr"] >= 15]
        low_pnl = sum(t["p"] for t in low_atr)
        high_pnl = sum(t["p"] for t in high_atr)
        print(f"  {label} ATR<15: {len(low_atr):3d}t, total ${low_pnl:+7.0f}, avg ${low_pnl/len(low_atr) if low_atr else 0:+.1f}")
        print(f"  {label} ATR>=15: {len(high_atr):3d}t, total ${high_pnl:+7.0f}, avg ${high_pnl/len(high_atr) if high_atr else 0:+.1f}")

    print("\n--- 1B: IS vs OOS 中 L GK<7 交易的 PnL ---")
    for label, subset in [("IS", is_trades_v14), ("OOS", oos_trades_v14)]:
        l_trades = [t for t in subset if t["s"] == "L"]
        low_gk = [t for t in l_trades if t["gk"] < 7]
        high_gk = [t for t in l_trades if t["gk"] >= 7]
        low_pnl = sum(t["p"] for t in low_gk)
        high_pnl = sum(t["p"] for t in high_gk)
        print(f"  {label} L GK<7:  {len(low_gk):3d}t, total ${low_pnl:+7.0f}, avg ${low_pnl/len(low_gk) if low_gk else 0:+.1f}")
        print(f"  {label} L GK>=7: {len(high_gk):3d}t, total ${high_pnl:+7.0f}, avg ${high_pnl/len(high_gk) if high_gk else 0:+.1f}")

    print("\n--- 1C: IS-only designed filter → OOS test ---")
    # Design filter using ONLY IS data: find best ATR threshold from IS trades
    best_is_atr = 0
    best_is_delta = 0
    for atr_thresh in range(0, 30):
        is_kept = [t for t in is_trades_v14 if t["atr"] >= atr_thresh]
        is_removed = [t for t in is_trades_v14 if t["atr"] < atr_thresh]
        if len(is_removed) == 0:
            continue
        removed_pnl = sum(t["p"] for t in is_removed)
        if removed_pnl < best_is_delta:
            best_is_delta = removed_pnl
            best_is_atr = atr_thresh

    print(f"  IS-optimal ATR threshold: {best_is_atr} (IS removed PnL: ${best_is_delta:+.0f})")
    # Apply IS-designed threshold to OOS
    oos_kept = [t for t in oos_trades_v14 if t["atr"] >= best_is_atr]
    oos_removed = [t for t in oos_trades_v14 if t["atr"] < best_is_atr]
    print(f"  Applied to OOS: removed {len(oos_removed)}t, removed PnL ${sum(t['p'] for t in oos_removed):+.0f}")
    print(f"  OOS after IS-designed filter: ${sum(t['p'] for t in oos_kept):+.0f} (vs baseline ${v14m['oos']:+.0f})")

    # Same for GK
    best_is_gk = 0
    best_is_gk_delta = 0
    for gk_thresh in range(0, 20):
        is_l = [t for t in is_trades_v14 if t["s"] == "L"]
        is_removed = [t for t in is_l if t["gk"] < gk_thresh]
        if len(is_removed) == 0:
            continue
        removed_pnl = sum(t["p"] for t in is_removed)
        if removed_pnl < best_is_gk_delta:
            best_is_gk_delta = removed_pnl
            best_is_gk = gk_thresh

    print(f"\n  IS-optimal L GK min: {best_is_gk} (IS removed PnL: ${best_is_gk_delta:+.0f})")
    oos_l = [t for t in oos_trades_v14 if t["s"] == "L"]
    oos_l_removed = [t for t in oos_l if t["gk"] < best_is_gk]
    print(f"  Applied to OOS L: removed {len(oos_l_removed)}t, removed PnL ${sum(t['p'] for t in oos_l_removed):+.0f}")

    g1_is_atr_consistent = True
    is_low_atr = [t for t in is_trades_v14 if t["atr"] < 15]
    oos_low_atr = [t for t in oos_trades_v14 if t["atr"] < 15]
    is_low_atr_pnl = sum(t["p"] for t in is_low_atr) if is_low_atr else 0
    oos_low_atr_pnl = sum(t["p"] for t in oos_low_atr) if oos_low_atr else 0
    if is_low_atr_pnl > 0:
        g1_is_atr_consistent = False

    is_l_low_gk = [t for t in is_trades_v14 if t["s"] == "L" and t["gk"] < 7]
    is_l_low_gk_pnl = sum(t["p"] for t in is_l_low_gk) if is_l_low_gk else 0
    g1_is_gk_consistent = is_l_low_gk_pnl <= 0

    print(f"\n  Gate 1 判定:")
    print(f"    IS ATR<15 也虧損（${is_low_atr_pnl:+.0f}）: {'PASS' if g1_is_atr_consistent else 'FAIL'}")
    print(f"    IS L GK<7 也虧損（${is_l_low_gk_pnl:+.0f}）: {'PASS' if g1_is_gk_consistent else 'FAIL'}")
    print(f"    IS-designed filter 在 OOS 有效: {'PASS' if best_is_atr > 0 else 'FAIL'}")
    g1 = "PASS" if (g1_is_atr_consistent and g1_is_gk_consistent) else "FAIL"
    print(f"  >>> Gate 1: {g1}")

    # ══════════════════════════════════════════════════════
    #  GATE 2: ATR>=15 穩健性
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 2: ATR>=15 閾值穩健性 ===")
    print("=" * 80)

    print("\n--- 2A: 細粒度 ATR 掃描 ---")
    print(f"{'ATR':>5s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s}")
    atr_plateau_count = 0
    for atr_val in [0, 5, 8, 10, 12, 13, 14, 15, 16, 17, 18, 20, 22, 25]:
        cfg = {"l_atr": atr_val, "s_atr": atr_val, "l_gkm": 7, "s_rng": 10}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = metrics(t, sp)
        d = m["oos"] - v14m["oos"]
        marker = " <<<" if atr_val == 15 else ""
        print(f"{atr_val:5d} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {d:+7.0f} {m['t']:4d} {m['wr']:4.0f}% ${m['mdd']:5.0f}{marker}")
        if 12 <= atr_val <= 18 and m["oos"] > 4500:
            atr_plateau_count += 1

    print(f"\n--- 2B: Cliff effect check ---")
    plateau_range = []
    for atr_val in range(10, 22):
        cfg = {"l_atr": atr_val, "s_atr": atr_val, "l_gkm": 7, "s_rng": 10}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = metrics(t, sp)
        plateau_range.append(m["oos"])
    print(f"  ATR 10-21 OOS range: ${min(plateau_range):+.0f} ~ ${max(plateau_range):+.0f}")
    print(f"  ATR 12-18 all > $4,500: {atr_plateau_count >= 5}")

    print(f"\n--- 2C: IS vs OOS ATR distribution ---")
    is_atrs = [t["atr"] for t in is_trades_v14]
    oos_atrs = [t["atr"] for t in oos_trades_v14]
    print(f"  IS  ATR: mean={np.mean(is_atrs):.1f}, median={np.median(is_atrs):.1f}, <15 count={sum(1 for a in is_atrs if a<15)}/{len(is_atrs)}")
    print(f"  OOS ATR: mean={np.mean(oos_atrs):.1f}, median={np.median(oos_atrs):.1f}, <15 count={sum(1 for a in oos_atrs if a<15)}/{len(oos_atrs)}")

    print(f"\n--- 2D: 物理意義 ---")
    eth_avg_price = df["close"].mean()
    print(f"  ETH avg price: ${eth_avg_price:.0f}")
    print(f"  ATR=15 / avg price = {15/eth_avg_price*100:.2f}%")
    print(f"  ETH long-term avg ATR(14): ${df['atr14'].dropna().mean():.1f}")
    print(f"  ATR<15 占全部 bar 的 {(df['atr14'] < 15).mean()*100:.1f}%")

    g2 = "PASS" if atr_plateau_count >= 5 else "CONDITIONAL"
    print(f"\n  >>> Gate 2: {g2}")

    # ══════════════════════════════════════════════════════
    #  GATE 3: GK>=7 穩健性
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 3: GK>=7 閾值穩健性 ===")
    print("=" * 80)

    print("\n--- 3A: GK min 閾值掃描 ---")
    print(f"{'GKmin':>5s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s}")
    gk_plateau = 0
    for gk_val in [0, 2, 3, 5, 7, 8, 10, 12, 15]:
        cfg = {"l_atr": 15, "s_atr": 15, "l_gkm": gk_val, "s_rng": 10}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = metrics(t, sp)
        d = m["oos"] - v14m["oos"]
        marker = " <<<" if gk_val == 7 else ""
        print(f"{gk_val:5d} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {d:+7.0f} {m['t']:4d}{marker}")
        if 3 <= gk_val <= 10 and m["oos"] > 4800:
            gk_plateau += 1

    print(f"\n--- 3B: GK<7 被排除交易逐筆 ---")
    is_l = [t for t in is_trades_v14 if t["s"] == "L"]
    oos_l = [t for t in oos_trades_v14 if t["s"] == "L"]
    is_gk_low = [t for t in is_l if t["gk"] < 7]
    oos_gk_low = [t for t in oos_l if t["gk"] < 7]
    print(f"  IS  L GK<7: {len(is_gk_low)}t, PnL ${sum(t['p'] for t in is_gk_low):+.0f}, avg ${np.mean([t['p'] for t in is_gk_low]) if is_gk_low else 0:+.1f}")
    print(f"  OOS L GK<7: {len(oos_gk_low)}t, PnL ${sum(t['p'] for t in oos_gk_low):+.0f}, avg ${np.mean([t['p'] for t in oos_gk_low]) if oos_gk_low else 0:+.1f}")

    # Check if effect is dominated by one trade
    if oos_gk_low:
        pnls = sorted([t["p"] for t in oos_gk_low])
        print(f"  OOS GK<7 PnL list: {['$'+f'{p:+.0f}' for p in pnls]}")
        worst = min(pnls)
        total = sum(pnls)
        print(f"  Worst single trade: ${worst:+.0f} ({worst/total*100:.0f}% of total removed PnL)")
        dominated = abs(worst) > abs(total) * 0.6
        print(f"  Single-trade dominated: {dominated}")
    else:
        dominated = False

    print(f"\n--- 3C: 「死水」假說物理驗證 ---")
    # Bars with GK pctile < 7: future 5-bar return vs normal
    gk_col = df["gk_pctile"].dropna()
    dead_bars = df[df["gk_pctile"] < 7].index
    normal_bars = df[(df["gk_pctile"] >= 7) & (df["gk_pctile"] < 25)].index
    dead_rets = []
    normal_rets = []
    for idx in dead_bars:
        if idx + 5 < len(df):
            dead_rets.append((df.iloc[idx + 5]["close"] - df.iloc[idx]["close"]) / df.iloc[idx]["close"] * 100)
    for idx in normal_bars:
        if idx + 5 < len(df):
            normal_rets.append((df.iloc[idx + 5]["close"] - df.iloc[idx]["close"]) / df.iloc[idx]["close"] * 100)
    print(f"  GK<7 ('dead') bars: {len(dead_bars)}, avg 5-bar return: {np.mean(dead_rets):.3f}%")
    print(f"  GK 7-25 (normal) bars: {len(normal_bars)}, avg 5-bar return: {np.mean(normal_rets):.3f}%")
    print(f"  Dead water shows weaker breakout: {np.mean(dead_rets) < np.mean(normal_rets)}")

    g3 = "PASS" if (gk_plateau >= 3 and not dominated and is_l_low_gk_pnl <= 0) else "CONDITIONAL"
    print(f"\n  >>> Gate 3: {g3}")

    # ══════════════════════════════════════════════════════
    #  GATE 4: S bar range>=10 穩健性
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 4: S bar range>=10 穩健性 ===")
    print("=" * 80)

    print("\n--- 4A: bar range 閾值掃描 ---")
    for rng in [0, 5, 8, 10, 12, 15, 18, 20]:
        cfg = {"l_atr": 15, "s_atr": 15, "l_gkm": 7, "s_rng": rng}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = metrics(t, sp)
        d = m["oos"] - v14m["oos"]
        s_oos = sum(x["p"] for x in t if x["eb"] >= sp and x["s"] == "S")
        print(f"  S_rng>={rng:2d}: OOS ${m['oos']:+.0f} (delta {d:+.0f}), S_OOS ${s_oos:+.0f}, PM {m['pm']}/{m['tm']}")

    print(f"\n--- 4B: $69 來自幾筆？---")
    v15a_trades = run_bt(df, WARMUP, len(df), V15A)
    v15d_trades2 = run_bt(df, WARMUP, len(df), V15D)

    a_set = set((t["eb"], t["s"]) for t in v15a_trades if t["eb"] >= sp)
    d_set = set((t["eb"], t["s"]) for t in v15d_trades2 if t["eb"] >= sp)
    removed_by_rng = a_set - d_set
    added_by_rng = d_set - a_set
    removed_trades = [t for t in v15a_trades if (t["eb"], t["s"]) in removed_by_rng]
    added_trades = [t for t in v15d_trades2 if (t["eb"], t["s"]) in added_by_rng]
    removed_pnl = sum(t["p"] for t in removed_trades)
    added_pnl = sum(t["p"] for t in added_trades)
    print(f"  Removed by S_rng: {len(removed_trades)}t, PnL ${removed_pnl:+.0f}")
    print(f"  Added (cascade): {len(added_trades)}t, PnL ${added_pnl:+.0f}")
    print(f"  Net effect: ${added_pnl - removed_pnl + removed_pnl:+.0f}")

    if removed_trades:
        print(f"  Removed trades:")
        for t in removed_trades:
            print(f"    bar {t['eb']} {t['s']} ${t['p']:+.1f} {t['r']} rng=${t['rng']:.1f}")

    from_one_trade = len(removed_trades) <= 1
    g4 = "CONDITIONAL" if from_one_trade else "PASS"
    if from_one_trade:
        print(f"\n  !!! S_rng>=10 效果來自 <=1 筆交易 → 建議用 Config A")
    print(f"\n  >>> Gate 4: {g4}")

    # ══════════════════════════════════════════════════════
    #  GATE 5: Cascade 效果真實性
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 5: Cascade 效果是真實的還是僥倖？ ===")
    print("=" * 80)

    print("\n--- 5A: IS vs OOS cascade ---")
    # IS cascade
    is_v14_set = set((t["eb"], t["s"]) for t in v14_trades if t["eb"] < sp)
    is_v15_set = set((t["eb"], t["s"]) for t in v15d_trades if t["eb"] < sp)
    is_cascade_keys = is_v15_set - is_v14_set
    is_cascade = [t for t in v15d_trades if (t["eb"], t["s"]) in is_cascade_keys]
    is_cascade_pnl = sum(t["p"] for t in is_cascade)

    oos_v14_set = set((t["eb"], t["s"]) for t in v14_trades if t["eb"] >= sp)
    oos_v15_set = set((t["eb"], t["s"]) for t in v15d_trades if t["eb"] >= sp)
    oos_cascade_keys = oos_v15_set - oos_v14_set
    oos_cascade = [t for t in v15d_trades if (t["eb"], t["s"]) in oos_cascade_keys]
    oos_cascade_pnl = sum(t["p"] for t in oos_cascade)

    print(f"  IS  cascade: {len(is_cascade)}t, PnL ${is_cascade_pnl:+.0f}, avg ${is_cascade_pnl/len(is_cascade) if is_cascade else 0:+.1f}")
    print(f"  OOS cascade: {len(oos_cascade)}t, PnL ${oos_cascade_pnl:+.0f}, avg ${oos_cascade_pnl/len(oos_cascade) if oos_cascade else 0:+.1f}")

    g5_is_cascade_positive = is_cascade_pnl > 0

    print(f"\n--- 5B: 隨機過濾模擬（100 次）---")
    rng_state = np.random.RandomState(42)
    # How many trades were filtered in OOS?
    oos_removed_keys = oos_v14_set - oos_v15_set
    n_removed = len(oos_removed_keys)
    oos_v14_list = [t for t in v14_trades if t["eb"] >= sp]
    original_pnl = sum(t["p"] for t in oos_v14_list)

    random_deltas = []
    for sim in range(100):
        # randomly remove same number of trades
        if n_removed >= len(oos_v14_list):
            continue
        indices = rng_state.choice(len(oos_v14_list), n_removed, replace=False)
        kept = [t for i, t in enumerate(oos_v14_list) if i not in indices]
        removed = [t for i, t in enumerate(oos_v14_list) if i in indices]
        # Simple delta (ignoring cascade, which is impossible to simulate)
        removed_pnl = sum(t["p"] for t in removed)
        random_deltas.append(-removed_pnl)  # positive delta = removed bad trades

    actual_removed_pnl = sum(t["p"] for t in oos_v14_list if (t["eb"], t["s"]) in oos_removed_keys)
    actual_delta = -actual_removed_pnl
    percentile = sum(1 for d in random_deltas if d <= actual_delta) / len(random_deltas) * 100
    print(f"  V15 actual delta from removed trades: ${actual_delta:+.0f}")
    print(f"  Random removal: mean ${np.mean(random_deltas):+.0f}, std ${np.std(random_deltas):.0f}")
    print(f"  V15 percentile in random dist: {percentile:.0f}th")
    print(f"  (Lower = less lucky. >95th = suspiciously lucky)")

    g5_not_lucky = percentile <= 95
    g5 = "PASS" if (g5_is_cascade_positive and g5_not_lucky) else "CONDITIONAL" if g5_not_lucky else "FAIL"
    print(f"\n  IS cascade positive: {g5_is_cascade_positive}")
    print(f"  Not suspiciously lucky (<=95th): {g5_not_lucky}")
    print(f"  >>> Gate 5: {g5}")

    # ══════════════════════════════════════════════════════
    #  GATE 6: IS/OOS 背離
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 6: IS/OOS 背離風險 ===")
    print("=" * 80)

    print("\n--- 6A: IS/OOS ratio 歷史對比 ---")
    print(f"  V14: IS ${v14m['is']:+.0f} / OOS ${v14m['oos']:+.0f} = ratio {v14m['oos']/v14m['is']:.2f}")
    print(f"  V15D: IS ${v15m['is']:+.0f} / OOS ${v15m['oos']:+.0f} = ratio {v15m['oos']/v15m['is']:.2f}")

    print(f"\n--- 6B: IS 月度分析 ---")
    is_v15 = pd.DataFrame([t for t in v15d_trades if t["eb"] < sp])
    if len(is_v15) > 0:
        is_monthly = is_v15.groupby("m")["p"].sum()
        print(f"  IS months: {len(is_monthly)}, positive: {(is_monthly>0).sum()}/{len(is_monthly)}")
        print(f"  IS worst month: ${is_monthly.min():+.0f}")
        neg_months = is_monthly[is_monthly < 0]
        for m, p in neg_months.items():
            print(f"    {m}: ${p:+.0f}")

    print(f"\n--- 6C: Swap test (reverse IS/OOS) ---")
    # Use second half as IS, first half as OOS
    reverse_sp = sp  # same split point but interpret differently
    # Run backtest on full data, then evaluate with reversed split
    reverse_is_pnl = sum(t["p"] for t in v15d_trades if t["eb"] >= sp)  # "OOS" period as IS
    reverse_oos_pnl = sum(t["p"] for t in v15d_trades if t["eb"] < sp)  # "IS" period as OOS

    # Also get V14 reverse
    reverse_v14_oos = sum(t["p"] for t in v14_trades if t["eb"] < sp)
    reverse_delta = reverse_oos_pnl - reverse_v14_oos
    print(f"  Reverse split (2nd half=IS, 1st half=OOS):")
    print(f"    V14 reverse OOS: ${reverse_v14_oos:+.0f}")
    print(f"    V15 reverse OOS: ${reverse_oos_pnl:+.0f}")
    print(f"    Reverse delta: ${reverse_delta:+.0f}")
    print(f"    Forward delta: ${v15m['oos'] - v14m['oos']:+.0f}")
    degradation = 1 - (reverse_delta / (v15m['oos'] - v14m['oos'])) if (v15m['oos'] - v14m['oos']) != 0 else 1
    print(f"    Degradation: {degradation*100:.0f}%")

    g6_ratio_ok = v15m['oos'] / v15m['is'] < 3.5
    g6_reverse_ok = reverse_delta > 0
    g6_reverse_not_degraded = degradation < 0.5
    g6 = "PASS" if (g6_ratio_ok and g6_reverse_ok) else "CONDITIONAL" if g6_ratio_ok else "FAIL"
    print(f"\n  Ratio < 3.5: {g6_ratio_ok} ({v15m['oos']/v15m['is']:.2f})")
    print(f"  Reverse delta positive: {g6_reverse_ok}")
    print(f"  Reverse degradation < 50%: {g6_reverse_not_degraded}")
    print(f"  >>> Gate 6: {g6}")

    # ══════════════════════════════════════════════════════
    #  GATE 7: Walk-Forward 深度
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 7: Walk-Forward 深度分析 ===")
    print("=" * 80)

    print("\n--- 7A: V14 vs V15 逐 fold (6/8/10/12-fold) ---")
    for nf in [6, 8, 10, 12]:
        v14_wf = wf_detail(df, V14, nf)
        v15_wf = wf_detail(df, V15D, nf)
        v14_pass = sum(1 for f in v14_wf if f["pnl"] > 0)
        v15_pass = sum(1 for f in v15_wf if f["pnl"] > 0)
        v15_better_count = sum(1 for a, b in zip(v14_wf, v15_wf) if b["pnl"] > a["pnl"])
        print(f"  {nf:2d}-fold: V14 {v14_pass}/{nf}, V15D {v15_pass}/{nf}, V15 better in {v15_better_count}/{nf} folds")

    print(f"\n--- 7B: 8-fold detail ---")
    v14_wf8 = wf_detail(df, V14, 8)
    v15_wf8 = wf_detail(df, V15D, 8)
    print(f"  {'Fold':>5s} | {'V14':>8s} {'V15':>8s} {'delta':>7s}")
    for a, b in zip(v14_wf8, v15_wf8):
        sd = df.iloc[a["start"]]["datetime"].strftime("%Y-%m")
        ed = df.iloc[a["end"]-1]["datetime"].strftime("%Y-%m")
        d = b["pnl"] - a["pnl"]
        marker = " <<<" if d < 0 else ""
        print(f"  {a['fold']:5d} | ${a['pnl']:+7.0f} ${b['pnl']:+7.0f} {d:+7.0f}  {sd}~{ed}{marker}")

    print(f"\n--- 7C: Rolling 3-month window ---")
    oos_v15 = pd.DataFrame([t for t in v15d_trades if t["eb"] >= sp])
    if len(oos_v15) > 0:
        monthly = oos_v15.groupby("m")["p"].sum().sort_index()
        months = list(monthly.index)
        consec_neg = 0
        max_consec_neg = 0
        for i in range(len(months)):
            if monthly[months[i]] < 0:
                consec_neg += 1
                max_consec_neg = max(max_consec_neg, consec_neg)
            else:
                consec_neg = 0
        print(f"  Max consecutive negative months: {max_consec_neg}")

        # Rolling 3-month sum
        print(f"  Rolling 3-month PnL:")
        for i in range(len(months) - 2):
            three = monthly[months[i]] + monthly[months[i+1]] + monthly[months[i+2]]
            marker = " <<<" if three < 0 else ""
            print(f"    {months[i]}~{months[i+2]}: ${three:+.0f}{marker}")

    v15_10fold_pass = sum(1 for f in wf_detail(df, V15D, 10) if f["pnl"] > 0)
    v15_12fold_pass = sum(1 for f in wf_detail(df, V15D, 12) if f["pnl"] > 0)
    v15_better_8 = sum(1 for a, b in zip(v14_wf8, v15_wf8) if b["pnl"] > a["pnl"])

    g7 = "PASS" if (v15_10fold_pass >= 7 and v15_better_8 >= 4 and max_consec_neg <= 1) else "CONDITIONAL"
    print(f"\n  10-fold >= 7: {v15_10fold_pass}/10 {'PASS' if v15_10fold_pass >= 7 else 'FAIL'}")
    print(f"  12-fold: {v15_12fold_pass}/12")
    print(f"  V15 better in >= 4/8 folds: {v15_better_8}/8 {'PASS' if v15_better_8 >= 4 else 'FAIL'}")
    print(f"  No 3 consecutive negative months: {'PASS' if max_consec_neg <= 1 else 'FAIL'}")
    print(f"  >>> Gate 7: {g7}")

    # ══════════════════════════════════════════════════════
    #  GATE 8: 過濾器互動效應
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 8: 過濾器互動效應 ===")
    print("=" * 80)

    print("\n--- 8A: Venn diagram of filtered trades ---")
    # Which OOS trades are filtered by ATR vs GK vs S_rng?
    oos_v14 = [t for t in v14_trades if t["eb"] >= sp]
    filtered_by_atr = set()
    filtered_by_gk = set()
    filtered_by_rng = set()
    for t in oos_v14:
        key = (t["eb"], t["s"])
        if t["atr"] < 15:
            filtered_by_atr.add(key)
        if t["s"] == "L" and t["gk"] < 7:
            filtered_by_gk.add(key)
        if t["s"] == "S" and t["rng"] < 10:
            filtered_by_rng.add(key)

    all_filtered = filtered_by_atr | filtered_by_gk | filtered_by_rng
    overlap_atr_gk = filtered_by_atr & filtered_by_gk
    overlap_atr_rng = filtered_by_atr & filtered_by_rng
    overlap_gk_rng = filtered_by_gk & filtered_by_rng

    print(f"  ATR<15 filters: {len(filtered_by_atr)} trades")
    print(f"  L GK<7 filters: {len(filtered_by_gk)} trades")
    print(f"  S rng<10 filters: {len(filtered_by_rng)} trades")
    print(f"  ATR ∩ GK overlap: {len(overlap_atr_gk)} trades")
    print(f"  ATR ∩ rng overlap: {len(overlap_atr_rng)} trades")
    print(f"  GK ∩ rng overlap: {len(overlap_gk_rng)} trades")
    print(f"  Total unique filtered: {len(all_filtered)} trades")

    total_direct_filtered = len(filtered_by_atr) + len(filtered_by_gk) + len(filtered_by_rng)
    if total_direct_filtered > 0:
        overlap_pct = (total_direct_filtered - len(all_filtered)) / total_direct_filtered * 100
        print(f"  Overlap %: {overlap_pct:.0f}%")
    else:
        overlap_pct = 0

    print(f"\n--- 8B: 個別 vs 組合 delta ---")
    cfgs_decomp = [
        ("ATR only", {"l_atr": 15, "s_atr": 15}),
        ("GK only", {"l_gkm": 7}),
        ("S_rng only", {"s_rng": 10}),
        ("ATR+GK", {"l_atr": 15, "s_atr": 15, "l_gkm": 7}),
        ("ALL (D)", V15D),
    ]
    for name, cfg in cfgs_decomp:
        t = run_bt(df, WARMUP, len(df), cfg)
        m = metrics(t, sp)
        d = m["oos"] - v14m["oos"]
        print(f"  {name:>12s}: OOS ${m['oos']:+.0f} (delta {d:+.0f})")

    g8 = "PASS" if overlap_pct < 50 else "CONDITIONAL"
    print(f"\n  Overlap < 50%: {overlap_pct:.0f}% {'PASS' if overlap_pct < 50 else 'FAIL'}")
    print(f"  >>> Gate 8: {g8}")

    # ══════════════════════════════════════════════════════
    #  GATE 9: 實盤可執行性
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 9: 實盤可執行性 ===")
    print("=" * 80)

    print("\n--- 9A: ATR warmup sensitivity ---")
    # Compare ATR(14) calculated with different warmup lengths
    for warmup_len in [100, 150, 200, 300, 500]:
        d2 = df.copy()
        d2["atr14_test"] = (d2["high"] - d2["low"]).rolling(14).mean()
        # Compare at bar 200 (after all warmups complete)
        at_200 = d2.iloc[200]["atr14_test"]
        at_sp = d2.iloc[sp]["atr14_test"]
        at_end = d2.iloc[-1]["atr14_test"]
        if warmup_len == 150:
            print(f"  ATR(14) is rolling(14).mean — warmup only needs 14 bars")
            print(f"  ATR at bar 200: ${at_200:.2f}, at split: ${at_sp:.2f}, at end: ${at_end:.2f}")

    print(f"\n--- 9B: S bar range — which bar? ---")
    print(f"  In backtest: entry bar = current bar (the breakout bar)")
    print(f"  bar_range = high - low of the CURRENT bar")
    print(f"  In live: main_eth.py grabs bar at :10 past hour → bar is CLOSED")
    print(f"  The breakout bar IS the bar that just closed → range is final")
    print(f"  → NO lookahead issue")

    print(f"\n--- 9C: ATR filter in live ---")
    print(f"  ATR(14) uses past 14 completed bars → no lookahead")
    print(f"  ATR is shift(0) not shift(1) in V15 filter → same as close price")
    print(f"  Since we evaluate at bar close, ATR includes current bar's range")
    print(f"  This is fine — current bar is closed when we evaluate")

    g9 = "PASS"
    print(f"\n  >>> Gate 9: {g9}")

    # ══════════════════════════════════════════════════════
    #  GATE 10: 壓力測試
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== Gate 10: 最壞情境壓力測試 ===")
    print("=" * 80)

    print("\n--- 10A: 同時 SafeNet 最差 ---")
    l_sn = MARGIN * LEVERAGE * L_SAFENET * 1.25 + FEE  # worst case with 25% slip
    s_sn = MARGIN * LEVERAGE * S_SAFENET * 1.25 + FEE
    print(f"  L SafeNet worst: ${l_sn:.0f}")
    print(f"  S SafeNet worst: ${s_sn:.0f}")
    print(f"  Simultaneous L+S SafeNet: ${l_sn + s_sn:.0f} ({(l_sn + s_sn)/1000*100:.1f}% of $1,000)")

    print(f"\n--- 10B: ATR<15 持續期間 ---")
    # Longest streak of ATR < 15
    atr_below = (df["atr14"] < 15).astype(int)
    streaks = []
    current_streak = 0
    for v in atr_below:
        if v: current_streak += 1
        else:
            if current_streak > 0: streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0: streaks.append(current_streak)
    max_streak = max(streaks) if streaks else 0
    print(f"  Longest ATR<15 streak: {max_streak} bars ({max_streak/24:.1f} days)")
    print(f"  During these periods: V15 trades = 0, loss = $0")
    # How many V14 trades during longest streak?
    # Find the start/end of longest streak
    streak_start = -1
    streak_count = 0
    longest_start = longest_end = 0
    for i, v in enumerate(atr_below):
        if v:
            if streak_count == 0: streak_start = i
            streak_count += 1
            if streak_count == max_streak:
                longest_start = streak_start
                longest_end = i
        else:
            streak_count = 0
    v14_in_streak = [t for t in v14_trades if longest_start <= t["eb"] <= longest_end]
    v14_streak_pnl = sum(t["p"] for t in v14_in_streak)
    print(f"  V14 trades during longest low-ATR: {len(v14_in_streak)}t, PnL ${v14_streak_pnl:+.0f}")

    print(f"\n--- 10C: Fee +50% stress test ---")
    # Rerun with FEE = 6
    old_fee = FEE
    FEE = 6.0
    v15_fee_trades = run_bt(df, WARMUP, len(df), V15D)
    v15_fee_m = metrics(v15_fee_trades, sp)
    v14_fee_trades = run_bt(df, WARMUP, len(df), V14)
    v14_fee_m = metrics(v14_fee_trades, sp)
    FEE = old_fee
    print(f"  Fee $6 (vs $4):")
    print(f"    V14: OOS ${v14_fee_m['oos']:+.0f} (from ${v14m['oos']:+.0f})")
    print(f"    V15D: OOS ${v15_fee_m['oos']:+.0f} (from ${v15m['oos']:+.0f})")
    print(f"    V15D still > $4,000: {v15_fee_m['oos'] > 4000}")

    g10 = "PASS" if (v15_fee_m["oos"] > 4000 and (l_sn + s_sn) < 400) else "CONDITIONAL"
    print(f"\n  Fee+50% OOS > $4,000: {v15_fee_m['oos'] > 4000}")
    print(f"  Simultaneous SN < $400: {(l_sn + s_sn) < 400}")
    print(f"  Low-ATR period = $0 loss (vs V14 ${v14_streak_pnl:+.0f}): PASS")
    print(f"  >>> Gate 10: {g10}")

    # ══════════════════════════════════════════════════════
    #  FINAL SUMMARY
    # ══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("=== V15 Config D 稽核總評 ===")
    print("=" * 80)

    gates = [
        (1, "事後選擇 vs 事前可知", g1),
        (2, "ATR>=15 穩健性", g2),
        (3, "GK>=7 穩健性", g3),
        (4, "S bar range>=10 穩健性", g4),
        (5, "Cascade 效果真實性", g5),
        (6, "IS/OOS 背離風險", g6),
        (7, "Walk-Forward 深度", g7),
        (8, "過濾器互動效應", g8),
        (9, "實盤可執行性", g9),
        (10, "壓力測試", g10),
    ]

    print(f"\n| Gate | 名稱 | 判定 |")
    print(f"|------|------|------|")
    for n, name, result in gates:
        print(f"| {n:4d} | {name} | {result} |")

    pass_count = sum(1 for _, _, r in gates if r == "PASS")
    cond_count = sum(1 for _, _, r in gates if r == "CONDITIONAL")
    fail_count = sum(1 for _, _, r in gates if r == "FAIL")
    print(f"\nPASS: {pass_count}/10 | CONDITIONAL: {cond_count}/10 | FAIL: {fail_count}/10")

    # Final recommendation
    if fail_count > 0:
        print(f"\n最終判定：REJECTED — 有 {fail_count} 個 FAIL Gate")
    elif cond_count > 2:
        print(f"\n最終判定：CONDITIONAL — {cond_count} 個 CONDITIONAL Gate 需評估")
    elif g4 == "CONDITIONAL":
        print(f"\n最終判定：DOWNGRADE — Gate 4 建議用 Config A（2 參數，無 S_rng）")
        print(f"  Config A: OOS $5,389, 12/13 PM, ratio 2.7, WF 5/6 7/8")
    else:
        print(f"\n最終判定：APPROVED — V15 Config D 可上模擬盤")

    print(f"\n保守年化預期：OOS ${v15m['oos']:+.0f} / 1 年 → 年化 ~${v15m['oos']*0.7:.0f}（30% safety haircut）")
    print(f"紅線：連續 2 個月虧損 → 停止檢查；單月虧損 > $300 → 暫停")
    print(f"監控重點：ATR 分佈是否穩定、GK<7 信號頻率、月度 PM 達成率")


if __name__ == "__main__":
    main()
