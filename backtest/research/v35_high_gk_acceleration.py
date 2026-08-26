"""V35 — High-GK acceleration overlay (pre-registered confirmation tests).

Question: when the live L strategy rejects a valid 15-bar upside breakout only
because GK is not compressed, can an already-known, close-time TBR exhaustion
signal safely add a small number of L entries?

Anti-look-ahead protocol:
* Candidate signal uses only data available at the signal-bar close.
* Thresholds 35/40/45 are declared before reading the 2026-08 holdout.
* Rule discovery is 2021-03-05..2024-08-25; validation ends 2026-07-30.
* 2026-08 is printed last and is not used to select a threshold.

This is research only. It does not import or modify the production entry code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = Path(__file__).with_name("v14_export_trades.py")
DATA_PATH = ROOT / "data" / "ETHUSDT_1h_latest730d.csv"
OUT_PATH = ROOT / "doc" / "v35_high_gk_acceleration_results.csv"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_overlay_engine():
    """Patch only the L entry predicate; all exits/risk state stay production-identical."""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    signature = (
        "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
        "                          realistic=False, slip_bps=0.0, margin_schedule=None):"
    )
    patched_signature = (
        "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
        "                          realistic=False, slip_bps=0.0, margin_schedule=None,\n"
        "                          overlay_l=None):"
    )
    predicate = "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):"
    patched_predicate = (
        "not np.isnan(pL[i]) and "
        "(pL[i] < L_GK_TH or (overlay_l is not None and bool(overlay_l[i]))) "
        "and brk_up[i]):"
    )
    for old, new in ((signature, patched_signature), (predicate, patched_predicate)):
        if source.count(old) != 1:
            raise RuntimeError(f"engine patch anchor not unique: {old[:70]}")
        source = source.replace(old, new)
    source = source.split("if __name__ == '__main__':")[0]
    spec = importlib.util.spec_from_loader("v35_overlay_engine", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["__file__"] = str(ENGINE_PATH)
    exec(compile(source, str(ENGINE_PATH), "exec"), module.__dict__)
    return module


def strict_percentile(values: pd.Series, window: int = 100) -> pd.Series:
    """Matches the strategy's strict-less-than percentile convention."""
    def rank_pct(w: pd.Series) -> float:
        value = w.iloc[-1]
        if pd.isna(value):
            return np.nan
        return float((w.iloc[:-1] < value).sum() / (len(w) - 1) * 100)
    return values.rolling(window, min_periods=window).apply(rank_pct, raw=False)


def build_tbr_overlay(df: pd.DataFrame, threshold: float) -> np.ndarray:
    """TBR feature is fully observable before the current breakout close is acted on."""
    volume = pd.to_numeric(df["volume"], errors="coerce").replace(0, np.nan)
    taker_buy = pd.to_numeric(df["taker_buy_volume"], errors="coerce")
    tbr_ma5 = taker_buy.rolling(5, min_periods=5).sum() / volume.rolling(5, min_periods=5).sum()
    tbr_pct = strict_percentile(tbr_ma5.shift(1), 100)
    # The engine itself still enforces breakout/session/R-gate/cooldown/cap/CB.
    return (tbr_pct < threshold).fillna(False).to_numpy(bool)


def build_clv_overlay(df: pd.DataFrame, threshold: float) -> np.ndarray:
    """Close-location value: the breakout bar must finish near its high.

    This uses the completed signal bar only, exactly as the live strategy uses
    that bar's close for the breakout decision.  It is deliberately limited to
    0.75/0.80/0.85; no threshold is selected from the sealed August window.
    """
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    clv = (close - low) / (high - low).replace(0, np.nan)
    return (clv >= threshold).fillna(False).to_numpy(bool)


def start_bar(df: pd.DataFrame, start: str) -> int:
    hit = np.flatnonzero(df["datetime"].astype(str).to_numpy() >= start)
    if not len(hit):
        raise ValueError(f"start outside data: {start}")
    return int(hit[0])


def bounded_trades(trades: list[dict], start: str, end: str) -> list[dict]:
    return [t for t in trades if start <= str(t["entry_dt"]) <= end]


