"""交易價格區間與進場 K 棒研究（不修改正式策略）。

資料來源：
1. 由 run_backtest.py 匯出的回測文字明細。
2. 由 analyze.py 匯出的正式盤文字明細。
3. Binance Futures 已收盤 ETHUSDT 1h 快取。

本工具會把畫面顯示的「成交時間」減一小時，對回訊號 K 棒的開盤時間，
再計算價格/K 棒分桶、正式盤與回測成交貼合，以及預先定義 gate 的時序驗證。
所有 gate 都只在記憶體中遮罩進場訊號，不會修改 strategy.py 或實盤設定。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "data" / "ETHUSDT_1h_latest730d.csv"
ENGINE_PATH = ROOT / "backtest" / "research" / "v14_export_trades.py"
MARGIN_SCHEDULE = [
    ("2000-01-01", 200),
    ("2026-07-03", 300),
    ("2026-08-01", 500),
]

ROW_RE = re.compile(
    r"^\s*(?P<num>\d+)\s+(?P<side>[LS])\s+"
    r"(?P<entry_dt>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"(?P<entry_price>\d+(?:\.\d+)?)\s+"
    r"(?P<exit_dt>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"(?P<exit_price>\d+(?:\.\d+)?)\s+"
    r"(?P<reason>.+?\([^)]*\))\s+(?P<numbers>.+?)\s+"
    r"(?P<regime>多頭 \(UP\)|偏多 \(MILD_UP\)|盤整 \(SIDE\)|空頭 \(DOWN\)|未知 \(NA\))\s*$"
)


def load_engine():
    spec = importlib.util.spec_from_file_location("price_candle_v14_engine", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_trade_text(path: Path, kind: str) -> pd.DataFrame:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        item = match.groupdict()
        nums = item.pop("numbers").split()
        if kind == "backtest":
            if len(nums) < 5:
                continue
            hold, margin, pnl, move_pct, margin_pnl_pct = nums[:5]
            item.update(
                hold=int(hold), margin=float(margin), pnl=float(pnl),
                move_pct=float(move_pct), margin_pnl_pct=float(margin_pnl_pct),
            )
        else:
            if len(nums) < 2:
                continue
            hold, pnl = nums[:2]
            item.update(hold=int(hold), margin=np.nan, pnl=float(pnl))
        item["num"] = int(item["num"])
        item["entry_dt"] = pd.Timestamp(item["entry_dt"])
        item["exit_dt"] = pd.Timestamp(item["exit_dt"])
        item["entry_price"] = float(item["entry_price"])
        item["exit_price"] = float(item["exit_price"])
        item["reason_code"] = {
            "止盈": "TP", "最長持倉": "MH", "浮盈回吐": "MFE",
            "延長超時": "MHx", "平保": "BE", "安全網": "SN",
        }.get(item["reason"].split()[0], item["reason"])
        item["regime_code"] = item["regime"].split("(")[-1].rstrip(")")
        rows.append(item)
    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError(f"無法從 {path} 解析交易明細")
    result["win"] = result["pnl"] > 0
    return result


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = wins / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * (center - half), 100 * (center + half))


def add_market_features(trades: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    d = candles.copy()
    d["datetime"] = pd.to_datetime(d["datetime"])
    d = d.set_index("datetime", drop=False)
    prev_close = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr14_pct"] = tr.rolling(14).mean() / d["close"] * 100
    d["volume_ratio_24"] = d["volume"] / d["volume"].shift(1).rolling(24).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    d["prev15_high"] = d["close"].shift(1).rolling(15).max()
    d["prev15_low"] = d["close"].shift(1).rolling(15).min()
    d["mom3"] = (d["close"] / d["close"].shift(3) - 1) * 100

    out = trades.copy()
    out["signal_dt"] = out["entry_dt"] - pd.Timedelta(hours=1)
    joined = d.reindex(out["signal_dt"]).reset_index(drop=True)
    missing = int(joined["close"].isna().sum())
    if missing:
        raise ValueError(f"有 {missing} 筆交易找不到對應訊號 K 棒")
    for column in [
        "open", "high", "low", "close", "volume", "atr14_pct",
        "volume_ratio_24", "sma200", "prev15_high", "prev15_low", "mom3",
    ]:
        out[column] = joined[column].to_numpy()

    sign = out["side"].map({"L": 1.0, "S": -1.0})
    span = (out["high"] - out["low"]).replace(0, np.nan)
    out["aligned_body_pct"] = sign * (out["close"] - out["open"]) / out["open"] * 100
    out["range_pct"] = span / out["open"] * 100
    out["close_strength"] = np.where(
        out["side"].eq("L"),
        (out["close"] - out["low"]) / span,
        (out["high"] - out["close"]) / span,
    )
    out["breakout_excess_pct"] = np.where(
        out["side"].eq("L"),
        (out["close"] / out["prev15_high"] - 1) * 100,
        (out["prev15_low"] / out["close"] - 1) * 100,
    )
    out["signed_dist_sma200_pct"] = sign * (out["close"] / out["sma200"] - 1) * 100
    out["signed_mom3_pct"] = sign * out["mom3"]
    out["entry_close_diff_bps"] = sign * (out["entry_price"] / out["close"] - 1) * 10000
    out["entry_hour"] = out["signal_dt"].dt.hour
    out["entry_weekday"] = out["signal_dt"].dt.dayofweek.map(
        {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    )
    return out


def summary(df: pd.DataFrame) -> dict:
    n = len(df)
    wins = int((df["pnl"] > 0).sum()) if n else 0
    losses = df.loc[df["pnl"] < 0, "pnl"].sum() if n else 0.0
    gains = df.loc[df["pnl"] > 0, "pnl"].sum() if n else 0.0
    lo, hi = wilson_interval(wins, n)
    return {
        "n": n,
        "wins": wins,
        "wr": 100 * wins / n if n else math.nan,
        "ci_lo": lo,
        "ci_hi": hi,
        "pnl": float(df["pnl"].sum()) if n else 0.0,
        "avg_pnl": float(df["pnl"].mean()) if n else math.nan,
        "pf": float(gains / abs(losses)) if losses < 0 else math.inf,
    }


def grouped_stats(df: pd.DataFrame, group_col: str) -> list[dict]:
    rows = []
    for key, group in df.groupby(group_col, observed=True, sort=True):
        row = {"bucket": str(key)}
        row.update(summary(group))
        rows.append(row)
    return rows


def add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    pmin = math.floor(out["entry_price"].min() / 500) * 500
    pmax = math.ceil(out["entry_price"].max() / 500) * 500 + 500
    price_edges = np.arange(pmin, pmax + 1, 500)
    out["price_band"] = pd.cut(out["entry_price"], price_edges, right=False)
    configs = {
        "body_band": ("aligned_body_pct", [-np.inf, 0.20, 0.40, 0.70, np.inf]),
        "strength_band": ("close_strength", [-np.inf, 0.60, 0.75, 0.90, np.inf]),
        "breakout_band": ("breakout_excess_pct", [-np.inf, 0.10, 0.25, 0.50, np.inf]),
        "volume_band": ("volume_ratio_24", [-np.inf, 0.80, 1.10, 1.50, np.inf]),
        "atr_band": ("atr14_pct", [-np.inf, 0.75, 1.00, 1.35, np.inf]),
        "mom3_band": ("signed_mom3_pct", [-np.inf, 0.75, 1.25, 2.00, np.inf]),
        "sma_dist_band": ("signed_dist_sma200_pct", [-np.inf, -5, 0, 5, np.inf]),
    }
    for name, (column, edges) in configs.items():
        out[name] = pd.cut(out[column], edges, right=False)
    return out


def engine_trades(engine, candles: pd.DataFrame, start: str, end: str | None,
                  gate_l: np.ndarray | None = None, gate_s: np.ndarray | None = None,
                  margin_schedule=None) -> pd.DataFrame:
    ind = engine.compute_indicators(candles)
    if gate_l is not None:
        ind["brk_up"] = ind["brk_up"] & np.asarray(gate_l, dtype=bool)
    if gate_s is not None:
        ind["brk_dn"] = ind["brk_dn"] & np.asarray(gate_s, dtype=bool)
    datetimes = candles["datetime"].to_numpy()
    start_bar = int(np.searchsorted(pd.to_datetime(datetimes), pd.Timestamp(start)))
    trades = engine.simulate_v14_detailed(
        ind, datetimes, start_bar=start_bar, realistic=True,
        slip_bps=0.0, margin_schedule=margin_schedule,
    )
    frame = pd.DataFrame(trades)
    if end is not None and not frame.empty:
        frame = frame[pd.to_datetime(frame["entry_dt"]) < pd.Timestamp(end)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["pnl", "entry_dt", "side"])
    frame = frame.rename(columns={"pnl_usd": "pnl"})
    return frame


def engine_summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0, "wr": math.nan, "pnl": 0.0, "pf": math.nan, "mdd": 0.0}
    pnl = frame["pnl"].astype(float)
    gains = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    cum = pnl.cumsum()
    return {
        "n": len(frame),
        "wr": float((pnl > 0).mean() * 100),
        "pnl": float(pnl.sum()),
        "pf": float(gains / abs(losses)) if losses < 0 else math.inf,
        "mdd": float(abs((cum - cum.cummax()).min())),
    }


def per_bar_features(candles: pd.DataFrame) -> dict[str, np.ndarray]:
    d = candles.copy()
    o, h, l, c, v = (d[x].astype(float) for x in ["open", "high", "low", "close", "volume"])
    prev = c.shift(1)
    tr = pd.concat([(h-l), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    span = (h-l).replace(0, np.nan)
    p15h = c.shift(1).rolling(15).max()
    p15l = c.shift(1).rolling(15).min()
    sma = c.rolling(200).mean()
    common = {
        "range": (span/o*100).to_numpy(),
        "vol": (v / v.shift(1).rolling(24).mean()).to_numpy(),
        "atr": (tr.rolling(14).mean()/c*100).to_numpy(),
        "hour": pd.to_datetime(d["datetime"]).dt.hour.to_numpy(),
        "dow": pd.to_datetime(d["datetime"]).dt.dayofweek.to_numpy(),
    }
    return {
        **common,
        "body_l": ((c-o)/o*100).to_numpy(),
        "body_s": ((o-c)/o*100).to_numpy(),
        "strength_l": ((c-l)/span).to_numpy(),
        "strength_s": ((h-c)/span).to_numpy(),
        "excess_l": ((c/p15h-1)*100).to_numpy(),
        "excess_s": ((p15l/c-1)*100).to_numpy(),
        "mom_l": ((c/c.shift(3)-1)*100).to_numpy(),
        "mom_s": ((c.shift(3)/c-1)*100).to_numpy(),
        "sma_l": ((c/sma-1)*100).to_numpy(),
        "sma_s": ((sma/c-1)*100).to_numpy(),
    }


def gate_research(engine, candles: pd.DataFrame) -> dict:
    f = per_bar_features(candles)
    n = len(candles)
    true = np.ones(n, dtype=bool)
    candidates: list[tuple[str, np.ndarray, np.ndarray]] = []
    for threshold in [0.20, 0.40, 0.70]:
        candidates.append((f"body>={threshold:.2f}%", f["body_l"] >= threshold, f["body_s"] >= threshold))
    for threshold in [0.60, 0.75, 0.90]:
        candidates.append((f"close_strength>={threshold:.2f}", f["strength_l"] >= threshold, f["strength_s"] >= threshold))
    for threshold in [0.10, 0.25, 0.50]:
        candidates.append((f"breakout_excess>={threshold:.2f}%", f["excess_l"] >= threshold, f["excess_s"] >= threshold))
    for threshold in [0.80, 1.10, 1.50]:
        candidates.append((f"volume_ratio>={threshold:.2f}", f["vol"] >= threshold, f["vol"] >= threshold))
    for threshold in [0.75, 1.25, 2.00]:
        candidates.append((f"momentum3>={threshold:.2f}%", f["mom_l"] >= threshold, f["mom_s"] >= threshold))
    for threshold in [-5.0, 0.0, 5.0]:
        candidates.append((f"signed_SMA200_dist>={threshold:.1f}%", f["sma_l"] >= threshold, f["sma_s"] >= threshold))
    # 分桶中若同時在回測與正式盤表現較好，再以 band-pass 做完整、具狀態的反事實重跑。
    band_candidates = [
        ("breakout_excess[0.10,0.25)",
         (f["excess_l"] >= 0.10) & (f["excess_l"] < 0.25),
         (f["excess_s"] >= 0.10) & (f["excess_s"] < 0.25)),
        ("volume_ratio[1.10,1.50)",
         (f["vol"] >= 1.10) & (f["vol"] < 1.50),
         (f["vol"] >= 1.10) & (f["vol"] < 1.50)),
        ("close_strength[0.60,0.75)",
         (f["strength_l"] >= 0.60) & (f["strength_l"] < 0.75),
         (f["strength_s"] >= 0.60) & (f["strength_s"] < 0.75)),
        ("ATR14>=1.35%", f["atr"] >= 1.35, f["atr"] >= 1.35),
        ("signed_SMA200_dist[0,5)%",
         (f["sma_l"] >= 0.0) & (f["sma_l"] < 5.0),
         (f["sma_s"] >= 0.0) & (f["sma_s"] < 5.0)),
    ]
    for name, gl, gs in band_candidates:
        candidates.extend([
            (name, gl, gs),
            (name + " L-only", gl, true),
            (name + " S-only", true, gs),
        ])
    candidates.extend([
        ("block_signal_hour_19", f["hour"] != 19, f["hour"] != 19),
        ("block_Tuesday", f["dow"] != 1, f["dow"] != 1),
    ])

    periods = {
        "discovery": ("2024-09-04", "2026-01-01"),
        "validation": ("2026-01-01", None),
        "full": ("2024-09-04", None),
    }
    baseline = {
        key: engine_summary(engine_trades(
            engine, candles, start, end, true, true, margin_schedule=MARGIN_SCHEDULE,
        ))
        for key, (start, end) in periods.items()
    }
    rows = []
    for name, gl, gs in candidates:
        row = {"name": name}
        for key, (start, end) in periods.items():
            row[key] = engine_summary(engine_trades(
                engine, candles, start, end, gl, gs, margin_schedule=MARGIN_SCHEDULE,
            ))
        rows.append(row)
    return {"baseline": baseline, "candidates": rows}


def match_live_to_backtest(live: pd.DataFrame, bt: pd.DataFrame) -> dict:
    left = live.copy()
    right = bt.copy()
    matched = left.merge(
        right,
        on=["side", "entry_dt"],
        how="left",
        suffixes=("_live", "_bt"),
        indicator=True,
    )
    exact = matched[matched["_merge"].eq("both")].copy()
    if exact.empty:
        return {"matched": 0, "total": len(left)}
    sign = exact["side"].map({"L": 1.0, "S": -1.0})
    exact["entry_adverse_bps"] = sign * (exact["entry_price_live"] / exact["entry_price_bt"] - 1) * 10000
    exact["exit_adverse_bps"] = -sign * (exact["exit_price_live"] / exact["exit_price_bt"] - 1) * 10000
    same_exit = exact["exit_dt_live"].eq(exact["exit_dt_bt"])
    same_reason = exact["reason_code_live"].eq(exact["reason_code_bt"])
    return {
        "matched": len(exact), "total": len(left),
        "same_exit_time": int(same_exit.sum()),
        "same_reason": int(same_reason.sum()),
        "entry_adverse_bps_mean": float(exact["entry_adverse_bps"].mean()),
        "entry_adverse_bps_median": float(exact["entry_adverse_bps"].median()),
        "exit_adverse_bps_mean": float(exact["exit_adverse_bps"].mean()),
        "exit_adverse_bps_median": float(exact["exit_adverse_bps"].median()),
        "pnl_delta_total": float((exact["pnl_live"] - exact["pnl_bt"]).sum()),
        "wr_live": float(exact["win_live"].mean() * 100),
        "wr_bt": float(exact["win_bt"].mean() * 100),
    }


def maxhold_forward(trades: pd.DataFrame, candles: pd.DataFrame) -> list[dict]:
    d = candles.copy()
    d["datetime"] = pd.to_datetime(d["datetime"])
    close = d.set_index("datetime")["close"]
    rows = []
    subset = trades[trades["reason_code"].eq("MH")].copy()
    for hours in [3, 6, 12, 24]:
        vals = []
        for t in subset.itertuples():
            # exit_dt 是成交時刻；該根 K 棒開盤時刻為 exit_dt-1h。
            future_bar = t.exit_dt - pd.Timedelta(hours=1) + pd.Timedelta(hours=hours)
            if future_bar not in close.index:
                continue
            sign = 1 if t.side == "L" else -1
            vals.append(sign * (float(close.loc[future_bar]) / t.entry_price - 1) * 100)
        arr = np.asarray(vals, dtype=float)
        rows.append({
            "hours": hours, "n": len(arr),
            "avg_entry_to_future_move_pct": float(arr.mean()) if len(arr) else math.nan,
            "positive_rate": float((arr > 0).mean() * 100) if len(arr) else math.nan,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=Path, required=True)
    ap.add_argument("--backtest", type=Path, required=True)
    ap.add_argument("--output-json", type=Path)
    args = ap.parse_args()

    candles = pd.read_csv(DATA_PATH)
    candles["datetime"] = pd.to_datetime(candles["datetime"])
    live = add_buckets(add_market_features(parse_trade_text(args.live, "live"), candles))
    bt = add_buckets(add_market_features(parse_trade_text(args.backtest, "backtest"), candles))
    engine = load_engine()
    current = engine_trades(
        engine, candles, "2024-09-04", "2026-09-05",
        margin_schedule=MARGIN_SCHEDULE,
    )

    grouped = {}
    for col in [
        "price_band", "body_band", "strength_band", "breakout_band",
        "volume_band", "atr_band", "mom3_band", "sma_dist_band",
    ]:
        grouped[col] = {
            "backtest": grouped_stats(bt, col),
            "live": grouped_stats(live, col),
            "discovery": grouped_stats(bt[bt["entry_dt"] < "2026-01-01"], col),
            "validation": grouped_stats(bt[bt["entry_dt"] >= "2026-01-01"], col),
        }

    result = {
        "coverage": {
            "bars": len(candles),
            "first_open": str(candles["datetime"].iloc[0]),
            "last_open": str(candles["datetime"].iloc[-1]),
            "last_closed": str(candles["datetime"].iloc[-1] + pd.Timedelta(hours=1)),
        },
        "attachment": {
            "backtest": summary(bt),
            "live": summary(live),
        },
        "engine_replay": engine_summary(current),
        "engine_attachment_count_equal": len(current) == len(bt),
        "groups": grouped,
        "price_by_side": {
            side: {
                "backtest": grouped_stats(bt[bt["side"].eq(side)], "price_band"),
                "live": grouped_stats(live[live["side"].eq(side)], "price_band"),
            }
            for side in ["L", "S"]
        },
        "calendar": {
            "hour_backtest": grouped_stats(bt, "entry_hour"),
            "weekday_backtest": grouped_stats(bt, "entry_weekday"),
            "hour_live": grouped_stats(live, "entry_hour"),
            "weekday_live": grouped_stats(live, "entry_weekday"),
        },
        "side": {
            "backtest": grouped_stats(bt, "side"),
            "live": grouped_stats(live, "side"),
        },
        "regime": {
            "backtest": grouped_stats(bt, "regime_code"),
            "live": grouped_stats(live, "regime_code"),
        },
        "live_parity": match_live_to_backtest(live, bt),
        "gate_research": gate_research(engine, candles),
        "maxhold_forward": {
            "backtest": maxhold_forward(bt, candles),
            "live": maxhold_forward(live, candles),
        },
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
