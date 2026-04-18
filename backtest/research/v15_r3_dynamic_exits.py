"""
V15 R3: Dynamic Exit Parameters + Entry Momentum Filter

Building on R1-R2 findings (ATR+GK filter as base).

假說：
  H5: Dynamic TP based on ATR — 高波動時加大 TP，低波動時縮小 TP
  H6: Dynamic MH based on entry GK — 低 GK（強壓縮）期待更大動能，用更長 MH
  H7: Entry momentum filter — 突破時 ETH 近期動量方向一致才進場
  H8: Bar range confirmation — 突破 bar 本身需有足夠 range

方法：在 R2 validated base (ATR>=15 + GK>=7) 上疊加
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
    d["bar_range"] = d["high"] - d["low"]

    # Momentum indicators
    d["ret_3h"] = d["close"].pct_change(3) * 100  # 3-bar return %
    d["ret_6h"] = d["close"].pct_change(6) * 100
    d["ret_12h"] = d["close"].pct_change(12) * 100

    # Bar body ratio
    d["body_ratio"] = abs(d["close"] - d["open"]) / (d["high"] - d["low"] + 1e-10)

    # ATR percentile (for dynamic exits)
    d["atr_pctile"] = d["atr14"].shift(1).rolling(100).apply(
        lambda s: (s.iloc[:-1] < s.iloc[-1]).sum() / (len(s) - 1) * 100 if len(s) > 1 else 50,
        raw=False
    )

    return d


def run_backtest(df, start_bar, end_bar, config):
    l_block_h = config.get("l_block_h", BLOCK_H)
    s_block_h = config.get("s_block_h", BLOCK_H)
    l_atr_min = config.get("l_atr_min", 0)
    s_atr_min = config.get("s_atr_min", 0)
    l_gk_min = config.get("l_gk_min", 0)
    s_gk_min = config.get("s_gk_min", 0)

    # Dynamic exit params
    l_tp_mode = config.get("l_tp_mode", "fixed")  # fixed, atr_scaled
    l_tp_base = config.get("l_tp_base", L_TP)
    l_tp_atr_scale = config.get("l_tp_atr_scale", 1.0)  # TP = base * (atr/median_atr * scale)
    s_tp_mode = config.get("s_tp_mode", "fixed")
    s_tp_base = config.get("s_tp_base", S_TP)

    # Entry momentum filter
    l_mom_filter = config.get("l_mom_filter", None)  # (period, direction) e.g., (3, "positive")
    s_mom_filter = config.get("s_mom_filter", None)

    # Bar range filter
    l_bar_range_min = config.get("l_bar_range_min", 0)  # minimum bar range in $ at entry
    s_bar_range_min = config.get("s_bar_range_min", 0)

    # Dynamic MH
    l_mh_mode = config.get("l_mh_mode", "fixed")  # fixed, gk_adaptive
    s_mh_mode = config.get("s_mh_mode", "fixed")

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

    # Precompute median ATR for scaling
    atr_median = df["atr14"].median()

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
            bh = i - pos_l["bar"]
            ep = pos_l["price"]
            rmfe = pos_l["rmfe"]
            mhr = pos_l["mhr"]

            # Dynamic MH
            if l_mh_mode == "gk_adaptive":
                base_mh = L_COND_MH if mhr else L_MH
                # Lower GK at entry → longer MH
                entry_gk = pos_l.get("entry_gk", 12)
                if entry_gk < 10:
                    emh = base_mh + 1  # extra bar for strong compression
                else:
                    emh = base_mh
            else:
                emh = L_COND_MH if mhr else L_MH

            bar_mfe = (row["high"] - ep) / ep
            rmfe = max(rmfe, bar_mfe)
            pos_l["rmfe"] = rmfe
            ex = ex_p = None

            # Dynamic TP
            if l_tp_mode == "atr_scaled":
                entry_atr = pos_l.get("entry_atr", atr_median)
                tp_pct = l_tp_base * (entry_atr / atr_median) * l_tp_atr_scale
                tp_pct = max(0.015, min(0.06, tp_pct))  # clamp 1.5% ~ 6%
            else:
                tp_pct = l_tp_base

            sl = ep * (1 - L_SAFENET)
            if row["low"] <= sl:
                ex, ex_p = "SafeNet", sl - (sl - row["low"]) * 0.25
            if ex is None:
                tp = ep * (1 + tp_pct)
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
                        emh = L_COND_MH if l_mh_mode != "gk_adaptive" else emh
                if bh >= emh:
                    if (row["close"] - ep) / ep > 0:
                        pos_l["ext"] = True
                        pos_l["ext_bar"] = i
                    else:
                        ex, ex_p = "MaxHold", row["close"]

            if ex:
                net = (ex_p - ep) * NOTIONAL / ep - FEE
                trades.append({"side": "L", "pnl": net, "reason": ex,
                               "entry_bar": pos_l["bar"], "exit_bar": i, "month": pos_l["month"]})
                daily_pnl += net
                monthly_pnl_l += net
                last_exit_l = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE: consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_l = None

        # === S EXIT ===
        if pos_s is not None:
            bh = i - pos_s["bar"]
            ep = pos_s["price"]
            ex = ex_p = None

            if s_tp_mode == "atr_scaled":
                entry_atr = pos_s.get("entry_atr", atr_median)
                tp_pct = s_tp_base * (entry_atr / atr_median)
                tp_pct = max(0.01, min(0.04, tp_pct))
            else:
                tp_pct = s_tp_base

            # Dynamic S MH
            if s_mh_mode == "gk_adaptive":
                entry_gk = pos_s.get("entry_gk", 17)
                if entry_gk < 15:
                    s_emh = S_MH + 1
                else:
                    s_emh = S_MH
            else:
                s_emh = S_MH

            sl = ep * (1 + S_SAFENET)
            if row["high"] >= sl:
                ex, ex_p = "SafeNet", sl + (row["high"] - sl) * 0.25
            if ex is None:
                tp = ep * (1 - tp_pct)
                if row["low"] <= tp:
                    ex, ex_p = "TP", tp
            if ex is None and pos_s.get("ext"):
                eb = i - pos_s["ext_bar"]
                if row["high"] >= ep:
                    ex, ex_p = "BE", ep
                elif eb >= S_EXT:
                    ex, ex_p = "MH-ext", row["close"]
            if ex is None and not pos_s.get("ext"):
                if bh >= s_emh:
                    if (ep - row["close"]) / ep > 0:
                        pos_s["ext"] = True
                        pos_s["ext_bar"] = i
                    else:
                        ex, ex_p = "MaxHold", row["close"]

            if ex:
                net = (ep - ex_p) * NOTIONAL / ep - FEE
                trades.append({"side": "S", "pnl": net, "reason": ex,
                               "entry_bar": pos_s["bar"], "exit_bar": i, "month": pos_s["month"]})
                daily_pnl += net
                monthly_pnl_s += net
                last_exit_s = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE: consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_s = None

        # === ENTRY ===
        gk_l = row.get("gk_pctile", np.nan)
        gk_s = row.get("gk_pctile_s", np.nan)
        if pd.isna(gk_l) or pd.isna(gk_s): continue
        if daily_pnl <= DAILY_LOSS or i < consec_cd_until: continue

        atr = row.get("atr14", 0)
        if pd.isna(atr): atr = 0

        # L entry
        if pos_l is None:
            sok = not (row["hour"] in l_block_h or row["weekday"] in L_BLOCK_D)
            mom_ok = True
            if l_mom_filter:
                period, direction = l_mom_filter
                ret_col = f"ret_{period}h"
                if ret_col in df.columns:
                    ret_val = row.get(ret_col, 0)
                    if direction == "positive" and ret_val <= 0:
                        mom_ok = False
                    elif direction == "non_negative" and ret_val < -0.5:
                        mom_ok = False

            bar_ok = row["bar_range"] >= l_bar_range_min if l_bar_range_min > 0 else True

            if (gk_l < L_GK_THRESH and gk_l >= l_gk_min and row["brk_long"] and sok
                    and (i - last_exit_l) >= L_CD and monthly_entries_l < L_CAP
                    and monthly_pnl_l > L_MONTHLY_LOSS and atr >= l_atr_min
                    and mom_ok and bar_ok):
                pos_l = {"bar": i, "price": row["close"], "rmfe": 0.0,
                         "mhr": False, "ext": False, "ext_bar": 0,
                         "month": month, "entry_atr": atr, "entry_gk": gk_l}
                monthly_entries_l += 1

        # S entry
        if pos_s is None:
            sok = not (row["hour"] in s_block_h or row["weekday"] in S_BLOCK_D)
            mom_ok = True
            if s_mom_filter:
                period, direction = s_mom_filter
                ret_col = f"ret_{period}h"
                if ret_col in df.columns:
                    ret_val = row.get(ret_col, 0)
                    if direction == "negative" and ret_val >= 0:
                        mom_ok = False
                    elif direction == "non_positive" and ret_val > 0.5:
                        mom_ok = False

            bar_ok = row["bar_range"] >= s_bar_range_min if s_bar_range_min > 0 else True

            if (gk_s < S_GK_THRESH and gk_s >= s_gk_min and row["brk_short"] and sok
                    and (i - last_exit_s) >= S_CD and monthly_entries_s < S_CAP
                    and monthly_pnl_s > S_MONTHLY_LOSS and atr >= s_atr_min
                    and mom_ok and bar_ok):
                pos_s = {"bar": i, "price": row["close"],
                         "ext": False, "ext_bar": 0,
                         "month": month, "entry_atr": atr, "entry_gk": gk_s}
                monthly_entries_s += 1

    return trades


def eval_full(trades, split):
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    if tdf.empty:
        return {"is": 0, "oos": 0, "oos_t": 0, "wr": 0, "mdd": 0, "wm": 0, "pm": "0/0",
                "l_oos": 0, "s_oos": 0, "l_t": 0, "s_t": 0}
    is_t = tdf[tdf["entry_bar"] < split]
    oos_t = tdf[tdf["entry_bar"] >= split]
    is_pnl = is_t["pnl"].sum()
    oos_pnl = oos_t["pnl"].sum()
    oos_wr = (oos_t["pnl"] > 0).mean() * 100 if len(oos_t) > 0 else 0
    eq = oos_t["pnl"].cumsum() if len(oos_t) > 0 else pd.Series([0])
    mdd = (eq.cummax() - eq).max()
    l_oos = oos_t[oos_t["side"] == "L"]["pnl"].sum() if len(oos_t) > 0 else 0
    s_oos = oos_t[oos_t["side"] == "S"]["pnl"].sum() if len(oos_t) > 0 else 0
    l_t = len(oos_t[oos_t["side"] == "L"]) if len(oos_t) > 0 else 0
    s_t = len(oos_t[oos_t["side"] == "S"]) if len(oos_t) > 0 else 0
    if len(oos_t) > 0:
        mo = oos_t.groupby("month")["pnl"].sum()
        wm = mo.min()
        pm = f"{(mo > 0).sum()}/{len(mo)}"
    else:
        wm, pm = 0, "0/0"
    return {"is": is_pnl, "oos": oos_pnl, "oos_t": len(oos_t), "wr": oos_wr,
            "mdd": mdd, "wm": wm, "pm": pm,
            "l_oos": l_oos, "s_oos": s_oos, "l_t": l_t, "s_t": s_t}


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


def print_result(name, m, baseline_oos, wf6, wf8):
    delta = m["oos"] - baseline_oos
    print(f"{name:>45s} | ${m['is']:+7.0f} ${m['oos']:+7.0f} {delta:+7.0f} "
          f"L${m['l_oos']:+5.0f}({m['l_t']:2d}) S${m['s_oos']:+5.0f}({m['s_t']:2d}) "
          f"WR{m['wr']:3.0f}% MDD${m['mdd']:4.0f} WM${m['wm']:+5.0f} {m['pm']:>5s} "
          f"{wf6}/6 {wf8}/8")


def main():
    print("Loading data...")
    df = load_and_prepare()
    split = len(df) // 2

    # Base config from R2 (ATR+GK, validated)
    BASE = {"l_atr_min": 15, "s_atr_min": 15, "l_gk_min": 7}

    # === BASELINE and R2 base ===
    print("\n" + "=" * 80)
    print("BASELINES")
    print("=" * 80)
    hdr = f"{'Config':>45s} | {'IS':>8s} {'OOS':>8s} {'delta':>7s} {'L_OOS':>10s} {'S_OOS':>10s} {'WR':>4s} {'MDD':>7s} {'WstMo':>7s} {'PM':>5s} {'WF':>8s}"
    print(hdr)

    v14_cfg = {}
    v14_trades = run_backtest(df, WARMUP, len(df), v14_cfg)
    v14_m = eval_full(v14_trades, split)
    v14_wf6, _ = walk_forward(df, v14_cfg, 6)
    v14_wf8, _ = walk_forward(df, v14_cfg, 8)
    baseline_oos = v14_m["oos"]
    print_result("V14 BASELINE", v14_m, baseline_oos, v14_wf6, v14_wf8)

    r2_trades = run_backtest(df, WARMUP, len(df), BASE)
    r2_m = eval_full(r2_trades, split)
    r2_wf6, _ = walk_forward(df, BASE, 6)
    r2_wf8, _ = walk_forward(df, BASE, 8)
    print_result("R2 base (ATR15+GK7)", r2_m, baseline_oos, r2_wf6, r2_wf8)

    # === H5: DYNAMIC TP ===
    print("\n" + "=" * 80)
    print("H5: Dynamic TP (ATR-scaled)")
    print("=" * 80)
    print(hdr)

    for l_scale in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5]:
        for s_mode in ["fixed"]:  # S TP fixed at 2% (V14 proved S params locked)
            cfg = {**BASE,
                   "l_tp_mode": "atr_scaled", "l_tp_atr_scale": l_scale}
            trades = run_backtest(df, WARMUP, len(df), cfg)
            m = eval_full(trades, split)
            wf6, _ = walk_forward(df, cfg, 6)
            wf8, _ = walk_forward(df, cfg, 8)
            print_result(f"L_TP atr*{l_scale}", m, baseline_oos, wf6, wf8)

    # === H6: DYNAMIC MH ===
    print("\n" + "=" * 80)
    print("H6: Dynamic MH (GK-adaptive)")
    print("=" * 80)
    print(hdr)

    for l_mh, s_mh in [("fixed", "fixed"), ("gk_adaptive", "fixed"),
                        ("fixed", "gk_adaptive"), ("gk_adaptive", "gk_adaptive")]:
        cfg = {**BASE, "l_mh_mode": l_mh, "s_mh_mode": s_mh}
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        print_result(f"L_MH={l_mh} S_MH={s_mh}", m, baseline_oos, wf6, wf8)

    # === H7: ENTRY MOMENTUM FILTER ===
    print("\n" + "=" * 80)
    print("H7: Entry Momentum Filter")
    print("=" * 80)
    print(hdr)

    momentum_configs = [
        ("L_ret3h>0", {**BASE, "l_mom_filter": (3, "positive")}),
        ("L_ret6h>0", {**BASE, "l_mom_filter": (6, "positive")}),
        ("L_ret12h>0", {**BASE, "l_mom_filter": (12, "positive")}),
        ("L_ret3h>=-0.5", {**BASE, "l_mom_filter": (3, "non_negative")}),
        ("S_ret3h<0", {**BASE, "s_mom_filter": (3, "negative")}),
        ("S_ret6h<0", {**BASE, "s_mom_filter": (6, "negative")}),
        ("S_ret12h<0", {**BASE, "s_mom_filter": (12, "negative")}),
        ("L_ret6h>0+S_ret6h<0", {**BASE, "l_mom_filter": (6, "positive"),
                                          "s_mom_filter": (6, "negative")}),
        ("L_ret3h>0+S_ret3h<0", {**BASE, "l_mom_filter": (3, "positive"),
                                          "s_mom_filter": (3, "negative")}),
    ]

    for name, cfg in momentum_configs:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        print_result(name, m, baseline_oos, wf6, wf8)

    # === H8: BAR RANGE AT ENTRY ===
    print("\n" + "=" * 80)
    print("H8: Bar Range Confirmation")
    print("=" * 80)
    print(hdr)

    for l_range in [0, 10, 15, 20, 25]:
        for s_range in [0, 10, 15, 20]:
            if l_range == 0 and s_range == 0:
                continue
            cfg = {**BASE, "l_bar_range_min": l_range, "s_bar_range_min": s_range}
            trades = run_backtest(df, WARMUP, len(df), cfg)
            m = eval_full(trades, split)
            wf6, _ = walk_forward(df, cfg, 6)
            wf8, _ = walk_forward(df, cfg, 8)
            print_result(f"L_rng>={l_range} S_rng>={s_range}", m, baseline_oos, wf6, wf8)

    # === BEST COMBINATION ===
    print("\n" + "=" * 80)
    print("BEST COMBINATIONS")
    print("=" * 80)
    print(hdr)

    # Combine promising elements from H5-H8
    best_combos = [
        ("R2 base", BASE),
        ("R2+L_TP_atr*1.0", {**BASE, "l_tp_mode": "atr_scaled", "l_tp_atr_scale": 1.0}),
        ("R2+L_gk_mh", {**BASE, "l_mh_mode": "gk_adaptive"}),
        ("R2+L_ret3h>0", {**BASE, "l_mom_filter": (3, "positive")}),
        ("R2+L_rng>=15", {**BASE, "l_bar_range_min": 15}),
        # Full R1 best + dynamic
        ("R1_full", {"l_atr_min": 15, "s_atr_min": 15,
                     "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16},
                     "l_gk_min": 7}),
        ("R1_full+L_TP_atr*1.0", {"l_atr_min": 15, "s_atr_min": 15,
                                    "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16},
                                    "l_gk_min": 7, "l_tp_mode": "atr_scaled", "l_tp_atr_scale": 1.0}),
        ("R1_full+L_gk_mh", {"l_atr_min": 15, "s_atr_min": 15,
                              "l_block_h": BLOCK_H | {8, 19}, "s_block_h": BLOCK_H | {16},
                              "l_gk_min": 7, "l_mh_mode": "gk_adaptive"}),
    ]

    for name, cfg in best_combos:
        trades = run_backtest(df, WARMUP, len(df), cfg)
        m = eval_full(trades, split)
        wf6, _ = walk_forward(df, cfg, 6)
        wf8, _ = walk_forward(df, cfg, 8)
        print_result(name, m, baseline_oos, wf6, wf8)


if __name__ == "__main__":
    main()
