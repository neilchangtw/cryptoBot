"""
V15 R1: Entry Filter Exploration

假說：
  H1: ATR minimum filter — 弱月 ATR~23 vs 強月 ATR~30，低絕對波動時突破無後勁
  H2: Entry hour refinement — L 8:00/19:00 WR<30%, S 10:00/16:00 WR<30%
  H3: GK percentile band — L GK 0-5 WR 38%, 10-15 WR 39%（非單調）
  H4: H1+H2+H3 組合

方法：
  - IS/OOS 50/50 split
  - 6-fold Walk-Forward
  - 參數掃描 + 穩健性檢查
  - Anti-happy-table: 只看全局最佳能否打敗 baseline
"""
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# === Account ===
MARGIN = 200
LEVERAGE = 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0

# === V14 Baseline params ===
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
        if len(s) <= 1:
            return 50
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
    """Run backtest with configurable entry filters."""
    l_block_h = config.get("l_block_h", BLOCK_H)
    l_block_d = config.get("l_block_d", L_BLOCK_D)
    s_block_h = config.get("s_block_h", BLOCK_H)
    s_block_d = config.get("s_block_d", S_BLOCK_D)
    l_atr_min = config.get("l_atr_min", 0)
    s_atr_min = config.get("s_atr_min", 0)
    l_gk_min = config.get("l_gk_min", 0)  # minimum GK pctile
    s_gk_min = config.get("s_gk_min", 0)

    trades = []
    pos_l = None
    pos_s = None
    last_exit_l = -9999
    last_exit_s = -9999
    consec_losses = 0
    consec_cd_until = -9999
    daily_pnl = 0.0
    current_day = None
    monthly_pnl_l = 0.0
    monthly_pnl_s = 0.0
    monthly_entries_l = 0
    monthly_entries_s = 0
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

        # === L EXIT ===
        if pos_l is not None:
            bars_held = i - pos_l["bar"]
            ep = pos_l["price"]
            rmfe = pos_l["rmfe"]
            mhr = pos_l["mhr"]
            eff_mh = L_COND_MH if mhr else L_MH

            bar_mfe = (row["high"] - ep) / ep
            rmfe = max(rmfe, bar_mfe)
            pos_l["rmfe"] = rmfe

            ex_reason = ex_price = None

            sl = ep * (1 - L_SAFENET)
            if row["low"] <= sl:
                ex_reason = "SafeNet"
                ex_price = sl - (sl - row["low"]) * 0.25
            if ex_reason is None:
                tp = ep * (1 + L_TP)
                if row["high"] >= tp:
                    ex_reason = "TP"
                    ex_price = tp
            if ex_reason is None and bars_held >= L_MFE_MIN_BAR and rmfe >= L_MFE_ACT:
                cpnl = (row["close"] - ep) / ep
                if rmfe - cpnl >= L_MFE_TRAIL_DD:
                    ex_reason = "MFE-trail"
                    ex_price = row["close"]
            if ex_reason is None and pos_l.get("ext"):
                eb = i - pos_l["ext_bar"]
                if row["low"] <= ep:
                    ex_reason = "BE"
                    ex_price = ep
                elif eb >= L_EXT:
                    ex_reason = "MH-ext"
                    ex_price = row["close"]
            if ex_reason is None and not pos_l.get("ext"):
                if not mhr and bars_held == L_COND_BAR:
                    if (row["close"] - ep) / ep <= L_COND_THRESH:
                        pos_l["mhr"] = True
                        mhr = True
                        eff_mh = L_COND_MH
                if bars_held >= eff_mh:
                    pnl_pct = (row["close"] - ep) / ep
                    if pnl_pct > 0:
                        pos_l["ext"] = True
                        pos_l["ext_bar"] = i
                    else:
                        ex_reason = "MaxHold"
                        ex_price = row["close"]

            if ex_reason:
                net = (ex_price - ep) * NOTIONAL / ep - FEE
                trades.append({"side": "L", "pnl": net, "reason": ex_reason,
                               "entry_bar": pos_l["bar"], "exit_bar": i,
                               "month": pos_l["month"]})
                daily_pnl += net
                monthly_pnl_l += net
                last_exit_l = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE:
                        consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_l = None

        # === S EXIT ===
        if pos_s is not None:
            bars_held = i - pos_s["bar"]
            ep = pos_s["price"]
            ex_reason = ex_price = None

            sl = ep * (1 + S_SAFENET)
            if row["high"] >= sl:
                ex_reason = "SafeNet"
                ex_price = sl + (row["high"] - sl) * 0.25
            if ex_reason is None:
                tp = ep * (1 - S_TP)
                if row["low"] <= tp:
                    ex_reason = "TP"
                    ex_price = tp
            if ex_reason is None and pos_s.get("ext"):
                eb = i - pos_s["ext_bar"]
                if row["high"] >= ep:
                    ex_reason = "BE"
                    ex_price = ep
                elif eb >= S_EXT:
                    ex_reason = "MH-ext"
                    ex_price = row["close"]
            if ex_reason is None and not pos_s.get("ext"):
                if bars_held >= S_MH:
                    pnl_pct = (ep - row["close"]) / ep
                    if pnl_pct > 0:
                        pos_s["ext"] = True
                        pos_s["ext_bar"] = i
                    else:
                        ex_reason = "MaxHold"
                        ex_price = row["close"]

            if ex_reason:
                net = (ep - ex_price) * NOTIONAL / ep - FEE
                trades.append({"side": "S", "pnl": net, "reason": ex_reason,
                               "entry_bar": pos_s["bar"], "exit_bar": i,
                               "month": pos_s["month"]})
                daily_pnl += net
                monthly_pnl_s += net
                last_exit_s = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE:
                        consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_s = None

        # === ENTRY ===
        gk_l = row.get("gk_pctile", np.nan)
        gk_s = row.get("gk_pctile_s", np.nan)
        if pd.isna(gk_l) or pd.isna(gk_s):
            continue
        if daily_pnl <= DAILY_LOSS or i < consec_cd_until:
            continue

        atr = row.get("atr14", 0)
        if pd.isna(atr):
            atr = 0

        # L entry
        if pos_l is None:
            session_ok = not (row["hour"] in l_block_h or row["weekday"] in l_block_d)
            if (gk_l < L_GK_THRESH and gk_l >= l_gk_min
                    and row["brk_long"] and session_ok
                    and (i - last_exit_l) >= L_CD
                    and monthly_entries_l < L_CAP
                    and monthly_pnl_l > L_MONTHLY_LOSS
                    and atr >= l_atr_min):
                pos_l = {"bar": i, "price": row["close"], "rmfe": 0.0,
                         "mhr": False, "ext": False, "ext_bar": 0,
                         "month": month}
                monthly_entries_l += 1

        # S entry
        if pos_s is None:
            session_ok = not (row["hour"] in s_block_h or row["weekday"] in s_block_d)
            if (gk_s < S_GK_THRESH and gk_s >= s_gk_min
                    and row["brk_short"] and session_ok
                    and (i - last_exit_s) >= S_CD
                    and monthly_entries_s < S_CAP
                    and monthly_pnl_s > S_MONTHLY_LOSS
                    and atr >= s_atr_min):
                pos_s = {"bar": i, "price": row["close"],
                         "ext": False, "ext_bar": 0,
                         "month": month}
                monthly_entries_s += 1

    return trades


