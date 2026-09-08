"""S 動態 TP 研究：Regime / GK / ATR / 突破強度決定 2.0/2.5/3.0%。

使用現行 V14+R+V25-D 引擎的記憶體副本，僅增加「進場時鎖定 TP」陣列；
不修改共用回測引擎或正式策略。
"""

from __future__ import annotations

import argparse
import inspect
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base


LEVELS = [0.020, 0.025, 0.030]
PERIODS = {
    "full": ("2024-09-04", None),
    "discovery": ("2024-09-04", "2026-01-01"),
    "validation": ("2026-01-01", None),
    "recent": ("2026-06-01", None),
}


def build_dynamic_simulator(engine):
    """從現行函式建立記憶體內研究副本；任何原始碼漂移會立即報錯。"""
    source = inspect.getsource(engine.simulate_v14_detailed)
    replacements = [
        (
            "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
            "                          realistic=False, slip_bps=0.0, margin_schedule=None):",
            "def simulate_dynamic_tp(ind, datetimes, start_bar=None,\n"
            "                        realistic=False, slip_bps=0.0, margin_schedule=None,\n"
            "                        l_tp_by_bar=None, s_tp_by_bar=None):",
        ),
        ("    lp_regime = \"NA\"\n", "    lp_regime = \"NA\"\n    lp_tp = L_TP\n"),
        ("    sp_regime = \"NA\"\n", "    sp_regime = \"NA\"\n    sp_tp = S_TP\n"),
        ("            l_tp_eff = _L_TP_BR.get(lp_regime, L_TP)\n", "            l_tp_eff = lp_tp\n"),
        ("            elif li <= ep * (1 - S_TP):\n", "            elif li <= ep * (1 - sp_tp):\n"),
        ("                ex_price = s_mkt if realistic else ep * (1 - S_TP)\n", "                ex_price = s_mkt if realistic else ep * (1 - sp_tp)\n"),
        (
            "            lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n",
            "            lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n"
            "            lp_tp = (float(l_tp_by_bar[i]) if l_tp_by_bar is not None "
            "else _L_TP_BR.get(lp_regime, L_TP))\n",
        ),
        (
            "            sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n",
            "            sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n"
            "            sp_tp = float(s_tp_by_bar[i]) if s_tp_by_bar is not None else S_TP\n",
        ),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError(f"動態 TP 引擎 patch 錨點失敗：{old[:60]!r}")
        source = source.replace(old, new)
    namespace = dict(engine.__dict__)
    exec(source, namespace)
    return namespace["simulate_dynamic_tp"]


def metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0, "wr": math.nan, "pnl": 0.0, "pf": math.nan, "mdd": 0.0}
    pnl = frame["pnl_usd"].astype(float)
    gains, losses = pnl[pnl > 0].sum(), pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    return {
        "n": len(frame),
        "wr": float((pnl > 0).mean() * 100),
        "pnl": float(pnl.sum()),
        "pf": float(gains / abs(losses)) if losses < 0 else math.inf,
        "mdd": float(abs((cum - cum.cummax()).min())),
    }


def trade_breakdown(frame: pd.DataFrame) -> dict:
    """只回傳本研究判讀需要的 S 交易樣本數與出場分布。"""
    if frame.empty:
        return {"s_trades": 0, "s_down_trades": 0, "s_down_exits": {}}
    s = frame[frame["side"] == "S"].copy()
    s_down = s[s["entry_regime"] == "DOWN"].copy()
    return {
        "s_trades": int(len(s)),
        "s_down_trades": int(len(s_down)),
        "s_down_exits": {
            str(key): int(value)
            for key, value in s_down["exit_reason"].value_counts().to_dict().items()
        },
        "s_down_pnl": float(s_down["pnl_usd"].sum()),
    }


