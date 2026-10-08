"""Refresh the read-only viewer's backtest snapshot from the latest closed bars."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from trade_viewer import load_trades


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT = DATA_DIR / "backtest_trades.txt"
DEFAULT_METRICS_OUTPUT = DATA_DIR / "backtest_trade_metrics.csv"
METRICS_FIELDS = ["side", "entry_exec_utc8", "exit_exec_utc8", "exit_reason", "mae_pct", "mfe_pct", "gk_pctile", "margin", "pnl_usd"]


def _resolve(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _atomic_write_csv(path: Path, rows: list[dict]) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=METRICS_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def refresh_trade_metrics(snapshot_trades: list[dict], metrics_path: Path) -> int:
    """以 run_backtest.py 預設參數重跑同一引擎，輸出逐筆 MAE／MFE／進場 GK 給 Viewer。

    只讀 run_backtest.py 的設定與研究引擎，不改任何策略程式；筆數或時間對不上就不寫檔，
    避免把不同批次的指標配到錯的交易上。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import pandas as pd
    import analysis_report
    import run_backtest

    kline_path = DATA_DIR / "ETHUSDT_1h_latest730d.csv"
    df = pd.read_csv(kline_path)
    engine = run_backtest._load_engine()
    indicators = engine.compute_indicators(df)
    trades = engine.simulate_v14_detailed(
        indicators, df["datetime"].values, start_bar=None,
        realistic=True, slip_bps=0.0,
        margin_schedule=run_backtest.MARGIN_SCHEDULE,
        extra_cost=5.0,
    )
    rows = []
    for trade in trades:
        rows.append({
            "side": trade["side"],
            "entry_exec_utc8": analysis_report.to_exec_time(str(trade["entry_dt"]).replace("T", " ")),
            "exit_exec_utc8": analysis_report.to_exec_time(str(trade["exit_dt"]).replace("T", " ")),
            "exit_reason": trade.get("exit_reason", ""),
            "mae_pct": trade.get("mae_pct", ""),
            "mfe_pct": trade.get("mfe_pct", ""),
            "gk_pctile": trade.get("gk_pctile", ""),
            "margin": trade.get("margin", ""),
            "pnl_usd": trade.get("pnl_usd", ""),
        })
    expected = {(t["side"], t["entry_time"][:16]) for t in snapshot_trades}
    produced = {(row["side"], row["entry_exec_utc8"][:16]) for row in rows}
    if expected != produced:
        raise ValueError(f"逐筆指標與回測快照不一致（快照 {len(expected)} 筆、重算 {len(produced)} 筆）")
    _atomic_write_csv(metrics_path, rows)
    return len(rows)


def main() -> int:
    output_path = Path(os.environ.get("VIEWER_BACKTEST_PATH", str(DEFAULT_OUTPUT))).expanduser()
    if not output_path.is_absolute():
        output_path = (ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(ROOT / "run_backtest.py"), "--refresh", "-t"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(f"回測快照更新失敗：run_backtest.py 結束碼 {result.returncode}", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode or 1
    if "實際資料：" not in result.stdout or "進出場明細" not in result.stdout:
        print("回測快照更新失敗：輸出缺少資料範圍或交易明細，保留舊快照。", file=sys.stderr)
        return 1

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(result.stdout)
            handle.flush()
            os.fsync(handle.fileno())

        trades = load_trades(temp_path)
        if not trades:
            raise ValueError("回測輸出沒有可解析的已平倉交易")
        os.replace(temp_path, output_path)
        temp_path = None
    except (OSError, ValueError) as exc:
        print(f"回測快照更新失敗：{exc}；保留舊快照。", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    print(f"回測快照已更新：{len(trades)} 筆；資料來源為最新已收盤 K 線。")

    metrics_path = _resolve(os.environ.get("VIEWER_BACKTEST_METRICS_PATH", str(DEFAULT_METRICS_OUTPUT)))
    try:
        count = refresh_trade_metrics(trades, metrics_path)
    except Exception as exc:  # 指標檔失敗不影響主快照；Viewer 會顯示缺值
        print(f"回測逐筆指標更新失敗：{type(exc).__name__}: {exc}；保留舊指標檔。", file=sys.stderr)
        return 0
    print(f"回測逐筆指標已更新：{count} 筆 MAE／MFE／進場 GK。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
