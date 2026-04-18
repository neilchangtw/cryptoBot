"""
V15 R4: Final Candidate Validation

Top candidates from R1-R3:
  A: R2 base (ATR15+GK7) — 2 params, OOS $5,389
  B: R1 full (ATR15+GK7+Hour) — 4 params, OOS $5,633
  C: R1_full+TP_atr*1.0 — 5 params, OOS $5,751
  D: R2 base+S_rng>=10 — 3 params, OOS $5,458, 13/13 PM

New directions to try:
  H9: BTC GK compression filter — only L when BTC also compressed
  H10: S bar range + R1_full combo
  H11: L TP/SL level scan with R2 base (independent of V14 params)
  H12: Final champion comprehensive WF + monthly + robustness
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0

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


def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    btc = pd.read_csv(DATA_DIR / "BTCUSDT_1h_latest730d.csv", parse_dates=["datetime"])

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

    # BTC GK for H9
    btc_ln_hl = np.log(btc["high"] / btc["low"])
    btc_ln_co = np.log(btc["close"] / btc["open"])
    btc_gk = 0.5 * btc_ln_hl**2 - (2 * np.log(2) - 1) * btc_ln_co**2
    btc["gk_ratio"] = btc_gk.rolling(5).mean() / btc_gk.rolling(20).mean()
    btc["gk_pctile"] = btc["gk_ratio"].shift(1).rolling(GK_WIN).apply(rp, raw=False)
    btc["ema20"] = btc["close"].ewm(span=20).mean()

    # Merge BTC data
    min_len = min(len(d), len(btc))
    d = d.iloc[:min_len].copy()
    d["btc_gk_pctile"] = btc["gk_pctile"].iloc[:min_len].values
    d["btc_close"] = btc["close"].iloc[:min_len].values
    d["btc_ema20"] = btc["ema20"].iloc[:min_len].values

    return d


def run_bt(df, s, e, cfg):
    l_bh = cfg.get("l_bh", BLOCK_H)
    s_bh = cfg.get("s_bh", BLOCK_H)
    l_atr = cfg.get("l_atr", 0)
    s_atr = cfg.get("s_atr", 0)
    l_gkm = cfg.get("l_gkm", 0)
    s_rng = cfg.get("s_rng", 0)
    l_tp_atr = cfg.get("l_tp_atr", None)  # ATR scale factor, None = fixed
    btc_gk_max = cfg.get("btc_gk_max", 999)  # BTC GK filter for L

    atr_med = df["atr14"].median()

    trades = []
    pl = ps = None
    lel = les = -9999
    cl = 0
    ccu = -9999
    dp = 0.0
    cd = cm = None
    mpl = mps = 0.0
    mel = mes = 0

    for i in range(s, e):
        r = df.iloc[i]
        dt = r["datetime"]
        dy = dt.date()
        mo = dt.strftime("%Y-%m")

        if dy != cd: dp = 0.0; cd = dy
        if mo != cm: mpl = mps = 0.0; mel = mes = 0; cm = mo

        # L exit
        if pl:
            bh = i - pl["b"]
            ep = pl["p"]
            rm = pl["rm"]
            mr = pl["mr"]
            emh = L_COND_MH if mr else L_MH
            rm = max(rm, (r["high"] - ep) / ep)
            pl["rm"] = rm
            ex = xp = None

            # TP level
            if l_tp_atr and pl.get("ea"):
                tp_pct = L_TP * (pl["ea"] / atr_med) * l_tp_atr
                tp_pct = max(0.015, min(0.06, tp_pct))
            else:
                tp_pct = L_TP

            sl = ep * (1 - L_SAFENET)
            if r["low"] <= sl: ex, xp = "SL", sl - (sl - r["low"]) * 0.25
            if not ex:
                tp = ep * (1 + tp_pct)
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
                    if (r["close"] - ep) / ep > 0:
                        pl["ext"] = True; pl["xb"] = i
                    else: ex, xp = "MH", r["close"]

            if ex:
                net = (xp - ep) * NOTIONAL / ep - FEE
                trades.append({"s": "L", "p": net, "r": ex, "eb": pl["b"], "xb": i, "m": pl["m"]})
                dp += net; mpl += net; lel = i
                if net < 0: cl += 1; ccu = i + CONSEC_CD if cl >= CONSEC_PAUSE else ccu
                else: cl = 0
                pl = None

        # S exit
        if ps:
            bh = i - ps["b"]
            ep = ps["p"]
            ex = xp = None
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
                trades.append({"s": "S", "p": net, "r": ex, "eb": ps["b"], "xb": i, "m": ps["m"]})
                dp += net; mps += net; les = i
                if net < 0: cl += 1; ccu = i + CONSEC_CD if cl >= CONSEC_PAUSE else ccu
                else: cl = 0
                ps = None

        # Entry
        gl = r.get("gk_pctile", np.nan)
        gs = r.get("gk_pctile_s", np.nan)
        if pd.isna(gl) or pd.isna(gs): continue
        if dp <= DAILY_LOSS or i < ccu: continue
        atr = r.get("atr14", 0)
        if pd.isna(atr): atr = 0

        # L
        if not pl:
            sok = not (r["hour"] in l_bh or r["weekday"] in L_BLOCK_D)
            btc_ok = True
            if btc_gk_max < 999:
                bgk = r.get("btc_gk_pctile", 50)
                if pd.isna(bgk) or bgk > btc_gk_max:
                    btc_ok = False

            if (gl < L_GK_THRESH and gl >= l_gkm and r["brk_long"] and sok
                    and (i - lel) >= L_CD and mel < L_CAP and mpl > L_MONTHLY_LOSS
                    and atr >= l_atr and btc_ok):
                pl = {"b": i, "p": r["close"], "rm": 0.0, "mr": False,
                      "ext": False, "xb": 0, "m": mo, "ea": atr}
                mel += 1

        # S
        if not ps:
            sok = not (r["hour"] in s_bh or r["weekday"] in S_BLOCK_D)
            rng_ok = r["bar_range"] >= s_rng if s_rng > 0 else True
            if (gs < S_GK_THRESH and r["brk_short"] and sok
                    and (i - les) >= S_CD and mes < S_CAP and mps > S_MONTHLY_LOSS
                    and atr >= s_atr and rng_ok):
                ps = {"b": i, "p": r["close"], "ext": False, "xb": 0, "m": mo}
                mes += 1

    return trades


def ev(trades, sp):
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    if tdf.empty:
        return {"is": 0, "oos": 0, "t": 0, "wr": 0, "mdd": 0, "wm": 0, "pm": "0/0",
                "lo": 0, "so": 0, "lt": 0, "st": 0}
    ist = tdf[tdf["eb"] < sp]
    oot = tdf[tdf["eb"] >= sp]
    ip = ist["p"].sum()
    op = oot["p"].sum()
    wr = (oot["p"] > 0).mean() * 100 if len(oot) > 0 else 0
    eq = oot["p"].cumsum() if len(oot) > 0 else pd.Series([0])
    mdd = (eq.cummax() - eq).max()
    lo = oot[oot["s"] == "L"]["p"].sum() if len(oot) > 0 else 0
    so = oot[oot["s"] == "S"]["p"].sum() if len(oot) > 0 else 0
    lt = len(oot[oot["s"] == "L"])
    st = len(oot[oot["s"] == "S"])
    if len(oot) > 0:
        mo = oot.groupby("m")["p"].sum()
        wm = mo.min()
        pm = f"{(mo > 0).sum()}/{len(mo)}"
    else: wm, pm = 0, "0/0"
    return {"is": ip, "oos": op, "t": len(oot), "wr": wr, "mdd": mdd, "wm": wm, "pm": pm,
            "lo": lo, "so": so, "lt": lt, "st": st}


def wf(df, cfg, nf=6):
    tot = len(df) - WARMUP
    fs = tot // nf
    ps = []
    for f in range(nf):
        s = WARMUP + f * fs
        e = s + fs if f < nf - 1 else len(df)
        t = run_bt(df, s, e, cfg)
        ps.append(sum(x["p"] for x in t))
    return sum(1 for p in ps if p > 0), ps


def pr(name, m, bo, w6, w8):
    d = m["oos"] - bo
    ratio = m["oos"] / m["is"] if m["is"] > 0 else 99
    print(f"{name:>42s} | ${m['is']:+6.0f} ${m['oos']:+6.0f} {d:+6.0f} "
          f"L${m['lo']:+5.0f}({m['lt']:2d}) S${m['so']:+5.0f}({m['st']:2d}) "
          f"WR{m['wr']:3.0f}% MDD${m['mdd']:4.0f} WM${m['wm']:+5.0f} {m['pm']:>5s} "
          f"{w6}/6 {w8}/8 r={ratio:.1f}")


def main():
    print("Loading data...")
    df = load_and_prepare()
    sp = len(df) // 2

    hdr = f"{'Config':>42s} | {'IS':>7s} {'OOS':>7s} {'delta':>6s} {'L_OOS':>10s} {'S_OOS':>10s} {'WR':>4s} {'MDD':>7s} {'WM':>7s} {'PM':>5s} {'WF':>8s} {'ratio':>5s}"

    # === BASELINES ===
    print("\n" + "=" * 80)
    print("TOP CANDIDATES")
    print("=" * 80)
    print(hdr)

    cfgs = [
        ("V14 BASELINE", {}),
        ("A: ATR15+GK7", {"l_atr": 15, "s_atr": 15, "l_gkm": 7}),
        ("B: R1_full", {"l_atr": 15, "s_atr": 15, "l_gkm": 7,
                        "l_bh": BLOCK_H | {8, 19}, "s_bh": BLOCK_H | {16}}),
        ("C: R1_full+TP_atr", {"l_atr": 15, "s_atr": 15, "l_gkm": 7,
                                "l_bh": BLOCK_H | {8, 19}, "s_bh": BLOCK_H | {16},
                                "l_tp_atr": 1.0}),
        ("D: A+S_rng>=10", {"l_atr": 15, "s_atr": 15, "l_gkm": 7, "s_rng": 10}),
        ("E: B+S_rng>=10", {"l_atr": 15, "s_atr": 15, "l_gkm": 7,
                            "l_bh": BLOCK_H | {8, 19}, "s_bh": BLOCK_H | {16},
                            "s_rng": 10}),
        ("F: C+S_rng>=10", {"l_atr": 15, "s_atr": 15, "l_gkm": 7,
                            "l_bh": BLOCK_H | {8, 19}, "s_bh": BLOCK_H | {16},
                            "l_tp_atr": 1.0, "s_rng": 10}),
    ]

    bo = None
    for name, cfg in cfgs:
        t = run_bt(df, WARMUP, len(df), cfg)
        m = ev(t, sp)
        w6, _ = wf(df, cfg, 6)
        w8, _ = wf(df, cfg, 8)
        if bo is None: bo = m["oos"]
        pr(name, m, bo, w6, w8)

    # === H9: BTC GK COMPRESSION ===
    print("\n" + "=" * 80)
    print("H9: BTC GK Compression Filter (L only)")
    print("=" * 80)
    print(hdr)

    base_cfg = {"l_atr": 15, "s_atr": 15, "l_gkm": 7}
    for btc_thresh in [15, 20, 25, 30, 35, 40, 50, 60]:
        cfg = {**base_cfg, "btc_gk_max": btc_thresh}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = ev(t, sp)
        w6, _ = wf(df, cfg, 6)
        w8, _ = wf(df, cfg, 8)
        pr(f"BTC_GK<={btc_thresh}", m, bo, w6, w8)

    # === H10: L TP/SL OPTIMIZATION on R2 base ===
    print("\n" + "=" * 80)
    print("H10: L TP & SL Level Scan (with R2 base)")
    print("=" * 80)
    print("Note: These override L_TP/L_SAFENET directly in exit logic")
    # Testing different TP atr scales more granularly
    for scale in [0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]:
        cfg = {**base_cfg, "l_tp_atr": scale}
        t = run_bt(df, WARMUP, len(df), cfg)
        m = ev(t, sp)
        w6, _ = wf(df, cfg, 6)
        w8, _ = wf(df, cfg, 8)
        pr(f"L_TP atr*{scale:.2f}", m, bo, w6, w8)

    # === FINAL CHAMPION: Deep Validation ===
    print("\n" + "=" * 80)
    print("FINAL CHAMPION DEEP VALIDATION")
    print("=" * 80)

    # I'll validate candidate A (simplest with strong results) and B (best overall)
    for champ_name, champ_cfg in [
        ("A: ATR15+GK7", {"l_atr": 15, "s_atr": 15, "l_gkm": 7}),
        ("B: R1_full", {"l_atr": 15, "s_atr": 15, "l_gkm": 7,
                        "l_bh": BLOCK_H | {8, 19}, "s_bh": BLOCK_H | {16}}),
    ]:
        print(f"\n--- {champ_name} ---")

        # 8-fold WF detail
        total = len(df) - WARMUP
        fs = total // 8
        base_cfg0 = {}
        print(f"\n  8-fold WF detail:")
        print(f"  {'Fold':>5s} {'Period':>30s} | {'V14':>7s} {'Champ':>7s} {'delta':>7s}")
        for f in range(8):
            s = WARMUP + f * fs
            e = s + fs if f < 7 else len(df)
            sd = df.iloc[s]["datetime"].strftime("%Y-%m-%d")
            ed = df.iloc[e-1]["datetime"].strftime("%Y-%m-%d")
            bt = run_bt(df, s, e, base_cfg0)
            ct = run_bt(df, s, e, champ_cfg)
            bp = sum(x["p"] for x in bt)
            cp = sum(x["p"] for x in ct)
            mk = " <<<" if cp < bp else ""
            print(f"    {f+1:3d}  {sd} ~ {ed} | ${bp:+6.0f} ${cp:+6.0f} {cp-bp:+7.0f}{mk}")

        # Monthly OOS detail
        ct = run_bt(df, WARMUP, len(df), champ_cfg)
        bt = run_bt(df, WARMUP, len(df), base_cfg0)
        cm_oos = pd.DataFrame([t for t in ct if t["eb"] >= sp])
        bm_oos = pd.DataFrame([t for t in bt if t["eb"] >= sp])

        months = sorted(set(cm_oos["m"].unique()) | set(bm_oos["m"].unique()))
        print(f"\n  Monthly OOS:")
        print(f"  {'Month':>8s} {'V14_L':>7s} {'V14_S':>7s} {'V14':>7s} | {'V15_L':>7s} {'V15_S':>7s} {'V15':>7s} {'delta':>7s}")
        for mo in months:
            bl = bm_oos[(bm_oos["m"] == mo) & (bm_oos["s"] == "L")]["p"].sum()
            bs = bm_oos[(bm_oos["m"] == mo) & (bm_oos["s"] == "S")]["p"].sum()
            cl = cm_oos[(cm_oos["m"] == mo) & (cm_oos["s"] == "L")]["p"].sum()
            cs = cm_oos[(cm_oos["m"] == mo) & (cm_oos["s"] == "S")]["p"].sum()
            d = (cl + cs) - (bl + bs)
            print(f"  {mo:>8s} ${bl:+6.0f} ${bs:+6.0f} ${bl+bs:+6.0f} | ${cl:+6.0f} ${cs:+6.0f} ${cl+cs:+6.0f} {d:+7.0f}")

        # Exit distribution
        print(f"\n  Exit distribution (OOS):")
        for side in ["L", "S"]:
            sub = cm_oos[cm_oos["s"] == side]
            print(f"    {side}: {len(sub)}t, PnL ${sub['p'].sum():+.0f}, WR {(sub['p']>0).mean()*100:.0f}%")
            for reason in ["TP", "MFE", "MH", "MHx", "BE", "SL"]:
                rs = sub[sub["r"] == reason]
                if len(rs) > 0:
                    print(f"      {reason:5s}: {len(rs):3d}t avg ${rs['p'].mean():+.1f}")

    # === PARAMETER SENSITIVITY HEATMAP for A ===
    print("\n" + "=" * 80)
    print("SENSITIVITY ANALYSIS: ATR threshold x GK minimum")
    print("=" * 80)

    print(f"\n{'':>10s}", end="")
    gk_vals = [0, 3, 5, 7, 10, 12]
    for g in gk_vals:
        print(f" GK>={g:2d}", end="")
    print()

    for a in [0, 10, 12, 15, 18, 20]:
        print(f"ATR>={a:2d}  ", end="")
        for g in gk_vals:
            cfg = {"l_atr": a, "s_atr": a, "l_gkm": g}
            t = run_bt(df, WARMUP, len(df), cfg)
            m = ev(t, sp)
            d = m["oos"] - bo
            print(f" {d:+6.0f}", end="")
        print()


if __name__ == "__main__":
    main()