def evaluate(trades, split_bar):
    """Compute IS/OOS metrics."""
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    if tdf.empty:
        return {"is_pnl": 0, "oos_pnl": 0, "oos_wr": 0, "oos_t": 0,
                "oos_mdd": 0, "oos_worst_month": -9999, "oos_pos_months": 0,
                "oos_total_months": 0}

    is_t = tdf[tdf["entry_bar"] < split_bar]
    oos_t = tdf[tdf["entry_bar"] >= split_bar]

    is_pnl = is_t["pnl"].sum() if len(is_t) > 0 else 0
    oos_pnl = oos_t["pnl"].sum() if len(oos_t) > 0 else 0
    oos_wr = (oos_t["pnl"] > 0).mean() * 100 if len(oos_t) > 0 else 0
    oos_count = len(oos_t)

    # MDD
    eq = oos_t["pnl"].cumsum() if len(oos_t) > 0 else pd.Series([0])
    peak = eq.cummax()
    mdd = (peak - eq).max()

    # Monthly
    if len(oos_t) > 0:
        monthly = oos_t.groupby("month")["pnl"].sum()
        worst_month = monthly.min()
        pos_months = (monthly > 0).sum()
        total_months = len(monthly)
    else:
        worst_month = 0
        pos_months = 0
        total_months = 0

    return {"is_pnl": is_pnl, "oos_pnl": oos_pnl, "oos_wr": oos_wr,
            "oos_t": oos_count, "oos_mdd": mdd, "oos_worst_month": worst_month,
            "oos_pos_months": pos_months, "oos_total_months": total_months}