def compare_trade_paths(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    """確認候選是否改變進場序列，並量化同一筆交易的損益差。"""
    keys = ["side", "entry_dt"]
    cand = candidate.copy()
    base_frame = baseline.copy()
    cand["entry_dt"] = pd.to_datetime(cand["entry_dt"])
    base_frame["entry_dt"] = pd.to_datetime(base_frame["entry_dt"])
    merged = cand.merge(
        base_frame,
        on=keys,
        how="outer",
        suffixes=("_candidate", "_baseline"),
        indicator=True,
    )
    common = merged[merged["_merge"] == "both"].copy()
    delta = common["pnl_usd_candidate"] - common["pnl_usd_baseline"]
    changed = common[delta.abs() > 0.005].copy()
    return {
        "same_entry_sequence": bool(len(common) == len(candidate) == len(baseline)),
        "common_trades": int(len(common)),
        "candidate_only": int((merged["_merge"] == "left_only").sum()),
        "baseline_only": int((merged["_merge"] == "right_only").sum()),
        "changed_common_trades": int(len(changed)),
        "positive_changes": int((delta > 0.005).sum()),
        "negative_changes": int((delta < -0.005).sum()),
        "sum_common_delta": float(delta.sum()),
        "median_changed_delta": float((
            changed["pnl_usd_candidate"] - changed["pnl_usd_baseline"]
        ).median()) if not changed.empty else 0.0,
    }


def run(simulator, engine, candles: pd.DataFrame, ind: dict, start: str,
        end: str | None, s_policy: np.ndarray | None, slip_bps: float = 0.0) -> pd.DataFrame:
    start_bar = int(np.searchsorted(candles["datetime"].to_numpy(), pd.Timestamp(start)))
    trades = simulator(
        ind, candles["datetime"].to_numpy(), start_bar=start_bar,
        realistic=True, slip_bps=slip_bps, margin_schedule=base.MARGIN_SCHEDULE,
        s_tp_by_bar=s_policy,
    )
    frame = pd.DataFrame(trades)
    if end is not None and not frame.empty:
        frame = frame[pd.to_datetime(frame["entry_dt"]) < pd.Timestamp(end)].copy()
    return frame


def policy_metrics(simulator, engine, candles, ind, policy) -> dict:
    return {
        period: metrics(run(simulator, engine, candles, ind, start, end, policy))
        for period, (start, end) in PERIODS.items()
    }


def market_features(candles: pd.DataFrame, ind: dict, engine) -> dict[str, np.ndarray]:
    close = candles["close"].astype(float)
    high = candles["high"].astype(float)
    low = candles["low"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    regimes = np.array([engine._classify_regime(x) for x in ind["slope"]], dtype=object)
    return {
        "regime": regimes,
        "gk": np.asarray(ind["pctile_S"], dtype=float),
        "atr": (tr.rolling(14).mean() / close * 100).to_numpy(),
        "breakout": ((pd.Series(ind["low_15"]) / close - 1) * 100).to_numpy()
        if "low_15" in ind else
        ((close.shift(1).rolling(15).min() / close - 1) * 100).to_numpy(),
    }


def map_policy(values: np.ndarray, bands: list[tuple], mapping: tuple[float, float, float]) -> np.ndarray:
    out = np.full(len(values), 0.020, dtype=float)
    for (lower, upper), tp in zip(bands, mapping):
        mask = np.ones(len(values), dtype=bool)
        if lower is not None:
            mask &= values >= lower
        if upper is not None:
            mask &= values < upper
        out[mask] = tp
    return out


def regime_policy(regimes: np.ndarray, mapping: tuple[float, float, float]) -> np.ndarray:
    out = np.full(len(regimes), 0.020, dtype=float)
    for regime, tp in zip(["DOWN", "MILD_UP", "UP"], mapping):
        out[regimes == regime] = tp
    return out


def candidate_policies(features: dict[str, np.ndarray]) -> list[dict]:
    rows = []
    # 趨勢由 DOWN→MILD_UP→UP；逆勢空單的 TP 不隨多頭程度增加。
    regime_maps = [x for x in itertools.product(LEVELS, repeat=3) if x[0] >= x[1] >= x[2]]
    for mapping in regime_maps:
        rows.append({"family": "regime", "mapping": mapping,
                     "policy": regime_policy(features["regime"], mapping),
                     "labels": ["DOWN", "MILD_UP", "UP"]})

    # GK 越低代表壓縮 percentile 越深，假設可給較遠 TP。
    monotone_down = [x for x in itertools.product(LEVELS, repeat=3) if x[0] >= x[1] >= x[2]]
    for mapping in monotone_down:
        rows.append({"family": "gk", "mapping": mapping,
                     "policy": map_policy(features["gk"], [(None, 12), (12, 24), (24, 35)], mapping),
                     "labels": ["GK<12", "12<=GK<24", "24<=GK<35"]})

    # ATR / 突破幅度越大，才允許較遠 TP。
    monotone_up = [x for x in itertools.product(LEVELS, repeat=3) if x[0] <= x[1] <= x[2]]
    for mapping in monotone_up:
        rows.append({"family": "atr", "mapping": mapping,
                     "policy": map_policy(features["atr"], [(None, .8), (.8, 1.2), (1.2, None)], mapping),
                     "labels": ["ATR<0.8", "0.8<=ATR<1.2", "ATR>=1.2"]})
        rows.append({"family": "breakout", "mapping": mapping,
                     "policy": map_policy(features["breakout"], [(None, .15), (.15, .5), (.5, None)], mapping),
                     "labels": ["BRK<0.15", "0.15<=BRK<0.5", "BRK>=0.5"]})
    return rows


def walk_forward(simulator, engine, candles, ind, policy, baseline_policy) -> dict:
    folds = [
        ("2024-09-04", "2025-01-01"), ("2025-01-01", "2025-05-01"),
        ("2025-05-01", "2025-09-01"), ("2025-09-01", "2026-01-01"),
        ("2026-01-01", "2026-05-01"), ("2026-05-01", None),
    ]
    details = []
    for start, end in folds:
        cand = metrics(run(simulator, engine, candles, ind, start, end, policy))
        base_result = metrics(run(simulator, engine, candles, ind, start, end, baseline_policy))
        details.append({"start": start, "end": end, "candidate": cand,
                        "baseline": base_result, "delta_pnl": cand["pnl"] - base_result["pnl"]})
    return {"wins": sum(x["delta_pnl"] > 0 for x in details), "folds": details}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-json", type=Path)
    args = ap.parse_args()
    candles = pd.read_csv(base.DATA_PATH)
    candles["datetime"] = pd.to_datetime(candles["datetime"])
    engine = base.load_engine()
    simulator = build_dynamic_simulator(engine)
    ind = engine.compute_indicators(candles)
    features = market_features(candles, ind, engine)
    baseline = policy_metrics(simulator, engine, candles, ind, None)
    baseline_full_frame = run(simulator, engine, candles, ind, "2024-09-04", None, None)

    evaluated = []
    for item in candidate_policies(features):
        row = {key: value for key, value in item.items() if key != "policy"}
        row["mapping"] = [round(x * 100, 2) for x in item["mapping"]]
        row.update(policy_metrics(simulator, engine, candles, ind, item["policy"]))
        evaluated.append((row, item["policy"]))

    family_best = {}
    diagnostics = {}
    for family in ["regime", "gk", "atr", "breakout"]:
        subset = [(row, policy) for row, policy in evaluated if row["family"] == family]
        ranked = sorted(subset, key=lambda x: x[0]["discovery"]["pnl"], reverse=True)
        best_row, best_policy = ranked[0]
        best_row = dict(best_row)
        best_row["walk_forward"] = walk_forward(simulator, engine, candles, ind, best_policy, None)
        best_row["slippage"] = {
            str(bps): metrics(run(simulator, engine, candles, ind, "2024-09-04", None, best_policy, bps))
            for bps in [2.0, 5.0]
        }
        family_best[family] = best_row
        diagnostics[family] = [x[0] for x in ranked[:5]]

    # S/DOWN 單一規則的細鄰域：避免只因 2.0/2.5/3.0 粗網格而誤判。
    down_tp_audit = []
    for tp in [0.020, 0.0225, 0.025, 0.0275, 0.030, 0.0325, 0.035]:
        policy = regime_policy(features["regime"], (tp, 0.020, 0.020))
        full_frame = run(simulator, engine, candles, ind, "2024-09-04", None, policy)
        row = {
            "down_tp": round(tp * 100, 2),
            **policy_metrics(simulator, engine, candles, ind, policy),
            "breakdown": trade_breakdown(full_frame),
            "path_comparison": compare_trade_paths(full_frame, baseline_full_frame),
            "walk_forward": walk_forward(simulator, engine, candles, ind, policy, None),
            "slippage": {
                str(bps): metrics(run(simulator, engine, candles, ind, "2024-09-04", None, policy, bps))
                for bps in [2.0, 5.0]
            },
        }
        down_tp_audit.append(row)

    result = {
        "baseline": baseline,
        "family_best_by_discovery": family_best,
        "top5_by_family": diagnostics,
        "candidate_count": len(evaluated),
        "s_down_tp_audit": down_tp_audit,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload, encoding="utf-8")
        print(f"wrote {args.output_json}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
