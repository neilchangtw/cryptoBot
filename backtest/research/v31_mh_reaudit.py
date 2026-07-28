"""
V31 — MaxHold reproducibility audit and fresh parameter research.

Goals:
1. Verify the current backtest data, constants, indicators and exit state machine against strategy.py.
2. Re-run MaxHold parameter cells with the current realistic execution model.
3. Test two interpretable path-dependent ideas without changing production strategy:
   - stalled-underwater adaptive MaxHold
   - breakout-reclaim failure exit
4. Gate candidates on discovery/validation/holdout, fold consistency and slippage.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
ENGINE_PATH = os.path.join(SCRIPT_DIR, "v14_export_trades.py")
DATA_PATH = os.path.join(ROOT, "data", "ETHUSDT_1h_latest730d.csv")
OUT_PATH = os.path.join(ROOT, "doc", "v31_mh_reaudit_results.csv")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import strategy

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


@dataclass
class Case:
    name: str
    family: str
    configure: Optional[Callable] = None
    policy: Optional[Callable] = None


def load_research_engine():
    """Load the production research engine and inject an optional MH policy hook."""
    with open(ENGINE_PATH, encoding="utf-8") as f:
        src = f.read()

    replacements = [
        (
            "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
            "                          realistic=False, slip_bps=0.0, margin_schedule=None):",
            "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
            "                          realistic=False, slip_bps=0.0, margin_schedule=None,\n"
            "                          mh_policy=None):",
        ),
        (
            "                    mh = L_CMH_MH if lp_reduced else l_mh_eff\n"
            "                    if not lp_ext:",
            "                    mh = L_CMH_MH if lp_reduced else l_mh_eff\n"
            "                    if mh_policy is not None:\n"
            "                        override = mh_policy(side='L', bar=i, entry_bar=lp_bar,\n"
            "                            bars_held=bh, current_pnl=cpnl, running_mfe=lp_mfe,\n"
            "                            running_mae=lp_mae, entry_regime=lp_regime,\n"
            "                            base_mh=mh, close=ci, closes=c)\n"
            "                        if override is not None:\n"
            "                            mh = max(1, int(override))\n"
            "                    if not lp_ext:",
        ),
        (
            "                cpnl = (ep - ci) / ep\n\n"
            "                if not sp_ext:\n"
            "                    if bh >= s_mh_eff:",
            "                cpnl = (ep - ci) / ep\n"
            "                s_mh_dynamic = s_mh_eff\n"
            "                if mh_policy is not None:\n"
            "                    override = mh_policy(side='S', bar=i, entry_bar=sp_bar,\n"
            "                        bars_held=bh, current_pnl=cpnl, running_mfe=sp_mfe,\n"
            "                        running_mae=sp_mae, entry_regime=sp_regime,\n"
            "                        base_mh=s_mh_eff, close=ci, closes=c)\n"
            "                    if override is not None:\n"
            "                        s_mh_dynamic = max(1, int(override))\n\n"
            "                if not sp_ext:\n"
            "                    if bh >= s_mh_dynamic:",
        ),
    ]
    for old, new in replacements:
        if src.count(old) != 1:
            raise AssertionError(f"engine hook anchor missing/non-unique: {old[:70]!r}")
        src = src.replace(old, new)

    src = src.split("if __name__ == '__main__':")[0]
    spec = importlib.util.spec_from_loader("v31_engine", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = ENGINE_PATH
    exec(compile(src, ENGINE_PATH, "exec"), mod.__dict__)
    return mod


def load_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
    return df


def audit_data(df: pd.DataFrame) -> dict:
    gaps = df["datetime"].diff().dropna()
    bad_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    result = {
        "rows": len(df),
        "start": str(df["datetime"].iloc[0]),
        "end": str(df["datetime"].iloc[-1]),
        "duplicates": int(df["datetime"].duplicated().sum()),
        "bad_gaps": int((gaps != pd.Timedelta(hours=1)).sum()),
        "nan_cells": int(df.isna().sum().sum()),
        "bad_ohlc": int(bad_ohlc.sum()),
        "nonpositive_volume": int((df["volume"] <= 0).sum()),
    }
    if any(result[k] for k in ["duplicates", "bad_gaps", "nan_cells", "bad_ohlc", "nonpositive_volume"]):
        raise AssertionError(f"data audit failed: {result}")
    return result


def audit_constants(mod) -> None:
    pairs = {
        "L_GK_S": strategy.L_GK_SHORT,
        "L_GK_L": strategy.L_GK_LONG,
        "S_GK_S": strategy.S_GK_SHORT,
        "S_GK_L": strategy.S_GK_LONG,
        "L_GK_TH": strategy.L_GK_THRESH,
        "S_GK_TH": strategy.S_GK_THRESH,
        "L_BRK": strategy.BRK_LOOK,
        "S_BRK": strategy.BRK_LOOK,
        "L_TP": strategy.L_TP_PCT,
        "L_SN": strategy.L_SAFENET_PCT,
        "L_MH": strategy.L_MAX_HOLD,
        "L_EXT": strategy.L_EXT_BARS,
        "L_MFE_ACT": strategy.L_MFE_ACT,
        "L_MFE_TR": strategy.L_MFE_TRAIL_DD,
        "L_CMH_BAR": strategy.L_COND_CHECK_BAR,
        "L_CMH_TH": strategy.L_COND_EXIT_THRESH,
        "L_CMH_MH": strategy.L_COND_REDUCED_MH,
        "S_TP": strategy.S_TP_PCT,
        "S_SN": strategy.S_SAFENET_PCT,
        "S_MH": strategy.S_MAX_HOLD,
        "S_EXT": strategy.S_EXT_BARS,
        "R_TH_UP": strategy.R_TH_UP,
        "R_TH_SIDE": strategy.R_TH_SIDE,
    }
    bad = [(name, getattr(mod, name), expected) for name, expected in pairs.items()
           if not math.isclose(float(getattr(mod, name)), float(expected), rel_tol=0, abs_tol=1e-12)]
    if dict(mod._L_TP_BR) != dict(strategy.L_TP_BY_REGIME):
        bad.append(("L_TP_BY_REGIME", mod._L_TP_BR, strategy.L_TP_BY_REGIME))
    if dict(mod._L_MH_BR) != dict(strategy.L_MH_BY_REGIME):
        bad.append(("L_MH_BY_REGIME", mod._L_MH_BR, strategy.L_MH_BY_REGIME))
    if dict(mod._S_MH_BR) != dict(strategy.S_MH_BY_REGIME):
        bad.append(("S_MH_BY_REGIME", mod._S_MH_BR, strategy.S_MH_BY_REGIME))
    if bad:
        raise AssertionError(f"constant parity failed: {bad}")


def audit_indicators(mod, df: pd.DataFrame, ind: dict) -> None:
    live = strategy.compute_indicators(df)
    pairs = [
        ("gk_pctile", "pctile_L"),
        ("gk_pctile_s", "pctile_S"),
        ("breakout_long", "brk_up"),
        ("breakout_short", "brk_dn"),
        ("regime_block_l", "regime_block_l"),
        ("regime_block_s", "regime_block_s"),
        ("sma_slope", "slope"),
    ]
    for live_col, bt_key in pairs:
        a = live[live_col].to_numpy(dtype=float)
        b = np.asarray(ind[bt_key], dtype=float)
        if not np.allclose(a, b, rtol=0, atol=1e-12, equal_nan=True):
            mismatch = int(np.sum(~np.isclose(a, b, rtol=0, atol=1e-12, equal_nan=True)))
            raise AssertionError(f"indicator parity failed: {live_col}/{bt_key}, mismatch={mismatch}")


def replay_exit_parity(mod, df: pd.DataFrame, trades: list[dict]) -> None:
    reason_map = {
        "SafeNet": "SN", "TP": "TP", "MFE-trail": "MFE",
        "MaxHold": "MH", "MH-ext": "MHx", "BE": "BE",
    }
    bad = []
    for trade in trades:
        eb = int(trade["entry_bar"])
        ep = float(df["close"].iloc[eb])
        ext = False
        ext_start = 0
        running_mfe = 0.0
        reduced = False
        got = None
        for i in range(eb + 1, min(len(df), eb + 24)):
            row = df.iloc[i]
            if trade["side"] == "L":
                out = strategy.check_exit_long(
                    ep, eb, i, float(row.high), float(row.low), float(row.close),
                    ext, ext_start, running_mfe, reduced, trade["entry_regime"],
                )
                running_mfe = out["running_mfe"]
                reduced = out["mh_reduced"]
            else:
                out = strategy.check_exit_short(
                    ep, eb, i, float(row.high), float(row.low), float(row.close),
                    ext, ext_start, trade["entry_regime"],
                )
            if out.get("start_extension"):
                ext = True
                ext_start = i
            if out["exit"]:
                got = (i, reason_map[out["reason"]])
                break
        expected = (int(trade["exit_bar"]), trade["exit_reason"])
        if got != expected:
            bad.append((trade["side"], eb, expected, got, trade["entry_regime"]))
    if bad:
        raise AssertionError(f"exit parity failed: {len(bad)} mismatches, examples={bad[:5]}")


def metrics(trades: list[dict], n_bars: int) -> dict:
    pnl = np.asarray([t["pnl_usd"] for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = np.cumsum(pnl) if len(pnl) else np.asarray([0.0])
    peak = np.maximum.accumulate(np.r_[0.0, equity])
    dd = peak - np.r_[0.0, equity]
    mh = [t for t in trades if t["exit_reason"] == "MH"]
    cuts = [0, n_bars // 2, n_bars * 3 // 4, n_bars + 1]
    seg = []
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        seg.append(sum(t["pnl_usd"] for t in trades if lo <= t["entry_bar"] < hi))
    folds = []
    edges = np.linspace(0, n_bars, 7, dtype=int)
    for lo, hi in zip(edges[:-1], edges[1:]):
        folds.append(sum(t["pnl_usd"] for t in trades if lo <= t["entry_bar"] < hi))
    return {
        "n": len(trades),
        "pnl": float(pnl.sum()),
        "wr": float((pnl > 0).mean() * 100) if len(pnl) else 0.0,
        "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else float("inf"),
        "mdd": float(dd.max()),
        "mh_n": len(mh),
        "mh_pnl": float(sum(t["pnl_usd"] for t in mh)),
        "discover": float(seg[0]),
        "validate": float(seg[1]),
        "holdout": float(seg[2]),
        "folds": folds,
    }


def reset_params(mod) -> None:
    mod.L_MH = int(strategy.L_MAX_HOLD)
    mod.L_CMH_MH = int(strategy.L_COND_REDUCED_MH)
    mod.S_MH = int(strategy.S_MAX_HOLD)
    mod._L_TP_BR = dict(strategy.L_TP_BY_REGIME)
    mod._L_MH_BR = dict(strategy.L_MH_BY_REGIME)
    mod._S_MH_BR = dict(strategy.S_MH_BY_REGIME)


def make_param_config(field: str, value: int) -> Callable:
    def configure(mod):
        if field == "L_DEFAULT":
            mod.L_MH = value
        elif field == "L_MILD_UP":
            mod._L_MH_BR["MILD_UP"] = value
        elif field == "L_CONDITIONAL":
            mod.L_CMH_MH = value
        elif field == "S_DEFAULT":
            mod.S_MH = value
        elif field == "S_MILD_UP":
            mod._S_MH_BR["MILD_UP"] = value
        elif field == "S_DOWN":
            mod._S_MH_BR["DOWN"] = value
        elif field == "S_UP":
            mod._S_MH_BR["UP"] = value
        else:
            raise ValueError(field)
    return configure


def make_stall_policy(target_side: str, min_bar: int, pnl_threshold: float,
                      mfe_cap: float, grace: int) -> Callable:
    def policy(**state):
        if state["side"] != target_side or state["bars_held"] < min_bar:
            return None
        if state["current_pnl"] <= pnl_threshold and state["running_mfe"] <= mfe_cap:
            return min_bar + grace
        return None
    return policy


def make_shallow_grace_policy(target_side: str, loss_cap: float, extra: int,
                              require_improving: bool) -> Callable:
    """At the normal MH boundary, grant shallow losers a bounded recovery window."""
    def policy(**state):
        if state["side"] != target_side:
            return None
        base_mh = int(state["base_mh"])
        if state["bars_held"] < base_mh:
            return None
        cpnl = float(state["current_pnl"])
        if not (-loss_cap <= cpnl <= 0.0):
            return None
        if require_improving:
            i = int(state["bar"])
            closes = state["closes"]
            if i <= state["entry_bar"]:
                return None
            improving = closes[i] > closes[i - 1] if target_side == "L" else closes[i] < closes[i - 1]
            if not improving:
                return None
        return base_mh + extra
    return policy

def make_reclaim_policy(target_side: str, min_bar: int, confirm: int,
                        depth: float) -> Callable:
    def policy(**state):
        if state["side"] != target_side or state["bars_held"] < min_bar:
            return None
        if state["current_pnl"] > 0:
            return None
        closes = state["closes"]
        eb = state["entry_bar"]
        i = state["bar"]
        if eb < 15 or i - confirm + 1 <= eb:
            return None
        if target_side == "L":
            level = float(np.max(closes[eb - 15:eb]))
            failed = all(float(closes[j]) <= level * (1 - depth)
                         for j in range(i - confirm + 1, i + 1))
        else:
            level = float(np.min(closes[eb - 15:eb]))
            failed = all(float(closes[j]) >= level * (1 + depth)
                         for j in range(i - confirm + 1, i + 1))
        return state["bars_held"] if failed else None
    return policy


def evaluate_case(mod, ind, datetimes, n_bars: int, case: Case,
                  slip_bps: float = 0.0) -> tuple[list[dict], dict]:
    reset_params(mod)
    if case.configure is not None:
        case.configure(mod)
    trades = mod.simulate_v14_detailed(
        ind, datetimes, realistic=True, slip_bps=slip_bps,
        margin_schedule=None, mh_policy=case.policy,
    )
    return trades, metrics(trades, n_bars)


def row_for(case: Case, met: dict, base: dict) -> dict:
    fold_delta = [a - b for a, b in zip(met["folds"], base["folds"])]
    return {
        "case": case.name,
        "family": case.family,
        "n": met["n"],
        "pnl": round(met["pnl"], 2),
        "delta": round(met["pnl"] - base["pnl"], 2),
        "wr": round(met["wr"], 2),
        "mdd": round(met["mdd"], 2),
        "mh_n": met["mh_n"],
        "mh_pnl": round(met["mh_pnl"], 2),
        "d_discover": round(met["discover"] - base["discover"], 2),
        "d_validate": round(met["validate"] - base["validate"], 2),
        "d_holdout": round(met["holdout"] - base["holdout"], 2),
        "folds_better": int(sum(x > 0 for x in fold_delta)),
        "worst_fold_delta": round(min(fold_delta), 2),
    }


def grace_neighborhood_pass(case_name: str, result: pd.DataFrame) -> bool:
    """Reject isolated grace optima after the main robustness gates."""
    match = re.fullmatch(r"GRACE_([LS])_LOSS([0-9.]+)_X([0-9]+)_IMP([01])", case_name)
    if not match:
        return True
    side, cap_s, extra_s, improving_s = match.groups()
    cap = float(cap_s)
    extra = int(extra_s)
    neighbors = []
    for _, row in result.iterrows():
        other = re.fullmatch(r"GRACE_([LS])_LOSS([0-9.]+)_X([0-9]+)_IMP([01])", str(row["case"]))
        if not other:
            continue
        o_side, o_cap_s, o_extra_s, o_imp = other.groups()
        o_cap, o_extra = float(o_cap_s), int(o_extra_s)
        if o_side != side or o_imp != improving_s:
            continue
        if abs(o_cap - cap) <= 0.00251 and abs(o_extra - extra) <= 1:
            if not (math.isclose(o_cap, cap, abs_tol=1e-9) and o_extra == extra):
                neighbors.append(row)
    if len(neighbors) < 4:
        return False
    positive_total = sum(float(r["delta"]) > 0 for r in neighbors)
    positive_all_segments = sum(
        float(r["d_discover"]) > 0
        and float(r["d_validate"]) > 0
        and float(r["d_holdout"]) > 0
        for r in neighbors
    )
    return (positive_total >= math.ceil(len(neighbors) * 0.60)
            and positive_all_segments >= math.ceil(len(neighbors) * 0.50))

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="smaller policy grid for smoke tests")
    args = ap.parse_args()

    df = load_data()
    mod = load_research_engine()
    data_result = audit_data(df)
    audit_constants(mod)
    ind = mod.compute_indicators(df)
    audit_indicators(mod, df, ind)
    datetimes = df["datetime"].to_numpy()

    baseline_case = Case("BASELINE", "baseline")
    baseline_trades, baseline = evaluate_case(mod, ind, datetimes, len(df), baseline_case)
    replay_exit_parity(mod, df, baseline_trades)

    print("V31 AUDIT PASS")
    print(data_result)
    print(f"constant/indicator/exit parity: PASS ({len(baseline_trades)} trades)")
    print(f"baseline: PnL ${baseline['pnl']:+,.2f}, WR {baseline['wr']:.1f}%, "
          f"MDD ${baseline['mdd']:.2f}, MH {baseline['mh_n']} ${baseline['mh_pnl']:+,.2f}")

    cases: list[Case] = []
    for field, values in [
        ("L_DEFAULT", range(4, 10)),
        ("L_MILD_UP", range(5, 10)),
        ("L_CONDITIONAL", range(3, 7)),
        ("S_DEFAULT", range(6, 21)),
        ("S_MILD_UP", range(8, 19)),
        ("S_DOWN", range(8, 19)),
        ("S_UP", range(5, 15)),
    ]:
        for value in values:
            cases.append(Case(f"{field}={value}", "mh_parameter",
                              configure=make_param_config(field, value)))

    if args.quick:
        l_bars, s_bars = [3, 5], [4, 7]
        pnl_thresholds, mfe_caps, graces = [0.0, -0.005], [0.005], [0, 1]
    else:
        l_bars, s_bars = range(2, 6), range(3, 9)
        pnl_thresholds = [0.0, -0.005, -0.010]
        mfe_caps = [0.005, 0.010, 0.99]
        graces = [0, 1, 2]

    for side, bars in [("L", l_bars), ("S", s_bars)]:
        for min_bar in bars:
            for pnl_th in pnl_thresholds:
                for mfe_cap in mfe_caps:
                    for grace in graces:
                        name = (f"STALL_{side}_N{min_bar}_P{pnl_th:+.3f}_"
                                f"M{mfe_cap:.3f}_G{grace}")
                        cases.append(Case(
                            name, "stalled_underwater",
                            policy=make_stall_policy(side, min_bar, pnl_th, mfe_cap, grace),
                        ))

    grace_caps = ([0.005, 0.010] if args.quick else
                  [0.0050, 0.0075, 0.0100, 0.0125, 0.0150, 0.0175, 0.0200, 0.0225, 0.0250])
    grace_extras = [1, 2] if args.quick else [1, 2, 3, 4, 5, 6]
    for side in ["L", "S"]:
        for loss_cap in grace_caps:
            for extra in grace_extras:
                for improving in [False, True]:
                    name = (f"GRACE_{side}_LOSS{loss_cap:.4f}_X{extra}_"
                            f"IMP{int(improving)}")
                    cases.append(Case(
                        name, "shallow_loss_grace",
                        policy=make_shallow_grace_policy(side, loss_cap, extra, improving),
                    ))
    reclaim_bars = [1, 2] if args.quick else [1, 2, 3, 4]
    depths = [0.0] if args.quick else [0.0, 0.001, 0.002]
    for side in ["L", "S"]:
        for min_bar in reclaim_bars:
            for confirm in [1, 2]:
                for depth in depths:
                    name = f"RECLAIM_{side}_N{min_bar}_C{confirm}_D{depth:.3f}"
                    cases.append(Case(
                        name, "breakout_reclaim",
                        policy=make_reclaim_policy(side, min_bar, confirm, depth),
                    ))

    rows = [row_for(baseline_case, baseline, baseline)]
    case_lookup = {baseline_case.name: baseline_case}
    for idx, case in enumerate(cases, 1):
        _, met = evaluate_case(mod, ind, datetimes, len(df), case)
        rows.append(row_for(case, met, baseline))
        case_lookup[case.name] = case
        if idx % 50 == 0:
            print(f"  evaluated {idx}/{len(cases)} cases")

    result = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    discovery_pass = result[
        (result["case"] != "BASELINE")
        & (result["d_discover"] > 0)
        & (result["d_validate"] > 0)
    ].sort_values(["d_discover", "d_validate"], ascending=False)

    print(f"\nDiscovery+validation pass: {len(discovery_pass)}/{len(cases)}")
    if len(discovery_pass):
        print(discovery_pass.head(20).to_string(index=False))

    # Holdout and robustness gate is reported only after discovery/validation selection.
    gated = discovery_pass[
        (discovery_pass["d_holdout"] > 0)
        & (discovery_pass["delta"] > 0)
        & (discovery_pass["folds_better"] >= 4)
        & (discovery_pass["mdd"] <= baseline["mdd"] * 1.10)
    ].copy()

    stress_rows = []
    for _, selected in gated.head(12).iterrows():
        case = case_lookup[selected["case"]]
        stress = {"case": case.name}
        stress_ok = True
        for slip in [2.0, 5.0]:
            _, bm = evaluate_case(mod, ind, datetimes, len(df), baseline_case, slip)
            _, cm = evaluate_case(mod, ind, datetimes, len(df), case, slip)
            delta = cm["pnl"] - bm["pnl"]
            stress[f"delta_{int(slip)}bp"] = round(delta, 2)
            stress_ok &= delta > 0
        stress["stress_pass"] = stress_ok
        stress_rows.append(stress)

    stress_df = pd.DataFrame(stress_rows)
    if len(stress_df):
        gated = gated.merge(stress_df, on="case", how="left")
        gated["neighborhood_pass"] = gated["case"].map(lambda x: grace_neighborhood_pass(x, result))
        promoted = gated[(gated["stress_pass"] == True) & (gated["neighborhood_pass"] == True)]
    else:
        promoted = gated.iloc[0:0]

    print(f"\nFull robustness gate before cost stress: {len(gated)}")
    if len(gated):
        print(gated.head(12).to_string(index=False))
    print(f"\nPROMOTED candidates: {len(promoted)}")
    if len(promoted):
        print(promoted.to_string(index=False))
    else:
        print("None — keep V14+R+V25-D unchanged.")
    print(f"\nFull results: {OUT_PATH}")


if __name__ == "__main__":
    main()