def metrics(trades: list[dict], threshold: float | None) -> dict:
    if not trades:
        return dict(trades=0, pnl=0.0, pf=np.nan, mdd=0.0, overlay_trades=0, overlay_pnl=0.0)
    table = pd.DataFrame(trades)
    pnl = float(table["pnl_usd"].sum())
    wins = float(table.loc[table.pnl_usd > 0, "pnl_usd"].sum())
    losses = abs(float(table.loc[table.pnl_usd < 0, "pnl_usd"].sum()))
    equity = table.pnl_usd.cumsum()
    mdd = abs(float((equity - equity.cummax()).min()))
    overlay = table[(table.side == "L") & (table.gk_pctile >= 25)]
    return dict(
        trades=len(table), pnl=round(pnl, 2), pf=round(wins / losses, 3) if losses else np.inf,
        mdd=round(mdd, 2), overlay_trades=len(overlay),
        overlay_pnl=round(float(overlay.pnl_usd.sum()), 2), threshold=threshold,
    )


def run_window(engine, ind, datetimes, overlay, start: str, end: str, slip_bps: float):
    trades = engine.simulate_v14_detailed(
        ind, datetimes, start_bar=start_bar(pd.DataFrame({"datetime": datetimes}), start),
        realistic=True, slip_bps=slip_bps, margin_schedule=None, overlay_l=overlay,
    )
    return bounded_trades(trades, start, end)


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    if not {"volume", "taker_buy_volume", "datetime"}.issubset(df.columns):
        raise RuntimeError("ETH cache lacks required OHLCV/taker-buy columns")
    engine = load_overlay_engine()
    ind = engine.compute_indicators(df)
    datetimes = df["datetime"].astype(str).to_numpy()

    windows = [
        ("IS", "2021-03-05 16:00:00", "2024-08-25 23:59:59"),
        ("OOS", "2024-08-26 00:00:00", "2026-07-30 14:00:00"),
        ("SEALED_2026_08", "2026-08-01 00:00:00", "2026-08-26 14:00:00"),
    ]
    base = np.zeros(len(df), dtype=bool)
    rows: list[dict] = []

    # Parity guard: patched engine with no overlay must remain exactly baseline-equivalent.
    unpatched = importlib.util.spec_from_file_location("v35_unpatched", ENGINE_PATH)
    raw_engine = importlib.util.module_from_spec(unpatched)
    unpatched.loader.exec_module(raw_engine)
    raw = raw_engine.simulate_v14_detailed(raw_engine.compute_indicators(df), datetimes, realistic=True)
    patched = engine.simulate_v14_detailed(ind, datetimes, realistic=True, overlay_l=base)
    if [(t["entry_dt"], t["exit_dt"], t["pnl_usd"]) for t in raw] != [(t["entry_dt"], t["exit_dt"], t["pnl_usd"]) for t in patched]:
        raise RuntimeError("patched engine parity failed")

    for name, start, end in windows:
        base_trades = run_window(engine, ind, datetimes, base, start, end, 0.0)
        base_metrics = metrics(base_trades, None)
        rows.append(dict(window=name, case="baseline", slip_bps=0.0, **base_metrics))
        # TBR is a documented prior hypothesis; CLV is the only additional
        # pre-registered OHLC confirmation.  Do not add further families from
        # this result table.
        families = (
            ("TBR<", (35.0, 40.0, 45.0), build_tbr_overlay, ".0f"),
            ("CLV>=", (0.75, 0.80, 0.85), build_clv_overlay, ".2f"),
        )
        for prefix, thresholds, builder, fmt in families:
            for threshold in thresholds:
                overlay = builder(df, threshold)
                for slip_bps in (0.0, 2.0, 5.0):
                    candidate = run_window(engine, ind, datetimes, overlay, start, end, slip_bps)
                    summary = metrics(candidate, threshold)
                    reference = base_metrics if slip_bps == 0 else metrics(
                        run_window(engine, ind, datetimes, base, start, end, slip_bps), None
                    )
                    rows.append(dict(
                        window=name, case=f"{prefix}{threshold:{fmt}}", slip_bps=slip_bps,
                        delta_pnl=round(summary["pnl"] - reference["pnl"], 2),
                        delta_mdd=round(summary["mdd"] - reference["mdd"], 2), **summary,
                    ))

    output = pd.DataFrame(rows)
    output["data_sha256"] = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()
    output.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"data: {df.datetime.iloc[0]} ~ {df.datetime.iloc[-1]} ({len(df)} bars)")
    print(f"parity: PASS | output: {OUT_PATH}")
    print(output.to_string(index=False))


if __name__ == "__main__":
    main()