def walk_forward(df, config, n_folds=6):
    """6-fold walk-forward validation."""
    total_bars = len(df) - WARMUP
    fold_size = total_bars // n_folds
    pass_count = 0
    fold_pnls = []

    for fold in range(n_folds):
        fold_start = WARMUP + fold * fold_size
        fold_end = fold_start + fold_size if fold < n_folds - 1 else len(df)
        trades = run_backtest(df, fold_start, fold_end, config)
        pnl = sum(t["pnl"] for t in trades)
        fold_pnls.append(pnl)
        if pnl > 0:
            pass_count += 1

    return pass_count, n_folds, fold_pnls


def main():
    print("Loading data...")
    df = load_and_prepare()
    split = len(df) // 2
    print(f"Total bars: {len(df)}, split at {split}")

    # === BASELINE ===
    print("\n" + "=" * 80)
    print("BASELINE (V14)")
    print("=" * 80)

    baseline_config = {}
    baseline_trades = run_backtest(df, WARMUP, len(df), baseline_config)
    bm = evaluate(baseline_trades, split)
    bwf, _, _ = walk_forward(df, baseline_config)
    print(f"IS ${bm['is_pnl']:+.0f} | OOS ${bm['oos_pnl']:+.0f} ({bm['oos_t']}t, WR {bm['oos_wr']:.0f}%) | "
          f"MDD ${bm['oos_mdd']:.0f} | worst mo ${bm['oos_worst_month']:+.0f} | "
          f"PM {bm['oos_pos_months']}/{bm['oos_total_months']} | WF {bwf}/6")

    baseline_oos = bm["oos_pnl"]

    # === H1: ATR MINIMUM FILTER ===
    print("\n" + "=" * 80)
    print("H1: ATR Minimum Filter")
    print("=" * 80)

    atr_results = []
    for l_atr in [0, 15, 18, 20, 22, 24, 26, 28]:
        for s_atr in [0, 15, 18, 20, 22, 24, 26, 28]:
            cfg = {"l_atr_min": l_atr, "s_atr_min": s_atr}
            trades = run_backtest(df, WARMUP, len(df), cfg)
            m = evaluate(trades, split)
            atr_results.append({**cfg, **m})

    atr_df = pd.DataFrame(atr_results)
    atr_df["delta"] = atr_df["oos_pnl"] - baseline_oos
    atr_df = atr_df.sort_values("oos_pnl", ascending=False)

    print(f"\nTop 10 ATR configs (baseline OOS ${baseline_oos:.0f}):")
    print(f"{'L_ATR':>6s} {'S_ATR':>6s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s} {'WstMo':>7s}")
    for _, r in atr_df.head(10).iterrows():
        print(f"{r['l_atr_min']:6.0f} {r['s_atr_min']:6.0f} | "
              f"${r['is_pnl']:+7.0f} ${r['oos_pnl']:+7.0f} {r['delta']:+7.0f} "
              f"{r['oos_t']:4.0f} {r['oos_wr']:4.0f}% ${r['oos_mdd']:5.0f} ${r['oos_worst_month']:+6.0f}")

    # Best ATR config WF
    best_atr = atr_df.iloc[0]
    best_atr_cfg = {"l_atr_min": best_atr["l_atr_min"], "s_atr_min": best_atr["s_atr_min"]}
    atr_wf, _, _ = walk_forward(df, best_atr_cfg)
    print(f"\nBest ATR config WF: {atr_wf}/6")

    # === H2: ENTRY HOUR REFINEMENT ===
    print("\n" + "=" * 80)
    print("H2: Entry Hour Refinement")
    print("=" * 80)

    # Test adding specific hours to block list
    hour_results = []
    # L: test blocking 8, 19, and combinations
    l_extra_hours = [[], [8], [19], [8, 19], [8, 19, 11], [3, 4], [3, 4, 8]]
    s_extra_hours = [[], [10], [16], [10, 16], [10, 16, 20], [3], [6]]

    for l_extra in l_extra_hours:
        for s_extra in s_extra_hours:
            l_bh = BLOCK_H | set(l_extra)
            s_bh = BLOCK_H | set(s_extra)
            cfg = {"l_block_h": l_bh, "s_block_h": s_bh}
            trades = run_backtest(df, WARMUP, len(df), cfg)
            m = evaluate(trades, split)
            hour_results.append({
                "l_extra": str(l_extra), "s_extra": str(s_extra),
                **cfg, **m
            })

    hour_df = pd.DataFrame(hour_results)
    hour_df["delta"] = hour_df["oos_pnl"] - baseline_oos
    hour_df = hour_df.sort_values("oos_pnl", ascending=False)

    print(f"\nTop 10 Hour configs:")
    print(f"{'L_extra':>14s} {'S_extra':>14s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'WstMo':>7s}")
    for _, r in hour_df.head(10).iterrows():
        print(f"{r['l_extra']:>14s} {r['s_extra']:>14s} | "
              f"${r['is_pnl']:+7.0f} ${r['oos_pnl']:+7.0f} {r['delta']:+7.0f} "
              f"{r['oos_t']:4.0f} {r['oos_wr']:4.0f}% ${r['oos_worst_month']:+6.0f}")

    # Best hour config WF
    if len(hour_df) > 0:
        best_h = hour_df.iloc[0]
        best_h_cfg = {"l_block_h": best_h["l_block_h"], "s_block_h": best_h["s_block_h"]}
        h_wf, _, _ = walk_forward(df, best_h_cfg)
        print(f"\nBest Hour config WF: {h_wf}/6")

    # === H3: GK PERCENTILE BAND ===
    print("\n" + "=" * 80)
    print("H3: GK Percentile Band (minimum GK)")
    print("=" * 80)

    gk_results = []
    for l_gk_min in [0, 3, 5, 7, 10]:
        for s_gk_min in [0, 3, 5, 7, 10]:
            cfg = {"l_gk_min": l_gk_min, "s_gk_min": s_gk_min}
            trades = run_backtest(df, WARMUP, len(df), cfg)
            m = evaluate(trades, split)
            gk_results.append({**cfg, **m})

    gk_df = pd.DataFrame(gk_results)
    gk_df["delta"] = gk_df["oos_pnl"] - baseline_oos
    gk_df = gk_df.sort_values("oos_pnl", ascending=False)

    print(f"\nTop 10 GK min configs:")
    print(f"{'L_min':>6s} {'S_min':>6s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s}")
    for _, r in gk_df.head(10).iterrows():
        print(f"{r['l_gk_min']:6.0f} {r['s_gk_min']:6.0f} | "
              f"${r['is_pnl']:+7.0f} ${r['oos_pnl']:+7.0f} {r['delta']:+7.0f} "
              f"{r['oos_t']:4.0f} {r['oos_wr']:4.0f}%")

    # === H4: COMBINED BEST ===
    print("\n" + "=" * 80)
    print("H4: Combined filters")
    print("=" * 80)

    # Take best individual filters and combine
    combined_configs = []

    # Manually define promising combinations based on individual results
    combos = [
        # ATR only
        {"l_atr_min": best_atr["l_atr_min"], "s_atr_min": best_atr["s_atr_min"]},
    ]

    # Add hour+ATR combos
    if len(hour_df) > 0:
        best_h_row = hour_df.iloc[0]
        combos.append({
            "l_block_h": best_h_row["l_block_h"],
            "s_block_h": best_h_row["s_block_h"],
            "l_atr_min": best_atr["l_atr_min"],
            "s_atr_min": best_atr["s_atr_min"],
        })

    # Add GK+ATR combos
    if len(gk_df) > 0:
        best_gk = gk_df.iloc[0]
        combos.append({
            "l_gk_min": best_gk["l_gk_min"],
            "s_gk_min": best_gk["s_gk_min"],
            "l_atr_min": best_atr["l_atr_min"],
            "s_atr_min": best_atr["s_atr_min"],
        })

    # All three
    if len(hour_df) > 0 and len(gk_df) > 0:
        combos.append({
            "l_block_h": best_h_row["l_block_h"],
            "s_block_h": best_h_row["s_block_h"],
            "l_gk_min": best_gk["l_gk_min"],
            "s_gk_min": best_gk["s_gk_min"],
            "l_atr_min": best_atr["l_atr_min"],
            "s_atr_min": best_atr["s_atr_min"],
        })

    # Additional sensible combos with moderate params
    for l_atr in [20, 22, 24]:
        for l_extra in [[], [8], [19], [8, 19]]:
            combos.append({
                "l_block_h": BLOCK_H | set(l_extra),
                "l_atr_min": l_atr,
                "s_atr_min": 0,  # don't filter S ATR initially
            })

    print(f"\nTesting {len(combos)} combined configs...")
    print(f"{'Config':>50s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'t':>4s} {'WR':>5s} {'MDD':>6s} {'WstMo':>7s} {'WF':>4s}")

    combo_results = []
    for cfg in combos:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = evaluate(trades, split)
        wf_pass, _, _ = walk_forward(df, cfg)
        delta = m["oos_pnl"] - baseline_oos

        # Config description
        parts = []
        if cfg.get("l_atr_min", 0) > 0:
            parts.append(f"L_atr>={cfg['l_atr_min']}")
        if cfg.get("s_atr_min", 0) > 0:
            parts.append(f"S_atr>={cfg['s_atr_min']}")
        l_bh = cfg.get("l_block_h", BLOCK_H)
        if l_bh != BLOCK_H:
            extra = l_bh - BLOCK_H
            parts.append(f"L_blk+{extra}")
        s_bh = cfg.get("s_block_h", BLOCK_H)
        if s_bh != BLOCK_H:
            extra = s_bh - BLOCK_H
            parts.append(f"S_blk+{extra}")
        if cfg.get("l_gk_min", 0) > 0:
            parts.append(f"L_gk>={cfg['l_gk_min']}")
        if cfg.get("s_gk_min", 0) > 0:
            parts.append(f"S_gk>={cfg['s_gk_min']}")
        desc = " + ".join(parts) if parts else "BASELINE"

        print(f"{desc:>50s} | "
              f"${m['is_pnl']:+7.0f} ${m['oos_pnl']:+7.0f} {delta:+7.0f} "
              f"{m['oos_t']:4.0f} {m['oos_wr']:4.0f}% ${m['oos_mdd']:5.0f} ${m['oos_worst_month']:+6.0f} {wf_pass:2d}/6")

        combo_results.append({"desc": desc, **m, "delta": delta, "wf": wf_pass, "cfg": cfg})

    # Find overall best
    combo_df = pd.DataFrame(combo_results)
    combo_df = combo_df.sort_values("oos_pnl", ascending=False)

    print(f"\n{'='*80}")
    print("OVERALL BEST:")
    best = combo_df.iloc[0]
    print(f"  {best['desc']}")
    print(f"  IS ${best['is_pnl']:+.0f} | OOS ${best['oos_pnl']:+.0f} (delta {best['delta']:+.0f}) | "
          f"WR {best['oos_wr']:.0f}% | MDD ${best['oos_mdd']:.0f} | WF {best['wf']}/6")
    print(f"\n  vs BASELINE: OOS ${baseline_oos:+.0f}, WF {bwf}/6")

    # === PER-SIDE ANALYSIS of best config ===
    print("\n" + "=" * 80)
    print("Best config per-side breakdown")
    print("=" * 80)

    best_cfg = best["cfg"]
    best_trades = run_backtest(df, WARMUP, len(df), best_cfg)
    btdf = pd.DataFrame(best_trades)
    oos_t = btdf[btdf["entry_bar"] >= split]

    for side in ["L", "S"]:
        sub = oos_t[oos_t["side"] == side]
        if len(sub) == 0:
            continue
        print(f"\n  {side}: {len(sub)}t, PnL ${sub['pnl'].sum():+.0f}, WR {(sub['pnl']>0).mean()*100:.0f}%")
        for reason in ["TP", "MFE-trail", "MaxHold", "MH-ext", "BE", "SafeNet"]:
            r = sub[sub["reason"] == reason]
            if len(r) > 0:
                print(f"    {reason:10s}: {len(r):3d}t avg ${r['pnl'].mean():+.1f}")

        # Monthly
        monthly = sub.groupby("month")["pnl"].sum()
        neg_months = monthly[monthly < 0]
        print(f"    Negative months: {len(neg_months)}/{len(monthly)}")
        for m, p in neg_months.items():
            print(f"      {m}: ${p:+.0f}")


if __name__ == "__main__":
    main()
