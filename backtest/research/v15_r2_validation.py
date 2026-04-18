"""
V15 R2: Deep Validation of R1 Best Candidates

Anti-happy-table discipline:
1. 8-fold Walk-Forward (stricter than 6-fold)
2. Parameter robustness: ALL neighbors of best config
3. IS/OOS consistency check per filter
4. Monthly PnL breakdown with positive month ratio
5. Trade-by-trade analysis: what exactly got filtered?
6. Individual filter contribution decomposition
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN = 200
LEVERAGE = 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0

L_GK_SHORT, L_GK_LONG = 5, 20
S_GK_SHORT, S_GK_LONG = 10, 30
GK_WIN = 100
BRK = 15
BLOCK_H = {0, 1, 2, 12}
L_BLOCK_D = {5, 6}
S_BLOCK_D = {0, 5, 6}
L_GK_THRESH = 25
L_SAFENET = 0.035
L_TP = 0.035
L_MH = 6
L_EXT = 2
L_CD = 6
L_CAP = 20
L_MONTHLY_LOSS = -75
L_MFE_ACT = 0.010
L_MFE_TRAIL_DD = 0.008
L_MFE_MIN_BAR = 1
L_COND_BAR = 2
L_COND_THRESH = -0.01
L_COND_MH = 5

S_GK_THRESH = 35
S_SAFENET = 0.04
S_TP = 0.02
S_MH = 10
S_EXT = 2
S_CD = 8
S_CAP = 20
S_MONTHLY_LOSS = -150

DAILY_LOSS = -200
CONSEC_PAUSE = 4
CONSEC_CD = 24
WARMUP = 150


def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    d = eth.copy()
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
    d["gk_ratio"] = gk.rolling(L_GK_SHORT).mean() / gk.rolling(L_GK_LONG).mean()
    d["gk_ratio_s"] = gk.rolling(S_GK_SHORT).mean() / gk.rolling(S_GK_LONG).mean()

    def rank_pctile(s):
        if len(s) <= 1: return 50
        return (s.iloc[:-1] < s.iloc[-1]).sum() / (len(s) - 1) * 100

    d["gk_pctile"] = d["gk_ratio"].shift(1).rolling(GK_WIN).apply(rank_pctile, raw=False)
    d["gk_pctile_s"] = d["gk_ratio_s"].shift(1).rolling(GK_WIN).apply(rank_pctile, raw=False)
    d["brk_max"] = d["close"].shift(1).rolling(BRK).max()
    d["brk_min"] = d["close"].shift(1).rolling(BRK).min()
    d["brk_long"] = d["close"] > d["brk_max"]
    d["brk_short"] = d["close"] < d["brk_min"]
    d["hour"] = d["datetime"].dt.hour
    d["weekday"] = d["datetime"].dt.weekday
    d["atr14"] = (d["high"] - d["low"]).rolling(14).mean()
    return d


def run_backtest(df, start_bar, end_bar, config):
    l_block_h = config.get("l_block_h", BLOCK_H)
    l_block_d = config.get("l_block_d", L_BLOCK_D)
    s_block_h = config.get("s_block_h", BLOCK_H)
    s_block_d = config.get("s_block_d", S_BLOCK_D)
    l_atr_min = config.get("l_atr_min", 0)
    s_atr_min = config.get("s_atr_min", 0)
    l_gk_min = config.get("l_gk_min", 0)
    s_gk_min = config.get("s_gk_min", 0)

    trades = []
    pos_l = pos_s = None
    last_exit_l = last_exit_s = -9999
    consec_losses = 0
    consec_cd_until = -9999
    daily_pnl = 0.0
    current_day = None
    monthly_pnl_l = monthly_pnl_s = 0.0
    monthly_entries_l = monthly_entries_s = 0
    current_month = None

    for i in range(start_bar, end_bar):
        row = df.iloc[i]
        dt = row["datetime"]
        day = dt.date()
        month = dt.strftime("%Y-%m")

        if day != current_day:
            daily_pnl = 0.0
            current_day = day
        if month != current_month:
            monthly_pnl_l = monthly_pnl_s = 0.0
            monthly_entries_l = monthly_entries_s = 0
            current_month = month

        # L exit
        if pos_l is not None:
            bh = i - pos_l["bar"]
            ep = pos_l["price"]
            rmfe = pos_l["rmfe"]
            mhr = pos_l["mhr"]
            emh = L_COND_MH if mhr else L_MH
            bar_mfe = (row["high"] - ep) / ep
            rmfe = max(rmfe, bar_mfe)
            pos_l["rmfe"] = rmfe
            ex = ex_p = None

            sl = ep * (1 - L_SAFENET)
            if row["low"] <= sl:
                ex, ex_p = "SafeNet", sl - (sl - row["low"]) * 0.25
            if ex is None:
                tp = ep * (1 + L_TP)
                if row["high"] >= tp:
                    ex, ex_p = "TP", tp
            if ex is None and bh >= L_MFE_MIN_BAR and rmfe >= L_MFE_ACT:
                if rmfe - (row["close"] - ep) / ep >= L_MFE_TRAIL_DD:
                    ex, ex_p = "MFE-trail", row["close"]
            if ex is None and pos_l.get("ext"):
                eb = i - pos_l["ext_bar"]
                if row["low"] <= ep:
                    ex, ex_p = "BE", ep
                elif eb >= L_EXT:
                    ex, ex_p = "MH-ext", row["close"]
            if ex is None and not pos_l.get("ext"):
                if not mhr and bh == L_COND_BAR:
                    if (row["close"] - ep) / ep <= L_COND_THRESH:
                        pos_l["mhr"] = True
                        mhr = True
                        emh = L_COND_MH
                if bh >= emh:
                    if (row["close"] - ep) / ep > 0:
                        pos_l["ext"] = True
                        pos_l["ext_bar"] = i
                    else:
                        ex, ex_p = "MaxHold", row["close"]

            if ex:
                net = (ex_p - ep) * NOTIONAL / ep - FEE
                trades.append({"side": "L", "pnl": net, "reason": ex,
                               "entry_bar": pos_l["bar"], "exit_bar": i,
                               "month": pos_l["month"],
                               "entry_dt": pos_l["dt"], "exit_dt": dt})
                daily_pnl += net
                monthly_pnl_l += net
                last_exit_l = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE: consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_l = None

        # S exit
        if pos_s is not None:
            bh = i - pos_s["bar"]
            ep = pos_s["price"]
            ex = ex_p = None

            sl = ep * (1 + S_SAFENET)
            if row["high"] >= sl:
                ex, ex_p = "SafeNet", sl + (row["high"] - sl) * 0.25
            if ex is None:
                tp = ep * (1 - S_TP)
                if row["low"] <= tp:
                    ex, ex_p = "TP", tp
            if ex is None and pos_s.get("ext"):
                eb = i - pos_s["ext_bar"]
                if row["high"] >= ep:
                    ex, ex_p = "BE", ep
                elif eb >= S_EXT:
                    ex, ex_p = "MH-ext", row["close"]
            if ex is None and not pos_s.get("ext"):
                if bh >= S_MH:
                    if (ep - row["close"]) / ep > 0:
                        pos_s["ext"] = True
                        pos_s["ext_bar"] = i
                    else:
                        ex, ex_p = "MaxHold", row["close"]

            if ex:
                net = (ep - ex_p) * NOTIONAL / ep - FEE
                trades.append({"side": "S", "pnl": net, "reason": ex,
                               "entry_bar": pos_s["bar"], "exit_bar": i,
                               "month": pos_s["month"],
                               "entry_dt": pos_s["dt"], "exit_dt": dt})
                daily_pnl += net
                monthly_pnl_s += net
                last_exit_s = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE: consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_s = None

        # Entry
        gk_l = row.get("gk_pctile", np.nan)
        gk_s = row.get("gk_pctile_s", np.nan)
        if pd.isna(gk_l) or pd.isna(gk_s): continue
        if daily_pnl <= DAILY_LOSS or i < consec_cd_until: continue

        atr = row.get("atr14", 0)
        if pd.isna(atr): atr = 0

        if pos_l is None:
            sok = not (row["hour"] in l_block_h or row["weekday"] in l_block_d)
            if (gk_l < L_GK_THRESH and gk_l >= l_gk_min and row["brk_long"] and sok
                    and (i - last_exit_l) >= L_CD and monthly_entries_l < L_CAP
                    and monthly_pnl_l > L_MONTHLY_LOSS and atr >= l_atr_min):
                pos_l = {"bar": i, "price": row["close"], "rmfe": 0.0,
                         "mhr": False, "ext": False, "ext_bar": 0,
                         "month": month, "dt": dt}
                monthly_entries_l += 1

        if pos_s is None:
            sok = not (row["hour"] in s_block_h or row["weekday"] in s_block_d)
            if (gk_s < S_GK_THRESH and gk_s >= s_gk_min and row["brk_short"] and sok
                    and (i - last_exit_s) >= S_CD and monthly_entries_s < S_CAP
                    and monthly_pnl_s > S_MONTHLY_LOSS and atr >= s_atr_min):
                pos_s = {"bar": i, "price": row["close"],
                         "ext": False, "ext_bar": 0,
                         "month": month, "dt": dt}
                monthly_entries_s += 1

    return trades


def walk_forward(df, config, n_folds=6):
    total = len(df) - WARMUP
    fold_size = total // n_folds
    pnls = []
    for f in range(n_folds):
        s = WARMUP + f * fold_size
        e = s + fold_size if f < n_folds - 1 else len(df)
        trades = run_backtest(df, s, e, config)
        pnls.append(sum(t["pnl"] for t in trades))
    return sum(1 for p in pnls if p > 0), pnls


def eval_full(trades, split):
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    if tdf.empty:
        return {"is": 0, "oos": 0, "oos_t": 0, "wr": 0, "mdd": 0, "wm": 0, "pm": "0/0"}
    is_t = tdf[tdf["entry_bar"] < split]
    oos_t = tdf[tdf["entry_bar"] >= split]
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()
    oos_wr = (oos_t["pnl"] > 0).mean() * 100 if len(oos_t) > 0 else 0
    eq = oos_t["pnl"].cumsum() if len(oos_t) > 0 else pd.Series([0])
    mdd = (eq.cummax() - eq).max()
    if len(oos_t) > 0:
        mo = oos_t.groupby("month")["pnl"].sum()
        wm = mo.min()
        pm = f"{(mo > 0).sum()}/{len(mo)}"
    else:
        wm, pm = 0, "0/0"
    return {"is": is_pnl, "oos": oos_pnl, "oos_t": len(oos_t), "wr": oos_wr,
            "mdd": mdd, "wm": wm, "pm": pm}


def main():
    print("Loading data...")
    df = load_and_prepare()
    split = len(df) // 2

    # Configs to validate
    BASELINE = {}
    R1_BEST = {
        "l_atr_min": 15, "s_atr_min": 15,
        "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16},
        "l_gk_min": 7,
    }
    # Individual components
    ATR_ONLY = {"l_atr_min": 15, "s_atr_min": 15}
    HOUR_ONLY = {"l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}}
    GK_ONLY = {"l_gk_min": 7}
    ATR_HOUR = {"l_atr_min": 15, "s_atr_min": 15,
                "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}}
    ATR_GK = {"l_atr_min": 15, "s_atr_min": 15, "l_gk_min": 7}
    HOUR_GK = {"l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}

    configs = [
        ("BASELINE", BASELINE),
        ("ATR>=15 only", ATR_ONLY),
        ("Hour only", HOUR_ONLY),
        ("GK>=7 only", GK_ONLY),
        ("ATR+Hour", ATR_HOUR),
        ("ATR+GK", ATR_GK),
        ("Hour+GK", HOUR_GK),
        ("ALL (R1 best)", R1_BEST),
    ]

    # === 1. Component decomposition ===
    print("\n" + "=" * 80)
    print("1. COMPONENT DECOMPOSITION")
    print("=" * 80)
    print(f"{'Config':>20s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s} {'WstMo':>7s} {'PM':>6s} {'WF6':>4s} {'WF8':>4s}")

    baseline_oos = None
    for name, cfg in configs:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        if baseline_oos is None:
            baseline_oos = m["oos"]
        delta = m["oos"] - baseline_oos
        print(f"{name:>20s} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {delta:+7.0f} "
              f"{m['oos_t']:4.0f} {m['wr']:4.0f}% ${m['mdd']:5.0f} ${m['wm']:+6.0f} {m['pm']:>5s} "
              f"{wf6:2d}/6 {wf8:2d}/8")

    # === 2. Parameter robustness of R1 BEST ===
    print("\n" + "=" * 80)
    print("2. PARAMETER ROBUSTNESS (R1 best neighbors)")
    print("=" * 80)

    # Perturb each parameter individually
    perturbations = [
        ("exact",          {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("L_atr=10",       {"l_atr_min": 10, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("L_atr=18",       {"l_atr_min": 18, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("L_atr=20",       {"l_atr_min": 20, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("S_atr=10",       {"l_atr_min": 15, "s_atr_min": 10, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("S_atr=18",       {"l_atr_min": 15, "s_atr_min": 18, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("S_atr=20",       {"l_atr_min": 15, "s_atr_min": 20, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("no L_blk8",      {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("no L_blk19",     {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7}),
        ("no S_blk16",     {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H, "l_gk_min": 7}),
        ("L_gk>=5",        {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 5}),
        ("L_gk>=3",        {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 3}),
        ("L_gk>=10",       {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 10}),
        ("no GK filter",   {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 0}),
        # Also try adding S GK filter
        ("S_gk>=5",        {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}, "l_gk_min": 7, "s_gk_min": 5}),
    ]

    print(f"{'Perturbation':>16s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s} {'WstMo':>7s} {'WF6':>4s} {'WF8':>4s}")
    for name, cfg in perturbations:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        delta = m["oos"] - baseline_oos
        marker = " <<<" if name == "exact" else ""
        print(f"{name:>16s} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {delta:+7.0f} "
              f"{m['oos_t']:4.0f} {m['wr']:4.0f}% ${m['mdd']:5.0f} ${m['wm']:+6.0f} "
              f"{wf6:2d}/6 {wf8:2d}/8{marker}")

    # === 3. Monthly breakdown comparison ===
    print("\n" + "=" * 80)
    print("3. MONTHLY PnL COMPARISON (OOS)")
    print("=" * 80)

    base_trades = run_backtest(df, WARMUP, len(df), BASELINE)
    best_trades = run_backtest(df, WARMUP, len(df), R1_BEST)

    base_oos = pd.DataFrame([t for t in base_trades if t["entry_bar"] >= split])
    best_oos = pd.DataFrame([t for t in best_trades if t["entry_bar"] >= split])

    all_months = sorted(set(base_oos["month"].unique()) | set(best_oos["month"].unique()))

    print(f"{'Month':>8s}  {'Base_L':>8s} {'Best_L':>8s} {'dL':>6s}  {'Base_S':>8s} {'Best_S':>8s} {'dS':>6s}  {'Base':>8s} {'Best':>8s} {'dTot':>6s}")
    for m in all_months:
        bl = base_oos[(base_oos["month"] == m) & (base_oos["side"] == "L")]["pnl"].sum()
        bel = best_oos[(best_oos["month"] == m) & (best_oos["side"] == "L")]["pnl"].sum()
        bs = base_oos[(base_oos["month"] == m) & (base_oos["side"] == "S")]["pnl"].sum()
        bes = best_oos[(best_oos["month"] == m) & (best_oos["side"] == "S")]["pnl"].sum()
        bt = bl + bs
        bet = bel + bes
        print(f"{m:>8s}  {bl:+8.0f} {bel:+8.0f} {bel-bl:+6.0f}  {bs:+8.0f} {bes:+8.0f} {bes-bs:+6.0f}  {bt:+8.0f} {bet:+8.0f} {bet-bt:+6.0f}")

    # === 4. Trade-level diff: what got filtered? ===
    print("\n" + "=" * 80)
    print("4. FILTERED TRADES (in baseline but not in best)")
    print("=" * 80)

    base_set = set((t["entry_bar"], t["side"]) for t in base_trades if t["entry_bar"] >= split)
    best_set = set((t["entry_bar"], t["side"]) for t in best_trades if t["entry_bar"] >= split)

    removed = base_set - best_set
    added = best_set - base_set

    removed_trades = [t for t in base_trades if (t["entry_bar"], t["side"]) in removed]
    removed_trades.sort(key=lambda t: t["entry_bar"])

    total_removed_pnl = sum(t["pnl"] for t in removed_trades)
    print(f"\n  Removed: {len(removed_trades)} trades, total PnL ${total_removed_pnl:+.0f}")
    print(f"  Added:   {len(added)} trades (from changed cooldown/cap interactions)")

    if removed_trades:
        print(f"\n  {'Date':>16s} {'Side':>4s} {'PnL':>8s} {'Reason':>10s}")
        for t in removed_trades:
            print(f"  {str(t.get('entry_dt',''))[:16]:>16s} {t['side']:>4s} ${t['pnl']:+7.1f} {t['reason']:>10s}")

    # What about cascade effects? (trades that changed because removed trades freed up cooldown)
    changed_trades = []
    for t in best_trades:
        key = (t["entry_bar"], t["side"])
        if key not in base_set and t["entry_bar"] >= split:
            changed_trades.append(t)

    if changed_trades:
        total_added_pnl = sum(t["pnl"] for t in changed_trades)
        print(f"\n  Cascade/added: {len(changed_trades)} trades, total PnL ${total_added_pnl:+.0f}")
        for t in changed_trades:
            print(f"  {str(t.get('entry_dt',''))[:16]:>16s} {t['side']:>4s} ${t['pnl']:+7.1f} {t['reason']:>10s}")

    # === 5. Walk-Forward detail ===
    print("\n" + "=" * 80)
    print("5. WALK-FORWARD DETAIL (8-fold)")
    print("=" * 80)

    total_bars = len(df) - WARMUP
    fold_size = total_bars // 8

    print(f"{'Fold':>5s} {'Period':>30s} | {'Base':>8s} {'Best':>8s} {'delta':>7s}")
    for f in range(8):
        s = WARMUP + f * fold_size
        e = s + fold_size if f < 7 else len(df)
        start_dt = df.iloc[s]["datetime"].strftime("%Y-%m-%d")
        end_dt = df.iloc[e-1]["datetime"].strftime("%Y-%m-%d")

        bt = run_backtest(df, s, e, BASELINE)
        bst = run_backtest(df, s, e, R1_BEST)
        bp = sum(t["pnl"] for t in bt)
        bsp = sum(t["pnl"] for t in bst)
        marker = " <<<" if bsp < bp else ""
        print(f"  {f+1:3d}  {start_dt} ~ {end_dt} | ${bp:+7.0f} ${bsp:+7.0f} {bsp-bp:+7.0f}{marker}")

    # === 6. Reduced filter sets (simpler might be more robust) ===
    print("\n" + "=" * 80)
    print("6. SIMPLER ALTERNATIVES (fewer parameters)")
    print("=" * 80)

    simpler = [
        ("BASELINE",          {}),
        ("ATR>=15 only",      {"l_atr_min": 15, "s_atr_min": 15}),
        ("ATR+GK (no hour)",  {"l_atr_min": 15, "s_atr_min": 15, "l_gk_min": 7}),
        ("ATR+Hour (no GK)",  {"l_atr_min": 15, "s_atr_min": 15, "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16}}),
        ("ALL (4 params)",    R1_BEST),
        # Even simpler: just L filters
        ("L_atr>=15 only",    {"l_atr_min": 15}),
        ("L_gk>=7 only",      {"l_gk_min": 7}),
        ("L_atr+L_gk",        {"l_atr_min": 15, "l_gk_min": 7}),
    ]

    print(f"{'Config':>22s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s} {'WstMo':>7s} {'WF6':>4s} {'WF8':>4s}")
    for name, cfg in simpler:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        delta = m["oos"] - baseline_oos
        print(f"{name:>22s} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {delta:+7.0f} "
              f"{m['oos_t']:4.0f} {m['wr']:4.0f}% ${m['mdd']:5.0f} ${m['wm']:+6.0f} "
              f"{wf6:2d}/6 {wf8:2d}/8")

    # === 7. IS/OOS Ratio check ===
    print("\n" + "=" * 80)
    print("7. OVERFITTING CHECK (IS/OOS ratio)")
    print("=" * 80)

    for name, cfg in configs:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        ratio = m["oos"] / m["is"] if m["is"] > 0 else float("inf")
        print(f"  {name:>20s}: IS ${m['is']:+7.0f}  OOS ${m['oos']:+7.0f}  ratio {ratio:.2f}")
    print("\n  Rule of thumb: OOS/IS > 3 is suspicious, < 0.5 is concerning")


if __name__ == "__main__":
    main()
